# -*- coding: utf-8 -*-
"""collectors 阶段测试目录（离线为主，联网冒烟标记为 data）。

测试按步骤就近归属：本目录只放采集链路的用例，跨模块契约测试见 tests/contract/。
带 @pytest.mark.data 的联网/数据冒烟用例由 `pytest -m "not data"` 默认排除。

保留本 __init__.py：test_collectors_imports.py 依赖 __name__ 推导包前缀。
"""
