# -*- coding: utf-8 -*-
"""失败任务台账（core 共享模块）：执行引擎与 GUI 服务共用。
数据：state/failures.json（追加式列表，每任务含 pipeline/app/task/reason/time/status/history）。"""
import json
import os
import threading
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # autofw 根
STATE = os.path.join(BASE, "state")
FAILURES_FILE = os.path.join(STATE, "failures.json")
_lock = threading.Lock()


def load():
    try:
        with open(FAILURES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
    except OSError:
        pass
    return []


def save(items):
    os.makedirs(STATE, exist_ok=True)
    with open(FAILURES_FILE, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def add_failure(app, task, reason, pipeline="default", detail=""):
    """记录一个失败细分任务。返回记录 id。"""
    from datetime import datetime as _dt
    with _lock:
        items = load()
        rec = {
            "id": "fail_%s" % _dt.now().strftime("%Y%m%d_%H%M%S_%f"),
            "pipeline": pipeline, "app": app, "task": task,
            "reason": reason, "detail": detail,
            "ts": time.time(),
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "status": "pending",
            "history": [{"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                         "action": "created", "note": reason}],
        }
        items.append(rec)
        save(items)
        return rec["id"]


def mark(fid, action, note=""):
    """handled / ignored / retried；状态流转写入 history（可追溯）。"""
    if action not in ("handled", "ignored", "retried"):
        return {"ok": False, "error": "处理动作无效（handled/ignored/retried）"}
    with _lock:
        items = load()
        for rec in items:
            if rec["id"] == fid:
                rec["status"] = action
                rec["history"].append({"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                                       "action": action, "note": note})
                save(items)
                return {"ok": True, "msg": "已标记为「%s」" % action}
        return {"ok": False, "error": "未找到失败记录：%s" % fid}


def list_all():
    items = load()
    return {"failures": sorted(items, key=lambda x: x.get("ts", 0), reverse=True),
            "count": len(items),
            "pending": sum(1 for x in items if x.get("status") == "pending")}
