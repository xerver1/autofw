# -*- coding: utf-8 -*-
"""App 适配器抽象：每 App 一个插件（R1 通用性核心）。
新 App 接入 = 1 个 Python 文件 + 主界面/关键按钮模板图。"""
import os
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from .exceptions import HumanTakeover

if TYPE_CHECKING:
    from .task import TaskContext


class AppAdapter(ABC):
    # 元信息
    package: str = ""              # 包名（唯一标识，前台校验/启停用）
    display_name: str = ""         # 展示名
    activity: str = ""             # 主 Activity（am start 用，可空）

    def __init__(self):
        self.templates: dict = {}  # 模板库 {name: np.ndarray}（实例属性，B6 修复：防跨 App 污染）

    # ---- 模板加载（A3 修复：pipeline 启动时调用，勿遗漏）----
    def load_templates(self, ctx: "TaskContext"):
        """默认实现：从 adapters/templates/<package>/ 加载全部 PNG，文件名即模板名。
        约定：main_scene（主界面，必填）、<按钮名>（如 collect_btn/popup_close/login_btn）。"""
        import cv2
        base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "adapters",
                            "templates", self.package)
        if not os.path.isdir(base):
            raise HumanTakeover(f"模板目录不存在: {base}（先采集模板再运行）")
        for name in os.listdir(base):
            if name.lower().endswith(".png"):
                key = name[:-4]
                self.templates[key] = cv2.imread(os.path.join(base, name))
        if "main_scene" not in self.templates:
            raise HumanTakeover(f"缺少 main_scene 模板: {base}")

    # ---- 必实现：环境原语 ----
    @abstractmethod
    def ensure_foreground(self, ctx: "TaskContext") -> bool:
        """拉回原语：前台校验失败时 am start 拉回；返回 True=已在前台。"""

    @abstractmethod
    def ensure_home(self, ctx: "TaskContext") -> bool:
        """拉回主界面（esc/返回链/点击主界面按钮）。"""

    @abstractmethod
    def login_recovery(self, ctx: "TaskContext") -> bool:
        """登录恢复子流程（冷启动/被顶号后）：点登录→等加载→回主界面。
        检测到"账号在别处登录"→ 抛 HumanTakeover（禁止自动无限重试，E2/E17）。"""

    # ---- 可选：弹窗处理 ----
    def close_popups(self, ctx: "TaskContext") -> bool:
        """关闭公告/更新/权限弹窗（每 App 配置关闭按钮模板）。默认无操作。"""
        return True

    # ---- 必实现：任务产出 ----
    @abstractmethod
    def build_tasks(self) -> list:
        """产出该 App 的任务列表（BaseTask 实例），顺序即执行顺序。"""
        return []

    # ---- 断点恢复前状态比对（幂等校验，E12）----
    def verify_state(self, ctx: "TaskContext", checkpoint) -> bool | None:
        """恢复前比对游戏内真实状态（R7 三态决策）：
        - True  = 确认未完成，可继续执行
        - False = 确认已完成（已结算/已领取）→ 调度跳过该子任务
        - None  = 无法确认（匹配不到）→ 调度暂停，交用户三选（继续/跳过/暂停）
        框架提供接口，具体实现（体力 OCR/结算画面特征）留适配层。"""
        return True
