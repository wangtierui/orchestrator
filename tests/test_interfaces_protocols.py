# -*- coding: utf-8 -*-
"""tests/test_interfaces_protocols.py — P1-3/P1-5 接口层收口的回归断言

覆盖（全部只读，不写任何生产产物）：
  · I-1 `theme_api` 实装：协议形状、主题映射与唯一事实源一致、守卫语义、空壳归零
  · I-3/I-5 判据 D：`modules/**` 零「兄弟模块路径拼接」
  · I-4 `interfaces/protocols`：四个接口满足各自协议
  · I-7/X3 `gate_runtime_hygiene`：接口空壳为 0、生产脚本日志化达标
"""
from __future__ import annotations

import os

import pytest

import paths
from gates import gate_no_cross_module_import, gate_runtime_hygiene
from interfaces import protocols, theme_api

# ---- I-1: theme_api 实装 ----


def test_theme_map_matches_single_source():
    """`THEME_MAP_P0` 不再是硬编码副本（re-export 唯一事实源）。"""
    from interfaces import rfn_api
    assert theme_api.THEME_MAP_P0 == rfn_api.theme_map()
    assert len(theme_api.theme_map()) == 11


def test_theme_api_no_stub():
    """三个方法不得再是 `raise NotImplementedError` 空壳（注释/文档提及不算）。"""
    fp = os.path.join(paths.INTERFACES_DIR, "theme_api.py")
    text = open(fp, encoding="utf-8").read()
    assert "raise NotImplementedError" not in text


def test_set_theme_rejects_unknown_rfn():
    """未登记 RFN → LookupError（防静默建档）；此路径在任何写盘之前。"""
    with pytest.raises(LookupError):
        theme_api.set_theme("RFN-0000000000000000", "T1", "test")


def test_set_theme_rejects_unknown_theme():
    with pytest.raises(ValueError):
        theme_api.set_theme("RFN-0000000000000000", "不存在的主题", "test")


def test_align_record_readonly_shape():
    """`align_record` 只读计算（不落盘、不登记 RFN）；无数据时跳过。"""
    from interfaces.internal_policy_api import get_internal_policy_api
    recs = (get_internal_policy_api().load_index() or {}).get("records") or []
    if not recs:
        pytest.skip("内部制度索引未生成")
    out = theme_api.align_record(recs[0].get("ipn", ""), theme_hint="T4")
    assert set(out) >= {"ipn", "title", "primary", "secondary", "method"}
    assert isinstance(out["secondary"], list)
    assert "hint_match" in out


# ---- I-3 / I-5: 判据 D ----


def test_no_sibling_module_path_concat():
    """`modules/**` 不得出现「路径构造 + 兄弟模块名字面量」（判据 D）。"""
    passed, detail = gate_no_cross_module_import.run()
    assert passed, detail.get("new")
    assert detail["new_count"] == 0


def test_judgment_d_detects_violation():
    """判据 D 的负例：构造一段拼接文本必须被抓出（防判据退化为恒真）。"""
    lines = ['X = os.path.join(MOD, "regulatory_scrapers", "published")']
    hits = gate_no_cross_module_import._scan_sibling_paths(
        lines, own="base_publish")
    assert hits and hits[0][1].startswith("D")
    # 经 SSOT 访问器的写法不算违规
    ok = ['X = os.path.join(module_dir("regulatory_scrapers"), "published")']
    assert gate_no_cross_module_import._scan_sibling_paths(ok, own="base_publish") == []


# ---- I-4: protocols ----


def _provider_cases():
    """协议 ← provider 对象：有类的三处以 getter 取实例；relations_api 为模块级函数面。"""
    from interfaces import clean_index_api, internal_policy_api, relations_api, rfn_api
    return [
        (clean_index_api.get_clean_index_api(), protocols.CleanIndexProvider, "clean_index_api"),
        (rfn_api.get_rfn_api(), protocols.RfnProvider, "rfn_api"),
        (internal_policy_api.get_internal_policy_api(), protocols.InternalPolicyProvider,
         "internal_policy_api"),
        (relations_api, protocols.RelationsProvider, "relations_api"),
    ]


@pytest.mark.parametrize("obj,proto,name", _provider_cases())
def test_provider_satisfies_protocol(obj, proto, name):
    """provider 必须满足协议声明的形状（含 I-3 新增的路径访问器）。"""
    missing = [n for n in getattr(proto, "__protocol_attrs__", ()) if not hasattr(obj, n)]
    assert missing == [], f"{name} 缺协议方法 {missing}"


def test_assert_provider_helper_raises():
    """`assert_provider` 的负例：不满足协议的对象必须抛出并列出缺失方法。"""
    class _Bad:
        pass
    with pytest.raises(TypeError) as ei:
        protocols.assert_provider(_Bad(), protocols.RelationsProvider)
    assert "load" in str(ei.value)


def test_new_path_accessors_resolve():
    """I-3 新增的路径访问器必须指向真实存在的目录/文件。"""
    from interfaces import clause_index_api, clean_index_api, internal_policy_api, rfn_api
    assert os.path.isdir(clean_index_api.published_dir())
    assert os.path.isdir(clause_index_api.clauses_dir())
    assert os.path.isdir(internal_policy_api.data_dir())
    assert os.path.isdir(internal_policy_api.published_dir())
    assert os.path.exists(rfn_api.registry_paths()["attr_csv"])


# ---- I-7 / X3: runtime hygiene ----


def test_interface_stubs_strict():
    """接口空壳判据已转严格且当前为 0。"""
    passed, detail = gate_runtime_hygiene.run()
    assert detail["interface_stubs"]["strict"] is True
    assert detail["interface_stubs"]["total"] == 0
    assert passed, detail.get("problems")


def test_prod_scripts_logging_floor():
    """三个生产脚本的 LOG 使用达下限、print 未超上限（残余 print 仅载荷/表格）。"""
    _, detail = gate_runtime_hygiene.run()
    logs = detail["prod_logging"]
    assert len(logs) == 3
    for rel, st in logs.items():
        assert st["log"] >= st["log_min"], rel
        assert st["print"] <= st["print_max"], rel


def test_logging_facility_available():
    """X3：统一日志设施可从 `std_lib.common_lib.logging` 直接取用。"""
    from std_lib.common_lib import logging as clog
    assert callable(clog.get_logger) and callable(clog.setup_cli_logging)
    assert clog.setup_logging is not None and clog.LogContext is not None
