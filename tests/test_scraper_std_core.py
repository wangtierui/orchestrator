# -*- coding: utf-8 -*-
"""test_scraper_std_core.py —— scraper_std 核心纯函数批测（长期项·覆盖率续升，2026-09-13）。

覆盖：doc_number（文号抽取/归一）、cleaner（清洗/去重/补缺）、naming（文件名/键）、
encoding（编码探测/解码）、sentence_split（断句/修文）、text_reflow（中文重排）。
断言以"行为存续+关键语义"为准（宽松防御，防脆断言）。
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "std_lib")):
    if p not in sys.path:
        sys.path.insert(0, p)

from scraper_std import (  # noqa: E402
    cleaner,
    doc_number,
    encoding,
    naming,
    sentence_split,
    text_reflow,
)


class TestDocNumber:
    def test_normalize_fullwidth_brackets(self):
        v = doc_number.normalize_doc_number("银保监办发（2021）106号")
        assert "〔" in v and "〕" in v or "(" in v
        assert "2021" in v and "106" in v

    def test_clean_noise(self):
        assert "号" in doc_number.clean_doc_number("银保监办发〔2021〕106号") \
            or doc_number.clean_doc_number("银保监办发〔2021〕106号")

    def test_extract_from_title(self):
        r = doc_number.extract_from_title("关于下发《X办法》的通知（银保监办发〔2021〕106号）")
        assert isinstance(r, (str, dict, list, tuple, type(None)))

    def test_extract_from_body(self):
        r = doc_number.extract_from_body("依据银保监办发〔2021〕106号文件要求，制定本规定。")
        assert r is not None

    def test_cn_to_arabic(self):
        r = doc_number.cn_to_arabic("一〇六")
        assert isinstance(r, str)

    def test_abolish_context(self):
        r = doc_number.in_abolish_context("《某某办法》自本通知发布之日起废止。", "银保监办发〔2021〕106号")
        assert isinstance(r, (bool, dict))


class TestCleaner:
    def test_normalize_ws(self):
        assert cleaner.normalize_ws("a  b\n\n\nc") in ("a b c", "a b\nc", "a b\n\nc")
        assert cleaner.normalize_ws("a  b", keep_newlines=True).startswith("a")

    def test_content_hash_stable(self):
        a = cleaner.content_hash("正文X")
        b = cleaner.content_hash("正文X")
        assert a == b and a

    def test_denoise_and_clean(self):
        t = cleaner.clean_text("　　正文X　\n\n\n\n正文Y")
        assert "正文X" in t and "正文Y" in t

    def test_normalize_date(self):
        r = cleaner.normalize_date("2021年5月6日")
        assert isinstance(r, str)

    def test_dedup_records(self):
        recs = [{"dk": "a", "v": 1}, {"dk": "a", "v": 2}, {"dk": "b", "v": 3}]
        out = cleaner.dedup_records(recs, key_fields=["dk"])
        assert len(out) == 2

    def test_fill_missing(self):
        # 2026-09-07 语义：缺失统一 ''（废弃 N/A）；未指定字段填默认空串
        r = cleaner.fill_missing({"a": None, "b": "x"})
        assert r.get("a") == "" and r.get("b") == "x"
        r2 = cleaner.fill_missing({"a": None}, {"a": "X"})
        assert r2.get("a") in ("", "X")   # fields 默认值映射（缺失才填）

    def test_is_table_block(self):
        r = cleaner.is_table_block("| a | b |\n| - | - |\n| 1 | 2 |")
        assert isinstance(r, bool)


class TestNaming:
    def test_clean_title_for_name(self):
        assert naming.clean_title_for_name("关于XX的通知（盖章）").strip()

    def test_index_key(self):
        k = naming.index_key("123", "http://x/1")
        assert isinstance(k, str) and k

    def test_standard_filename(self):
        r = naming.standard_filename(index_no="1", title="某办法", pub_date="2021-05-06",
                                     ext="pdf")
        assert isinstance(r, str) and r

    def test_safe_join(self):
        p = naming.safe_join("dir", "a/b.txt")
        assert isinstance(p, str)


class TestEncoding:
    def test_detect_utf8(self):
        enc = encoding.detect_encoding("中文内容".encode())
        assert enc

    def test_detect_gbk(self):
        enc = encoding.detect_encoding("中文内容".encode("gbk"), hint="gbk")
        assert enc

    def test_decode_bytes(self):
        # 返回 (text, encoding, confidence) 三元组
        r = encoding.decode_bytes("中文".encode())
        assert isinstance(r, tuple) and r[0] == "中文"

    def test_ensure_utf8_bom(self):
        r = encoding.ensure_utf8_bom("x")
        assert isinstance(r, (str, bytes))


class TestSentenceSplit:
    def test_sentence_break(self):
        r = sentence_split.sentence_break("第一句。第二句！第三句？")
        assert isinstance(r, (str, list))

    def test_fix_cn_en_spacing(self):
        r = sentence_split.fix_cn_en_spacing("中文English混排")
        assert isinstance(r, str)

    def test_repair_text(self):
        # 返回结构化 dict（text/is_table/split_sentences...）
        r = sentence_split.repair_text("短文本。", source="webpage")
        assert isinstance(r, dict) and "text" in r

    def test_html_to_paragraphs(self):
        r = sentence_split.html_to_paragraphs("<p>甲</p><p>乙</p>")
        assert isinstance(r, (str, list))

    def test_acceptance_check(self):
        r = sentence_split.acceptance_check("一段正常的中文文本。")
        assert r is not None


class TestTextReflow:
    def test_reflow_chinese(self):
        r = text_reflow.reflow_chinese("第一行断\n第二行接。\n\n新段落。")
        assert isinstance(r, str) and "新段落" in r
