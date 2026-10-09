# -*- coding: utf-8 -*-
"""离线重构契约：产物文件名 + 默认落盘路径自包含（不依赖 refactor/ 之外）。

refactor/ 必须是独立项目：默认输出不得指向外部 modules/，且必须收口在 refactor/ 内。
本用例不联网，静态校验：
  1) DEFAULT_OUT 不含 "modules" 且落在 refactor/ 项目根内。
（约定产物文件名由 test_smoke 联网落盘验证，不在此重复。）
"""
import importlib
import os

import pytest

# (源包, 约定产物文件名)
CONTRACT = [
    ("mof", "mof_laws.json"),
    ("pbc", "pbc_laws.json"),
    ("gov", "gov_laws.json"),
    ("nfra", "nfra_regulations.json"),
]

# 自包含导入：前缀随测试被收集的包名走（collectors.* 或 refactor.collectors.*）
_COLLECTORS_PKG = __name__.rsplit(".", 2)[0]


def _collector_mod(src):
    return importlib.import_module(f"{_COLLECTORS_PKG}.{src}.collector")


@pytest.mark.parametrize("src,fn", CONTRACT)
def test_default_out_self_contained(src, fn):
    mod = _collector_mod(src)
    default_out = getattr(mod, "DEFAULT_OUT", None)
    # nfra 等若未导出 DEFAULT_OUT 则跳过路径断言（其落盘目录由 --out-dir 控制）。
    if default_out is None:
        pytest.skip(f"{src} 未导出 DEFAULT_OUT，路径契约由调用方保证")
    # 不得指向外部 modules/
    assert "modules" not in default_out, (
        f"{src}.DEFAULT_OUT 不应指向外部 modules/：{default_out}"
    )
    # 必须收口在 refactor/ 项目根内（自包含，不依赖仓库其它目录）
    refactor_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(mod.__file__))))
    assert os.path.abspath(default_out).startswith(os.path.abspath(refactor_root)), (
        f"{src}.DEFAULT_OUT 必须落在 refactor/ 内，实际：{default_out}"
    )
