# -*- coding: utf-8 -*-
"""flow_executor.py - 真实设备执行器：ADB 动作原语 + 模板匹配/OCR 条件判定。

动作原语（action 节点 config.action）:
  adb_tap x,y           点击屏幕坐标
  am_start activity     启动 Activity
  wait ms               等待毫秒
  swipe x1,y1,x2,y2,ms  滑动
  key keycode           按键（back/home 等）
条件判定（condition 节点 config.kind）:
  match_image   模板匹配（cv2.matchTemplate ≥ 阈值 0.8）
  ocr_contains  当前屏 OCR 是否包含文本
  expr          布尔值（config.condition / config.expr）

异常约定：判定/动作失败必须通过 last_error 暴露给引擎写入事件与台账，
判定异常向上抛（由引擎记为失败），绝不静默降级为 false。
"""
import os
import sys
import threading
import time
import base64
import numpy as np

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

import cv2  # noqa: E402

from core.device import ADB as _DEV_ADB, ADDR as _DEV_ADDR, EmulatorDevice  # noqa: E402
from core.flow_engine import FlowExecutor, _to_bool  # noqa: E402

MATCH_THRESHOLD = 0.8
_ocr_lock = threading.Lock()
_ocr_engine = None


def _get_ocr():
    """懒加载 RapidOCR（与 gui/server.py 同一策略；首次自动下载模型 ~15MB）。"""
    global _ocr_engine
    if _ocr_engine is None:
        with _ocr_lock:
            if _ocr_engine is None:
                from rapidocr_onnxruntime import RapidOCR
                _ocr_engine = RapidOCR()
    return _ocr_engine


class FlowDeviceExecutor(FlowExecutor):
    def __init__(self, device=None, templates_dir=None,
                 adb=os.environ.get("ADB_EXE") or _DEV_ADB,
                 addr=os.environ.get("ADB_ADDR") or _DEV_ADDR):
        self.device = device
        self.templates_dir = templates_dir
        self._adb = adb
        self._addr = addr
        self.last_error = ""

    def _dev(self):
        if self.device is None:
            self.device = EmulatorDevice(self._adb or _DEV_ADB, self._addr or _DEV_ADDR)
        return self.device

    # ---------- 动作 ----------
    # ---- 47 轮：点击生效自检 helper ----
    def _snap_safe(self):
        """抓当前画面（失败返回 None，不抛异常——设备离线时静默跳过自检）。"""
        try:
            import cv2, numpy as np
            return self._dev().screencap()  # ndarray (BGR)
            
        except Exception:
            return None

    def _diff_score(self, a, b):
        """前后帧差异：返回 {changed, mean_abs, changed_ratio}。阈值保守（防动画误报）。"""
        try:
            import cv2, numpy as np
            if a.shape != b.shape:
                return {"changed": True, "mean_abs": None, "changed_ratio": None}  # 分辨率变化=切页，算有效
            g1 = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY)
            g2 = cv2.cvtColor(b, cv2.COLOR_BGR2GRAY)
            d = cv2.absdiff(g1, g2)
            mean_abs = float(d.mean())
            changed_ratio = float((d > 24).sum()) / d.size  # 显著变化像素占比
            changed = mean_abs >= 0.15 or changed_ratio >= 0.001
            return {"changed": changed, "mean_abs": round(mean_abs, 3), "changed_ratio": round(changed_ratio, 5)}
        except Exception:
            return None

    def execute_action(self, node, ctx):
        cfg = node.get("config") or {}
        action = cfg.get("action") or ""
        name = node.get("name") or action
        self.last_error = ""
        try:
            parts = action.split()
            if parts[0] == "adb_tap":
                # 46 轮根治（双源坐标不一致）：编辑器标记 tap_x/y 优先于 action 串坐标——
                # 标记即真源，用户标记后无需再点「应用为 adb_tap」；action 串坐标作回退
                tx, ty = cfg.get("tap_x"), cfg.get("tap_y")
                if isinstance(tx, (int, float)) and isinstance(ty, (int, float)) and not isinstance(tx, bool) and not isinstance(ty, bool):
                    px, py = int(tx), int(ty)
                else:
                    if len(parts) < 3:
                        raise ValueError("adb_tap 需要坐标（adb_tap x y）")
                    px, py = int(parts[1]), int(parts[2])
                # 47 轮：点击生效自检——tap 前后各抓一帧比对差异；零差异=点击无效果（坐标错/
                # 弹窗遮挡），写入 last_error 与事件日志；不改变流程走向（只补可观测性）
                self.tap_verify = None
                before = self._snap_safe()
                self._dev().tap(px, py)
                import time as _t
                _t.sleep(0.9)  # 等界面反馈（动画/弹窗）
                after = self._snap_safe()
                if before is not None and after is not None:
                    self.tap_verify = self._diff_score(before, after)
                    if self.tap_verify is not None and not self.tap_verify["changed"]:
                        self.last_error = "点击 (%d,%d) 后画面无变化——可能坐标无效、按钮被遮挡或界面未响应" % (px, py)
                self.last_tap = {"x": px, "y": py, "verify": self.tap_verify}
                return True
            if parts[0] == "am_start":
                act = parts[1] if len(parts) > 1 else None
                self._dev().start_app(ctx.get("pkg", ""), act)
                return True
            if parts[0] == "wait":
                ms = int(parts[1]) if len(parts) > 1 else 1000
                time.sleep(max(0.05, ms / 1000.0))
                return True
            if parts[0] == "swipe":
                if len(parts) < 5:
                    raise ValueError("swipe 需要坐标（swipe x1 y1 x2 y2 ms）")
                self._dev().swipe(int(parts[1]), int(parts[2]), int(parts[3]), int(parts[4]),
                                  int(parts[5]) if len(parts) > 5 else 300)
                return True
            if parts[0] == "key":
                if len(parts) < 2:
                    raise ValueError("key 需要键码（key back/home）")
                self._dev().key(parts[1])
                return True
            if parts[0] == "ocr_check":
                return self._ocr_contains(cfg.get("text") or "")
            self.last_error = "未知动作原语: %s" % action
            return False
        except Exception as e:  # noqa: BLE001
            self.last_error = "动作 %s 失败: %s" % (name, e)
            return False

    # ---------- 条件 ----------
    def evaluate_condition(self, node, ctx):
        """判定异常向上抛（引擎记失败），不静默降级为 false。"""
        cfg = node.get("config") or {}
        kind = cfg.get("kind") or "expr"
        self.last_error = ""
        if kind == "match_image":
            return self._match_image(cfg, ctx)
        if kind == "region_match":
            return self._region_match(cfg, ctx)
        if kind == "ocr_contains":
            return self._ocr_contains(cfg.get("text") or "")
        # expr：condition 或 expr 字段（布尔归一化，防 "false" 字符串陷阱）
        try:
            return _to_bool(cfg.get("condition", cfg.get("expr")))
        except ValueError as e:
            self.last_error = str(e)
            raise

    def _match_image(self, cfg, ctx):
        """模板匹配：当前屏截图 vs adapters/templates/<pkg>/<image>。返回 bool。"""
        img_name = cfg.get("image") or ""
        if not img_name:
            raise ValueError("条件节点未选择匹配图像")
        tdir = self.templates_dir or os.path.join(BASE, "adapters", "templates", ctx.get("pkg", ""))
        tpath = os.path.join(tdir, img_name)
        if not os.path.isfile(tpath):
            raise FileNotFoundError("模板不存在: %s" % tpath)
        screen = self._dev().screencap()
        if screen is None or getattr(screen, "size", 0) == 0:
            raise RuntimeError("截图为空（设备离线或保活/虚拟屏问题）")
        tmpl = cv2.imread(tpath)
        if tmpl is None:
            raise RuntimeError("模板无法读取: %s" % tpath)
        try:
            res = cv2.matchTemplate(screen, tmpl, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, _ = cv2.minMaxLoc(res)
        except cv2.error as e:
            raise RuntimeError("匹配失败（模板大于截图?）: %s" % e)
        return float(max_val) >= MATCH_THRESHOLD

    def _region_match(self, cfg, ctx):
        """区域匹配：当前屏截图 → 裁剪 region_match 区域 → 在区域内匹配目标图像（target_crop_b64）。
        阈值使用 config.match_threshold（百分比，默认 80）。
        match_method: 可单选或组合（列表），支持：
          default  整块像素相似度（TM_CCOEFF_NORMED）
          edge     边缘匹配（Canny 后比对，抗背景动画）
          feature  特征点匹配（ORB，抗背景/光照变化）
        多方法组合时采用 OR 逻辑：任一方法命中即视为匹配成功（更稳健）。
        """
        rm = cfg.get("region_match") or {}
        tc = cfg.get("target_crop") or {}
        b64 = cfg.get("target_crop_b64") or ""
        if not rm or not tc or not b64:
            raise ValueError("区域匹配条件未完整配置（需在编辑器圈选区域和目标图像）")
        x, y, w, h = int(rm.get("x", 0)), int(rm.get("y", 0)), int(rm.get("w", 0)), int(rm.get("h", 0))
        if w < 5 or h < 5:
            raise ValueError("匹配区域过小（需至少 5×5 像素）")
        screen = self._dev().screencap()
        if screen is None or getattr(screen, "size", 0) == 0:
            raise RuntimeError("截图为空（设备离线或保活/虚拟屏问题）")
        # 解码目标图像（dataURL: data:image/png;base64,...）
        payload = b64.split(",")[-1]
        try:
            raw = base64.b64decode(payload)
        except Exception as e:
            raise RuntimeError("目标图像数据无法解码: %s" % e)
        arr = np.frombuffer(raw, np.uint8)
        tmpl = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if tmpl is None:
            raise RuntimeError("目标图像数据无法解码为图片")
        sh, sw = screen.shape[:2]
        # 区域越界防御（L2）：负坐标钳 0 —— 负切片会从头绕到尾裁出微小区域导致 matchTemplate -215
        if x < 0 or y < 0:
            x, y = max(0, x), max(0, y)
        # 右/下越界裁齐
        x2, y2 = min(x + w, sw), min(y + h, sh)
        if x >= sw or y >= sh or x2 <= x or y2 <= y:
            raise RuntimeError("匹配区域超出截图边界（区域 %d,%d %dx%d vs 截图 %dx%d）" % (x, y, w, h, sw, sh))
        region = screen[y:y2, x:x2]
        # 模板必须完整落在区域内，否则 matchTemplate 抛裸 -215 断言；这里提前拦截给人话错误
        if tmpl.shape[0] > region.shape[0] or tmpl.shape[1] > region.shape[1]:
            raise RuntimeError(
                "目标图像 %dx%d 大于匹配区域（区域被截为 %dx%d；区域 %d,%d %dx%d vs 截图 %dx%d）"
                "——请重新圈选匹配区域（上次可能框到了截图外）"
                % (tmpl.shape[1], tmpl.shape[0], region.shape[1], region.shape[0], x, y, w, h, sw, sh))
        th = float(cfg.get("match_threshold", 80)) / 100.0

        methods = cfg.get("match_method") or ["default"]
        if isinstance(methods, str):
            methods = [methods]
        if not methods:
            methods = ["default"]

        scores = {}
        try:
            if "default" in methods:
                res = cv2.matchTemplate(region, tmpl, cv2.TM_CCOEFF_NORMED)
                _, v, _, _ = cv2.minMaxLoc(res)
                scores["default"] = float(v)
            if "edge" in methods:
                scores["edge"] = self._match_edge(region, tmpl)
            if "feature" in methods:
                ok, fscore = self._match_feature(region, tmpl)
                scores["feature"] = fscore if ok else None
        except cv2.error as e:
            raise RuntimeError("区域匹配失败（目标图像大于区域?）: %s" % e)

        # OR 逻辑：任一方法达到阈值即命中
        matched = any((s is not None and s >= th) for s in scores.values())
        # 记录各方法得分，便于调试（写入 last_error 之外，由引擎忽略即可）
        self.match_scores = scores
        return matched

    def _match_edge(self, region, tmpl):
        """边缘匹配：Canny 提取后做滑动匹配，抗背景动画扰动。
        模板通常小于区域（区域带余量），沿用 matchTemplate 的滑动机制，不做整体 resize。"""
        def canny(g):
            g = cv2.cvtColor(g, cv2.COLOR_BGR2GRAY)
            g = cv2.GaussianBlur(g, (3, 3), 0)
            return cv2.Canny(g, 50, 150)
        re = canny(region)
        te = canny(tmpl)
        if re.shape[0] < te.shape[0] or re.shape[1] < te.shape[1]:
            # 模板比区域大，退化到整块比对
            if re.shape != te.shape:
                re = cv2.resize(re, (te.shape[1], te.shape[0]))
            res = cv2.matchTemplate(re, te, cv2.TM_CCOEFF_NORMED)
            _, v, _, _ = cv2.minMaxLoc(res)
            return float(v)
        res = cv2.matchTemplate(re, te, cv2.TM_CCOEFF_NORMED)
        _, v, _, _ = cv2.minMaxLoc(res)
        return float(v)

    def _match_feature(self, region, tmpl):
        """特征点匹配：ORB 关键点 + BFMatcher，返回 (成功, 得分)。
        得分定义为 inliers / max(模板关键点数, 1)，抗背景/光照变化。"""
        try:
            orb = cv2.ORB_create()
            kp1, des1 = orb.detectAndCompute(cv2.cvtColor(region, cv2.COLOR_BGR2GRAY), None)
            kp2, des2 = orb.detectAndCompute(cv2.cvtColor(tmpl, cv2.COLOR_BGR2GRAY), None)
            if des1 is None or des2 is None or len(des2) < 4:
                return (False, 0.0)
            bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
            matches = bf.match(des1, des2)
            if not matches:
                return (False, 0.0)
            matches = sorted(matches, key=lambda m: m.distance)
            # 取距离中位数衡量一致性（越小越相似）
            dists = [m.distance for m in matches[: min(len(matches), 30)]]
            avg_dist = sum(dists) / len(dists)
            # 归一化：距离 0→1.0，距离 100→0.0
            score = max(0.0, 1.0 - avg_dist / 100.0)
            return (True, float(score))
        except Exception:
            return (False, 0.0)

    def _ocr_contains(self, text):
        if not text:
            raise ValueError("OCR 判定未填写目标文字")
        engine = _get_ocr()
        screen = self._dev().screencap()
        if screen is None or getattr(screen, "size", 0) == 0:
            raise RuntimeError("截图为空，无法 OCR")
        ok, buf = cv2.imencode(".png", screen)
        if not ok:
            raise RuntimeError("截图编码失败")
        result, _ = engine(buf.tobytes())
        if not result:
            return False
        for line in result:
            if text in (line[1] or ""):
                return True
        return False
