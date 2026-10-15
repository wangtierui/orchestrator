# -*- coding: utf-8 -*-
"""tests/test_collectors_dual_form.py — collectors 子包**双形态可导入**契约（W4 L1）。

背景（2026-10-15 L1 重组）：`collectors/` 由单层拍平改为「按源建子包」。每个采集脚本
既要能被**包形态**加载（`modules.regulatory_scrapers.collectors.<src>.<role>`——测试、
importlib、clean_index 等走这条），又要能以**脚本形态**独立启动
（`python collectors/<src>/<role>.py`——编排器以子进程调用的真实方式）。二者靠
「相对导入 + 裸名兜底」双形态 import 支撑，任一侧失配都会在生产链上炸。

本测试固化两个维度（此前**双侧都无覆盖**）：
  1. 包形态：逐模块 import + 取一个已知符号（re-export 名单里的**死符号**即在此暴露
     —— 如 `sha256_of` 于 B2 删除后残留于名单，两形态皆 ImportError）；
  2. 脚本形态：逐入口 `--help` 起（自含引导的**上溯层数**错位只在此暴露——迁子包后
     ".."×3 只到 modules/，`import paths` 即 ModuleNotFoundError）。

另附路径纪律断言：sources.yaml 的 collector 字段 ↔ 实际文件 ↔ 编排器 argv 三方一致
（L1 移了 25 个文件，仅靠测试无法覆盖 yaml/编排器的接线漂移）。
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COLL = os.path.join(ROOT, "modules", "regulatory_scrapers", "collectors")
PY = sys.executable

pytestmark = pytest.mark.contract

# (全限定模块名, 必须存在的符号)
PKG_PROBES = [
    ("modules.regulatory_scrapers.collectors.gov.collector", "build_config"),
    ("modules.regulatory_scrapers.collectors.gov.parse", "SOURCES"),
    ("modules.regulatory_scrapers.collectors.gov.zhengceku", "ZhengcekuScraper"),
    ("modules.regulatory_scrapers.collectors.gov.attachments", "fetch_gov_attachments"),
    ("modules.regulatory_scrapers.collectors.mof.collector", "fetch_list_page"),
    ("modules.regulatory_scrapers.collectors.mof.attachments", "normalize_date"),
    ("modules.regulatory_scrapers.collectors.nfra.collector", "main"),
    ("modules.regulatory_scrapers.collectors.nfra.fetch_lists", "main"),
    ("modules.regulatory_scrapers.collectors.nfra.fill_details", "main"),
    ("modules.regulatory_scrapers.collectors.nfra.fetch_attachments", "ocr_pdf"),
    ("modules.regulatory_scrapers.collectors.nfra.attachments_extract", "extract_xlsx"),
    ("modules.regulatory_scrapers.collectors.nfra.weekly", "main"),
    ("modules.regulatory_scrapers.collectors.pbc.collector", "extract_pdf_text"),
    ("modules.regulatory_scrapers.collectors.pbc.parse", "strip_tags"),
    ("modules.regulatory_scrapers.collectors.pbc.recover", "needs_extraction"),
    ("modules.regulatory_scrapers.collectors.supp.parser", "extract_document_text"),
    ("modules.regulatory_scrapers.collectors.supp.sanitize", "sanitize_csv"),
    ("modules.regulatory_scrapers.collectors.supp.normalize", "_flatten_text"),
    ("modules.regulatory_scrapers.collectors.supp.ocr", "ocr_scanned"),
]

# 入口脚本（须能独立 `--help` 起：编排器以子进程调用）
SCRIPT_ENTRIES = [
    "gov/collector.py", "gov/zhengceku.py",
    "mof/collector.py",
    "nfra/collector.py", "nfra/fetch_lists.py", "nfra/fill_details.py",
    "nfra/weekly.py", "nfra/fetch_attachments.py",
    "pbc/collector.py",
    "supp/ingest.py", "supp/ingest_local_dir.py", "supp/ingest_batch.py",
    "supp/parser.py", "supp/ocr.py", "supp/sanitize.py",
]


@pytest.mark.parametrize("mod,symbol", PKG_PROBES, ids=[m for m, _ in PKG_PROBES])
def test_package_form_importable(mod: str, symbol: str) -> None:
    """包形态：模块可导入且 re-export 名单中的符号真实存在。"""
    import importlib

    m = importlib.import_module(mod)
    assert hasattr(m, symbol), f"{mod} 缺少符号 {symbol}（re-export 名单与源侧不同步）"


@pytest.mark.parametrize("rel", SCRIPT_ENTRIES)
def test_script_form_startable(rel: str) -> None:
    """脚本形态：`python <file> --help` 可起（自含引导的上溯层数正确、兄弟裸名可解析）。"""
    p = os.path.join(COLL, rel)
    assert os.path.exists(p), f"入口脚本缺失 {rel}"
    r = subprocess.run([PY, p, "--help"], cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=180)
    err = r.stderr or ""
    for bad in ("ModuleNotFoundError", "ImportError", "AttributeError"):
        assert bad not in err, f"{rel} 脚本形态启动失败（{bad}）：\n{err[-800:]}"


def test_sources_yaml_collector_paths_exist() -> None:
    """sources.yaml 的 collector 字段（两级点号）↔ 实际文件 一致。"""
    from config.loader import active_source_ids, collector_module, load_sources

    srcs = load_sources()
    for sid in sorted(set(active_source_ids(refresh=False))):
        mod = collector_module(sid)
        assert mod, f"{sid} 缺 collector 字段"
        assert "." in mod, f"{sid}: collector 应为两级点号路径（gov.collector），实为 {mod!r}"
        rel = os.path.join(*mod.split(".")) + ".py"
        assert os.path.exists(os.path.join(COLL, rel)), f"{sid}: {mod} 对应文件不存在（{rel}）"
        assert sid in srcs


def test_orchestrator_argv_points_to_existing_scripts() -> None:
    """编排器 COLLECT_CMD 的 argv 路径全部真实存在（接线漂移即 FAIL）。"""
    text = open(os.path.join(ROOT, "tools", "run_production_refresh.py"),
                encoding="utf-8", errors="replace").read()
    import re

    pat = re.compile(r'os\.path\.join\(COLLECTORS,((?:\s*"[^"]+",?)+)\)')
    seen = 0
    for m in pat.finditer(text):
        segs = re.findall(r'"([^"]+)"', m.group(1))
        rel = os.path.join(*segs)
        seen += 1
        assert os.path.exists(os.path.join(COLL, rel)), f"编排器 argv 指向不存在的脚本 {rel}"
    assert seen >= 8, f"编排器 COLLECT 形态引用仅 {seen} 处（预期 ≥8，疑解析失效）"
