# -*- coding: utf-8 -*-
"""autofw GUI 控制面板服务端：最小本地 API + 静态页面。
启动：.venv\\Scripts\\python gui\\server.py  →  http://127.0.0.1:8765
API：
  GET  /                 → 控制面板页面
  GET  /api/status       → {running, pipeline, completed, intent_tail, log_tail}
  POST /api/start        → 启动流水线（main.py，后台子进程）
  POST /api/stop         → 停止流水线（终止子进程）
"""
import base64
import hashlib
import io
import json
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # autofw 根
PY = os.path.join(BASE, ".venv", "Scripts", "python.exe")
STATE = os.path.join(BASE, "state")
PORT = 8765

# 面板进程也接 app.log：否则面板触发的 flow/批量执行日志不会出现在主控制台「运行日志」（R13）
import logging as _logging
import logging.handlers as _logging_handlers

_app_log = _logging.getLogger("autofw")
if not any(getattr(h, "_autofw_gui", False) for h in _app_log.handlers):
    _fh = _logging_handlers.RotatingFileHandler(
        os.path.join(STATE, "app.log"), maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8")
    _fh.setFormatter(_logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    _fh._autofw_gui = True
    _app_log.addHandler(_fh)
    _app_log.setLevel(_logging.INFO)

# 打印节点专用日志：state/prints.log（前端「打印输出」面板独立展示）
_prints_logger = _logging.getLogger("autofw.prints")
if not any(getattr(h, "_autofw_gui_prints", False) for h in _prints_logger.handlers):
    _pfh = _logging_handlers.RotatingFileHandler(
        os.path.join(STATE, "prints.log"), maxBytes=2 * 1024 * 1024, backupCount=2, encoding="utf-8")
    _pfh.setFormatter(_logging.Formatter("%(asctime)s %(message)s"))
    _pfh._autofw_gui_prints = True
    _prints_logger.addHandler(_pfh)
    _prints_logger.setLevel(_logging.INFO)
    _prints_logger.propagate = False

STEPS_DIR = os.path.join(STATE, "steps")
LEDGER = os.path.join(STEPS_DIR, "ledger.json")
MAX_BODY = 48 * 1024 * 1024  # 48MB（47g：graph 含内联图时 8MB 不够保存）

proc = None          # 流水线子进程
proc_lock = threading.Lock()
steps_lock = threading.Lock()
paused = False       # 失败暂停状态（人工可恢复）


def ensure_steps_dir():
    os.makedirs(STEPS_DIR, exist_ok=True)


def load_ledger():
    try:
        with open(LEDGER, "r", encoding="utf-8") as f:
            return json.load(f)
    except OSError:
        return []


def save_ledger(records):
    with open(LEDGER, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)


def submit_step(payload):
    """提交一步人工操作：截图(base64) + 点击坐标 + 判定结果。
    校验：base64 可解码 / PNG/JPEG 魔数 / 坐标在截图范围内 / 重复提交(hash)。"""
    global paused
    try:
        image_b64 = payload.get("image_b64", "")
        points = payload.get("points", [])
        verdict = payload.get("verdict", "success")
        note = (payload.get("note") or "").strip()
        name = (payload.get("name") or "screenshot").strip()
    except Exception:
        return {"ok": False, "error": "请求体解析失败"}

    # 1. base64 解码
    try:
        raw = base64.b64decode(image_b64)
    except Exception:
        return {"ok": False, "error": "截图数据无法解码（base64 无效）"}
    if not raw:
        return {"ok": False, "error": "截图内容为空"}
    # 2. 格式魔数校验（PNG/JPEG）
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        ext = "png"
    elif raw[:2] == b"\xff\xd8":
        ext = "jpg"
    else:
        return {"ok": False, "error": "不支持的文件格式：仅支持 PNG/JPEG 截图"}
    # 3. 重复提交（内容 hash，10 分钟内同图拒绝）
    hex_hash = hashlib.sha256(raw).hexdigest()[:16]
    now = time.time()
    for r in load_ledger():
        if r.get("hash") == hex_hash and now - r.get("ts", 0) < 600:
            return {"ok": False, "error": "重复提交：该截图 10 分钟内已提交过（台账 #%s）" % r.get("id")}
    # 4. 坐标校验（需图片尺寸）
    from PIL import Image
    import io as _io
    try:
        with Image.open(_io.BytesIO(raw)) as im:
            img_w, img_h = im.size
    except Exception:
        return {"ok": False, "error": "截图无法解析（图片数据损坏）"}
    if not points or not isinstance(points, list):
        return {"ok": False, "error": "未标记点击位置（至少一个）"}
    clean_points = []
    for p in points:
        try:
            if isinstance(p, dict):
                x, y = int(p.get("x")), int(p.get("y"))
            else:
                x, y = int(p[0]), int(p[1])
        except Exception:
            return {"ok": False, "error": "坐标格式错误：需要 [x,y] 数组或 {x,y} 对象"}
        if x < 0 or y < 0 or x >= img_w or y >= img_h:
            return {"ok": False, "error": "坐标越界：(%d,%d) 超出截图 %dx%d" % (x, y, img_w, img_h)}
        clean_points.append([x, y])
    if verdict not in ("success", "fail"):
        return {"ok": False, "error": "判定结果无效（应为 success/fail）"}

    # 5. 保存截图与台账
    with steps_lock:
        ensure_steps_dir()
        from datetime import datetime as _dt
        rec_id = "step_%s" % _dt.now().strftime("%Y%m%d_%H%M%S_%f")
        img_path = os.path.join(STEPS_DIR, rec_id + "." + ext)
        with open(img_path, "wb") as f:
            f.write(raw)
        records = load_ledger()
        record = {
            "id": rec_id,
            "name": name,
            "image": os.path.basename(img_path),
            "points": clean_points,
            "verdict": verdict,
            "note": note,
            "ts": now,
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "hash": hex_hash,
            "paused": verdict == "fail",
        }
        records.append(record)
        save_ledger(records)
        if verdict == "fail":
            paused = True
    return {"ok": True, "id": rec_id, "paused": paused,
            "msg": "已记录（%s）" % ("失败，流程已暂停" if verdict == "fail" else "成功")}


def resume():
    global paused
    with steps_lock:
        paused = False
    return {"ok": True, "msg": "已恢复流程（暂停状态清除）"}


# ============ OCR 识别（RapidOCR，懒加载引擎） ============
OCR_ENGINE = None
OCR_AUDIT = os.path.join(STATE, "ocr_audit.log")


def _ocr_engine():
    """懒加载 OCR 引擎（首次调用才初始化，避免拖慢 server 启动）。"""
    global OCR_ENGINE
    if OCR_ENGINE is None:
        from rapidocr_onnxruntime import RapidOCR
        OCR_ENGINE = RapidOCR()
    return OCR_ENGINE


def ocr_recognize(payload):
    """识别上传图片中的文字。返回 {ok, texts:[{text, confidence, box}], elapsed_ms, engine}。"""
    import base64 as _b64
    try:
        image_b64 = payload.get("image_b64", "")
    except Exception:
        return {"ok": False, "error": "请求体解析失败"}
    try:
        raw = _b64.b64decode(image_b64)
    except Exception:
        _audit_ocr("REJECT", "base64_invalid", 0, 0)
        return {"ok": False, "error": "图片数据无法解码（base64 无效）"}
    if not raw:
        _audit_ocr("REJECT", "empty", 0, 0)
        return {"ok": False, "error": "图片内容为空"}
    if raw[:8] != b"\x89PNG\r\n\x1a\n" and raw[:2] != b"\xff\xd8":
        _audit_ocr("REJECT", "bad_format", 0, len(raw))
        return {"ok": False, "error": "不支持的图片格式：仅支持 PNG/JPEG"}
    # 尺寸限制（防超大图拖慢）
    if len(raw) > 10 * 1024 * 1024:
        _audit_ocr("REJECT", "too_large", 0, len(raw))
        return {"ok": False, "error": "图片过大（上限 10MB）"}
    import io as _io
    import time as _time
    import numpy as _np
    import cv2 as _cv2
    try:
        img = _cv2.imdecode(_np.frombuffer(raw, _np.uint8), _cv2.IMREAD_COLOR)
        if img is None:
            return {"ok": False, "error": "图片无法解析（数据损坏）"}
    except Exception:
        return {"ok": False, "error": "图片无法解析（数据损坏）"}
    t0 = _time.time()
    try:
        engine = _ocr_engine()
        result, _ = engine(img)
    except Exception as e:
        _audit_ocr("ERROR", "engine_fail: %r" % e, 0, 0)
        return {"ok": False, "error": "OCR 服务不可用：%s" % str(e)[:120]}
    elapsed = (_time.time() - t0) * 1000
    texts = []
    if result:
        for box, text, score in result:
            pts = [[float(p[0]), float(p[1])] for p in box]
            texts.append({"text": str(text), "confidence": round(float(score), 4),
                          "box": pts})
    _audit_ocr("OK", "texts=%d" % len(texts), elapsed, len(raw))
    return {"ok": True, "texts": texts, "elapsed_ms": round(elapsed, 1),
            "engine": "rapidocr-onnxruntime", "count": len(texts)}


def _audit_ocr(status, detail, elapsed_ms, bytes_len):
    try:
        with open(OCR_AUDIT, "a", encoding="utf-8") as f:
            f.write("%s %s %s elapsed=%dms bytes=%d\n" % (
                time.strftime("%Y-%m-%d %H:%M:%S"), status, detail, elapsed_ms, bytes_len))
    except OSError:
        pass


# ============ 待手动处理（失败任务台账） ============
import sys as _sys
if BASE not in _sys.path:
    _sys.path.insert(0, BASE)
import core.failures as failures_core
failures_lock = threading.Lock()

# ============ 图形化工作流（flows） ============
import core.flow_engine as flow_engine
from flow_executor import FlowDeviceExecutor
flow_lock = threading.Lock()
_flow_runner = None
_flow_runner_lock = threading.Lock()


# ============ 子任务（小任务）模型：目录化存储 + 批量执行 ============
# 存储布局：state/flows/<pkg>/meta.json（任务台账）+ state/flows/<pkg>/<taskId>.json（每任务一个 flow）
# 兼容：state/flows/<pkg>.json 旧单文件在启动时自动迁移为 <pkg>/main.json。
import re as _re  # noqa: E402（此处先导入，供任务模型使用）

FLOWS_ROOT = flow_engine.FLOWS_DIR
MAX_TASK_VERSION_KEEP = 20
MUMU_WAIT_START = 15      # 启动等待（秒）
MUMU_WAIT_STOP = 12       # 停止等待（秒）
tasks_lock = threading.RLock()
# MuMuManager 实例锁：launch/info/shutdown 为单实例 CLI，并发调用（如前端状态轮询
# 的 info 与启动请求的 launch 同时发生）会被 MuMuManager 互斥拒绝（rc≠0 无输出），
# 导致启动偶发失败。所有 MuMuManager 调用必须串行化。
_MUMU_LOCK = threading.Lock()
def _mumu_run(args, timeout=10, retries=3, retry_gap=0.6):
    """串行化执行 MuMuManager 命令，返回 (returncode, stdout, stderr)。

    所有 MuMuManager 调用经此函数统一串行化（launch/info/shutdown 为单实例 CLI，
    高频或并发调用会被互斥拒绝导致偶发 rc≠0 且无输出）。该拒绝是“实例忙”的瞬态
    错误，故内置重试（默认 3 次，间隔 retry_gap）消化瞬态失败；调用方仍以轮询真实
    状态兜底，不依赖单次 rc。

    环境注意：面板 server 经 venv 启动后，os.environ["PATH"] 常被 venv 站点路径污染，
    导致 MuMuManager.exe（控制台程序，依赖系统 DLL 搜索路径）启动即 rc=1 无输出。
    因此此处构造一份“干净环境”：保留 SystemRoot/SystemDrive/COMSPEC 等，并将系统
    PATH（system32 + Windows + MuMu 安装目录 + 原 PATH）合并传入。
    """
    env = dict(os.environ)
    mumu_dir = os.path.dirname(MUMU_MANAGER)
    sys_paths = [r"C:\Windows\system32", r"C:\Windows", mumu_dir]
    orig_path = env.get("PATH") or ""
    cleaned = orig_path.split(os.pathsep) if orig_path else []
    # 去重合并：系统路径优先，并补 MuMu 目录
    seen, merged = set(), []
    for p in sys_paths + cleaned:
        if p and p not in seen:
            seen.add(p); merged.append(p)
    env["PATH"] = os.pathsep.join(merged)
    if not env.get("SystemRoot"):
        env["SystemRoot"] = r"C:\Windows"
    if not env.get("SystemDrive"):
        env["SystemDrive"] = r"C:"
    if not env.get("COMSPEC"):
        env["COMSPEC"] = r"C:\Windows\system32\cmd.exe"
    with _MUMU_LOCK:
        last_rc, last_out, last_err = -1, "", ""
        for attempt in range(retries):
            try:
                r = subprocess.run([MUMU_MANAGER] + args, capture_output=True, text=True,
                                   timeout=timeout, encoding="utf-8", errors="replace", env=env)
                last_rc, last_out, last_err = r.returncode, (r.stdout or ""), (r.stderr or "")
                # rc=0 或确有输出（即便 rc≠0 也视为有效响应）即接受
                if r.returncode == 0 or last_out.strip():
                    return last_rc, last_out, last_err
            except Exception as e:
                last_rc, last_out, last_err = -1, "", "%s: %s" % (type(e).__name__, str(e)[:200])
            if attempt < retries - 1:
                time.sleep(retry_gap)
        return last_rc, last_out, last_err
_meta_warnings = {}       # pkg -> 台账损坏重建提示（不崩溃，随 API 返回 warning 字段）
_run_all = None           # run-all 会话（running/stop/done…）
_run_all_lock = threading.Lock()
_TASK_ID_RE = _re.compile(r"^[a-z0-9][a-z0-9-]*$")


def _safe_pkg(pkg):
    """目录名净化（与 flow_engine._flow_path 同一规则，保证迁移前后一致）。"""
    return _re.sub(r"[\\/:*?\"<>|\s]+", "_", pkg).strip("._") or "default"


def _valid_task_id(task_id):
    return bool(task_id) and bool(_TASK_ID_RE.match(task_id)) and len(task_id) <= 64


def _flow_dir(pkg):
    return os.path.join(FLOWS_ROOT, _safe_pkg(pkg))


def task_path(pkg, task_id):
    return os.path.join(_flow_dir(pkg), task_id + ".json")


def save_meta(meta, dpath=None):
    """写 meta.json：先写 .tmp 再 os.replace（防写坏）。"""
    if dpath is None:
        dpath = _flow_dir(meta.get("pkg", ""))
    os.makedirs(dpath, exist_ok=True)
    mpath = os.path.join(dpath, "meta.json")
    tmp = mpath + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    _safe_replace(tmp, mpath)


def _rebuild_meta(dpath, pkg):
    """重建默认台账（meta 缺失/损坏时）：默认 main 任务 + 扫描已有任务文件防孤儿。"""
    tasks = [{"id": "main", "name": "默认任务", "enabled": True, "order": 0}]
    seen = {"main"}
    order = 1
    try:
        fnames = sorted(f for f in os.listdir(dpath)
                        if f.endswith(".json") and f != "meta.json")
    except OSError:
        fnames = []
    for fn in fnames:
        tid = fn[:-5]
        if not _valid_task_id(tid):
            continue
        if tid in seen:
            # main 已在默认任务中：仅尝试补全显示名（读取 flow 文件 name 字段）
            if tid == "main":
                try:
                    with open(os.path.join(dpath, fn), "r", encoding="utf-8") as f:
                        flow = json.load(f)
                    nm = (flow.get("name") or "").strip()
                    if nm:
                        tasks[0]["name"] = nm
                except (OSError, ValueError):
                    pass
            continue
        name = tid
        try:
            with open(os.path.join(dpath, fn), "r", encoding="utf-8") as f:
                flow = json.load(f)
            name = (flow.get("name") or "").strip() or tid
        except (OSError, ValueError):
            pass
        seen.add(tid)
        tasks.append({"id": tid, "name": name, "enabled": True, "order": order})
        order += 1
    meta = {"pkg": pkg, "tasks": tasks}
    save_meta(meta, dpath=dpath)
    return meta


def load_meta(pkg):
    """读任务台账。损坏（JSON 解析失败）→ 备份 meta.json.corrupt-<ts> 并重建默认，不崩溃。"""
    dpath = _flow_dir(pkg)
    mpath = os.path.join(dpath, "meta.json")
    if not os.path.isdir(dpath):
        return None
    if not os.path.isfile(mpath):
        with tasks_lock:
            return _rebuild_meta(dpath, pkg)
    try:
        with open(mpath, "r", encoding="utf-8") as f:
            meta = json.load(f)
        if not isinstance(meta, dict) or not isinstance(meta.get("tasks"), list):
            raise ValueError("meta 结构无效")
        return meta
    except (OSError, ValueError):
        with tasks_lock:
            ts = time.strftime("%Y%m%d_%H%M%S")
            try:
                os.replace(mpath, "%s.corrupt-%s" % (mpath, ts))
                _meta_warnings[pkg] = "任务台账损坏，已备份为 meta.json.corrupt-%s 并重建" % ts
            except OSError:
                _meta_warnings[pkg] = "任务台账损坏且无法备份，已重建"
            return _rebuild_meta(dpath, pkg)


def meta_warning(pkg):
    return _meta_warnings.get(pkg)


def meta_tasks(pkg):
    """按 order 排序的任务列表（无 meta 返回 None）。"""
    meta = load_meta(pkg)
    if meta is None:
        return None
    return sorted(meta.get("tasks", []), key=lambda t: (t.get("order", 0), t.get("id", "")))


def default_task_id(meta):
    """默认任务：order 最小的 enabled 任务；都 disabled 则第一个任务。"""
    tasks = meta.get("tasks", [])
    enabled = [t for t in tasks if t.get("enabled", True)]
    pool = enabled or tasks
    if not pool:
        return None
    return min(pool, key=lambda t: (t.get("order", 0), t.get("id", "")))["id"]


def task_load(pkg, task_id):
    if not _valid_task_id(task_id):
        return None
    try:
        with open(task_path(pkg, task_id), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


# 51 轮：历史快照瘦身——剥离大字段（内联图 b64）。快照仅用于版本回滚，当前 graph 永远完整；
# 不剥离会让 20 份快照 x 每份几 MB 内联图把任务 JSON 滚到几百 MB（task5 曾达 565MB，加载即崩）
STRIP_INLINE_KEYS = ("tap_img_data", "target_crop_b64", "image_b64")
STRIP_INLINE_LIMIT = 200 * 1024  # >200KB 视为大图内联


def _strip_inline(obj):
    """递归剥离 dict/list 内超长内联图字段（原地修改并计数）。"""
    n = 0
    if isinstance(obj, dict):
        for k, v in list(obj.items()):
            if k in STRIP_INLINE_KEYS and isinstance(v, str) and len(v) > STRIP_INLINE_LIMIT:
                obj[k] = None
                n += 1
            elif isinstance(v, (dict, list)):
                n += _strip_inline(v)
    elif isinstance(obj, list):
        for it in obj:
            if isinstance(it, (dict, list)):
                n += _strip_inline(it)
    return n


def _safe_replace(tmp, path, max_retries=8, backoff=0.25):
    """原子替换封装（替代裸 os.replace）。

    根因：Windows 下外部扫描代理（如 HFComponents/SklearnHF.pyd）或 Windows Defender
    实时防护会对刚落盘的大文件（如 task8.json 3.97MB）短暂持锁，导致 os.replace
    报 [WinError 5] 拒绝访问。该锁是瞬时的，扫描结束即释放。
    v2（2026-09-05）：线性退避总窗口仅 ~2s，实测不够（task6.json 再次失败）；
    改为指数退避 backoff*2^i（单次封顶 3s），默认 8 次总窗口 ~13s，覆盖更长的扫描持锁。
    耗尽后抛出最后一个异常，错误信息不变。
    """
    last = None
    for i in range(max_retries):
        try:
            os.replace(tmp, path)
            return
        except OSError as e:
            last = e
            if i < max_retries - 1:
                time.sleep(min(backoff * (2 ** i), 3.0))
    raise last


def task_save(pkg, task_id, payload):
    """保存指定任务 flow：version+1、versions 保留最近 20 版、updated 刷新。"""
    if not _valid_task_id(task_id):
        return {"ok": False, "error": "任务 ID 无效"}
    graph = payload.get("graph")
    if not isinstance(graph, dict) or not isinstance(graph.get("nodes"), list):
        return {"ok": False, "error": "graph 结构无效（缺 nodes）"}
    subgraphs = payload.get("subgraphs") or {}
    name = (payload.get("name") or pkg).strip() or pkg
    path = task_path(pkg, task_id)
    if not os.path.isfile(path):
        return {"ok": False, "error": "任务不存在：%s/%s" % (pkg, task_id)}
    with tasks_lock:
        try:
            with open(path, "r", encoding="utf-8") as f:
                old = json.load(f)
        except (OSError, ValueError):
            old = None
        version = (old.get("version") or 0) + 1 if old else 1
        rec = {"pkg": pkg, "name": name, "version": version,
               "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
               "graph": graph, "subgraphs": subgraphs, "versions": []}
        if old:
            hist = old.get("versions") or []
            snap = {"version": old.get("version", 0),
                    "updated": old.get("updated", ""),
                    "graph": old.get("graph"), "subgraphs": old.get("subgraphs")}
            _strip_inline(snap)  # 51 轮：快照剥离内联大图，防历史版本滚到数百 MB
            hist.append(snap)
            rec["versions"] = hist[-MAX_TASK_VERSION_KEEP:]
        tmp = path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(rec, f, ensure_ascii=False, indent=2)
            _safe_replace(tmp, path)
        except OSError as e:
            return {"ok": False, "error": "保存失败（磁盘/权限）: %s" % e}
    return {"ok": True, "version": version, "task_id": task_id, "msg": "已保存 v%d" % version}


def _start_node():
    return {"id": "s1", "type": "start", "name": "开始",
            "pos": {"x": 40, "y": 60}, "config": {}}


def task_add(pkg, name):
    """新增任务：生成空 flow（start 节点）+ meta 追加并持久化。id 规则：main 之后 task2/task3…（跳过已存在）。"""
    name = (name or "").strip()
    if not name:
        return {"ok": False, "error": "任务名称不能为空"}
    with tasks_lock:
        meta = load_meta(pkg)
        if meta is None:
            return {"ok": False, "error": "工作流不存在：%s" % pkg}
        existing = {t.get("id") for t in meta["tasks"]}
        n = 2
        while "task%d" % n in existing:
            n += 1
        tid = "task%d" % n
        order = max([t.get("order", 0) for t in meta["tasks"]] or [-1]) + 1
        flow = {"pkg": pkg, "name": name, "version": 1,
                "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
                "graph": {"nodes": [_start_node()], "edges": []},
                "subgraphs": {}, "versions": []}
        path = task_path(pkg, tid)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(flow, f, ensure_ascii=False, indent=2)
        _safe_replace(tmp, path)
        meta["tasks"].append({"id": tid, "name": name, "enabled": True, "order": order})
        save_meta(meta)
    audit("TASK_ADD", "%s/%s (%s)" % (pkg, tid, name))
    return {"ok": True, "task": {"id": tid, "name": name, "enabled": True, "order": order},
            "msg": "已创建任务「%s」（%s）" % (name, tid)}


def task_rename(pkg, payload):
    tid = (payload.get("id") or "").strip()
    name = (payload.get("name") or "").strip()
    if not _valid_task_id(tid):
        return {"ok": False, "error": "任务 ID 无效"}
    if not name:
        return {"ok": False, "error": "任务名称不能为空"}
    with tasks_lock:
        meta = load_meta(pkg)
        if meta is None:
            return {"ok": False, "error": "工作流不存在：%s" % pkg}
        for t in meta["tasks"]:
            if t.get("id") == tid:
                t["name"] = name
                save_meta(meta)
                # 同步 flow 文件内 name 字段（台账重建时名称保持一致）
                path = task_path(pkg, tid)
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        flow = json.load(f)
                    flow["name"] = name
                    with open(path + ".tmp", "w", encoding="utf-8") as f:
                        json.dump(flow, f, ensure_ascii=False, indent=2)
                    _safe_replace(path + ".tmp", path)
                except (OSError, ValueError):
                    pass
                audit("TASK_RENAME", "%s/%s → %s" % (pkg, tid, name))
                return {"ok": True, "msg": "已重命名任务为「%s」" % name}
        return {"ok": False, "error": "任务不存在：%s" % tid}


def task_delete(pkg, payload):
    tid = (payload.get("id") or "").strip()
    if not _valid_task_id(tid):
        return {"ok": False, "error": "任务 ID 无效"}
    with tasks_lock:
        meta = load_meta(pkg)
        if meta is None:
            return {"ok": False, "error": "工作流不存在：%s" % pkg}
        if not any(t.get("id") == tid for t in meta["tasks"]):
            return {"ok": False, "error": "任务不存在：%s" % tid}
        if len(meta["tasks"]) <= 1:
            return {"ok": False, "error": "至少保留一个任务"}
        meta["tasks"] = [t for t in meta["tasks"] if t.get("id") != tid]
        save_meta(meta)
        try:
            os.remove(task_path(pkg, tid))
        except OSError:
            pass
        audit("TASK_DEL", "%s/%s" % (pkg, tid))
        return {"ok": True, "msg": "已删除任务（%s）" % tid}


def task_toggle(pkg, payload):
    tid = (payload.get("id") or "").strip()
    enabled = payload.get("enabled")
    if not _valid_task_id(tid):
        return {"ok": False, "error": "任务 ID 无效"}
    if not isinstance(enabled, bool):
        return {"ok": False, "error": "enabled 必须是布尔值"}
    with tasks_lock:
        meta = load_meta(pkg)
        if meta is None:
            return {"ok": False, "error": "工作流不存在：%s" % pkg}
        for t in meta["tasks"]:
            if t.get("id") == tid:
                t["enabled"] = enabled
                save_meta(meta)
                audit("TASK_TOGGLE", "%s/%s → %s" % (pkg, tid, enabled))
                return {"ok": True, "enabled": enabled,
                        "msg": "已%s「%s」" % ("启用" if enabled else "停用", t["name"])}
        return {"ok": False, "error": "任务不存在：%s" % tid}


def task_order(pkg, payload):
    ids = payload.get("ids")
    if not isinstance(ids, list) or not ids:
        return {"ok": False, "error": "顺序列表无效"}
    if any(not _valid_task_id(i) for i in ids):
        return {"ok": False, "error": "顺序包含无效任务 ID"}
    with tasks_lock:
        meta = load_meta(pkg)
        if meta is None:
            return {"ok": False, "error": "工作流不存在：%s" % pkg}
        by_id = {t.get("id"): t for t in meta["tasks"]}
        missing = [i for i in ids if i not in by_id]
        if missing:
            return {"ok": False, "error": "顺序包含不存在的任务：%s" % ",".join(missing)}
        for i, tid in enumerate(ids):
            by_id[tid]["order"] = i
        # 补上未提及的（防御）
        extra = [t for t in meta["tasks"] if t.get("id") not in ids]
        for i, t in enumerate(extra):
            t["order"] = len(ids) + i
        save_meta(meta)
    audit("TASK_ORDER", "%s: %s" % (pkg, " -> ".join(ids)))
    return {"ok": True, "msg": "顺序已保存（%d 个任务）" % len(ids)}


def tasks_get(pkg):
    tasks = meta_tasks(pkg)
    if tasks is None:
        return {"ok": False, "error": "工作流不存在：%s" % pkg}
    resp = {"ok": True, "pkg": pkg, "tasks": tasks, "count": len(tasks)}
    w = meta_warning(pkg)
    if w:
        resp["warning"] = w
    return resp


def _resolve_default_flow(pkg):
    """解析默认任务 flow：(flow, task_id)；无目录化存储则回退旧单文件。"""
    meta = load_meta(pkg)
    if meta is not None:
        tid = default_task_id(meta)
        if tid:
            return task_load(pkg, tid), tid
        return None, None
    return flow_engine.load_flow(pkg), None


# ---------- run-all 批量执行（只执行勾选任务，按新顺序串行） ----------

def run_all_start(pkg):
    """启动批量执行：读 meta → 按 order 取 enabled 任务 → 串行执行（每任务独立 Runner/Executor）。"""
    global _run_all
    meta = load_meta(pkg)
    if meta is None:
        return {"ok": False, "error": "工作流不存在：%s" % pkg}
    tasks = [t for t in meta.get("tasks", []) if t.get("enabled", True)]
    tasks.sort(key=lambda t: (t.get("order", 0), t.get("id", "")))
    if not tasks:
        return {"ok": False, "error": "没有已勾选（启用）的任务"}
    with _run_all_lock:
        if _run_all is not None and _run_all.get("running"):
            return {"ok": False, "error": "已有批量执行在进行中"}
        with _flow_runner_lock:
            if _flow_runner is not None and not getattr(_flow_runner, "_done", True):
                return {"ok": False, "error": "已有工作流在运行，请先停止"}
        session = {"pkg": pkg, "running": True, "task_index": 0,
                   "task_id": None, "task_name": None, "total": len(tasks),
                   "done": [], "error": None, "stop": False, "runner": None,
                   "last_log": "准备执行 %d 个勾选任务" % len(tasks)}
        _run_all = session
    threading.Thread(target=_run_all_worker, args=(pkg, tasks, session), daemon=True).start()
    return {"ok": True, "msg": "已启动批量执行（%d 个勾选任务）" % len(tasks)}


def _run_all_worker(pkg, tasks, session):
    """run-all 工作线程：串行执行每个勾选任务；stop 标志置位后当前任务结束即跳出。"""
    try:
        for i, t in enumerate(tasks):
            session["task_index"] = i
            session["task_id"] = t["id"]
            session["task_name"] = t["name"]
            session["last_log"] = "正在执行任务「%s」（%d/%d）" % (t["name"], i + 1, len(tasks))
            flow = task_load(pkg, t["id"])
            if flow is None:
                session["done"].append({"id": t["id"], "name": t["name"],
                                        "ok": False, "status": "missing"})
                session["last_log"] = "任务「%s」flow 文件缺失，跳过" % t["name"]
                if session.get("stop"):
                    break
                continue
            errors = [x for x in flow_engine.validate_flow(flow) if x.get("level") == "error"]
            if errors:
                session["done"].append({"id": t["id"], "name": t["name"], "ok": False,
                                        "status": "invalid", "note": errors[0]["msg"]})
                session["last_log"] = "任务「%s」校验未通过，跳过（%s）" % (t["name"], errors[0]["msg"])
                if session.get("stop"):
                    break
                continue
            executor = FlowDeviceExecutor()
            runner = flow_engine.FlowRunner(executor, flow)
            session["runner"] = runner
            try:
                summary = runner.run()
            finally:
                session["runner"] = None
            status = summary.get("status", "error")
            rec = dict(summary)
            rec["note"] = "任务「%s」：%s" % (t["name"], summary.get("note", ""))
            try:
                flow_engine.save_run_log(pkg, rec)
            except Exception:  # noqa: BLE001
                pass
            session["done"].append({"id": t["id"], "name": t["name"], "ok": status == "ok",
                                    "status": status, "note": summary.get("note", "")})
            session["last_log"] = "任务「%s」执行完成：%s" % (t["name"], status)
            if session.get("stop"):
                session["last_log"] = "已停止：完成 %d/%d 个任务" % (i + 1, len(tasks))
                break
        else:
            session["last_log"] = "全部 %d 个任务执行完成" % len(tasks)
    except Exception as e:  # noqa: BLE001
        session["error"] = str(e)
        session["last_log"] = "批量执行异常：%s" % e
    finally:
        session["running"] = False
        session["stop"] = False
        session.pop("runner", None)


# ---------- 模拟器启停（MuMuManager 官方 CLI，语义参照 core/device.py emulator_restart） ----------

def mumu_status_payload():
    """overview/status 用：get_mumu_status + installed/running/adb 布尔别名。"""
    st = get_mumu_status()
    st["installed"] = bool(st.get("detected") or os.path.exists(MUMU_MANAGER))
    st["running"] = bool(st.get("running") or st.get("android_started") or st.get("adb_ok"))
    st["adb"] = bool(st.get("adb_ok"))
    return st


def _mumu_is_up(st=None):
    st = st or get_mumu_status()
    return bool(st.get("running") or st.get("android_started") or st.get("adb_ok"))


def mumu_start():
    """启动模拟器：已运行直接返回；否则 launch 并轮询等待（≤15s）。

    注意：MuMuManager.exe 在部分子进程环境（无关联控制台）下偶发 returncode≠0
    且无 stdout/stderr 输出，导致单次 launch 调用看似“失败”，但模拟器实际已启动。
    因此不再以单次 launch 的 returncode 作为成败判据，而是发起 launch 后统一用
    get_mumu_status() 轮询真实状态（与 mumu_stop 的轮询兜底对称）。
    """
    st = get_mumu_status()
    if _mumu_is_up(st):
        return {"ok": True, "state": "running", "detail": st.get("detail")}
    if not os.path.exists(MUMU_MANAGER):
        return {"ok": False, "state": "stopped", "error": "未检测到 MuMuManager，请确认安装路径"}
    # 发起 launch（失败仅记录，不立即返回；最终以轮询状态为准）
    rc, out, err = _mumu_run(["control", "-v", "0", "launch"], timeout=10)
    if rc != 0:
        audit("MUMU_LAUNCH", "launch rc=%s out=%r err=%r" % (rc, out[:120], err[:120]))
    # 轮询真实状态：模拟器起来即成功（容忍 launch 命令偶发无输出）
    deadline = time.time() + MUMU_WAIT_START
    detail = st.get("detail")
    while time.time() < deadline:
        time.sleep(2)
        s2 = get_mumu_status()
        if _mumu_is_up(s2):
            return {"ok": True, "state": "running", "detail": s2.get("detail")}
        detail = s2.get("detail")
    return {"ok": False, "state": "starting", "detail": detail,
            "error": "启动超时，请检查模拟器（launch 已发出但未检测到运行）"}


def mumu_stop():
    """关闭模拟器：未运行直接返回；否则 shutdown 并等待退出（≤12s）。

    与 mumu_start 对称：shutdown 命令在无关联控制台子进程下偶发 returncode≠0
    且无输出，但模拟器实际进入 stopping。故不以单次 returncode 判成败，而是
    发起 shutdown 后轮询真实状态，退出即成功。
    """
    st = get_mumu_status()
    if not _mumu_is_up(st):
        return {"ok": True, "state": "stopped", "detail": st.get("detail")}
    if not os.path.exists(MUMU_MANAGER):
        return {"ok": False, "state": "running", "error": "未检测到 MuMuManager，请确认安装路径"}
    try:
        rc, out, err = _mumu_run(["control", "-v", "0", "shutdown"], timeout=10)
        if rc != 0:
            audit("MUMU_SHUTDOWN", "shutdown rc=%s out=%r err=%r" % (rc, out[:120], err[:120]))
    except Exception as e:
        audit("MUMU_SHUTDOWN", "shutdown EXC %s: %s" % (type(e).__name__, str(e)[:200]))
    deadline = time.time() + MUMU_WAIT_STOP
    while time.time() < deadline:
        time.sleep(2)
        s2 = get_mumu_status()
        if not _mumu_is_up(s2):
            return {"ok": True, "state": "stopped", "detail": s2.get("detail")}
    return {"ok": True, "state": "stopping", "detail": "已发送停止指令（等待模拟器退出）"}


def mumu_adb_connect():
    """重连 ADB（模拟器在跑但 adb 失联时的修复入口）。

    时序（与 scan_device_apps 的“确保连接”对称，但加重试兜底）：
      1) adb connect 127.0.0.1:16384
      2) 轮询 get-state，device 即成功
      3) 失败则 adb kill-server → start-server → 重新 connect，再轮询
      4) 仍失败：MuMuManager info 探测实际 adb_port，若端口漂移则连接新地址并 get-state 验证
    仅在 ADB 可执行文件存在且模拟器进程/实例已检测到时才尝试；
    若模拟器本身未运行，直接返回提示，不盲目 connect。
    """
    if not os.path.exists(ADB_EXE):
        return {"ok": False, "adb_ok": False,
                "error": "未找到 ADB（%s），请确认 MuMu 安装路径" % ADB_EXE}
    st = get_mumu_status()
    if not (st.get("detected") or st.get("running") or st.get("android_started")):
        return {"ok": False, "adb_ok": False,
                "error": "未检测到模拟器实例，请先「启动」模拟器再连接 ADB"}

    def _try_connect(addr=ADB_ADDR):
        # 先 connect（已连也会幂等返回 already connected）
        try:
            subprocess.run([ADB_EXE, "connect", addr], capture_output=True, text=True,
                           timeout=8, encoding="utf-8", errors="replace")
        except Exception as e:
            audit("MUMU_ADB", "connect EXC %s: %s" % (type(e).__name__, str(e)[:200]))
        # 轮询 get-state（最多 ~6s）
        deadline = time.time() + 6
        while time.time() < deadline:
            try:
                r = subprocess.run([ADB_EXE, "-s", addr, "get-state"],
                                   capture_output=True, text=True, timeout=5,
                                   encoding="utf-8", errors="replace")
                if r.returncode == 0 and "device" in (r.stdout or "").lower():
                    return True
            except Exception:
                pass
            time.sleep(1.5)
        return False

    # 第一次尝试
    if _try_connect():
        return {"ok": True, "adb_ok": True,
                "detail": "ADB 已连接 %s" % ADB_ADDR, "state": "running"}
    # 失败：kill-server 复位后重试一次
    audit("MUMU_ADB", "首次 connect 失败，尝试 kill-server 复位")
    try:
        subprocess.run([ADB_EXE, "kill-server"], capture_output=True, text=True,
                       timeout=8, encoding="utf-8", errors="replace")
        subprocess.run([ADB_EXE, "start-server"], capture_output=True, text=True,
                       timeout=10, encoding="utf-8", errors="replace")
    except Exception as e:
        audit("MUMU_ADB", "kill/start-server EXC %s: %s" % (type(e).__name__, str(e)[:200]))
    time.sleep(2)
    if _try_connect():
        return {"ok": True, "adb_ok": True,
                "detail": "ADB 已连接 %s（已复位 adb server）" % ADB_ADDR, "state": "running"}
    # 最后一级：MuMuManager 探测当前实例实际 adb_port（模拟器重启后端口可能漂移）
    try:
        rc, out, err = _mumu_run(["info", "-v", "0"], timeout=5)
        if rc == 0:
            for line in (out or "").splitlines():
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    info = json.loads(line)
                except Exception:
                    continue
                if not (info.get("is_main") or str(info.get("index")) == "0"):
                    continue
                port = info.get("adb_port")
                if not port:
                    break
                cand = "%s:%s" % (info.get("adb_host_ip") or "127.0.0.1", port)
                if cand != ADB_ADDR and _try_connect(cand):
                    return {"ok": True, "adb_ok": True,
                            "detail": "ADB 已连接 %s（MuMuManager 探测端口）" % cand,
                            "state": "running"}
                break
    except Exception as e:
        audit("MUMU_ADB", "MuMuManager 探测端口 EXC %s: %s" % (type(e).__name__, str(e)[:200]))
    return {"ok": False, "adb_ok": False,
            "error": "ADB 仍无法连接 %s，请确认模拟器已完全启动或手动检查" % ADB_ADDR}


# 已知应用名映射（避免包名末段无法识别；category=game 仅限确认的游戏，未知一律 app 不误判）
APP_NAME_MAP = {
    "com.leiyan.mdjl.an": {"name": "锚点降临", "category": "game"},
    "com.bilibili.azurlane": {"name": "碧蓝航线", "category": "game"},
    "com.feimo.hazeworld.mumu": {"name": "灰烬世界", "category": "game"},
    "com.neversoft.rpg.erolabs": {"name": "Cherry Tale", "category": "game"},
    "com.zlongame.phoenix": {"name": "Phoenix", "category": "game"},
    "com.wn.app.np": {"name": "NP管理器", "category": "app"},
    "com.cjtec.uncompress": {"name": "解压工具", "category": "app"},
}


def scan_device_apps():
    """扫描 MuMu 模拟器中已安装的第三方应用（pm list packages -3 + dumpsys versionName）。"""
    if not os.path.exists(ADB_EXE):
        return {"ok": False, "error": "未找到 ADB（%s），请确认 MuMu 安装路径" % ADB_EXE}
    # 1) 确保连接
    try:
        subprocess.run([ADB_EXE, "connect", ADB_ADDR], capture_output=True, text=True, timeout=8,
                       encoding="utf-8", errors="replace")
        r = subprocess.run([ADB_EXE, "-s", ADB_ADDR, "get-state"],
                           capture_output=True, text=True, timeout=8,
                           encoding="utf-8", errors="replace")
        if r.returncode != 0 or "device" not in (r.stdout or ""):
            return {"ok": False, "error": "ADB 无法连接模拟器（%s），请确认 MuMu 已启动" % ADB_ADDR}
    except Exception as e:
        return {"ok": False, "error": "ADB 连接异常：%s" % str(e)[:200]}
    # 2) 列出第三方应用包名
    try:
        r = subprocess.run([ADB_EXE, "-s", ADB_ADDR, "shell", "pm list packages -3"],
                           capture_output=True, text=True, timeout=15,
                           encoding="utf-8", errors="replace")
        if r.returncode != 0:
            return {"ok": False, "error": "pm list packages 失败：%s" % ((r.stderr or "").strip()[:200])}
        pkgs = [l.replace("package:", "").strip() for l in (r.stdout or "").splitlines() if l.strip().startswith("package:")]
    except Exception as e:
        return {"ok": False, "error": "扫描异常：%s" % str(e)[:200]}
    # 3) 逐包获取 versionName（dumpsys package <pkg> | grep versionName）
    apps = []
    for pkg in pkgs:
        ver = ""
        try:
            r2 = subprocess.run([ADB_EXE, "-s", ADB_ADDR, "shell", "dumpsys package %s" % pkg],
                                capture_output=True, text=True, timeout=10,
                                encoding="utf-8", errors="replace")
            for line in (r2.stdout or "").splitlines():
                line = line.strip()
                if line.startswith("versionName="):
                    ver = line.split("=", 1)[1].strip()
                    break
        except Exception:
            pass
        mapped = APP_NAME_MAP.get(pkg)
        if mapped:
            name, category = mapped["name"], mapped["category"]
        else:
            name = pkg.split(".")[-1] if "." in pkg else pkg
            category = "app"
        apps.append({"pkg": pkg, "name": name, "version": ver, "category": category})
    audit("SCAN_APPS", "%d apps: %s" % (len(apps), ", ".join("%s(%s)" % (a["pkg"], a["name"]) for a in apps[:5])))
    return {"ok": True, "apps": apps, "count": len(apps)}


def get_app_icon(pkg):
    """获取应用图标（base64 PNG）：缓存 → zip 提取 → Pillow 生成首字母图标"""
    import hashlib
    icon_dir = os.path.join(STATE, "app_icons")
    os.makedirs(icon_dir, exist_ok=True)
    cache_path = os.path.join(icon_dir, pkg.replace(".", "_") + ".png")
    # 1. 缓存命中
    if os.path.exists(cache_path):
        with open(cache_path, "rb") as f:
            return f.read()
    # 2. 尝试从 APK 提取图标
    try:
        icon_data = _extract_apk_icon(pkg)
        if icon_data:
            with open(cache_path, "wb") as f:
                f.write(icon_data)
            return icon_data
    except Exception:
        pass
    # 3. Pillow 生成首字母占位图标
    from PIL import Image, ImageDraw
    mapped = APP_NAME_MAP.get(pkg, {})
    name = mapped.get("name", pkg.split(".")[-1] if "." in pkg else pkg)
    letter = name[0] if name else "?"
    h = int(hashlib.md5(pkg.encode()).hexdigest()[:6], 16)
    r, g, b = (h >> 16) & 0xFF, (h >> 8) & 0xFF, h & 0xFF
    r = max(r, 60); g = max(g, 60); b = max(b, 60)
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([4, 4, 60, 60], fill=(r, g, b, 255))
    # 简单文字（用 Pillow 默认字体可能不支持中文，降级为英文首字母或拼音首字母）
    try:
        from PIL import ImageFont
        font = ImageFont.truetype("C:\\Windows\\Fonts\\msyh.ttc", 28)
    except Exception:
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), letter, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text((32 - tw // 2, 32 - th // 2 - bbox[1]), letter, fill="white", font=font)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    data = buf.read()
    with open(cache_path, "wb") as f:
        f.write(data)
    return data


def _extract_apk_icon(pkg):
    """尝试从 APK zip 尾部提取 mipmap 图标（失败返回 None）"""
    if not os.path.exists(ADB_EXE):
        return None
    import struct as _struct
    try:
        r = subprocess.run([ADB_EXE, "-s", ADB_ADDR, "shell", "pm path %s" % pkg],
                           capture_output=True, text=True, timeout=10, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            return None
        apks = [l.strip()[len("package:"):] for l in (r.stdout or "").splitlines() if l.strip().startswith("package:")]
        base = next((a for a in apks if "base.apk" in a), apks[0] if apks else None)
        if not base:
            return None
        r2 = subprocess.run([ADB_EXE, "-s", ADB_ADDR, "exec-out", "tail -c 65536 %s" % base],
                            capture_output=True, timeout=10)
        if r2.returncode != 0 or len(r2.stdout) < 22:
            return None
        tail = r2.stdout
        eocd = None
        for i in range(len(tail) - 22, -1, -1):
            if tail[i:i+4] == b"PK\x05\x06":
                eocd = i; break
        if eocd is None or eocd + 20 > len(tail):
            return None
        cd_sz = _struct.unpack("<I", tail[eocd+12:eocd+16])[0]
        cd_off = _struct.unpack("<I", tail[eocd+16:eocd+20])[0]
        r3 = subprocess.run([ADB_EXE, "-s", ADB_ADDR, "exec-out", "dd if=%s bs=1024 skip=%d count=%d 2>/dev/null" % (
            base, cd_off // 1024, (cd_sz + 1023) // 1024)],
            capture_output=True, timeout=15)
        if r3.returncode != 0:
            return None
        cd = r3.stdout[cd_off % 1024: cd_off % 1024 + cd_sz]
        cands = []
        pos = 0
        while pos + 46 <= len(cd):
            if cd[pos:pos+4] != b"PK\x01\x02":
                pos += 1; continue
            name_len = _struct.unpack("<H", cd[pos+28:pos+30])[0]
            extra_len = _struct.unpack("<H", cd[pos+30:pos+32])[0]
            comment_len = _struct.unpack("<H", cd[pos+32:pos+34])[0]
            comp_size = _struct.unpack("<I", cd[pos+20:pos+24])[0]
            local_off = _struct.unpack("<I", cd[pos+42:pos+46])[0]
            name = cd[pos+46:pos+46+name_len].decode("utf-8", "replace")
            low = name.lower()
            if name.endswith(".png") and "mipmap" in low:
                dens = 0
                if "xxxhdpi" in low: dens = 640
                elif "xxhdpi" in low: dens = 480
                elif "xhdpi" in low: dens = 320
                elif "hdpi" in low: dens = 240
                elif "mdpi" in low: dens = 160
                cands.append((dens, name, local_off, comp_size))
            pos += 46 + name_len + extra_len + comment_len
        if not cands:
            return None
        cands.sort(key=lambda x: -x[0])
        _, _, l_off, l_size = cands[0]
        r4 = subprocess.run([ADB_EXE, "-s", ADB_ADDR, "exec-out", "dd if=%s bs=1 skip=%d count=64 2>/dev/null" % (base, l_off)],
                            capture_output=True, timeout=10)
        if r4.returncode != 0 or len(r4.stdout) < 30:
            return None
        lh = r4.stdout
        lname_len = _struct.unpack("<H", lh[26:28])[0]
        lextra_len = _struct.unpack("<H", lh[28:30])[0]
        data_off = l_off + 30 + lname_len + lextra_len
        r5 = subprocess.run([ADB_EXE, "-s", ADB_ADDR, "exec-out", "dd if=%s bs=1024 skip=%d count=%d 2>/dev/null" % (
            base, data_off // 1024, (l_size + 1023) // 1024)],
            capture_output=True, timeout=10)
        if r5.returncode != 0:
            return None
        icon = r5.stdout[data_off % 1024: data_off % 1024 + l_size]
        if icon.startswith(b"\x89PNG"):
            return icon
    except Exception:
        pass
    return None


# ---------- 存储迁移（启动时执行一次，幂等） ----------

def migrate_flows():
    """旧单文件 state/flows/<pkg>.json → 目录化 state/flows/<pkg>/（meta.json + main.json）。
    幂等：目录已存在则跳过；半迁移（meta 在但 main.json 缺）时补位。返回迁移记录列表。"""
    if not os.path.isdir(FLOWS_ROOT):
        return []
    migrated = []
    for fn in sorted(os.listdir(FLOWS_ROOT)):
        if not fn.endswith(".json") or fn == "run_logs.json":
            continue
        legacy = os.path.join(FLOWS_ROOT, fn)
        try:
            with open(legacy, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or "graph" not in data:
            continue  # 非 flow 文件，跳过
        pkg = data.get("pkg") or fn[:-5]
        dpath = _flow_dir(pkg)
        mpath = os.path.join(dpath, "meta.json")
        mainp = os.path.join(dpath, "main.json")
        if os.path.isdir(dpath):
            if os.path.isfile(mpath) and not os.path.isfile(mainp):
                try:
                    os.replace(legacy, mainp)
                    migrated.append((fn, dpath, "补位"))
                except OSError:
                    pass
            continue
        task_name = (data.get("name") or "").strip() or "默认任务"
        meta = {"pkg": pkg, "tasks": [{"id": "main", "name": task_name,
                                       "enabled": True, "order": 0}]}
        try:
            os.makedirs(dpath, exist_ok=True)
            save_meta(meta, dpath=dpath)
            os.replace(legacy, mainp)
            migrated.append((fn, dpath, "main.json"))
            audit("FLOW_MIGRATE", "%s → %s/（v%d，%d 个历史版本）"
                  % (fn, dpath, data.get("version", 1), len(data.get("versions", []))))
        except OSError as e:
            audit("FLOW_MIGRATE_ERR", "%s: %s" % (fn, e))
    return migrated


# ============ 图形化工作流（flows）——任务感知版本 ============

def flows_payload():
    """工作流列表：目录化任务（含 task_count/tasks）+ 兼容遗留单文件。"""
    flows = []
    try:
        for d in sorted(os.listdir(FLOWS_ROOT)):
            dpath = os.path.join(FLOWS_ROOT, d)
            if not os.path.isdir(dpath):
                continue
            if not os.path.isfile(os.path.join(dpath, "meta.json")):
                # 无台账目录：只有存在任务文件才算 flow 目录（避免杂项目录被重建台账）
                try:
                    has_flow = any(f.endswith(".json") for f in os.listdir(dpath))
                except OSError:
                    has_flow = False
                if not has_flow:
                    continue
            meta = load_meta(d)
            if meta is None:
                continue
            pkg = meta.get("pkg") or d
            tasks = sorted(meta.get("tasks", []), key=lambda t: (t.get("order", 0), t.get("id", "")))
            if not tasks:
                continue
            entry = {"pkg": pkg, "name": tasks[0].get("name", pkg),
                     "task_count": len(tasks),
                     "tasks": [{"id": t.get("id"), "name": t.get("name"),
                                "enabled": t.get("enabled", True), "order": t.get("order", 0)}
                               for t in tasks]}
            flow = task_load(pkg, tasks[0]["id"])
            if flow:
                entry["version"] = flow.get("version", 0)
                entry["updated"] = flow.get("updated", "")
            flows.append(entry)
    except OSError:
        pass
    # 兼容：尚未迁移的旧单文件（迁移失败等极端情况）
    for f in flow_engine.list_flows():
        if not any(x["pkg"] == f["pkg"] for x in flows):
            f["task_count"] = 1
            f["tasks"] = [{"id": "main", "name": f.get("name", f["pkg"]),
                           "enabled": True, "order": 0}]
            flows.append(f)
    flows.sort(key=lambda x: x["pkg"])
    return {"flows": flows}


def flow_get(pkg):
    flow, _ = _resolve_default_flow(pkg)
    if flow is None:
        return {"ok": False, "error": "工作流不存在：%s" % pkg}
    return flow


def flow_save(pkg, payload):
    """保存默认任务 flow（无目录化存储则走旧单文件逻辑）。"""
    payload["pkg"] = pkg
    flow, tid = _resolve_default_flow(pkg)
    if flow is None:
        return {"ok": False, "error": "工作流不存在：%s" % pkg}
    if tid is not None:
        r = task_save(pkg, tid, payload)
    else:
        r = flow_engine.save_flow(pkg, payload)
    if r.get("ok"):
        audit("FLOW_SAVE", "%s v%d" % (pkg, r["version"]))
    return r


def flow_validate(pkg, payload):
    flow = payload if isinstance(payload, dict) and payload.get("graph") else None
    if flow is None:
        flow, _ = _resolve_default_flow(pkg)
    if flow is None:
        return {"ok": False, "error": "工作流不存在：%s" % pkg}
    issues = flow_engine.validate_flow(flow)
    # 与 flow_run 保持一致：warning 不阻塞运行，只有 error 才使 ok=False。
    # （此前用 not issues，导致仅含 warning 的流程在编辑器被误判为校验未通过、无法运行）
    errors = [i for i in issues if i.get("level") == "error"]
    return {"ok": not errors, "issues": issues, "count": len(issues)}


def flow_run(pkg, mock=False, start_node_id=None, task_id=None):
    """启动工作流执行（线程）。mock=True 不碰设备（用 MockExecutor）。
    start_node_id 不为 None 时从该节点开始执行（调试用：从选中节点运行）。
    task_id 不为 None 时运行指定子任务（编辑器当前画布任务），否则回退默认任务。"""
    global _flow_runner
    if task_id:
        flow = task_load(pkg, task_id)
        if flow is None:
            return {"ok": False, "error": "任务不存在：%s/%s" % (pkg, task_id)}
    else:
        flow, _ = _resolve_default_flow(pkg)
    if flow is None:
        return {"ok": False, "error": "工作流不存在：%s" % pkg}
    issues = flow_engine.validate_flow(flow)
    errors = [i for i in issues if i.get("level") == "error"]
    if errors:
        return {"ok": False, "error": "校验未通过：%d 个错误（如 %s）" % (len(errors), errors[0]["msg"])}
    with _run_all_lock:
        if _run_all is not None and _run_all.get("running"):
            return {"ok": False, "error": "批量执行进行中，请先停止"}
        with _flow_runner_lock:
            if _flow_runner is not None and not getattr(_flow_runner, "_done", True):
                return {"ok": False, "error": "已有工作流在运行，请先停止"}
            if mock:
                executor = flow_engine.MockExecutor()
            else:
                executor = FlowDeviceExecutor()
            runner = flow_engine.FlowRunner(executor, flow)
            _flow_runner = runner

    def _worker():
        try:
            summary = runner.run(start_node_id)
            runner._done = True
            flow_engine.save_run_log(pkg, summary)
        except Exception as e:  # noqa: BLE001
            runner._done = True
            flow_engine.save_run_log(pkg, {"status": "error", "note": str(e),
                                           "steps": runner.steps, "nodes": runner.node_recs,
                                           "events": runner.events})

    runner._done = False
    threading.Thread(target=_worker, daemon=True).start()
    return {"ok": True, "msg": "已启动执行（%s）%s" % ("试运行 mock" if mock else "真实设备",
                                                  ("｜从节点 %s 开始" % start_node_id) if start_node_id else "")}


def flow_stop():
    """停止：优先中止 run-all（置停止标志 + 当前任务 FlowRunner.stop）。"""
    global _flow_runner
    with _run_all_lock:
        if _run_all is not None and _run_all.get("running"):
            _run_all["stop"] = True
            runner = _run_all.get("runner")
            if runner is not None:
                runner.stop()
            return {"ok": True, "msg": "已发送终止指令（批量执行将在当前任务结束后停止）"}
    with _flow_runner_lock:
        if _flow_runner is not None:
            _flow_runner.stop()
            return {"ok": True, "msg": "已发送终止指令"}
    return {"ok": False, "error": "没有正在运行的工作流"}


def flow_pause():
    """暂停/继续：优先作用于 run-all 的当前任务。"""
    with _run_all_lock:
        if _run_all is not None and _run_all.get("running"):
            runner = _run_all.get("runner")
            if runner is None:
                return {"ok": False, "error": "批量执行中当前任务尚未开始"}
            if getattr(runner, "aborted", False):
                return {"ok": False, "error": "工作流已发送终止指令，无需暂停"}
            if getattr(runner, "paused", False):
                runner.resume()
                return {"ok": True, "msg": "已继续"}
            runner.pause()
            return {"ok": True, "msg": "已暂停"}
    with _flow_runner_lock:
        if _flow_runner is not None and not getattr(_flow_runner, "_done", True):
            if getattr(_flow_runner, "aborted", False):
                return {"ok": False, "error": "工作流已发送终止指令，无需暂停"}
            if getattr(_flow_runner, "paused", False):
                _flow_runner.resume()
                return {"ok": True, "msg": "已继续"}
            _flow_runner.pause()
            return {"ok": True, "msg": "已暂停"}
    return {"ok": False, "error": "没有正在运行的工作流"}


def flow_run_status():
    """运行状态：单任务正在运行 → 原单任务响应；否则存在 run-all 会话 → {mode:run_all,…}。"""
    with _run_all_lock:
        run_all = _run_all
    with _flow_runner_lock:
        single_running = _flow_runner is not None and not getattr(_flow_runner, "_done", True)
    if run_all is not None and not single_running:
        s = run_all
        runner = s.get("runner")
        return {"mode": "run_all", "pkg": s.get("pkg"),
                "running": bool(s.get("running")),
                "task_index": s.get("task_index", 0),
                "task_id": s.get("task_id"), "task_name": s.get("task_name"),
                "total": s.get("total", 0), "done": s.get("done", []),
                "last_log": s.get("last_log", ""), "error": s.get("error"),
                "paused": bool(runner and getattr(runner, "paused", False))}
    with _flow_runner_lock:
        if _flow_runner is None:
            return {"running": False}
        r = _flow_runner
        done = getattr(r, "_done", True)
        return {"running": not done, "paused": getattr(r, "paused", False),
                "steps": r.steps, "events": r.events[-50:],
                "nodes": r.node_recs[-50:]}


def flow_prints():
    """打印节点输出：返回 state/prints.log 全文（前端分页展示）。"""
    p = os.path.join(STATE, "prints.log")
    lines = []
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
        except Exception:
            lines = []
    return {"ok": True, "lines": lines, "count": len(lines)}


def flow_get_task(pkg, task_id):
    d = task_load(pkg, task_id)
    if isinstance(d, dict) and "versions" in d:
        d.pop("versions")  # 51 轮：GET 出口剔除版本历史（前端零消费；565MB 响应会让浏览器字符串超限崩溃）
    return d


def flow_save_task(pkg, task_id, payload):
    """保存指定任务 flow（version+1 / versions 前 20 版 / updated）。"""
    payload["pkg"] = pkg
    r = task_save(pkg, task_id, payload)
    if r.get("ok"):
        audit("FLOW_SAVE", "%s/%s v%d" % (pkg, task_id, r["version"]))
    return r


def flow_logs(pkg):
    return {"logs": flow_engine.list_run_logs(pkg)}


def stress_report(payload):
    """压测结果落盘（state/stress_reports.json）供自动化验证。"""
    rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
           "nodes": payload.get("nodes"), "links": payload.get("links"),
           "render_ms": payload.get("render_ms")}
    p = os.path.join(STATE, "stress_reports.json")
    try:
        with open(p, "r", encoding="utf-8") as f:
            items = json.load(f)
    except (OSError, ValueError):
        items = []
    items.append(rec)
    items = items[-20:]
    with open(p, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    return {"ok": True}


def load_failures():
    return failures_core.load()


def save_failures(items):
    failures_core.save(items)


def add_failure(app, task, reason, pipeline="default", detail=""):
    return failures_core.add_failure(app, task, reason, pipeline, detail)


def mark_failure(fid, payload):
    return failures_core.mark(fid, payload.get("action"), payload.get("note", ""))


def failures_payload():
    return failures_core.list_all()


# ============ 带图步骤：模板图片上传 ============
def upload_template(pkg, payload):
    """上传一张图片作为该 App 的模板（步骤匹配图像）。返回模板文件名。"""
    import base64 as _b64
    name = (payload.get("name") or "").strip()
    image_b64 = payload.get("image_b64", "")
    if not name or not image_b64:
        return {"ok": False, "error": "文件名与图片内容不能为空"}
    # 文件名白名单（防路径穿越）
    if not re.match(r"^[\w\u4e00-\u9fa5-]+\.(png|jpe?g)$", name):
        return {"ok": False, "error": "文件名不合法（仅字母数字中文横线 + png/jpg）"}
    try:
        raw = _b64.b64decode(image_b64)
    except Exception:
        return {"ok": False, "error": "图片数据无法解码"}
    if raw[:8] != b"\x89PNG\r\n\x1a\n" and raw[:2] != b"\xff\xd8":
        return {"ok": False, "error": "不支持的图片格式：仅支持 PNG/JPEG"}
    if len(raw) > 8 * 1024 * 1024:
        return {"ok": False, "error": "图片过大（上限 8MB）"}
    tdir = os.path.join(BASE, "adapters", "templates", pkg)
    os.makedirs(tdir, exist_ok=True)
    ext = "png" if raw[:8] == b"\x89PNG\r\n\x1a\n" else "jpg"
    base = name.rsplit(".", 1)[0]
    fname = base + "." + ext
    fpath = os.path.join(tdir, fname)
    with open(fpath, "wb") as f:
        f.write(raw)
    audit("TPL_UPLOAD", "%s/%s" % (pkg, fname))
    return {"ok": True, "name": fname, "msg": "已上传模板 %s" % fname}


import re  # noqa: E402  (upload_template 用)


# ============ 流水线编排（pipeline editor） ============
PIPELINE_CFG = os.path.join(STATE, "pipeline_cfg.json")
PIPELINE_LIVE = os.path.join(STATE, "pipeline_live.json")  # 61 轮：流水线实时进度（flow_pipeline.py 守护线程原子写）


def pipeline_live():
    """只读流水线实时进度：当前任务 + 正在执行的节点（供控制台跳转画布/状态展示）。
    文件缺失/损坏 → {running:False, phase:"unknown"}。
    2026-09-19：running 但 ts 过旧（>90s，子进程被杀/崩溃时可能未写 idle）→ 视为 stale，
    返回 running=False，避免控制台/画布一直显示「运行中」。"""
    d = read_json(PIPELINE_LIVE)
    if not isinstance(d, dict):
        return {"running": False, "phase": "unknown"}
    if d.get("running"):
        ts = d.get("ts") or ""
        stale = False
        age = None
        try:
            t = time.strptime(str(ts)[:19], "%Y-%m-%d %H:%M:%S")
            age = time.time() - time.mktime(t)
            stale = age > 90
        except (ValueError, TypeError):
            stale = not str(ts).strip()  # 无时间戳且 running → 视为陈旧
        if stale:
            out = dict(d)
            out["running"] = False
            out["phase"] = "stale"
            if age is not None:
                out["stale_age_s"] = int(age)
            return out
    return d
AUDIT_LOG = os.path.join(STATE, "pipeline_audit.log")
pipeline_lock = threading.Lock()


def _candidates():
    """候选 App 列表：从适配器注册表 + 模板目录动态发现（真实后端数据，非写死）。"""
    cands = []
    try:
        import sys as _sys
        if BASE not in _sys.path:
            _sys.path.insert(0, BASE)   # 使 adapters 内的 core 导入可用（显示名修复）
        sys_path = os.path.join(BASE, "adapters")
        if sys_path not in _sys.path:
            _sys.path.insert(0, sys_path)
        import importlib
        reg = importlib.import_module("adapters")   # 按包导入，相对导入（.example_app 等）才可用
        for pkg, cls in reg.ADAPTERS.items():
            display = getattr(cls, "display_name", pkg)
            cands.append({"pkg": pkg, "name": display})
    except Exception as e:
        # 回退：扫描 templates 目录
        tdir = os.path.join(BASE, "adapters", "templates")
        if os.path.isdir(tdir):
            for d in os.listdir(tdir):
                if os.path.isdir(os.path.join(tdir, d)):
                    cands.append({"pkg": d, "name": d})
    return cands


def _default_steps(pkg):
    """从适配器 build_tasks 获取默认步骤（真实数据）。"""
    steps = []
    try:
        import sys as _sys
        if BASE not in _sys.path:
            _sys.path.insert(0, BASE)
        ap = os.path.join(BASE, "adapters")
        if ap not in _sys.path:
            _sys.path.insert(0, ap)
        import importlib
        reg = importlib.import_module("adapters")
        cls = reg.ADAPTERS.get(pkg)
        if cls:
            adapter = cls()
            for t in adapter.build_tasks():
                steps.append({"name": t.task_id, "action": "task:" + t.task_id, "params": ""})
    except Exception:
        pass
    return steps


def load_pipeline_cfg():
    try:
        with open(PIPELINE_CFG, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "apps" in data:
            return data
    except OSError:
        pass
    return {"apps": [], "updated_at": None}


def save_pipeline_cfg(data):
    data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(PIPELINE_CFG, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def audit(op, detail):
    try:
        with open(AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write("%s %s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), op, detail))
    except OSError:
        pass


def pipeline_payload():
    cfg = load_pipeline_cfg()
    apps = []
    for i, a in enumerate(cfg.get("apps", [])):
        steps = []
        for s in a.get("steps", []):
            step = dict(s)
            # 匹配图像自动关联：步骤 image 字段为空时，从模板目录按名称/动作前缀匹配
            if not step.get("image"):
                img = auto_match_image(a["pkg"], step)
                if img:
                    step["image"] = img
            steps.append(step)
        apps.append({"pkg": a["pkg"], "name": a.get("name", a["pkg"]),
                     "order": i + 1, "enabled": a.get("enabled", True),
                     "steps": steps})
    return {"apps": apps, "candidates": _candidates(),
            "count": len(apps), "updated_at": cfg.get("updated_at")}


def _templates_of(pkg):
    """某 App 模板目录下的 PNG 列表（真实文件）。"""
    tdir = os.path.join(BASE, "adapters", "templates", pkg)
    if not os.path.isdir(tdir):
        return []
    return sorted(f for f in os.listdir(tdir) if f.lower().endswith(".png"))


def auto_match_image(pkg, step):
    """自动关联：步骤 name 或 action 包含模板名前缀（如 '点击进入游戏'→enter_btn 不匹配，
    优先匹配 name 与模板名公共子串；找不到返回 None 不报错）。"""
    tpls = _templates_of(pkg)
    if not tpls:
        return None
    text = ((step.get("name") or "") + " " + (step.get("action") or "")).lower()
    # 1) 精确：模板名（去扩展名）出现在 name/params 中
    for t in tpls:
        base = t[:-4].lower()
        if base in text or base in ((step.get("params") or "").lower()):
            return t
    # 2) 兜底：首个模板（主界面 main_scene 优先除外）——返回 None 更安全（不强行关联）
    return None


# MUMU_MANAGER / ADB_EXE 在下方 _locate_mumu_tool 定义后立即解析（发布可移植版）


def _locate_mumu_tool(name):
    """定位 MuMu 12 的 adb.exe / MuMuManager.exe（开源发行用：原写死路径仅适用单机）。
    优先级：AUTOFW_MUMU_HOME 环境变量 → 注册表卸载信息 → 常见安装位置 → 旧默认值。"""
    cand = []
    home = os.environ.get("AUTOFW_MUMU_HOME", "")
    if home:
        cand += [os.path.join(home, "nx_main", name), os.path.join(home, name)]
    try:  # 注册表卸载信息反查安装目录
        import winreg
        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
                try:
                    k = winreg.OpenKey(root, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
                                       0, winreg.KEY_READ | view)
                except OSError:
                    continue
                with k:
                    i = 0
                    while True:
                        try:
                            sub = winreg.EnumKey(k, i)
                            i += 1
                        except OSError:
                            break
                        try:
                            with winreg.OpenKey(k, sub) as sk:
                                dn = (winreg.QueryValueEx(sk, "DisplayName")[0] or "")
                                if "mumu" not in dn.lower():
                                    continue
                                loc = ""
                                try:
                                    loc = winreg.QueryValueEx(sk, "InstallLocation")[0] or ""
                                except OSError:
                                    pass
                                if not loc:
                                    try:
                                        icon = winreg.QueryValueEx(sk, "DisplayIcon")[0] or ""
                                        loc = icon.split(",")[0]
                                    except OSError:
                                        pass
                                if loc:
                                    d = os.path.dirname(os.path.normpath(loc))
                                    cand += [os.path.join(d, "nx_main", name),
                                             os.path.join(d, name)]
                        except OSError:
                            continue
    except Exception:
        pass
    for base in (r"C:\Program Files\Netease", r"C:\Program Files (x86)\Netease",
                 r"D:\Program Files\Netease", "C:\\", "D:\\", "E:\\"):
        try:  # 目录名含 mumu 的常见安装位置
            if os.path.isdir(base):
                for entry in os.scandir(base):
                    n = entry.name.lower()
                    if entry.is_dir() and n.startswith("mumu"):
                        cand += [os.path.join(entry.path, "nx_main", name),
                                 os.path.join(entry.path, name)]
        except OSError:
            pass
    cand.append(os.path.join("D:", "MuMuPlayer", "nx_main", name))  # 旧版默认值兜底
    for p in cand:
        if p and os.path.isfile(p):
            return p
    return cand[-1]


ADB_ADDR = "127.0.0.1:16384"

MUMU_MANAGER = _locate_mumu_tool("MuMuManager.exe")  # 导入时一次性解析（原写死单机路径，发布可移植版）
ADB_EXE = _locate_mumu_tool("adb.exe")


def get_mumu_status():
    """MuMu 模拟器状态：连接/运行/Android 启动（主实例，只读探测）。
    探测源优先级：MuMuManager info → adb get-state/boot_completed → 进程存在性。
    （MuMuManager 存在互斥/占用问题，同一时刻第二个实例返回 rc=1 且无输出，
    不可单点依赖，失败时降级到 adb/进程探测。）"""
    status = {"detected": False, "running": False, "adb_ok": False,
              "android_started": False, "detail": "未检测到模拟器", "ts": time.time()}
    mm_ok = False
    # 1) MuMuManager 进程/实例状态（成功优先；失败/未命中记 audit）
    try:
        rc, out, err = _mumu_run(["info", "-v", "0"], timeout=5)
        if rc == 0 and "is_android_started" in out:
            status["detected"] = True
            status["running"] = "\"is_process_started\": true" in out
            status["android_started"] = "\"is_android_started\": true" in out
            mm_ok = True
        else:
            audit("MUMU_PROBE", "未命中: rc=%s out_len=%d stderr=%s stdout_head=%s" % (
                rc, len(out or ""), (err or "")[:150].replace("\n", " "),
                (out or "")[:120].replace("\n", " ")))
    except Exception as e:
        audit("MUMU_PROBE", "EXC %s: %s" % (type(e).__name__, str(e)[:200]))
    # 2) adb 连通（主实例 16384）
    try:
        if os.path.exists(ADB_EXE):
            r = subprocess.run([ADB_EXE, "-s", ADB_ADDR, "get-state"],
                               capture_output=True, text=True, timeout=5,
                               encoding="utf-8", errors="replace")
            status["adb_ok"] = "device" in r.stdout.lower()
    except Exception:
        pass
    # 3) MuMuManager 失败/未命中时降级：adb boot_completed + 模拟器进程存在性
    if not mm_ok:
        try:
            if status["adb_ok"] and os.path.exists(ADB_EXE):
                rb = subprocess.run([ADB_EXE, "-s", ADB_ADDR, "shell", "getprop", "sys.boot_completed"],
                                    capture_output=True, text=True, timeout=5,
                                    encoding="utf-8", errors="replace")
                if rb.returncode == 0 and (rb.stdout or "").strip() == "1":
                    status["android_started"] = True
                    status["detected"] = True
                    status["running"] = True
                else:
                    audit("MUMU_PROBE", "降级 adb boot_completed 未就绪: rc=%s out=%r" % (
                        rb.returncode, (rb.stdout or "")[:30]))
            # 进程存在性兜底（MuMuNxDevice / MuMuNxMain 任一存在即已检测到）
            for img in ("MuMuNxDevice.exe", "MuMuNxMain.exe"):
                try:
                    pl = subprocess.run(["tasklist", "/FI", "IMAGENAME eq " + img],
                                        capture_output=True, text=True, timeout=5,
                                        encoding="utf-8", errors="replace")
                    if img in (pl.stdout or ""):
                        status["detected"] = True
                        if not status["running"]:
                            status["running"] = True
                        break
                except Exception:
                    pass
        except Exception as e:
            audit("MUMU_PROBE", "降级探测 EXC %s: %s" % (type(e).__name__, str(e)[:200]))
    if status["detected"] and status["android_started"]:
        status["detail"] = "运行中（Android 已启动）"
    elif status["detected"] and status["running"]:
        status["detail"] = "已启动（Android 启动中）"
    elif status["detected"]:
        status["detail"] = "已检测到（进程存在）"
    elif status["adb_ok"]:
        status["detail"] = "已检测到（adb 连通）"
    else:
        status["detail"] = "未检测到模拟器"
    return status


def overview_payload():
    cfg = load_pipeline_cfg()
    apps = [{"pkg": a["pkg"], "name": a.get("name", a["pkg"]),
             "step_count": len(a.get("steps", [])), "enabled": a.get("enabled", True)}
            for a in cfg.get("apps", [])]
    return {"apps": apps, "mumu": mumu_status_payload(),
            "app_count": len(apps), "updated_at": cfg.get("updated_at")}


def add_app(payload):
    pkg = (payload.get("pkg") or "").strip()
    if not pkg:
        return {"ok": False, "error": "App 包名不能为空"}
    cands = _candidates()
    in_whitelist = any(c["pkg"] == pkg for c in cands)
    source = (payload.get("source") or "").strip()
    # 扫描来源的 app 允许直接添加（无需适配器也可配置基础自动化流程）
    if not in_whitelist and source != "scan":
        return {"ok": False, "error": "该 App 不在候选白名单中（%s）——通过扫描设备应用添加，或在适配器注册表中注册" % pkg}
    with pipeline_lock:
        cfg = load_pipeline_cfg()
        if any(a["pkg"] == pkg for a in cfg.get("apps", [])):
            return {"ok": False, "error": "重复添加：该 App 已在流水线中"}
        name = (payload.get("name") or "").strip() or next((c["name"] for c in cands if c["pkg"] == pkg), pkg)
        cfg.setdefault("apps", []).append({"pkg": pkg, "name": name,
                                           "enabled": True, "steps": _default_steps(pkg)})
        save_pipeline_cfg(cfg)
        audit("ADD_APP", pkg)
    # 初始化工作流脚手架：默认 main 任务（空 start 图）+ meta 台账（2026-08-29 修复：
    # 此前只写 pipeline_cfg，任务列表 tasks_get→load_meta 因目录缺失报「工作流不存在」）
    dpath = _flow_dir(pkg)
    os.makedirs(dpath, exist_ok=True)
    if not os.path.isfile(os.path.join(dpath, "meta.json")):
        fpath = os.path.join(dpath, "main.json")
        if not os.path.isfile(fpath):  # 只在无任何流程文件时写空图，绝不覆盖旧版半初始化目录里的 main.json
            flow = {"pkg": pkg, "name": name, "version": 1,
                    "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "graph": {"nodes": [_start_node()], "edges": []},
                    "subgraphs": {}, "versions": []}
            with tasks_lock:
                tmp = fpath + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(flow, f, ensure_ascii=False, indent=2)
                _safe_replace(tmp, fpath)
        _rebuild_meta(dpath, pkg)  # 台账缺失就重建：_rebuild_meta 会扫描已有任务文件防孤儿
    return {"ok": True, "msg": "已添加 %s（流水线末尾，第 %d 位）" % (name, len(cfg["apps"]))}


def delete_app(pkg):
    with pipeline_lock:
        cfg = load_pipeline_cfg()
        before = len(cfg.get("apps", []))
        cfg["apps"] = [a for a in cfg.get("apps", []) if a["pkg"] != pkg]
        if len(cfg["apps"]) == before:
            return {"ok": False, "error": "未找到该 App：%s" % pkg}
        save_pipeline_cfg(cfg)
        audit("DEL_APP", pkg)
    return {"ok": True, "msg": "已删除 %s" % pkg}


def toggle_app_enabled(pkg, enabled=None):
    """App 级启停（控制台 App 列表勾选）。enabled=None 时翻转当前值。"""
    with pipeline_lock:
        cfg = load_pipeline_cfg()
        for a in cfg.get("apps", []):
            if a.get("pkg") == pkg:
                cur = a.get("enabled", True)
                new = (not cur) if enabled is None else bool(enabled)
                a["enabled"] = new
                save_pipeline_cfg(cfg)
                audit("APP_TOGGLE", "%s → %s" % (pkg, "on" if new else "off"))
                return {"ok": True, "enabled": new,
                        "msg": "已%s「%s」" % ("启用" if new else "停用", a.get("name") or pkg)}
    return {"ok": False, "error": "未找到该 App：%s" % pkg}


def reorder_apps(payload):
    order = payload.get("apps")
    if not isinstance(order, list) or not order:
        return {"ok": False, "error": "顺序列表无效"}
    with pipeline_lock:
        cfg = load_pipeline_cfg()
        by_pkg = {a["pkg"]: a for a in cfg.get("apps", [])}
        missing = [p for p in order if p not in by_pkg]
        if missing:
            return {"ok": False, "error": "顺序包含不存在的 App：%s" % ",".join(missing)}
        new_apps = [by_pkg[p] for p in order]
        # 补上未提及的（防御）
        for a in cfg.get("apps", []):
            if a["pkg"] not in order:
                new_apps.append(a)
        cfg["apps"] = new_apps
        save_pipeline_cfg(cfg)
        audit("REORDER", " -> ".join(order))
    return {"ok": True, "msg": "顺序已保存（%d 个 App）" % len(order)}


def add_step(pkg, payload):
    name = (payload.get("name") or "").strip()
    action = (payload.get("action") or "").strip()
    params = (payload.get("params") or "").strip()
    image = (payload.get("image") or "").strip()
    step_type = (payload.get("type") or "").strip()
    note = (payload.get("note") or "").strip()
    enable = payload.get("enable", True)
    position = payload.get("position")  # int 或 "end"
    if not name:
        return {"ok": False, "error": "步骤名称不能为空"}
    if image and not any(image == t for t in _templates_of(pkg)):
        return {"ok": False, "error": "匹配图像无效：%s 不在该 App 模板目录中" % image}
    valid_types = ("", "app_launch", "window_switch", "condition_wait", "retry",
                   "timeout", "click", "ocr_check", "swipe", "key")
    if step_type not in valid_types:
        return {"ok": False, "error": "步骤类型无效：%s（可选：%s）" % (step_type, "/".join(valid_types))}
    step = {"name": name, "action": action or "manual", "params": params}
    if step_type:
        step["type"] = step_type
    if note:
        step["note"] = note
    if enable is not True:
        step["enable"] = bool(enable)
    if image:
        step["image"] = image
    with pipeline_lock:
        cfg = load_pipeline_cfg()
        app = next((a for a in cfg.get("apps", []) if a["pkg"] == pkg), None)
        if not app:
            return {"ok": False, "error": "未找到 App：%s" % pkg}
        steps = app.setdefault("steps", [])
        if position == "end" or position is None or position == "":
            steps.append(step)
            idx = len(steps) - 1
        else:
            try:
                idx = int(position)
            except Exception:
                return {"ok": False, "error": "插入位置无效"}
            if idx < 0 or idx > len(steps):
                return {"ok": False, "error": "插入位置越界（0-%d）" % len(steps)}
            steps.insert(idx, step)
        save_pipeline_cfg(cfg)
        audit("ADD_STEP", "%s@%s pos=%s" % (pkg, name, position))
    return {"ok": True, "msg": "已插入步骤「%s」到第 %d 位" % (name, idx + 1)}


def toggle_step(pkg, idx):
    """步骤启停勾选（编排面板）：enable 翻转，返回新状态。"""
    with pipeline_lock:
        cfg = load_pipeline_cfg()
        for a in cfg.get("apps", []):
            if a["pkg"] == pkg:
                steps = a.get("steps", [])
                try:
                    i = int(idx)
                except (TypeError, ValueError):
                    return {"ok": False, "error": "步骤索引无效"}
                if not (0 <= i < len(steps)):
                    return {"ok": False, "error": "步骤索引越界"}
                cur = steps[i].get("enable", True)
                steps[i]["enable"] = not cur
                save_pipeline_cfg(cfg)
                return {"ok": True, "enable": not cur,
                        "msg": "已%s「%s」" % ("启用" if not cur else "停用", steps[i]["name"])}
        return {"ok": False, "error": "未找到该 App：%s" % pkg}


def delete_step(pkg, idx):
    try:
        idx = int(idx)
    except Exception:
        return {"ok": False, "error": "步骤索引无效"}
    with pipeline_lock:
        cfg = load_pipeline_cfg()
        app = next((a for a in cfg.get("apps", []) if a["pkg"] == pkg), None)
        if not app:
            return {"ok": False, "error": "未找到 App：%s" % pkg}
        steps = app.get("steps", [])
        if idx < 0 or idx >= len(steps):
            return {"ok": False, "error": "步骤索引越界（0-%d）" % (len(steps) - 1)}
        removed = steps.pop(idx)
        save_pipeline_cfg(cfg)
        audit("DEL_STEP", "%s@%s" % (pkg, removed.get("name")))
    return {"ok": True, "msg": "已删除步骤「%s」" % removed.get("name")}


def tail(path, n=12):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return [l.rstrip() for l in lines[-n:]]
    except OSError:
        return []


def read_json(path):
    try:
        # utf-8-sig：兼容 PowerShell 写出的 BOM（否则 json.load 抛 JSONDecodeError）
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def status_payload():
    running = proc is not None and proc.poll() is None
    # 进程已退出则记录退出码，便于面板区分"运行中"与"秒退（退出码X）"
    exit_code = None
    if proc is not None and proc.poll() is not None:
        try:
            exit_code = proc.returncode
        except Exception:
            exit_code = None
    ck = read_json(os.path.join(STATE, "checkpoint.a.json"))
    return {
        "running": running,
        "exit_code": exit_code,
        "pipeline_index": ck.get("pipeline_index", 0),
        "app": ck.get("app"),
        "completed": ck.get("completed", []),
        "intent_tail": tail(os.path.join(STATE, "intent.log")),
        "log_tail": tail(os.path.join(STATE, "app.log")),
        "print_tail": tail(os.path.join(STATE, "prints.log"), n=50),
        "mumu": mumu_status_payload(),
        "live": pipeline_live(),  # 61 轮：流水线实时任务/节点（控制台展示与跳转）
    }


def _sync_pipeline_json_from_cfg():
    """启动流水线前：用控制台 pipeline_cfg.json 的 apps 对齐 pipeline.json 执行名单。

    历史问题：控制台 App 列表读 state/pipeline_cfg.json，而 start_pipeline 启动的
    FlowPipeline 只读项目根 pipeline.json 的 pipeline[]，两套名单不同步会导致
    「界面启用 2 个 App，流水线只跑 1 个」。
    - cfg.apps 为空时不动 pipeline.json（防误清空）。
    - 保留 pipeline.json 的 mode/recovery/checkpoint；条目顺序 = cfg.apps 顺序。
    - tasks 字段：已有同 pkg 条目则保留原 tasks（adapter 遗留），新条目 tasks=[]。
    """
    pj_path = os.path.join(BASE, "pipeline.json")
    cfg_path = os.path.join(STATE, "pipeline_cfg.json")
    try:
        with open(pj_path, "r", encoding="utf-8") as f:
            pj = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(pj, dict):
        return None
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        apps = cfg.get("apps") if isinstance(cfg, dict) else None
    except (OSError, ValueError):
        apps = None
    if not apps or not isinstance(apps, list):
        return pj  # 无控制台名单则保持原 pipeline.json
    old_by_pkg = {}
    for e in (pj.get("pipeline") or []):
        if isinstance(e, dict) and e.get("app"):
            old_by_pkg[e["app"]] = e
    new_pipe = []
    for a in apps:
        if not isinstance(a, dict):
            continue
        pkg = (a.get("pkg") or "").strip()
        if not pkg:
            continue
        prev = old_by_pkg.get(pkg) or {}
        new_pipe.append({
            "app": pkg,
            "tasks": prev.get("tasks") or [],
            "enabled": bool(a.get("enabled", True)),
        })
    if not new_pipe:
        return pj
    pj["pipeline"] = new_pipe
    try:
        tmp = pj_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(pj, f, ensure_ascii=False, indent=2)
        os.replace(tmp, pj_path)
        audit("PIPELINE_SYNC", "pipeline.json ← pipeline_cfg apps: " +
              ",".join("%s:%s" % (x["app"], "on" if x["enabled"] else "off") for x in new_pipe))
    except OSError:
        pass
    return pj


def start_pipeline():
    global proc
    with proc_lock:
        if proc is not None and proc.poll() is None:
            return {"ok": False, "msg": "流水线已在运行中"}
        cfg = os.path.join(BASE, "pipeline.json")
        # 2026-09-19 授权：启动前用控制台 pipeline_cfg.json 对齐 pipeline.json 执行名单
        try:
            _sync_pipeline_json_from_cfg()
        except Exception:  # noqa: BLE001
            pass  # 同步失败不阻断启动，仍用现有 pipeline.json
        # 读取 pipeline.json 的 mode 字段，传给后端 --mode（flow=图形化工作流 B2 / adapter=旧路由）
        mode = "adapter"
        try:
            with open(cfg, "r", encoding="utf-8") as f:
                mode = json.load(f).get("mode", "adapter") or "adapter"
        except (OSError, ValueError):
            pass
        # B2 调试可观测性：子进程 stderr 落盘（不再被 DEVNULL 吞掉），
        # 启动后若秒退，可直接 tail state/pipeline_stderr.log 看真实报错。
        _err_path = os.path.join(STATE, "pipeline_stderr.log")
        _err_fh = open(_err_path, "wb")
        proc = subprocess.Popen([PY, "main.py", "--config", cfg, "--mode", mode],
                                cwd=BASE, stdout=subprocess.DEVNULL,
                                stderr=_err_fh)
        return {"ok": True, "msg": "已启动（PID %d，模式=%s）" % (proc.pid, mode)}


def stop_pipeline():
    global proc
    with proc_lock:
        if proc is None or proc.poll() is not None:
            return {"ok": False, "msg": "流水线未在运行"}
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
        proc = None
        return {"ok": True, "msg": "已停止"}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")  # UX-06 修复：file:// 直开时可跨源读取 API
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except (ConnectionAbortedError, BrokenPipeError, ConnectionResetError, TimeoutError):
            # 客户端在响应写回前已断开（刷新/切页/关标签，WinError 10053）。
            # 业务侧通常已完成（如 task_save 已落盘），丢响应不丢数据；静默避免 traceback 刷屏。
            pass

    def do_OPTIONS(self):
        # UX-06 修复：file:// 直开时 POST(JSON) 会触发 CORS 预检，需应答 OPTIONS
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        if self.path == "/" or self.path.split("?")[0] == "/index.html":
            p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
            with open(p, "rb") as f:
                self._send(200, f.read(), "text/html")
        elif self.path.split("?")[0] == "/step_editor.html":
            p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "step_editor.html")
            with open(p, "rb") as f:
                self._send(200, f.read(), "text/html")
        elif self.path.split("?")[0] == "/pipeline_editor.html":
            p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pipeline_editor.html")
            with open(p, "rb") as f:
                self._send(200, f.read(), "text/html")
        elif self.path.split("?")[0] == "/ocr.html":
            p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ocr.html")
            with open(p, "rb") as f:
                self._send(200, f.read(), "text/html")
        elif self.path.split("?")[0] == "/flow_editor.html":
            p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "flow_editor.html")
            with open(p, "rb") as f:
                self._send(200, f.read(), "text/html")
        elif self.path == "/api/pipeline":
            self._send(200, json.dumps(pipeline_payload(), ensure_ascii=False))
        elif self.path.split("?")[0] == "/api/scan_device_apps":
            self._send(200, json.dumps(scan_device_apps(), ensure_ascii=False))
        elif self.path.startswith("/api/app_icon/"):
            pkg = self.path[len("/api/app_icon/"):]
            data = get_app_icon(pkg)
            if data:
                self._send(200, data, "image/png")
            else:
                self._send(404, b"", "text/plain")
        elif self.path == "/api/overview":
            self._send(200, json.dumps(overview_payload(), ensure_ascii=False))
        elif self.path.startswith("/api/templates/"):
            rest = self.path[len("/api/templates/"):]
            parts = rest.split("/", 1)
            pkg = parts[0]
            if len(parts) == 1:
                self._send(200, json.dumps({"pkg": pkg, "templates": _templates_of(pkg)}, ensure_ascii=False))
            else:
                fname = parts[1]
                tdir = os.path.join(BASE, "adapters", "templates", pkg)
                fpath = os.path.join(tdir, fname)
                # 路径穿越防护
                if os.path.dirname(fpath) != tdir or not os.path.isfile(fpath):
                    self._send(404, json.dumps({"error": "template not found"}, ensure_ascii=False))
                    return
                with open(fpath, "rb") as f:
                    self._send(200, f.read(), "image/png")
        elif self.path.split("?")[0] == "/api/device/screenshot":
            # 截取模拟器当前画面（点击定位图选点用）；设备离线返回 503
            try:
                from flow_executor import FlowDeviceExecutor as _FDE_snap
                import cv2 as _cv2_snap
                img = _FDE_snap()._dev().screencap()
                ok2, buf2 = _cv2_snap.imencode(".png", img)
                if not ok2:
                    raise RuntimeError("PNG encode failed")
                self._send(200, buf2.tobytes(), "image/png")
            except Exception as e:  # noqa: BLE001
                self._send(503, json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        elif self.path == "/api/status":
            self._send(200, json.dumps(status_payload(), ensure_ascii=False))
        elif self.path == "/api/pipeline/live":
            self._send(200, json.dumps(pipeline_live(), ensure_ascii=False))
        elif self.path == "/api/failures":
            self._send(200, json.dumps(failures_payload(), ensure_ascii=False))
        elif self.path == "/api/flows":
            self._send(200, json.dumps(flows_payload(), ensure_ascii=False))
        elif self.path.startswith("/api/flows/") and self.path.endswith("/logs"):
            pkg = self.path[len("/api/flows/"):-len("/logs")]
            self._send(200, json.dumps(flow_logs(pkg), ensure_ascii=False))
        elif self.path == "/api/flows/runstatus":
            self._send(200, json.dumps(flow_run_status(), ensure_ascii=False))
        elif self.path == "/api/flows/prints":
            self._send(200, json.dumps(flow_prints(), ensure_ascii=False))
        elif self.path.startswith("/api/apps/tasks/"):
            pkg = self.path[len("/api/apps/tasks/"):]
            if pkg and "/" not in pkg:
                self._send(200, json.dumps(tasks_get(pkg), ensure_ascii=False))
            else:
                self._send(404, json.dumps({"error": "not found"}, ensure_ascii=False))
        elif self.path.startswith("/api/flows/"):
            rest = self.path[len("/api/flows/"):]
            if not rest:
                self._send(404, json.dumps({"error": "not found"}, ensure_ascii=False))
            elif "/" in rest:
                pkg, _, task_id = rest.partition("/")
                flow = flow_get_task(pkg, task_id)
                if flow is None:
                    self._send(404, json.dumps({"ok": False, "error": "任务不存在：%s/%s" % (pkg, task_id)}, ensure_ascii=False))
                else:
                    self._send(200, json.dumps(flow, ensure_ascii=False))
            else:
                self._send(200, json.dumps(flow_get(rest), ensure_ascii=False))
        elif self.path == "/api/steps":
            payload = {"records": load_ledger(), "paused": paused,
                       "count": len(load_ledger())}
            self._send(200, json.dumps(payload, ensure_ascii=False))
        elif self.path.startswith("/api/steps/") and self.path.endswith("/image"):
            rec_id = self.path.split("/")[3]
            for r in load_ledger():
                if r["id"] == rec_id:
                    img = os.path.join(STEPS_DIR, r["image"])
                    try:
                        with open(img, "rb") as f:
                            ctype = "image/png" if r["image"].endswith(".png") else "image/jpeg"
                            self._send(200, f.read(), ctype)
                    except OSError:
                        self._send(404, json.dumps({"error": "image missing"}, ensure_ascii=False))
                    return
            self._send(404, json.dumps({"error": "record not found"}, ensure_ascii=False))
        else:
            # UX-05 修复：静态兜底 —— serve gui/ 目录下的 .js/.css/图片等静态资源（含路径穿越防护）
            rel = self.path.split("?")[0].lstrip("/")
            if rel and ".." not in rel and not rel.startswith("api/"):
                fp = os.path.join(os.path.dirname(os.path.abspath(__file__)), rel)
                if os.path.isfile(fp):
                    ext = os.path.splitext(fp)[1].lower()
                    ctype = {
                        ".js": "text/javascript", ".css": "text/css", ".png": "image/png",
                        ".jpg": "image/jpeg", ".svg": "image/svg+xml", ".ico": "image/x-icon",
                        ".map": "application/json", ".json": "application/json",
                    }.get(ext, "application/octet-stream")
                    with open(fp, "rb") as f:
                        self._send(200, f.read(), ctype)
                    return
            self._send(404, json.dumps({"error": "not found"}, ensure_ascii=False))

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length <= 0 or length > MAX_BODY:
            return None, "请求体为空或超过 8MB"
        try:
            return json.loads(self.rfile.read(length).decode("utf-8")), None
        except Exception:
            return None, "请求体 JSON 解析失败"

    def do_DELETE(self):
        path = self.path
        if path.startswith("/api/templates/"):
            rest = path[len("/api/templates/"):]
            parts = rest.split("/", 1)
            if len(parts) != 2 or not parts[1]:
                self._send(400, json.dumps({"error": "need pkg/name"}, ensure_ascii=False))
                return
            pkg, fname = parts[0], parts[1]
            tdir = os.path.join(BASE, "adapters", "templates", pkg)
            fpath = os.path.join(tdir, fname)
            # 路径穿越防护：必须落在模板目录内且确为文件
            if os.path.dirname(fpath) != tdir or not os.path.isfile(fpath):
                self._send(404, json.dumps({"error": "template not found"}, ensure_ascii=False))
                return
            try:
                os.unlink(fpath)
            except OSError as ex:
                self._send(500, json.dumps({"error": str(ex)}, ensure_ascii=False))
                return
            self._send(200, json.dumps({"ok": True, "deleted": fname}, ensure_ascii=False))
            return
        elif path.startswith("/api/pipeline/apps/"):
            rest = path[len("/api/pipeline/apps/"):]
            if "/steps/" in rest:
                pkg, _, idx = rest.partition("/steps/")
                self._send(200, json.dumps(delete_step(pkg, idx), ensure_ascii=False))
            else:
                self._send(200, json.dumps(delete_app(rest), ensure_ascii=False))
        else:
            self._send(404, json.dumps({"error": "not found"}, ensure_ascii=False))

    def do_POST(self):
        if self.path == "/api/start":
            self._send(200, json.dumps(start_pipeline(), ensure_ascii=False))
        elif self.path == "/api/stop":
            self._send(200, json.dumps(stop_pipeline(), ensure_ascii=False))
        elif self.path == "/api/steps/submit":
            payload, err = self._read_json_body()
            if err:
                self._send(400, json.dumps({"ok": False, "error": err}, ensure_ascii=False))
                return
            self._send(200, json.dumps(submit_step(payload), ensure_ascii=False))
        elif self.path == "/api/steps/resume":
            self._send(200, json.dumps(resume(), ensure_ascii=False))
        elif self.path == "/api/ocr":
            payload, err = self._read_json_body()
            if err:
                self._send(400, json.dumps({"ok": False, "error": err}, ensure_ascii=False))
                return
            self._send(200, json.dumps(ocr_recognize(payload), ensure_ascii=False))
        elif self.path == "/api/failures/add":
            payload, err = self._read_json_body()
            if err:
                self._send(400, json.dumps({"ok": False, "error": err}, ensure_ascii=False))
                return
            fid = add_failure(payload.get("app", "?"), payload.get("task", "?"),
                              payload.get("reason", "test"), payload.get("pipeline", "default"),
                              payload.get("detail", ""))
            self._send(200, json.dumps({"ok": True, "id": fid}, ensure_ascii=False))
        elif self.path == "/api/failures/clear":
            # 清理信息：归档备份后清空【全部】记录（含已处理/已忽略历史），
            # 界面不再显示旧信息；审计数据先写入 state/failures_archive.json 不丢
            cleared = 0
            with failures_lock:
                items = load_failures()
                cleared = len(items)
                if cleared:
                    try:
                        pass  # 使用 failures_core.FAILURES_FILE（core.failures 已在顶部 import）
                        arch = failures_core.FAILURES_FILE.replace("failures.json", "failures_archive.json")
                        prev = []
                        if os.path.isfile(arch):
                            with open(arch, "r", encoding="utf-8") as af:
                                prev = json.load(af)
                        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
                        for rec in items:
                            rec.setdefault("history", []).append(
                                {"ts": stamp, "action": "cleared", "note": "一键清理信息（归档保存）"})
                        prev.extend(items)
                        with open(arch, "w", encoding="utf-8") as af:
                            json.dump(prev, af, ensure_ascii=False, indent=1)
                    except Exception:
                        pass  # 归档失败不阻塞清理
                    save_failures([])
            self._send(200, json.dumps({"ok": True, "cleared": cleared}, ensure_ascii=False))
        elif self.path.startswith("/api/failures/") and self.path.endswith("/mark"):
            fid = self.path[len("/api/failures/"):-len("/mark")]
            payload, err = self._read_json_body()
            if err:
                self._send(400, json.dumps({"ok": False, "error": err}, ensure_ascii=False))
                return
            self._send(200, json.dumps(mark_failure(fid, payload), ensure_ascii=False))
        elif self.path.startswith("/api/templates/") and self.path.endswith("/upload"):
            pkg = self.path[len("/api/templates/"):-len("/upload")]
            payload, err = self._read_json_body()
            if err:
                self._send(400, json.dumps({"ok": False, "error": err}, ensure_ascii=False))
                return
            self._send(200, json.dumps(upload_template(pkg, payload), ensure_ascii=False))
        elif self.path == "/api/stress/report":
            payload, err = self._read_json_body()
            if err:
                self._send(400, json.dumps({"ok": False, "error": err}, ensure_ascii=False))
                return
            self._send(200, json.dumps(stress_report(payload), ensure_ascii=False))
        elif self.path == "/api/flows/stop":
            self._send(200, json.dumps(flow_stop(), ensure_ascii=False))
        elif self.path == "/api/flows/pause":
            self._send(200, json.dumps(flow_pause(), ensure_ascii=False))
        elif self.path == "/api/mumu/start":
            self._send(200, json.dumps(mumu_start(), ensure_ascii=False))
        elif self.path == "/api/mumu/stop":
            self._send(200, json.dumps(mumu_stop(), ensure_ascii=False))
        elif self.path == "/api/mumu/adb_connect":
            self._send(200, json.dumps(mumu_adb_connect(), ensure_ascii=False))
        elif self.path.startswith("/api/apps/tasks/") and self.path.endswith("/add"):
            pkg = self.path[len("/api/apps/tasks/"):-len("/add")]
            payload, err = self._read_json_body()
            if err:
                self._send(400, json.dumps({"ok": False, "error": err}, ensure_ascii=False))
                return
            self._send(200, json.dumps(task_add(pkg, payload.get("name", "")), ensure_ascii=False))
        elif self.path.startswith("/api/apps/tasks/") and self.path.endswith("/rename"):
            pkg = self.path[len("/api/apps/tasks/"):-len("/rename")]
            payload, err = self._read_json_body()
            if err:
                self._send(400, json.dumps({"ok": False, "error": err}, ensure_ascii=False))
                return
            self._send(200, json.dumps(task_rename(pkg, payload), ensure_ascii=False))
        elif self.path.startswith("/api/apps/tasks/") and self.path.endswith("/delete"):
            pkg = self.path[len("/api/apps/tasks/"):-len("/delete")]
            payload, err = self._read_json_body()
            if err:
                self._send(400, json.dumps({"ok": False, "error": err}, ensure_ascii=False))
                return
            self._send(200, json.dumps(task_delete(pkg, payload), ensure_ascii=False))
        elif self.path.startswith("/api/apps/tasks/") and self.path.endswith("/toggle"):
            pkg = self.path[len("/api/apps/tasks/"):-len("/toggle")]
            payload, err = self._read_json_body()
            if err:
                self._send(400, json.dumps({"ok": False, "error": err}, ensure_ascii=False))
                return
            self._send(200, json.dumps(task_toggle(pkg, payload), ensure_ascii=False))
        elif self.path.startswith("/api/apps/tasks/") and self.path.endswith("/order"):
            pkg = self.path[len("/api/apps/tasks/"):-len("/order")]
            payload, err = self._read_json_body()
            if err:
                self._send(400, json.dumps({"ok": False, "error": err}, ensure_ascii=False))
                return
            self._send(200, json.dumps(task_order(pkg, payload), ensure_ascii=False))
        elif self.path.startswith("/api/flows/") and self.path.endswith("/run-all"):
            pkg = self.path[len("/api/flows/"):-len("/run-all")]
            self._send(200, json.dumps(run_all_start(pkg), ensure_ascii=False))
        elif self.path.startswith("/api/flows/") and self.path.endswith("/run"):
            pkg = self.path[len("/api/flows/"):-len("/run")]
            task_id = None
            if "/" in pkg:  # pkg/task/run：编辑器按当前画布任务运行（2026-08-30 第 40 轮）
                pkg, _, task_id = pkg.partition("/")
                task_id = task_id or None
            payload, err = self._read_json_body()
            if err:
                payload = {}
            sid = (payload or {}).get("start_node_id")
            self._send(200, json.dumps(flow_run(pkg, start_node_id=sid, task_id=locals().get("task_id")), ensure_ascii=False))
        elif self.path.startswith("/api/flows/") and self.path.endswith("/run-mock"):
            pkg = self.path[len("/api/flows/"):-len("/run-mock")]
            task_id = None
            if "/" in pkg:  # pkg/task/run-mock：同上按任务运行
                pkg, _, task_id = pkg.partition("/")
                task_id = task_id or None
            payload, err = self._read_json_body()
            if err:
                payload = {}
            sid = (payload or {}).get("start_node_id")
            self._send(200, json.dumps(flow_run(pkg, mock=True, start_node_id=sid, task_id=locals().get("task_id")), ensure_ascii=False))
        elif self.path.startswith("/api/flows/") and self.path.endswith("/validate"):
            pkg = self.path[len("/api/flows/"):-len("/validate")]
            payload, err = self._read_json_body()
            if err:
                payload = None
            self._send(200, json.dumps(flow_validate(pkg, payload or {}), ensure_ascii=False))
        elif self.path.startswith("/api/flows/"):
            rest = self.path[len("/api/flows/"):]
            if not rest:
                self._send(404, json.dumps({"error": "not found"}, ensure_ascii=False))
                return
            payload, err = self._read_json_body()
            if err:
                self._send(400, json.dumps({"ok": False, "error": err}, ensure_ascii=False))
                return
            if "/" in rest:
                pkg, _, task_id = rest.partition("/")
                self._send(200, json.dumps(flow_save_task(pkg, task_id, payload), ensure_ascii=False))
            else:
                self._send(200, json.dumps(flow_save(rest, payload), ensure_ascii=False))
        elif self.path == "/api/pipeline/apps":
            payload, err = self._read_json_body()
            if err:
                self._send(400, json.dumps({"ok": False, "error": err}, ensure_ascii=False))
                return
            self._send(200, json.dumps(add_app(payload), ensure_ascii=False))
        elif self.path == "/api/pipeline/order":
            payload, err = self._read_json_body()
            if err:
                self._send(400, json.dumps({"ok": False, "error": err}, ensure_ascii=False))
                return
            self._send(200, json.dumps(reorder_apps(payload), ensure_ascii=False))
        elif self.path.startswith("/api/pipeline/apps/") and "/steps/" in self.path and self.path.endswith("/toggle"):
            rest = self.path[len("/api/pipeline/apps/"):-len("/toggle")]
            pkg, _, idx = rest.partition("/steps/")
            self._send(200, json.dumps(toggle_step(pkg, idx), ensure_ascii=False))
        elif self.path.startswith("/api/pipeline/apps/") and self.path.endswith("/toggle"):
            # App 级启停（控制台勾选）；步骤级 toggle 已在上方按 /steps/ 分支拦截
            pkg = self.path[len("/api/pipeline/apps/"):-len("/toggle")]
            self._send(200, json.dumps(toggle_app_enabled(pkg), ensure_ascii=False))
        elif self.path.startswith("/api/pipeline/apps/") and self.path.endswith("/steps"):
            pkg = self.path[len("/api/pipeline/apps/"):-len("/steps")]
            payload, err = self._read_json_body()
            if err:
                self._send(400, json.dumps({"ok": False, "error": err}, ensure_ascii=False))
                return
            self._send(200, json.dumps(add_step(pkg, payload), ensure_ascii=False))
        else:
            self._send(404, json.dumps({"error": "not found"}, ensure_ascii=False))

    def log_message(self, fmt, *args):
        pass  # 静默访问日志


if __name__ == "__main__":
    print("[autofw-gui] 面板服务: http://127.0.0.1:%d" % PORT)
    # 存储迁移：旧单文件 state/flows/<pkg>.json → 目录化 <pkg>/（meta.json + main.json），幂等
    try:
        migrated = migrate_flows()
        if migrated:
            for fn, dpath, kind in migrated:
                print("[autofw-gui] 迁移: %s → %s/（%s）" % (fn, dpath, kind))
        else:
            print("[autofw-gui] 迁移: 无需迁移（已目录化或无旧文件）")
    except Exception as e:  # noqa: BLE001
        print("[autofw-gui] 迁移失败（不阻塞启动）: %s" % e)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
