# -*- coding: utf-8 -*-
"""任务基类：任务树叶子 = 幂等执行单元。
所有副作用操作（消耗体力/货币/领取/购买）必须拆成独立子任务，
且前后各一个校验点（precheck 预检 / postcheck 后验）——防重复消耗的唯一办法（E12）。"""
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .device import EmulatorDevice
    from .checkpoint import Checkpoint


class TaskContext:
    """执行上下文：设备 + 断点 + 截图识别服务（模板匹配/OCR）。"""

    def __init__(self, device: "EmulatorDevice", checkpoint: "Checkpoint", adapter):
        self.device = device
        self.checkpoint = checkpoint
        self.adapter = adapter
        self._frame = None          # 当前帧缓存（场景缓存，同帧不重复识别）
        self.templates = adapter.templates  # {name: np.ndarray} 模板库

    def frame(self):
        """取当前帧（缓存，动作后失效）。"""
        if self._frame is None:
            self._frame = self.device.screencap()
        return self._frame

    def invalidate(self):
        self._frame = None

    def find(self, template_name: str, threshold: float = 0.8):
        """模板匹配：返回 (cx, cy) 中心坐标或 None。"""
        import cv2
        img = self.frame()
        tpl = self.templates[template_name]
        res = cv2.matchTemplate(img, tpl, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(res)
        if max_val >= threshold:
            h, w = tpl.shape[:2]
            return (max_loc[0] + w // 2, max_loc[1] + h // 2)
        return None

    def tap_template(self, template_name: str, threshold: float = 0.8) -> bool:
        pos = self.find(template_name, threshold)
        if pos:
            self.device.tap(*pos)
            self.invalidate()
            return True
        return False


class BaseTask(ABC):
    """子任务：可安全重跑的最小单位。task_id 稳定，用于断点引用与幂等跳过。"""

    def __init__(self, task_id: str):
        self.task_id = task_id

    def precheck(self, ctx: TaskContext) -> bool:
        """前置校验：当前状态允许执行该副作用操作（默认 True）。"""
        return True

    @abstractmethod
    def run(self, ctx: TaskContext):
        """主体：截图→识别→操作→确认。"""

    def postcheck(self, ctx: TaskContext) -> bool:
        """结果确认：副作用确实发生（体力变化/界面跳转）。默认 True。"""
        return True

    # 幂等执行模板：precheck → run → postcheck，任何一步失败抛 RetryableError
    def execute(self, ctx: TaskContext):
        if not self.precheck(ctx):
            raise RuntimeError(f"precheck failed: {self.task_id}")
        self.run(ctx)
        if not self.postcheck(ctx):
            raise RuntimeError(f"postcheck failed: {self.task_id}")
