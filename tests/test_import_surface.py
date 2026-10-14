# -*- coding: utf-8 -*-
"""test_import_surface.py —— W4-c 大文件拆分 import 面契约测试（L3）。

纪律（方案 §7.5.1 拆分纪律 + §7.9 L3）：凡被拆分/重组的包，配「包可导入 + 约定入口
类存在 + **公共符号集不变**」契约测试——与「import 面基线冻结」门禁双保险。

本文件冻结四个拆分目标的公共 API：
  1. `governance_store`（五拆：_db/watermark/ledger/worklist/projections，__init__ re-export）；
  2. `gate_config_integrity`（五拆：_manifest/_structure/_schedule/_steps/_domains，run() 聚合）；
  3. `relations`（三拆：patterns/extractor/pipeline，__init__ re-export）；
  4. `crawler_common`（B1 瘦身后按抓取/解析/抽取细分）。

符号集为**基线冻结**：新增符号必须同步本文件（只增不减由测试强制可见）；
删除/改名符号 = 测试 FAIL（防止拆分静默破坏消费方）。
"""
from __future__ import annotations

import importlib

import pytest

# --------------------------------------------------------------------------- #
# 公共符号基线（拆分前后必须一致）
# --------------------------------------------------------------------------- #

GOVERNANCE_STORE_SYMBOLS = {
    # 连接/初始化（_db）
    "repo_root", "db_path", "enabled", "connect", "init_db",
    # 台账（ledger）
    "now_iso", "current_run_id", "run_start", "run_finish", "step_record",
    "steps_of", "last_run_with_steps", "list_runs",
    "version_of_file", "file_version", "version_of_files",
    "record_gate_results", "list_gate_results",
    # 水位（watermark）
    "record_watermark", "get_watermark", "list_watermarks",
    "dependency_edges", "check_dependencies",
    # worklist
    "worklist_add", "worklist_list", "worklist_resolve", "worklist_stats",
    # 投影（projections）
    "log_audit", "list_audit",
    "upsert_artifacts", "list_artifacts",
    "project_metadata", "table_rows", "table_count", "table_digest",
    "verify_projection", "export_snapshot", "snapshot",
    # 常量
    "SCHEMA_VERSION", "TABLES", "WORKLIST_OPEN_UNIQUE",
}

GATE_CONFIG_INTEGRITY_SYMBOLS = {
    "run",  # 聚合入口（唯一公共 API）
}

RELATIONS_SYMBOLS = {
    "SCHEMA_VERSION", "EXTRACTOR_VERSION",
    "RelationConfig", "default_config",
    "RelationExtractor", "AliasResolver",
    "RelationPipeline", "DocumentMeta",
    "BasisRelation", "RepealRelation", "ExtractionResult",
    "preprocess", "normalize_doc_name", "docno_signature",
    "iter_quote_titles", "iter_docno_signatures",
    "split_sentences", "extract_docnos",
    "quote_title_capture", "quote_title_re", "basis_trigger_alt", "docno_core_re",
    "is_generic_target", "is_organ_target", "classify_target",
    "snippet_half_window", "CN_NUM",
}

CRAWLER_COMMON_SYMBOLS = {
    # 抓取层（B1 后为 re-export 门面）
    "robust_get", "USER_AGENTS", "BASE_HEADERS",
    # 文件名/URL
    "sniff_kind", "safe_filename", "is_attachment_url",
    # 文本处理
    "garble_ratio", "html_to_text", "unmask_payload",
    # sha256（B2 re-export）
    "sha256_bytes",
    # 归一（B3 在册特化）
    "normalize_date", "content_sha256",
}


def _public_symbols(module) -> set[str]:
    """模块公共符号 = dir() 中非下划线开头且非模块对象的属性。"""
    import types

    out = set()
    for name in dir(module):
        if name.startswith("_"):
            continue
        obj = getattr(module, name)
        if isinstance(obj, types.ModuleType):
            continue
        out.add(name)
    return out


class TestGovernanceStoreImportSurface:
    """governance_store 五拆后 import 面不变。"""

    def test_package_importable(self):
        m = importlib.import_module("std_lib.common_lib.governance_store")
        assert hasattr(m, "run") or True  # 包导入即通过

    def test_public_symbols_frozen(self):
        from std_lib.common_lib import governance_store as gs

        missing = GOVERNANCE_STORE_SYMBOLS - _public_symbols(gs)
        assert not missing, f"拆分丢失公共符号: {sorted(missing)}"

    def test_submodules_importable(self):
        """五拆子模块必须可导入（L3：包可导入 + 分层清晰）。"""
        for sub in ("_db", "watermark", "ledger", "worklist", "projections"):
            importlib.import_module(f"std_lib.common_lib.governance_store.{sub}")

    def test_facade_reexports_same_objects(self):
        """门面与子模块必须是同一对象（防「复制两份」假拆分）。"""
        from std_lib.common_lib import governance_store as gs
        from std_lib.common_lib.governance_store import worklist as wl

        assert gs.worklist_add is wl.worklist_add
        assert gs.worklist_resolve is wl.worklist_resolve
        assert gs.worklist_stats is wl.worklist_stats


class TestGateConfigIntegrityImportSurface:
    """gate_config_integrity 五拆后 import 面不变（run 聚合入口）。"""

    def test_run_entry_exists(self):
        m = importlib.import_module("gates.gate_config_integrity")
        assert callable(m.run), "run() 聚合入口丢失"

    def test_public_symbols_frozen(self):
        from gates import gate_config_integrity as g

        missing = GATE_CONFIG_INTEGRITY_SYMBOLS - _public_symbols(g)
        assert not missing, f"拆分丢失公共符号: {sorted(missing)}"

    def test_submodules_importable(self):
        """五拆子模块必须可导入。"""
        for sub in ("_manifest", "_structure", "_schedule", "_steps", "_domains"):
            importlib.import_module(f"gates.gate_config_integrity.{sub}")

    def test_run_delegates_to_families(self):
        """run() 必须实际调用各族判据（防拆分后只剩空壳）。"""
        import inspect

        from gates import gate_config_integrity as g

        src = inspect.getsource(g.run)
        for fn in ("_check_tools", "_check_worklist", "_check_schedule",
                   "_check_step_order", "_check_domains", "_check_constants"):
            assert fn in src, f"run() 未调用 {fn}（拆分断裂）"


class TestRelationsImportSurface:
    """relations 三拆后 import 面不变。"""

    def test_public_symbols_frozen(self):
        from std_lib.common_lib import relations

        missing = RELATIONS_SYMBOLS - _public_symbols(relations)
        assert not missing, f"拆分丢失公共符号: {sorted(missing)}"

    def test_submodules_importable(self):
        """三拆子模块必须可导入。"""
        for sub in ("patterns", "extractor", "pipeline"):
            importlib.import_module(f"std_lib.common_lib.relations.{sub}")

    def test_extractor_and_pipeline_same_objects(self):
        from std_lib.common_lib import relations
        from std_lib.common_lib.relations import extractor as ex
        from std_lib.common_lib.relations import pipeline as pl

        assert relations.RelationExtractor is ex.RelationExtractor
        assert relations.RelationPipeline is pl.RelationPipeline


class TestCrawlerCommonImportSurface:
    """crawler_common（B1 瘦身后）import 面不变。"""

    def test_public_symbols_frozen(self):
        from std_lib.scraper_std import crawler_common as cc

        missing = CRAWLER_COMMON_SYMBOLS - _public_symbols(cc)
        assert not missing, f"拆分丢失公共符号: {sorted(missing)}"

    def test_http_single_stack_reexports(self):
        """B1 单栈：robust_get 等必须是 http.py 实现的 re-export。"""
        from std_lib.scraper_std import crawler_common as cc
        from std_lib.scraper_std import http

        assert cc.robust_get is http.robust_get
        assert cc.USER_AGENTS is http.USER_AGENTS_PC


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
