# -*- coding: utf-8 -*-
"""refactor/collectors 包内测试目录（离线为主，联网冒烟标记为 data）。

说明：本目录并非项目主测试树（项目统一在仓库根 tests/），而是随重构 collectors
就近落放的回归用例。已加入 pyproject.toml 的 testpaths，故 `pytest` 可自动发现；
纯离线用例始终跑，联网冒烟用例带 @pytest.mark.data，无网/无数据环境用
`pytest -m "not data"` 排除（与项目 tests/ 口径一致）。
"""
