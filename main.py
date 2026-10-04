# -*- coding: utf-8 -*-
"""框架入口：加载编排 → 环境自检门 → 恢复断点 → 跑流水线。"""
import argparse
import logging
import logging.handlers
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core.checkpoint import Checkpoint
from core.device import EmulatorDevice
from core.gate import Gate
from core.pipeline import Pipeline
from core.recovery import CircuitBreaker, Recovery, Watchdog
from adapters import ADAPTERS

# 日志轮转（W-06）：文件 handler 10MB×3，保留最近 3 次运行；控制台同步输出
os.makedirs("state", exist_ok=True)
_fmt = logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")
_fh = logging.handlers.RotatingFileHandler(
    "state/app.log", maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8")
_fh.setFormatter(_fmt)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logging.getLogger().addHandler(_fh)
log = logging.getLogger("autofw")

# 打印节点专用日志：state/prints.log（前端「打印输出」面板独立展示，避免混入运行日志）
_prints_fh = logging.handlers.RotatingFileHandler(
    "state/prints.log", maxBytes=2 * 1024 * 1024, backupCount=2, encoding="utf-8")
_prints_fh.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
_prints_logger = logging.getLogger("autofw.prints")
_prints_logger.setLevel(logging.INFO)
_prints_logger.addHandler(_prints_fh)
_prints_logger.propagate = False  # 不向上冒泡到 root，避免重复写入 app.log


def main():
    ap = argparse.ArgumentParser(description="通用多 App 自动化框架")
    ap.add_argument("--config", default="pipeline.json", help="编排配置路径")
    ap.add_argument("--mode", default=None,
                    help="执行模式：flow=图形化工作流（B2）/ adapter=旧适配器路由（默认按 pipeline.json 的 mode 字段）")
    ap.add_argument("--no-gate", action="store_true", help="跳过环境自检门（调试用）")
    ap.add_argument("--no-resume", action="store_true", help="忽略断点从头跑")
    args = ap.parse_args()

    # 1. 环境自检门（E19/E20/E21）
    if not args.no_gate:
        ok, issues = Gate().check()
        if not ok:
            for i in issues:
                log.error("环境自检失败: %s", i)
            log.error("流水线拒绝启动（修复后重试）")
            sys.exit(2)

    # 2. 组装运行时（配置面接线：recovery/checkpoint 参数真正从配置读取）
    import json as _json
    _cfg = _json.load(open(args.config, encoding="utf-8"))
    _rec = _cfg.get("recovery", {})
    _ck = _cfg.get("checkpoint", {})
    device = EmulatorDevice()
    checkpoint = Checkpoint()
    watchdog = Watchdog(timeout=float(_rec.get("watchdog_timeout", 60)),
                        timeout_long=float(_rec.get("watchdog_timeout_long", 180)),
                        click_limit=int(_rec.get("click_circuit_breaker", 12)))
    breaker = CircuitBreaker(max_failures=int(_rec.get("circuit_breaker_failures", 5)))
    recovery = Recovery(device, breaker, watchdog)

    # 3. 恢复断点（--no-resume 或配置 resume_on_start=false 时从头跑）
    resume_on_start = bool(_ck.get("resume_on_start", True))
    if args.no_resume or not resume_on_start:
        checkpoint._data["completed"] = []  # 从头跑（不落盘旧断点）
    elif checkpoint.load():
        log.info("检测到断点: app=%s task=%s completed=%d",
                 checkpoint.get("app"), checkpoint.get("task_id"),
                 len(checkpoint.get("completed", [])))

    # 4. 跑流水线（B1 修复：--no-resume 真正生效，resume 开关传入 Pipeline）
    # 执行模式：命令行 --mode 优先；否则取 pipeline.json 顶层 mode 字段（默认 adapter）。
    import json as _json
    _cfg_mode = _json.load(open(args.config, encoding="utf-8")).get("mode", "adapter")
    mode = (args.mode or _cfg_mode or "adapter").lower()
    if mode == "flow":
        from core.flow_pipeline import FlowPipeline
        log.info("执行模式=flow（图形化工作流，B2 路线）")
        flow_pipeline = FlowPipeline(args.config, device=device, recovery=recovery,
                                     resume=not args.no_resume)
        flow_pipeline.run()
    else:
        log.info("执行模式=adapter（旧适配器路由）")
        pipeline = Pipeline(args.config, ADAPTERS, device, checkpoint, recovery,
                            resume=not args.no_resume)
        pipeline.run()
    log.info("流水线完成")


if __name__ == "__main__":
    main()
