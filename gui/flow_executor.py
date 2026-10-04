# -*- coding: utf-8 -*-
"""flow_executor.py - 兼容垫片：真实设备执行器已迁移到 core/flow_executor.py。

保留本文件仅为向后兼容（server.py / 旧调用方继续从 gui.flow_executor 导入）。
新增逻辑请改 core/flow_executor.py，本文件只做再导出。
"""
import sys
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

from core.flow_executor import (  # noqa: E402,F401
    FlowDeviceExecutor,
    MATCH_THRESHOLD,
    _get_ocr,
)

__all__ = ["FlowDeviceExecutor", "MATCH_THRESHOLD", "_get_ocr"]
