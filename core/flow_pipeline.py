# -*- coding: utf-8 -*-
"""flow_pipeline.py - B2 路线：编排取 state/flows/<pkg>/ 图形化工作流，执行单元用 FlowRunner。

取代 pipeline.py 的适配器硬编码任务段：
- 读 state/flows/<pkg>/meta.json 的 enabled 任务（按 order 排序），依次执行。
- 每个任务 = 一个 FlowRunner(FlowDeviceExecutor(templates_dir=adapters/templates/<pkg>)).run()。
- 复用现有日志 / 失败台账(add_failure) / 运行台账(save_run_log) / ADB 重连骨架。
- 不做主界面就绪等待前置（决策①A：就绪完全交给 flow 编排，框架不管）。
- 模板目录沿用 adapters/templates/<pkg>，与现有 main_scene/enter_btn 模板一致。

入口由 main.py --mode flow 调用；pipeline.json 的 mode 字段控制走此路径还是旧 adapter 路径。
"""
import json
import logging
import os
import re
import threading
import time

import cv2

from .flow_engine import FlowRunner, validate_flow, save_run_log
from .flow_executor import FlowDeviceExecutor
from .device import EmulatorDevice
from .failures import add_failure

log = logging.getLogger("autofw")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # autofw 根
FLOWS_ROOT = os.path.join(BASE, "state", "flows")
ADAPTER_TEMPLATES = os.path.join(BASE, "adapters", "templates")

# 61 轮：流水线实时进度文件（面板「跳转到运行中任务画布」与状态展示的数据源）。
# 由守护线程每 500ms 原子覆写；读取方：gui/server.py 的 pipeline_live()（只读）。
LIVE_PATH = os.path.join(BASE, "state", "pipeline_live.json")


def write_live(payload):
    """原子写实时进度（tmp + replace）。写失败静默——上报不得影响执行主链路。"""
    try:
        tmp = LIVE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, LIVE_PATH)
    except OSError:
        pass


def write_live_idle():
    """流水线整体退出时归位（phase=idle），避免面板读到陈旧的 task_done。"""
    write_live({"running": False, "phase": "idle",
                "ts": time.strftime("%Y-%m-%d %H:%M:%S")})

_SAFE_RE = re.compile(r"[\\/:*?\"<>|\s]+")


def _safe_pkg(pkg):
    return _SAFE_RE.sub("_", pkg).strip("._") or "default"


def _flow_dir(pkg):
    return os.path.join(FLOWS_ROOT, _safe_pkg(pkg))


def _valid_task_id(tid):
    return bool(tid) and len(tid) <= 64 and bool(re.match(r"^[A-Za-z0-9_-]+$", tid))


def load_meta(pkg):
    """读任务台账（state/flows/<pkg>/meta.json）。缺失/损坏→None。"""
    dpath = _flow_dir(pkg)
    mpath = os.path.join(dpath, "meta.json")
    if not os.path.isfile(mpath):
        return None
    try:
        with open(mpath, "r", encoding="utf-8") as f:
            meta = json.load(f)
        if not isinstance(meta, dict) or not isinstance(meta.get("tasks"), list):
            return None
        return meta
    except (OSError, ValueError):
        return None


def meta_tasks(pkg):
    """按 order 排序的任务列表（含 disabled）。无 meta→None。"""
    meta = load_meta(pkg)
    if meta is None:
        return None
    return sorted(meta.get("tasks", []), key=lambda t: (t.get("order", 0), t.get("id", "")))


def task_load(pkg, task_id):
    """读单个任务 flow 文件（state/flows/<pkg>/<taskId>.json）。"""
    if not _valid_task_id(task_id):
        return None
    try:
        with open(os.path.join(_flow_dir(pkg), task_id + ".json"), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


class FlowPipeline:
    """按 pipeline.json 的 entries（app + enabled）顺序，逐个跑该 App 的图形化工作流。"""

    def __init__(self, config_path: str, device: EmulatorDevice = None,
                 recovery=None, resume: bool = True):
        self.config = json.load(open(config_path, encoding="utf-8"))
        self.device = device
        self.recovery = recovery
        self.resume = resume
        self.entries = [e for e in self.config.get("pipeline", [])
                        if e.get("enabled", True)]
        self.templates_dir = None  # 每个 app 独立设置

    def run(self):
        log.info("[flow_pipeline] 启动（mode=flow），共 %d 个启用 App", len(self.entries))
        try:
            for idx, entry in enumerate(self.entries, 1):
                pkg = entry["app"]
                log.info("[flow_pipeline] (%d/%d) 开始执行 App：%s", idx, len(self.entries), pkg)
                self._run_app(pkg)
        finally:
            write_live_idle()  # 61 轮：无论正常结束/异常，归位实时进度（面板不再显示运行中）

    def _run_app(self, pkg):
        # ADB 链路保活（沿用 pipeline.py 的切换协议前置）
        if self.device is None:
            self.device = EmulatorDevice()
        if not self.device.ping():
            self.device.reconnect()
        # 模板目录绑定到该 App
        self.templates_dir = os.path.join(ADAPTER_TEMPLATES, pkg)
        if not os.path.isdir(self.templates_dir):
            log.warning("[flow_pipeline] 模板目录不存在：%s（条件/匹配节点可能失败）",
                        self.templates_dir)
        try:
            tasks = meta_tasks(pkg)
            if tasks is None:
                # 兼容：无 meta 但存在 main.json（旧单文件迁移未覆盖的场景）
                if os.path.isfile(os.path.join(_flow_dir(pkg), "main.json")):
                    tasks = [{"id": "main", "name": "默认任务", "enabled": True, "order": 0}]
                else:
                    log.error("[flow_pipeline] App %s 无可用工作流（meta/task 缺失）", pkg)
                    add_failure(pkg, ["?"], "无可用工作流（meta/task 缺失）", pipeline="flow")
                    return
            enabled = [t for t in tasks if t.get("enabled", True)]
            if not enabled:
                log.warning("[flow_pipeline] App %s 无可启用任务，跳过", pkg)
                return
            for t in enabled:
                self._run_task(pkg, t)
        except Exception as e:  # noqa: BLE001
            log.error("[flow_pipeline] App %s 执行异常：%s", pkg, e)
            add_failure(pkg, ["?"], "执行异常: %s" % e, pipeline="flow")

    def _run_task(self, pkg, task_meta):
        task_id = task_meta["id"]
        name = task_meta.get("name", task_id)
        flow = task_load(pkg, task_id)
        if flow is None:
            log.error("[flow_pipeline] 任务 %s/%s flow 文件缺失，跳过", pkg, task_id)
            add_failure(pkg, [task_id], "flow 文件缺失", pipeline="flow")
            return
        # 静态校验（error 级阻断，warning 放行）
        errs = [x for x in validate_flow(flow) if x.get("level") == "error"]
        if errs:
            log.error("[flow_pipeline] 任务 %s/%s 校验未通过，跳过：%s",
                      pkg, task_id, errs[0]["msg"])
            add_failure(pkg, [task_id], "校验未通过: %s" % errs[0]["msg"], pipeline="flow")
            return
        # 真实设备执行器（模板目录绑定 pkg）
        executor = FlowDeviceExecutor(device=self.device,
                                       templates_dir=self.templates_dir)
        runner = FlowRunner(executor, flow)
        log.info("[flow_pipeline] 执行任务 %s/%s：「%s」", pkg, task_id, name)
        # 61 轮：实时进度上报 — 守护线程每 500ms 从 runner.node_recs 取「已进入未退出」的
        # 节点（= 正在执行），原子写 pipeline_live.json。读取方：面板 /api/pipeline/live、
        # 控制台「跳转到运行中任务画布」。上报失败静默，绝不影响执行主链路。
        stop_ev = threading.Event()

        def _monitor():
            while not stop_ev.wait(0.5):
                cur = None
                try:
                    for rec in reversed(runner.node_recs):
                        if rec.get("exit") is None:
                            cur = rec
                            break
                except Exception:  # noqa: BLE001
                    cur = None
                write_live({"running": True, "phase": "task", "pkg": pkg,
                            "task_id": task_id, "task_name": name,
                            "node_id": cur.get("id") if cur else None,
                            "node_name": cur.get("name") if cur else None,
                            "node_type": cur.get("type") if cur else None,
                            "steps": runner.steps,
                            "ts": time.strftime("%Y-%m-%d %H:%M:%S")})

        th = threading.Thread(target=_monitor, daemon=True)
        th.start()
        summary = None
        run_err = None
        try:
            summary = runner.run()
        except Exception as e:  # noqa: BLE001
            run_err = e
        finally:
            stop_ev.set()  # 先停上报线程，再落任务结束状态
        if run_err is not None:
            log.error("[flow_pipeline] 任务 %s/%s 运行异常：%s", pkg, task_id, run_err)
            add_failure(pkg, [task_id], "运行异常: %s" % run_err, pipeline="flow")
            write_live({"running": False, "phase": "task_done", "pkg": pkg,
                        "task_id": task_id, "task_name": name, "status": "error",
                        "steps": runner.steps,
                        "ts": time.strftime("%Y-%m-%d %H:%M:%S")})
            return
        status = summary.get("status", "error")
        if status != "ok":
            log.warning("[flow_pipeline] 任务 %s/%s 结束：%s（%s）",
                        pkg, task_id, status, summary.get("note", ""))
            add_failure(pkg, [task_id], "状态=%s: %s" % (status, summary.get("note", "")),
                        pipeline="flow")
        else:
            log.info("[flow_pipeline] 任务 %s/%s 完成（%d 步）", pkg, task_id,
                     summary.get("steps", 0))
        write_live({"running": False, "phase": "task_done", "pkg": pkg,
                    "task_id": task_id, "task_name": name, "status": status,
                    "steps": summary.get("steps", 0),
                    "ts": time.strftime("%Y-%m-%d %H:%M:%S")})
        # 运行台账（面板 /api/flows/<pkg>/logs 可见）
        try:
            rec = dict(summary)
            rec["note"] = "任务「%s」：%s" % (name, summary.get("note", ""))
            save_run_log(pkg, rec)
        except Exception:  # noqa: BLE001
            pass
