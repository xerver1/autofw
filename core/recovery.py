# -*- coding: utf-8 -*-
"""容灾层：前台双确认 + 拉回 / 熔断器 / 分级降级 / 看门狗 / 点击循环检测。
设计依据：反方评审 23 项事故场景（E1/E2/E12/E15/E20/E21 等 P0 项）。"""
import logging
import time
from collections import deque
from typing import TYPE_CHECKING, Optional

from .exceptions import (AppStuckError, EmulatorDownError, HumanTakeover,
                         RetryableError)

log = logging.getLogger("autofw")

if TYPE_CHECKING:
    from .device import EmulatorDevice


class CircuitBreaker:
    """熔断器：连续失败达阈值触发，降级链由 Recovery 执行。"""

    def __init__(self, max_failures: int = 5):
        self.max_failures = max_failures
        self._failures = 0
        self._open_until = 0.0

    def record_success(self):
        self._failures = 0

    def record_failure(self) -> bool:
        """返回 True = 熔断已触发（连续失败超阈值）。"""
        self._failures += 1
        if self._failures >= self.max_failures:
            self._open_until = time.time() + 30
            return True
        return False

    def is_open(self) -> bool:
        return time.time() < self._open_until


class Watchdog:
    """期望状态看门狗：等待画面出现超时分级；同按钮点击循环检测。"""

    def __init__(self, timeout: float = 60.0, timeout_long: float = 180.0,
                 click_limit: int = 12):
        self.timeout = timeout
        self.timeout_long = timeout_long
        self.click_limit = click_limit
        self._click_record: deque = deque(maxlen=15)   # 最近点击的模板名
        self._wait_start: Optional[float] = None

    def start_wait(self, long: bool = False):
        self._wait_start = time.time()
        self._wait_limit = self.timeout_long if long else self.timeout

    def check_wait(self) -> bool:
        """等待期间调用：超时 → 抛 AppStuckError（游戏还活着但卡住）。"""
        if self._wait_start and time.time() - self._wait_start > self._wait_limit:
            raise AppStuckError("watchdog timeout: expected scene not found")
        return True

    def record_click(self, template_name: str):
        """同按钮点击 ≥12 次或两按钮各 ≥6 次 → 熔断（E15，ALAS 同款）。"""
        self._click_record.append(template_name)
        counts = {}
        for name in self._click_record:
            counts[name] = counts.get(name, 0) + 1
        if any(c >= self.click_limit for c in counts.values()):
            raise AppStuckError("click circuit breaker: same button clicked too many times")
        top2 = sorted(counts.values(), reverse=True)[:2]
        if len(top2) == 2 and top2[0] >= 6 and top2[1] >= 6:
            raise AppStuckError("click oscillation detected")


class Recovery:
    """容灾编排：前台双确认 + 降级链（重试 → adb 重连 → 重启 app → 重启模拟器 → 停止告警）。"""

    def __init__(self, device: "EmulatorDevice", breaker: Optional[CircuitBreaker] = None,
                 watchdog: Optional[Watchdog] = None):
        self.device = device
        self.breaker = breaker or CircuitBreaker()
        self.watchdog = watchdog or Watchdog()

    # ---- 前台双确认（E1/E4）：dumpsys 包名 + 截图特征，双过才放行 ----
    def assert_foreground(self, adapter, ctx, max_pull_back: int = 3) -> bool:
        for attempt in range(max_pull_back):
            dumpsys_ok = self.device.is_foreground(adapter.package)
            visual_ok = self._confirm_visual(ctx)   # 单前台截图协议（W-04）
            if dumpsys_ok and visual_ok:
                self.breaker.record_success()
                return True
            log.warning("foreground assert attempt %d/%d failed (focus=%s visual=%s), pulling back",
                        attempt + 1, max_pull_back, dumpsys_ok, visual_ok)
            # 纠正：拉回（am start → 关弹窗 → 回主界面）
            adapter.ensure_foreground(ctx)
            adapter.close_popups(ctx)
            adapter.ensure_home(ctx)
            time.sleep(2)
        if self.breaker.record_failure():
            raise HumanTakeover("foreground assert failed after pull-back attempts")
        raise RetryableError("foreground assert failed")

    def _confirm_visual(self, ctx, wait_sec: float = 4.0, frames: int = 2,
                        interval: float = 1.0) -> bool:
        """单前台截图协议（W-04，架构 §4.7）：拉回后等待 wait_sec，再连续 frames 帧
        命中 main_scene 才算确认回到 App（解决保活模式下截到桌面/其他 tab 的矛盾）。
        等待秒数与连续帧数为建议值（待实测校准）。"""
        time.sleep(wait_sec)
        hits = 0
        for _ in range(frames):
            ctx.invalidate()   # 强制新帧（绕过帧缓存）
            if ctx.find("main_scene") is not None:
                hits += 1
                if hits >= frames:
                    return True
            else:
                hits = 0
            time.sleep(interval)
        return False

    # ---- 降级链：任何命令/任务失败统一入口 ----
    def handle_failure(self, exc: Exception, adapter=None) -> bool:
        """返回 True=已恢复可继续；False=需停止（抛 HumanTakeover）。
        降级链：重试 → adb 重连 → 重启 app → 重启模拟器（W-05）→ 停止告警。"""
        if isinstance(exc, HumanTakeover):
            raise exc
        if isinstance(exc, EmulatorDownError) or not self.device.ping():
            # 第 2 级：adb 重连
            try:
                self.device.reconnect()
                return True
            except EmulatorDownError:
                # 第 4 级：重启模拟器（三级心跳 2/3 级同样走此路径，W-05）
                log.warning("adb reconnect failed, escalating to emulator restart")
                try:
                    self.device.emulator_restart()
                    return True
                except EmulatorDownError:
                    raise HumanTakeover("emulator restart failed, manual intervention needed")
        if isinstance(exc, AppStuckError):
            # 第 3 级：重启 app（走冷启动 → login_recovery）
            if adapter:
                self.device.close_app(adapter.package)
                self.device.start_app(adapter.package, adapter.activity)
                time.sleep(5)
            return True
        # 第 1 级：重试（由上层 retry 循环控制次数）
        if self.breaker.record_failure():
            raise HumanTakeover(f"circuit breaker opened: {exc}")
        return True
