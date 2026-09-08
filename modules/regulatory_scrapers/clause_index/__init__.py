# -*- coding: utf-8 -*-
"""
modules.regulatory_scrapers.clause_index — 五源 clean 条文抽取固定节点（②，2026-09-08）

定位：clean 清洗后的**固定流程节点**——对五源 latest cleaned jsonl 每份文件 body_text 做
条文结构抽取（共享 std_lib.scraper_std.document_structure），产物供：
  - regulatory_classifier 主题/子主题分类等节点（主题报告条款维度 / match_theme_docs 条款证据）；
  - internal_policy_drafter 条款级对照（外部监管文件《X》第N条 正文）。
对称 clean_index：build（增量，仅当 clean 快照新于产物）→ data/clauses/{src}_clauses_{date}.jsonl
（每行=一份文件的条款结构）+ clause_index_state.json（快照日期登记）。

产物行 schema：
  {dedup_key, source_url, document_number, title, chapter_count, article_count,
   chapters:[{no,title}], articles:[{no,number,body}]}
"""
from __future__ import annotations

import json
import os
import re
import sys

csv = __import__("csv")

csv.field_size_limit(sys.maxsize)

_PKG = os.path.dirname(os.path.abspath(__file__))          # modules/regulatory_scrapers/clause_index
_SCRAPERS = os.path.dirname(_PKG)                           # modules/regulatory_scrapers
_ORCH_ROOT = os.path.dirname(os.path.dirname(_SCRAPERS))
for _p in (_SCRAPERS, _ORCH_ROOT, os.path.join(_ORCH_ROOT, "std_lib")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from clean_index import get_clean_index  # noqa: E402   clean 快照单一事实源

from std_lib.common_lib.norm import norm_docno  # noqa: E402
from std_lib.scraper_std.document_structure import extract_structure  # noqa: E402 共享条文抽取

CLAUSE_DIR = os.path.join(_SCRAPERS, "data", "clauses")
_STATE_PATH = os.path.join(CLAUSE_DIR, "clause_index_state.json")
_SOURCES = ("gov", "mof", "nfra", "pbc", "supp")


def _load_state():
    if not os.path.exists(_STATE_PATH):
        return {}
    try:
        return json.load(open(_STATE_PATH, encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state):
    os.makedirs(CLAUSE_DIR, exist_ok=True)
    tmp = _STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, _STATE_PATH)


# 无换行长文条锚切分：clean body_text 多被归一为单段（无 \n），须先按 章/条 起始切行，
# extract_structure（行式）才能识别。排除「内嵌引用」误切（前为汉字/字母数字/闭引括号，
# 如 "《保险法》第X条" / "依照本办法第X条" → 不切）；句读/空白/行首后的条起始才切。
_CHAPTER_ANCHOR_RE = re.compile(r"(?<![\n])(第[〇0-9一二三四五六七八九十百千两]+\s*章)")
_ARTICLE_ANCHOR_RE = re.compile(r"(?<![\u4e00-\u9fffA-Za-z0-9》」』”）])(第[〇0-9一二三四五六七八九十百千两]+\s*条)")


def segment_body(body: str) -> str:
    """把无换行正文按 章/条 起始切为多行（供 extract_structure 行式识别）。"""
    t = body or ""
    t = _CHAPTER_ANCHOR_RE.sub(r"\n\1", t)
    t = _ARTICLE_ANCHOR_RE.sub(r"\n\1", t)
    return t


def latest_clause_path(src: str) -> str:
    st = _load_state()
    date = (st.get(src) or {}).get("date", "")
    if not date:
        return ""
    p = os.path.join(CLAUSE_DIR, f"{src}_clauses_{date}.jsonl")
    return p if os.path.exists(p) else ""


def iter_file_clauses(src: str, path: str = ""):
    """流式逐行 yield 某源条款记录（每行一份文件）。path 缺省用最新。"""
    p = path or latest_clause_path(src)
    if not p or not os.path.exists(p):
        return
    with open(p, encoding="utf-8") as fh:
        for ln in fh:
            if not ln.strip():
                continue
            try:
                yield json.loads(ln)
            except Exception:
                continue


def build_clause_index(rebuild: bool = False) -> dict:
    """clean → 条文 固定节点（②）。增量：仅对 clean 快照日期新于 clause 产物的源抽取。
    rebuild=True 强制全量重建。返回 {src: {date, built, files}}。"""
    os.makedirs(CLAUSE_DIR, exist_ok=True)
    ci = get_clean_index()
    state = {} if rebuild else _load_state()
    out = {}
    for src in _SOURCES:
        cp = ci.latest_jsonl_path(src)
        if not cp or not os.path.exists(cp):
            out[src] = {"error": "no clean jsonl"}
            continue
        # 快照日期 = 文件名末 8 位
        m = re.search(r"_cleaned_(\d{8})\.jsonl$", cp)
        date = m.group(1) if m else "unknown"
        if not rebuild and (state.get(src) or {}).get("date") == date:
            out[src] = {"date": date, "built": False, "files": (state.get(src) or {}).get("files", 0)}
            continue
        dest = os.path.join(CLAUSE_DIR, f"{src}_clauses_{date}.jsonl")
        tmp = dest + ".tmp"
        n = 0
        with open(cp, encoding="utf-8") as fin, open(tmp, "w", encoding="utf-8") as fout:
            for ln in fin:
                if not ln.strip():
                    continue
                try:
                    rec = json.loads(ln)
                except Exception:
                    continue
                body = segment_body(rec.get("body_text") or "")
                stru = extract_structure(body) if body.strip() else {
                    "chapters": [], "articles": [], "chapter_count": 0, "article_count": 0}
                row = {
                    "dedup_key": rec.get("dedup_key", ""),
                    "source_url": rec.get("source_url", ""),
                    "document_number": rec.get("document_number", ""),
                    "title": rec.get("title", ""),
                    "chapter_count": stru["chapter_count"],
                    "article_count": stru["article_count"],
                    "chapters": stru["chapters"],
                    "articles": [
                        {**a, "number": re.sub(r"\s+", "", a.get("number", "") or "")}  # 条号形态归一（去内部空格）
                        for a in stru["articles"]
                    ],
                }
                fout.write(json.dumps(row, ensure_ascii=False) + "\n")
                n += 1
        os.replace(tmp, dest)
        state[src] = {"date": date, "files": n, "clause_path": dest}
        out[src] = {"date": date, "built": True, "files": n}
    _save_state(state)
    out["_state"] = _STATE_PATH
    out["_dir"] = CLAUSE_DIR
    return out


def validate_schema() -> dict:
    """产物契约自检（字段统一性 + 条号形态；契约常量 interfaces.contract.CLAUSE_*）。"""
    from interfaces import contract  # noqa: PLC0415
    problems = []
    stat = {"files": 0, "articles": 0, "chapters": 0}
    line_f = set(contract.CLAUSE_LINE_FIELDS)
    art_f = set(contract.CLAUSE_ARTICLE_FIELDS)
    ch_f = set(contract.CLAUSE_CHAPTER_FIELDS)
    for s in _SOURCES:
        for cl in iter_file_clauses(s):
            stat["files"] += 1
            if set(cl.keys()) != line_f:
                problems.append(f"{s} 行键集漂移: {sorted(set(cl.keys()) ^ line_f)[:4]}")
            if not isinstance(cl.get("chapter_count"), int) or not isinstance(cl.get("article_count"), int):
                problems.append(f"{s} count 非 int")
            if cl.get("article_count") != len(cl.get("articles") or []):
                problems.append(f"{s} article_count!=len(articles)")
            for a in cl.get("articles") or []:
                stat["articles"] += 1
                if set(a.keys()) != art_f:
                    problems.append(f"{s} article 键漂移")
                if not isinstance(a.get("no"), int):
                    problems.append(f"{s} article.no 非 int")
                if not re.fullmatch(r"第[0-9〇一二三四五六七八九十百千两]+条", a.get("number", "") or ""):
                    problems.append(f"{s} 条号形态异常: {a.get('number', '')!r}")
            for c in cl.get("chapters") or []:
                stat["chapters"] += 1
                if set(c.keys()) != ch_f:
                    problems.append(f"{s} chapter 键漂移")
    return {**stat, "consistent": not problems, "problems": problems[:20]}


def find_clauses(docno: str = "", title: str = "", src: str = ""):
    """按 发文字号归一 / 标题 匹配查找条款（流式逐源；返回 generator 逐份匹配记录）。"""
    nd = norm_docno(docno) if docno else ""
    nt = re.sub(r"[《》\s]", "", title or "")
    sources = (src,) if src else _SOURCES
    for s in sources:
        for cl in iter_file_clauses(s):
            if nd and len(nd) >= 5:
                if norm_docno(cl.get("document_number", "")) == nd:
                    yield {**cl, "src": s}
                    continue
            if nt and re.sub(r"[《》\s]", "", cl.get("title", "") or "") == nt:
                yield {**cl, "src": s}


if __name__ == "__main__":  # 离线自检（不触发 build）
    st = _load_state()
    print("[clause_index] state:", {k: v.get("date") for k, v in st.items()})
    for s in _SOURCES:
        p = latest_clause_path(s)
        if p:
            first = next(iter_file_clauses(s), None)
            print(f"  {s}: {os.path.basename(p)} files={st.get(s, {}).get('files')} "
                  f"sample_articles={first and first.get('article_count')}")
