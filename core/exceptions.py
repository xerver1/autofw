# -*- coding: utf-8 -*-
"""异常分级：异常即控制流（借鉴 ALAS 异常分类学 + MAA 转移分支）。
正常结束/用户停止走正常路径；其余按可恢复性分级，统一由 Pipeline 分发。"""


class TaskEnd(Exception):
    """正常结束：当前任务完成，继续下一个。"""


class TaskDisabled(Exception):
    """用户停止：记录断点后退出。"""


class RetryableError(Exception):
    """可重试：识别失败/瞬时抖动，重试有上限（max_retry）。"""


class AppStuckError(Exception):
    """重启可治：游戏卡死/黑屏/掉线无响应，重启 app 继续。"""


class EmulatorDownError(Exception):
    """环境级：模拟器/ADB 不可用，重启模拟器。"""


class HumanTakeover(Exception):
    """不可治：需人工介入（如顶号、未知界面、外部系统非幂等）。"""
