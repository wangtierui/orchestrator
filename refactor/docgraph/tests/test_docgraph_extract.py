# -*- coding: utf-8 -*-
"""docgraph/extract.py 字段对齐锁定：cleaned 正文为规范信封 content，旧名兼容回退。

对应重构：load_corpus 抽出的纯函数 _payload_to_doc 统一映射点；
正文读 content（旧版 body_text / text 回退，原则9），且不携带未消费的 source。
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from docgraph.extract import _doc_id, _norm, _payload_to_doc  # noqa: E402


def test_payload_to_doc_reads_content():
    # 无 index_no / id → doc_id 回退到 title（docgraph 标题解析设计，不用 dedup_key）
    p = {"title": "测试办法", "content": "第一条 内容。"}
    d = _payload_to_doc(p)
    assert d["doc_id"] == "测试办法"
    assert d["title"] == "测试办法"
    assert d["text"] == "第一条 内容。"


def test_payload_to_doc_body_text_fallback():
    # 旧版 cleaned 仅含 body_text（content 缺失）→ 兼容回退
    p = {"title": "旧办法", "body_text": "第一条 旧。"}
    assert _payload_to_doc(p)["text"] == "第一条 旧。"


def test_payload_to_doc_text_fallback():
    # 更旧格式仅含 text → 仍可读
    p = {"title": "老办法", "text": "第一条 老。"}
    assert _payload_to_doc(p)["text"] == "第一条 老。"


def test_payload_to_doc_no_source_carried():
    # source 在 docgraph 抽取中无消费，不应进入文档表示（最小化携带）
    p = {"title": "X", "content": "y", "source": "gov"}
    assert "source" not in _payload_to_doc(p)


def test_doc_id_prefers_explicit_id_then_title():
    assert _doc_id({"title": "办法A"}) == "办法A"
    assert _doc_id({"index_no": "NO1", "title": "办法B"}) == "NO1"
    assert _doc_id({"id": "ID2", "title": "办法C"}) == "ID2"


def test_norm_strips_punctuation():
    # 书名号/括号在首尾时去除；空白先被并入 strip 之外的 re.sub 清理
    assert _norm("《办法》") == "办法"
    assert _norm(" 办法 ") == "办法"
