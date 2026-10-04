# -*- coding: utf-8 -*-
"""示例 App 适配器：展示新 App 接入的完整形态（R1）。
真实使用时：替换 package/activity、填充模板图（main_scene/按钮）、实现任务逻辑。
模板加载：基类 AppAdapter.load_templates() 自动从 adapters/templates/<package>/ 加载全部 PNG。"""
from core.adapter import AppAdapter
from core.exceptions import HumanTakeover, RetryableError
from core.task import BaseTask


class ExampleCollectTask(BaseTask):
    """示例任务：点击「领取」按钮直到消失（含副作用校验点示范）。"""

    def __init__(self):
        super().__init__("collect")

    def precheck(self, ctx) -> bool:
        # 副作用预检：按钮存在才执行（防误点）
        return ctx.find("collect_btn") is not None

    def run(self, ctx):
        if not ctx.tap_template("collect_btn"):
            raise RetryableError("collect button not found")

    def postcheck(self, ctx) -> bool:
        # 副作用后验：按钮已消失 = 领取成功（或弹窗出现走 close_popups）
        ctx.invalidate()
        return ctx.find("collect_btn") is None


class ExampleAppAdapter(AppAdapter):
    """示例：把包名/Activity/任务替换成你的目标 App 即可接入。"""

    package = "com.example.gameA"
    display_name = "示例游戏A"
    activity = ".MainActivity"

    def __init__(self):
        super().__init__()   # 初始化实例级 templates（B6 修复）

    def ensure_foreground(self, ctx) -> bool:
        if ctx.device.is_foreground(self.package):
            return True
        ctx.device.start_app(self.package, self.activity)   # 拉回
        return True

    def ensure_home(self, ctx) -> bool:
        # 回主界面：点返回键 N 次或点主界面按钮（模板）
        for _ in range(3):
            if ctx.find("main_scene") is not None:
                return True
            ctx.device.key("BACK")
        return ctx.find("main_scene") is not None

    def login_recovery(self, ctx) -> bool:
        # 冷启动/被顶号恢复：等登录按钮 → 点击 → 等主界面（真实实现按 App 实际界面）
        if ctx.find("login_btn"):
            ctx.tap_template("login_btn")
        return True

    def close_popups(self, ctx) -> bool:
        if ctx.find("popup_close"):
            ctx.tap_template("popup_close")
            return True
        return False

    def build_tasks(self):
        return [ExampleCollectTask()]

    def verify_state(self, ctx, checkpoint) -> bool | None:
        # 断点恢复前比对（R7 三态）：默认返回 True（未完成）；真实实现按 App 实际状态
        key = f"{self.package}:collect"
        if checkpoint.is_completed(key):
            return False   # 已完成 → 跳过
        return True        # 未完成 → 继续
