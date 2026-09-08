# -*- coding: utf-8 -*-
"""
std_lib.scraper_std.document_structure — 法规/制度条文结构解析（R21 落地）

统一解析中文法规/制度正文的「章-条」骨架（docx_utils 既有 split_into_articles 的
通用化 + 章识别 + 条文语义正文拆分），供：
  - 五源 cleaned 监管文件（match_theme_docs 的条款引用/报告明细）
  - internal_policy_base 内部制度（merged_view / drafter 条款对照）
两处复用，消除散落的第X条正则。

能力：
  extract_structure(text) -> {
     chapters: [{no, title, range}]       # 第X章 定位（可为空）
     articles: [{no, number, body}]       # 第X条 起行的条文（body=自条文起至下一条文/章前）
     article_count, chapter_count, method
  }
  条文行识别兼容：第N条 / 第[一二三…]条 / 第一条…（阿拉伯或中文数字）。
  method ∈ {regex} 恒为 regex 解析（OCR/抽取噪声场景由调用方先 normalize）。

设计（R21 关联）：
  - config/ocr.yaml document_parse.structure_extractor 指向本模块；
  - 纯函数、零 IO，接受规范化正文 str。
"""
from __future__ import annotations

import re

# 中文数字 1..9999 支持
_CN = "一二三四五六七八九十百千万零〇两"
_ARTICLE_RE = re.compile(r"^第\s*([0-9]+|[%s]+)\s*条" % _CN)
_CHAPTER_RE = re.compile(r"^第\s*([0-9]+|[%s]+)\s*章" % _CN)
# 附件/附则起标记（视为正文尾）
_TAIL_RE = re.compile(r"^(附\s*则|附件|附表|附\s*录|附录|关于.*的通知$|关于印发.*的通知$)")
# 章/条标题尾部目录点线 + 页码（如「第1章 基本管理....1」「第一条 ….3」）净化
_TRAIL_DOTS_RE = re.compile(r"[.。·•…\s]*\d*\s*$")
_HEADER_DROP = re.compile(r"^(目录|目\s*录|卷首语|扉页|编写说明)")


def _to_int(num: str) -> int:
    """中文数字/阿拉伯数字 → int（粗解析，百千内够用）。失败返回 0。"""
    if num.isdigit():
        return int(num)
    table = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
             "六": 6, "七": 7, "八": 8, "九": 9}
    unit = {"十": 10, "百": 100, "千": 1000}
    total = cur = 0
    for ch in num:
        if ch in table:
            cur = table[ch]
        elif ch in unit:
            if cur == 0:
                cur = 1
            total += cur * unit[ch]
            cur = 0
        else:
            return 0
    return total + cur


def is_article_line(line: str) -> bool:
    return bool(_ARTICLE_RE.match(line.strip()))


def extract_article_no(line: str) -> str:
    m = _ARTICLE_RE.match(line.strip())
    return m.group(0) if m else ""


def is_chapter_line(line: str) -> bool:
    return bool(_CHAPTER_RE.match(line.strip()))


def _drop_toc_chapters(chapters: list[dict]) -> list[dict]:
    """剔除目录页残留章标题（2026-09-08 增补）。

    带目录文档中，目录区先罗列全部章标题（无任何条文跟随），随后正文再出现各章。
    特征：章号重复出现——保留每组重复章中的**最晚**者（正文在文档后部，且其
    article_index 指向真实条文区间），其前同名目录章删除。无重复章号的文档不受影响。
    """
    if not chapters:
        return chapters
    seen_no = {}
    for i, ch in enumerate(chapters):
        seen_no.setdefault(ch.get("no"), []).append(i)
    dup_no = {no for no, idxs in seen_no.items() if len(idxs) > 1}
    if not dup_no:
        return chapters
    # 保留全部非重复章 + 每重复组的最晚者
    drop = {i for no, idxs in seen_no.items()
            if no in dup_no for i in idxs[:-1]}
    return [ch for i, ch in enumerate(chapters) if i not in drop]


def extract_structure(text: str) -> dict:
    """主入口：解析规范化正文 → 章/条结构。text 为空返回空结构。"""
    if not text or not text.strip():
        return {"chapters": [], "articles": [], "chapter_count": 0,
                "article_count": 0, "method": "regex", "tail_marker": ""}
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    chapters = []      # {no, title, start_article_no}
    articles = []      # {no, number, body}
    tail_marker = ""
    current_article = None

    def _flush_article():
        nonlocal current_article
        if current_article is not None:
            body = "\n".join(current_article["_buf"]).strip()
            articles.append({"no": current_article["no"], "number": current_article["number"],
                             "body": body})
            current_article = None

    for ln in lines:
        if _HEADER_DROP.match(ln) or _TAIL_RE.match(ln):
            tail_marker = tail_marker or ln
            if _TAIL_RE.match(ln):
                _flush_article()
                break
            continue
        cm = _CHAPTER_RE.match(ln)
        if cm:
            _flush_article()
            ch_no = _to_int(cm.group(1))
            ch_title = ln[cm.end():].strip()
            # 净化为「章名」，剥离章首页版式点线与页码（如「基本管理....1」→「基本管理」）
            ch_title = re.sub(r"[.。·•…\s]{2,}\d*\s*$", "", ch_title).strip()
            chapters.append({"no": ch_no or len(chapters) + 1,
                             "title": ch_title,
                             "article_index": len(articles)})
            continue
        am = _ARTICLE_RE.match(ln)
        if am:
            _flush_article()
            current_article = {"no": _to_int(am.group(1)) or len(articles) + 1,
                               "number": am.group(0), "_buf": [ln]}
            continue
        if current_article is not None:
            current_article["_buf"].append(ln)
        # 章标题后的章总述/条文间过渡文本（非章非条且无 open article）——忽略（结构噪声）
    _flush_article()
    chapters = _drop_toc_chapters(chapters)
    return {"chapters": chapters, "articles": articles,
            "chapter_count": len(chapters), "article_count": len(articles),
            "method": "regex", "tail_marker": tail_marker[:20]}


_ARTICLE_HEAD_RE = re.compile(r"^第\s*(?:[0-9]+|[一二三四五六七八九十百千万零〇两]+)\s*条[、．.\s]?")


def _article_body(body: str) -> str:
    """条文 body 剥离行首「第X条」前缀（body 以起条文行开头时），保留其余正文。"""
    s = (body or "").strip()
    m = _ARTICLE_HEAD_RE.match(s)
    return s[m.end():].strip() if m else s


def render_markdown(stru: dict, title: str = "") -> str:
    """条文结构 → Markdown 视图（供 drafter 条款对照/人工审阅/LLM 检视）。

    JSON 为规范源（结构化可程序消费），MD 为渲染视图——二者由同一 extract_structure 派生，
    不另造解析歧义。条文按章分组输出：
      # <title>
      ## 第一章 总则
      **第一条** 条文正文…
    """
    lines = []
    if title:
        lines.append(f"# {title}")
        lines.append("")
    chapters = stru.get("chapters") or []
    articles = stru.get("articles") or []
    if not chapters:
        for a in articles:
            lines.append(f"**{a['number']}** {_article_body(a.get('body', ''))}")
            lines.append("")
        return "\n".join(lines).strip()
    # 章边界：chapter.article_index 为该章首条在 articles 的索引
    bounds = [c.get("article_index", 0) for c in chapters]
    for i, ch in enumerate(chapters):
        start = bounds[i] if i < len(bounds) else len(articles)
        end = bounds[i + 1] if i + 1 < len(bounds) else len(articles)
        ch_title = ch.get("title") or ""
        ch_no = ch.get("no", i + 1)
        lines.append(f"## 第{ch_no}章 {ch_title}".rstrip())
        lines.append("")
        for a in articles[start:end]:
            lines.append(f"**{a.get('number', '')}** {_article_body(a.get('body', ''))}")
            lines.append("")
    # 兜底：无任何章（前面已 return）或最后一个章未覆盖到尾部时的章外条文
    # （正常时最后章 end=len(articles) 已覆盖，无需重复追加）
    return "\n".join(lines).strip()


if __name__ == "__main__":  # 离线自检
    demo = (
        "第一章 总则\n第一条 为了规范…，制定本规定。\n"
        "第二条 本规定适用于…。\n第二章 分则\n第三条 机构应当…\n"
        "附件 表单\n附表1"
    )
    r = extract_structure(demo)
    assert r["chapter_count"] == 2 and r["article_count"] == 3, r
    assert r["chapters"][1]["title"] == "分则"
    assert r["articles"][0]["number"] == "第一条"
    md = render_markdown(r, title="测试规定")
    assert "## 第一章 总则" in md and "**第一条** 为了规范" in md, md
    print("[document_structure] 自检通过：%d 章 / %d 条 + MD 渲染" % (r["chapter_count"], r["article_count"]))
