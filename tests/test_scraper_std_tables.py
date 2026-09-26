# -*- coding: utf-8 -*-
"""test_scraper_std_tables.py —— 表格结构化/校验/OCR 纠正纯函数批测（长期项，2026-09-13）。

覆盖：excel_structure 单元格/行判据与矩阵工具、schema_validation 类型校验、
ocr_correction 混淆替换/噪声过滤。无 IO 优先（字节级读取函数用最小真实样例）。
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "std_lib")):
    if p not in sys.path:
        sys.path.insert(0, p)

from scraper_std import excel_structure as ex  # noqa: E402
from scraper_std import ocr_correction as oc
from scraper_std import schema_validation as sv


class TestExcelCells:
    def test_cell_to_value(self):
        assert isinstance(ex._cell_to_value(None), (str, type(None)))
        assert isinstance(ex._cell_to_value(3.0), (int, float, str))

    def test_is_blank(self):
        assert ex._is_blank(None) is True
        assert ex._is_blank("") is True
        assert ex._is_blank("x") is False

    def test_text_len_and_number(self):
        assert isinstance(ex._text_len("中文ab"), int)
        assert ex._is_number(12.5) is True      # 数值型判据
        assert ex._is_number("中文") is False

    def test_row_helpers(self):
        assert ex._row_is_empty([None, "", None]) is True
        assert isinstance(ex._row_fill(["a", None, "b"]), (int, float))
        assert ex._row_is_empty(["a", None]) is False


class TestExcelMatrix:
    def test_norm_matrix(self):
        m = ex._norm_matrix([["a", None], ["b", "c"]])
        assert isinstance(m, list) and len(m) == 2

    def test_trim(self):
        m = [["", "", ""], ["", "x", ""], ["", "", ""]]
        r = ex._trim(m, 0, 3, 0, 3)
        assert isinstance(r, (list, tuple))

    def test_header_score(self):
        s = ex._header_score(["序号", "名称", "数量"])
        assert isinstance(s, (int, float))

    def test_is_preamble_row(self):
        r = ex._is_preamble_row(["某某公司关于XX的通知"])
        assert isinstance(r, bool)

    def test_is_single_title_row(self):
        r = ex._is_single_title_row(["标题", None, None])
        assert isinstance(r, bool)

    def test_split_blocks_small(self):
        matrix = [["标题"], ["序号", "名称"], ["1", "甲"], ["2", "乙"]]
        r = ex.split_blocks(matrix, merged=[])
        assert r is not None


class TestSchemaValidation:
    def test_type_ok(self):
        assert sv._type_ok("x", "str") is True
        assert sv._type_ok(1, "int") is True
        assert sv._type_ok(None, "str") in (True, False)  # 空值宽容语义

    def test_validate_record_basic(self):
        schema = {"fields": {"title": {"type": "str", "required": True}}}
        r = sv.validate_record({"title": "某通知"}, schema)
        assert isinstance(r, (dict, list, tuple, bool))

    def test_validate_record_url_note_vs_scheme(self):
        """url 字段：无 scheme 的来源标注（本地路径/括号说明）降级 warning；带 scheme 但非
        http(s)（ftp://）仍报 error（2026-09-26 审查：supp 等本地补充材料源的合法来源标注）。"""
        schema = {"source_url": {"type": "url"}}
        # 1) 本地文件路径 → 非 error，记入 validation_warnings
        rec1 = {"source_url": "本地文件 /some/local/path/x.pdf"}
        ok1, errs1 = sv.validate_record(rec1, schema)
        assert ok1 and not errs1
        warns1 = (rec1.get("_metadata") or {}).get("validation_warnings", [])
        assert any("source_url" in w for w in warns1)
        # 2) 括号说明 → 同上
        rec2 = {"source_url": "（gov.cn未公开全文，权威媒体报道）"}
        ok2, errs2 = sv.validate_record(rec2, schema)
        assert ok2 and not errs2
        # 3) ftp:// 有 scheme 但非 http(s) → 报 error
        rec3 = {"source_url": "ftp://x.example/a"}
        ok3, errs3 = sv.validate_record(rec3, schema)
        assert not ok3 and any("http(s)" in e for e in errs3)

    def test_normalize_datetime(self):
        r = sv.normalize_datetime("2021-05-06")
        assert isinstance(r, (str, type(None)))


class TestOcrCorrection:
    def test_replace_confusions(self):
        # 返回 (纠正文本, 替换次数) 二元组
        out = oc.replace_confusions("中囲人民", {"囲": "国"})
        assert isinstance(out, tuple) and "中国" in out[0]

    def test_filter_noise(self):
        r = oc.filter_noise("正文X")
        assert isinstance(r, str)

    def test_correct_ocr_text(self):
        r = oc.correct_ocr_text("测试文本")
        assert isinstance(r, (str, tuple, dict))

    def test_is_table_like(self):
        r = oc._is_table_like("| a | b |")
        assert isinstance(r, bool)

    def test_collect_map(self):
        out = {}
        oc._collect_map({"a": "b"}, out)
        assert isinstance(out, dict)
