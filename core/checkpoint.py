# -*- coding: utf-8 -*-
"""断点持久化：state.json 原子写（temp+fsync+rename）+ 双副本轮流 + 意图日志。
持久化三件：流水线进度 / 资源计数 / 环境事实。易变项（display id/坐标）永不落盘。"""
import json
import os
import time

STATE_DIR = "state"
INTENT_LOG = "intent.log"


class Checkpoint:
    def __init__(self, state_dir: str = STATE_DIR):
        self.state_dir = state_dir
        os.makedirs(state_dir, exist_ok=True)
        self.path_a = os.path.join(state_dir, "checkpoint.a.json")
        self.path_b = os.path.join(state_dir, "checkpoint.b.json")
        self.intent = os.path.join(state_dir, INTENT_LOG)
        self._data = {
            "pipeline_index": 0,      # 当前 App 在流水线中的索引
            "app": None,              # 当前 App 包名
            "task_id": None,          # 当前子任务 ID
            "completed": [],          # 已完成子任务列表（幂等跳过依据）
            "resources": {},          # 资源计数（体力/次数，供 verify_state 比对）
            "env": {},                # 环境事实（保活/分辨率/实例索引）
            "updated_at": None,
        }

    # ---- 写入 ----
    def save(self, intent: str):
        """意图日志先行（崩溃后可重放），再原子写双副本。"""
        with open(self.intent, "a", encoding="utf-8") as f:
            f.write(f"{time.time():.3f} {intent}\n")
        self._data["updated_at"] = time.time()
        for path in (self.path_a, self.path_b):  # 双副本轮流，防写一半损坏
            self._atomic_write(path)

    def _atomic_write(self, path: str):
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)  # 原子替换

    # ---- 读取 ----
    def load(self) -> bool:
        for path in (self.path_a, self.path_b):
            if os.path.exists(path):
                try:
                    with open(path, encoding="utf-8") as f:
                        self._data.update(json.load(f))
                    return True
                except (json.JSONDecodeError, OSError):
                    continue  # 该副本损坏，试另一个
        return False

    # ---- 访问器 ----
    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value

    def mark_completed(self, task_id: str):
        if task_id not in self._data["completed"]:
            self._data["completed"].append(task_id)

    def is_completed(self, task_id: str) -> bool:
        return task_id in self._data["completed"]

    def update_resource(self, key: str, value):
        self._data["resources"][key] = value
