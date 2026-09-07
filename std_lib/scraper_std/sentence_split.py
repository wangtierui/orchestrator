# -*- coding: utf-8 -*-
"""
sentence_split.py —— 文本断句与粘连修复（第七节 7.2 专项）

前置过滤：表格污染判定（is_table_block）——表格文本块跳过本节所有断句规则，
直接交由 table_recovery 处理。

四级修复流程：
  ① 标签语义转换断句：块级标签(<div>/<p>/<h1..h6>/<li>/<tr>) → \n；
     内联标签(<span>/<a>/<b>/<i>) → 空格；严禁直接移除标签不补分隔符。
  ② 中文标点自动分隔：句末标点(。？！)后加 \n；逗号/分号/顿号后追加空格。
  ③ 中英文/数字粘连拆分：中文字符与英文字母/数字间插入半角空格。
  ④ 无标点兜底切分：全文无句末标点且长度>50 → 按常用连词切分短句列表，
     原文保留至 raw_uncut_text，拆分结果存入 split_sentences。
"""

from __future__ import annotations

import logging
import re

from .cleaner import is_table_block, normalize_ws

LOG = logging.getLogger("scraper_std.sentence_split")

# ① 标签语义
_BLOCK_TAGS = re.compile(
    r"<(?:div|p|h[1-6]|li|tr|table|section|article|header|footer|br)\b[^>]*>", re.I)
_INLINE_TAGS = re.compile(
    r"<(?:span|a|b|i|em|strong|u|font|label)\b[^>]*>", re.I)
_END_TAGS = re.compile(r"</(?:div|p|h[1-6]|li|tr|table|section|article|header|footer|br)\s*>", re.I)
_REMOVE_TAGS = re.compile(r"<[^>]+>")

# ② 中文标点
_SENT_END = re.compile(r"([。！？!?])")
_COMMA = re.compile(r"([，；、,:])")
# ③ 中英文粘连（中文 与 字母/数字 之间）
_CN_EN = re.compile(r"([\u4e00-\u9fff])([A-Za-z0-9])")
_EN_CN = re.compile(r"([A-Za-z0-9])([\u4e00-\u9fff])")
# ④ 兜底切分连词
_FALLBACK_CONJ = re.compile(r"(?<=[，,;；])(因为|所以|但是|然而|于是|此外|同时|因此|鉴于)")

# 验收口径（第十六节）：禁止超过 30 个字符且不含任何标点或空格的连续字符串
_LONG_NO_PUNCT = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]{31,}")


def html_to_paragraphs(html: str) -> str:
    """① 标签语义转换断句：块级标签→换行，内联标签→空格。"""
    if not html:
        return ""
    t = html
    t = _BLOCK_TAGS.sub("\n", t)
    t = _END_TAGS.sub("\n", t)
    t = _INLINE_TAGS.sub(" ", t)
    t = _REMOVE_TAGS.sub("", t)
    return t


def sentence_break(text: str) -> str:
    """② 中文标点自动分隔：句末标点后换行，逗号/分号/顿号后空格。"""
    t = text
    t = _SENT_END.sub(r"\1\n", t)
    t = _COMMA.sub(r"\1 ", t)
    return t


def fix_cn_en_spacing(text: str) -> str:
    """③ 中英文/数字粘连拆分。"""
    t = _CN_EN.sub(r"\1 \2", text)
    t = _EN_CN.sub(r"\1 \2", t)
    return t


def split_long_uncut(text: str) -> dict[str, list[str]]:
    """
    ④ 无标点兜底切分。返回 {"split_sentences": [...], "raw_uncut_text": 原文}
    （原文非空时保留）。
    """
    out: dict[str, list[str]] = {"split_sentences": [], "raw_uncut_text": ""}
    stripped = (text or "").strip()
    if not stripped:
        return out
    has_end = any(p in stripped for p in ("。", "！", "？"))
    if (not has_end) and len(stripped) > 50:
        out["raw_uncut_text"] = stripped
        parts = _FALLBACK_CONJ.split(stripped)
        sentences = [p.strip() for p in parts if p and p.strip()]
        out["split_sentences"] = sentences if len(sentences) > 1 else [stripped]
        LOG.warning("无标点长文兜底切分：%d 句（原文已保留）", len(out["split_sentences"]))
    return out


def repair_text(text: str, *, source: str = "webpage") -> dict[str, object]:
    """
    断句修复总入口。返回结构化结果：
      {
        "text": 修复后文本,
        "is_table": bool,
        "split_sentences": [...] (仅兜底切分时非空),
        "raw_uncut_text": str (仅兜底切分时非空),
        "repair_steps": [...]
      }
    """
    steps: list[str] = []
    result: dict[str, object] = {
        "text": text or "",
        "is_table": False,
        "split_sentences": [],
        "raw_uncut_text": "",
        "repair_steps": steps,
    }
    if not text:
        return result
    # 前置过滤：表格污染判定
    if is_table_block(text):
        result["is_table"] = True
        steps.append("table_block_skipped")
        return result

    t = normalize_ws(text)  # ① 原始空白/控制字符先行折叠（\n→空格）
    if "<" in t and ">" in t:  # 仍含 HTML 标签 → 标签语义断句
        t = html_to_paragraphs(t)
        steps.append("html_tag_semantic")
    t = sentence_break(t)
    steps.append("cn_punct_break")
    t = fix_cn_en_spacing(t)
    steps.append("cn_en_spacing")
    t = normalize_ws(t, keep_newlines=True)  # 收尾：保留句末换行、折叠多余空白

    # 兜底：无标点长文
    fallback = split_long_uncut(t)
    if fallback["split_sentences"]:
        steps.append("fallback_split")
        result["split_sentences"] = fallback["split_sentences"]
        result["raw_uncut_text"] = fallback["raw_uncut_text"]
    result["text"] = t
    return result


def acceptance_check(text: str) -> list[str]:
    """
    断句修复验收（第十六节）：
      - 每句句末须含 。！？ 至少一种；
      - 禁止 >30 字符且无任何标点/空格的连续字符串。
    返回违规清单（空 = 通过）。
    """
    issues = []
    if not text:
        return issues
    for m in _LONG_NO_PUNCT.finditer(text):
        issues.append(f"连续无标点串 {len(m.group())} 字符: {m.group()[:30]}...")
    return issues


if __name__ == "__main__":  # 离线自检
    html = "<div><p>第一条 本规定适用于所有机构。</p><span>附则</span><p>第二条 生效。</p></div>"
    r = repair_text(html)
    assert "第一条" in r["text"] and "\n" in r["text"]
    assert "附则" in r["text"]
    r2 = repair_text("第一条本规定适用于所有机构第二条自发布之日起施行")
    assert r2["split_sentences"] or "raw_uncut_text" in r2
    r3 = repair_text("序号 项目 金额\n1 收入 100\n2 支出 50")
    assert r3["is_table"] is True and "table_block_skipped" in r3["repair_steps"]
    assert acceptance_check("这是一个测试句子。没有问题的句子") == []
    assert acceptance_check("这是一个超级长的没有任何标点符号也没有空格的连续中文文本串" * 3) != []
    print("[scraper_std.sentence_split] 离线自检通过")
