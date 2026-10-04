# -*- coding: utf-8 -*-
"""适配器注册表：包名 → 适配器类。新 App 接入时在此登记。"""
from .example_app import ExampleAppAdapter
from .np_manager import NpManagerAdapter
from .cherry_tale import CherryTaleAdapter
from .uncompress import UncompressAdapter

ADAPTERS = {
    ExampleAppAdapter.package: ExampleAppAdapter,
    NpManagerAdapter.package: NpManagerAdapter,
    CherryTaleAdapter.package: CherryTaleAdapter,
    UncompressAdapter.package: UncompressAdapter,
}
