# -*- coding: utf-8 -*-
"""离线导入契约：四个源 collector 包必须可导入，且暴露约定入口类。

不联网、不落盘，仅验证"搬过去的包能被 Python 正确识别"——对应重构时修掉的
_GUIDE_ROOT 这类导入期 bug 的回归防护。
"""
import importlib

import pytest

# 自包含导入前缀：随测试被收集的包名走（collectors.* 或 refactor.collectors.*）
_COLLECTORS_PKG = ".".join(__name__.split(".")[:-2])  # 去掉 .tests.test_imports

# (源包, 约定入口类)
COLLECTORS = [
    ("mof", "MofCollector"),
    ("pbc", "PbcCollector"),
    ("gov", "GovCollector"),
    ("nfra", "NfraCollector"),
]


@pytest.mark.parametrize("src,cls", COLLECTORS)
def test_collector_package_importable(src, cls):
    mod = importlib.import_module(f"{_COLLECTORS_PKG}.{src}")
    assert hasattr(mod, cls), f"{src} 包缺少约定入口类 {cls}"
