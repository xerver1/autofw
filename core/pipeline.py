# -*- coding: utf-8 -*-
"""流水线调度器：FIFO 队列 + 单线程 + 控制流异常（三框架共识）。
职责：加载编排 → 环境自检 → 断点恢复 → 逐 App 执行（切换协议）→ 持久化进度。"""
import json
import logging
import os
import time

import cv2

from .adapter import AppAdapter
from .checkpoint import Checkpoint
from .device import EmulatorDevice
from .failures import add_failure
from .exceptions import (AppStuckError, EmulatorDownError, HumanTakeover,
                         RetryableError, TaskDisabled, TaskEnd)
from .recovery import Recovery
from .task import TaskContext

log = logging.getLogger("autofw")


class Pipeline:
    def __init__(self, config_path: str, adapters: dict[str, AppAdapter],
                 device: EmulatorDevice, checkpoint: Checkpoint, recovery: Recovery,
                 resume: bool = True):
        self.config = json.load(open(config_path, encoding="utf-8"))
        self.adapters = adapters          # 包名 → 适配器实例
        self.device = device
        self.checkpoint = checkpoint
        self.recovery = recovery
        self.resume = resume
        # 配置面接线（静态审查发现：recovery.max_retry 此前为死配置，此处真正读取）
        self.max_retry = int(self.config.get("recovery", {}).get("max_retry", 5))
        self.entries = [e for e in self.config["pipeline"] if e.get("enabled", True)]

    # ---- 主入口 ----
    def run(self):
        if self.resume:
            self.checkpoint.load()
        else:
            log.info("resume disabled, starting from scratch")
        start_idx = self.checkpoint.get("pipeline_index", 0)
        for idx in range(start_idx, len(self.entries)):
            entry = self.entries[idx]
            log.info("[流水线] (%d/%d) 开始执行 App：%s", idx + 1, len(self.entries), entry["app"])
            self.checkpoint.set("pipeline_index", idx)
            self.checkpoint.set("app", entry["app"])
            self.checkpoint.save(f"pipeline start: {entry['app']}")
            ok = self._run_app(entry)
            if not ok:
                # 三选"暂停"/HumanTakeover → 停止流水线，断点停在当前 App（重启后从它续）
                log.warning("pipeline stopped at %s (resume will restart from here)", entry["app"])
                break
        else:
            self.checkpoint.save("pipeline complete")

    # ---- 单 App 执行（切换协议核心）----
    def _run_app(self, entry) -> bool:
        adapter = self.adapters[entry["app"]]()   # 实例化适配器（ADAPTERS 注册的是类）
        ctx = TaskContext(self.device, self.checkpoint, adapter)
        try:
            # 模板加载（A3 修复：模板采集后必须接线，否则 find() KeyError）
            adapter.load_templates(ctx)
            # 切换协议：启动 app → 等待就绪（特征画面判定，禁止固定 sleep，E9）
            if not self.device.ping():
                self.device.reconnect()
            self.device.start_app(adapter.package, adapter.activity)
            log.info("[流水线] 已启动 App：%s (%s)，等待进入主界面…", adapter.display_name, adapter.package)
            self._wait_ready(adapter, ctx, timeout=180)
            adapter.login_recovery(ctx)
            # 执行该 App 的任务列表（W-03 修复：启动前校验 tasks 一致性，R1-6）
            tasks = {t.task_id: t for t in adapter.build_tasks()}
            configured = set(entry["tasks"])
            available = set(tasks.keys())
            missing = configured - available
            extra = available - configured
            if missing:
                raise HumanTakeover(
                    f"pipeline.json tasks 与 build_tasks() 不一致，缺失任务: {sorted(missing)}")
            if extra:
                log.warning("适配器提供了但未配置的任务（不执行）: %s", sorted(extra))
            for task_id in entry["tasks"]:
                task = tasks[task_id]
                if self.checkpoint.is_completed(f"{adapter.package}:{task_id}"):
                    log.info("skip completed task %s (checkpoint)", task_id)
                    continue
                log.info("[流水线] 执行任务：%s：%s", adapter.display_name, task_id)
                self._run_task_with_retry(adapter, ctx, task)
                self.checkpoint.mark_completed(f"{adapter.package}:{task_id}")
                log.info("[流水线] 任务完成：%s：%s", adapter.display_name, task_id)
                self.checkpoint.save(f"task done: {task_id}")
        except HumanTakeover as e:
            log.error("human takeover: %s", e)
            # 失败台账：记录细分失败任务（待手动处理模块数据源）
            try:
                add_failure(adapter.package, entry.get("tasks", ["?"]),
                            str(e), pipeline="default")
            except Exception as fe:
                log.warning("failure record failed: %s", fe)
            self._save_evidence(ctx)
            return False
        finally:
            # 切换协议：关 A（close_app = force-stop 主 + MuMuManager 兜底 + app info 确认）
            # M3 实测修复（E21）：adb 断连时 close_app 会抛异常，不得让 finally 崩溃——
            # 先尝试重连再关闭，仍失败则记录警告（关闭失败不阻断流水线状态保存）
            try:
                self.device.close_app(adapter.package)
            except Exception as e:
                log.warning("close_app failed (reconnect first): %s", e)
                try:
                    self.device.reconnect()
                    self.device.close_app(adapter.package)
                except Exception as e2:
                    log.warning("close_app still failed after reconnect: %s", e2)
            self.checkpoint.save(f"app closed: {adapter.package}")
        return True

    # ---- 等待 app 就绪：轮询截图找主界面特征 ----
    def _wait_ready(self, adapter, ctx, timeout: float = 180.0):
        deadline = time.time() + timeout
        pull_count = 0
        while time.time() < deadline:
            try:
                # 黑屏检测（E20 变体，M1 实测发现：force-stop 后屏幕无焦点黑屏，需 WAKEUP）
                frame = ctx.frame()
                if frame is not None and float(frame.mean()) < 5.0:
                    log.warning("black frame detected, sending WAKEUP")
                    ctx.device.key("KEYCODE_WAKEUP")
                    ctx.invalidate()
                    time.sleep(3)
                    continue
                if ctx.find("main_scene") is not None:
                    log.info("[流水线] App 就绪（主界面已识别）：%s (%s)", adapter.display_name, adapter.package)
                    return
            except KeyError as e:
                log.warning("main_scene template missing: %s (collect templates first)", e)
                raise HumanTakeover("main_scene template not loaded")
            except Exception as e:
                log.warning("wait_ready frame error: %s", e)
            # 前台拉回（E1/E16，M2 实测发现：保活模式下其他 App 可能抢占前台，
            # wait_ready 需周期性 am start 拉回目标 App，否则 main_scene 永不命中）
            pull_count += 1
            if pull_count % 4 == 0:
                log.warning("main_scene not found for a while, pulling back foreground")
                try:
                    adapter.ensure_foreground(ctx)
                    ctx.invalidate()
                except Exception as e:
                    log.warning("pull-back failed: %s", e)
            time.sleep(3)
        # 超时取证（R15）：保存最后一帧，便于排查卡在哪个画面导致 main_scene 匹配不到
        try:
            frame = ctx.frame()
            if frame is not None:
                p = os.path.join("state", "main_scene_timeout_frame.png")
                cv2.imwrite(p, frame)
                log.warning("[流水线] 就绪超时，已保存当前画面取证图：%s", p)
        except Exception as e:  # noqa: BLE001
            log.warning("timeout frame capture failed: %s", e)
        raise AppStuckError(f"app not ready in {timeout}s: {adapter.package}")

    # ---- 子任务执行：重试有上限 + 容灾兜底 + 恢复三态决策（R7）----
    def _run_task_with_retry(self, adapter, ctx, task):
        max_retry = self.max_retry
        for attempt in range(max_retry):
            try:
                # 断点恢复前状态比对（幂等，E12）：已确认完成 → 跳过；无法确认 → 暂停三态
                decision = self._resume_decision(adapter, ctx, task)
                if decision == "skip":
                    log.info("resume decision=skip: %s", task.task_id)
                    self.checkpoint.mark_completed(f"{adapter.package}:{task.task_id}")
                    return
                if decision == "pause":
                    raise HumanTakeover(f"resume paused by user: {task.task_id}")
                # decision == "continue"：前台双确认（E1）后执行
                self.recovery.assert_foreground(adapter, ctx)
                task.execute(ctx)
                self.recovery.breaker.record_success()
                return
            except (RetryableError, RuntimeError) as e:
                # adb 类失败（RuntimeError）→ 接入容灾降级（重连序列，B3 修复）
                if isinstance(e, RuntimeError):
                    log.warning("runtime error, trying recovery: %s", e)
                    if self.recovery.handle_failure(e, adapter):
                        time.sleep(5)
                        continue
                log.warning("retry %d/%d for %s: %s", attempt + 1, max_retry, task.task_id, e)
                time.sleep(3)
            except (AppStuckError, EmulatorDownError) as e:
                if self.recovery.handle_failure(e, adapter):
                    time.sleep(5)
                else:
                    raise
        raise HumanTakeover(f"task failed after {max_retry} retries: {task.task_id}")
    def _resume_decision(self, adapter, ctx, task):
        """续跑决策（R7）：verify_state 比对游戏内真实状态。
        - 已确认完成（checkpoint 或 verify_state=False）→ skip
        - 确认未完成 → continue
        - 无法确认（verify_state 返回 None / 匹配不到）→ 暂停，三选项：
          1=手动接管后继续  2=跳过该任务  3=暂停（等用户处理）
        匹配不到禁止自动跳过（防重复消耗/损失，E12）。"""
        key = f"{adapter.package}:{task.task_id}"
        if self.checkpoint.is_completed(key):
            return "skip"
        verdict = adapter.verify_state(ctx, self.checkpoint)
        if verdict is True:
            return "continue"
        if verdict is False:
            return "skip"
        # verdict is None：无法确认 → 暂停三态
        self._save_evidence(ctx)
        log.warning("resume: cannot verify state of %s (evidence saved)", task.task_id)
        if self.config.get("checkpoint", {}).get("resume_auto", "ask") == "skip":
            log.warning("resume_auto=skip configured, skipping %s", task.task_id)
            return "skip"
        if self.config.get("checkpoint", {}).get("resume_auto") == "continue":
            return "continue"
        choice = input(f"[断点] 无法确认任务 [{task.task_id}] 的游戏内状态\n"
                       f"  1=手动接管后继续  2=跳过该任务  3=暂停退出\n"
                       f"  请选择 (1/2/3): ").strip()
        return {"1": "continue", "2": "skip", "3": "pause"}.get(choice, "pause")

    def _save_evidence(self, ctx):
        """失败证据：错误帧截图 + 断点（可追溯，三框架共识）。"""
        try:
            import cv2
            img = ctx.device.screencap()
            cv2.imwrite("state/error_frame.png", img)
            log.info("error evidence saved: state/error_frame.png")
        except Exception:
            pass
