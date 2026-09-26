# -*- coding: utf-8 -*-
"""
modules.regulatory_scrapers.clause_index — 五源 clean 条文抽取固定节点（②，2026-09-08）

定位：clean 清洗后的**固定流程节点**——对五源 latest cleaned jsonl 每份文件 body_text 做
条文结构抽取（共享 std_lib.scraper_std.document_structure），产物供：
  - regulatory_classifier 主题/子主题分类等节点（主题报告条款维度 / match_theme_docs 条款证据）；
  - internal_policy_drafter 条款级对照（外部监管文件《X》第N条 正文）。
对称 clean_index：build（增量，仅当 clean 快照新于产物）→ data/clauses/{src}_clauses_{date}.jsonl
（每行=一份文件的条款结构）+ clause_index_state.json（快照日期登记）。

产物行 schema（F-D10 补 4 维 + 2026-09-18 解析适配补 7 维 + 2026-09-20 条内层级 1 维）：
  {dedup_key, source_url, document_number, title,
   rfn, timeliness_status, publish_date, effective_date,
   chapter_count, article_count,
   chapters:[{no,title,article_index}], articles:[{no,number,body}],
   parse_mode, parse_score, parse_meta, is_fallback,
   structure:[{level,number,title,content,items,children}], structure_count, validation,
   article_structure:[{level:条,number,title,content,items:[{level:项,...}],children}]}

2026-09-20 六类缺陷修复（问题驱动；详见 reports 排查报告）：
  - **锚切分（F1）**：`segment_outline`/`segment_body` 后接 `resplit_embedded_anchors`
    段内二次切分 —— 修复「二、基本原则（一）服务…」中 `（一）` 被吞（缺失子节点）；
  - **标题·正文分离（F2）**：`_split_node_title_body` —— 修复「（一）总体目标 借鉴…」
    整段正文进 `title`、「五、发行人应充分…」无标题却被写成 title；
  - **文档级尾部截断（F3）**：`strip_document_tail` —— 修复条款内混入
    `附：/答记者问/发文机关+日期/联合发布网页噪声`，留痕 `parse_meta.tail_cut`；
  - **解析输入归一（F5a）**：`normalize_parse_text` —— NBSP/注入型空格归一（只读，不写回事实源）；
  - **条内层级（F6）**：`parse_article_structure` —— law 条文内「项/目」带 level（`article_structure`）；
  - **检核增强（F7）**：V008 结构完整性 / V009 尾部污染 / V010 空白污染。

2026-09-18 解析适配（问题驱动）：
  - **空解析**：通知/公告/批复类正文无「第X条」，旧实现产出 `article_count=0 / articles=[]`
    → 现经 `parse_document` 自动降级（notice/bulletin/plan → structure；plain 兜底计数），
    层级序号**原样保留**（不重标为「第X条」，避免下游引用臆造条号）。
  - **换行截断 + 交叉引用误判**：`第五十三条、` + `第五十四条规定的行为…` 曾被切成两条
    （条号重复）→ 切分层收紧为"真条首"判据 + 合并层收编续行残片（详见
    `std_lib.scraper_std.document_structure` docstring 取舍节）。
"""
from __future__ import annotations

import json
import os
import re
import shutil
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
from std_lib.scraper_std.document_structure import (  # noqa: E402 共享条文抽取/渲染
    CN_NUM_CHARS,
    extract_structure,  # noqa: F401  （保留导出：历史调用方）
    parse_document,
    render_markdown,
    segment_body,  # noqa: F401  （保留导出：唯一实现已上收本模块上游，见文件末尾说明）
    segment_outline,  # noqa: F401  （保留导出：非条文体锚切分）
    validate_clauses,
)

CLAUSE_DIR = os.path.join(_SCRAPERS, "data", "clauses")
_CLAUSE_HISTORY_DIR = os.path.join(CLAUSE_DIR, "history")   # clauses 历史版本归档（单版化，2026-09-10）
_STATE_PATH = os.path.join(CLAUSE_DIR, "clause_index_state.json")
_SOURCES = ("gov", "mof", "nfra", "pbc", "supp")

# F-D10（2026-09-13 SSOT 专项）：RFN 桥表（classifier 唯一登记源）——条款行内联 rfn 投影。
# v2 §3.1.3 I-3（2026-09-26）：路径改经 `interfaces.rfn_api.registry_paths()` 唯一入口
# （原为拼兄弟模块目录字符串，`gate_no_cross_module_import` 判据 D 已断言该纪律）。
from interfaces.rfn_api import registry_paths as _registry_paths  # noqa: E402

_RFN_BRIDGE = _registry_paths()["bridge_csv"]


def _load_rfn_bridge() -> tuple:
    """rfn_clean_bridge.csv → ({dedup_key: rfn}, {source_url: rfn})；缺表返回空映射（降级不阻断）。"""
    by_dk: dict = {}
    by_url: dict = {}
    if not os.path.exists(_RFN_BRIDGE):
        return by_dk, by_url
    with open(_RFN_BRIDGE, encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            rfn = (row.get("rfn") or row.get("监管文件编号") or "").strip()
            if not rfn:
                continue
            dk = (row.get("dedup_key") or "").strip()
            url = (row.get("source_url") or "").strip()
            if dk:
                by_dk.setdefault(dk, rfn)
            if url:
                by_url.setdefault(url, rfn)
    return by_dk, by_url


def _rotate_clause_history(current_dates: dict[str, str], keep: int = 3) -> None:
    """clauses 单版化（2026-09-10）：CLAUSE_DIR 仅保留当前日期产物，旧日期 jsonl/md 移入
    data/clauses/history/ 并仅保留最近 keep 个日期版本。"""
    os.makedirs(_CLAUSE_HISTORY_DIR, exist_ok=True)
    for src in _SOURCES:
        cur = current_dates.get(src, "")
        pat = re.compile(rf"^{src}_clauses_(\d{{8}})\.(jsonl|md)$")
        for fn in list(os.listdir(CLAUSE_DIR)):
            m = pat.match(fn)
            if not m or m.group(1) == cur:
                continue
            try:
                shutil.move(os.path.join(CLAUSE_DIR, fn),
                            os.path.join(_CLAUSE_HISTORY_DIR, fn))
            except OSError:
                pass
        dates = sorted({pat.match(fn).group(1) for fn in os.listdir(_CLAUSE_HISTORY_DIR)
                        if pat.match(fn)})
        drop = dates[:-keep] if len(dates) > keep else []
        for fn in list(os.listdir(_CLAUSE_HISTORY_DIR)):
            m = pat.match(fn)
            if m and m.group(1) in drop:
                try:
                    os.remove(os.path.join(_CLAUSE_HISTORY_DIR, fn))
                except OSError:
                    pass


def _load_state():
    if not os.path.exists(_STATE_PATH):
        return {}
    try:
        d = json.load(open(_STATE_PATH, encoding="utf-8"))
        # F-D14：读侧剥离版本键（写侧注入 _meta；消费逻辑零感知）
        if isinstance(d, dict):
            d.pop("_meta", None)
        return d
    except Exception:
        return {}


def _save_state(state):
    os.makedirs(CLAUSE_DIR, exist_ok=True)
    # F-D14（H-01）：版本锚点——**副本**写入（不污染调用方对象；load 侧剥离）
    import time as _t  # noqa: PLC0415
    payload = dict(state)
    payload["_meta"] = {"schema_version": "1.0", "written_by": "clause_index",
                        "written_at": _t.strftime("%Y-%m-%d %H:%M:%S")}
    tmp = _STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, _STATE_PATH)


# 2026-09-18：章/条锚切分已上收 `std_lib.scraper_std.document_structure.segment_body`
# （唯一实现；原 `_ARTICLE_ANCHOR_RE` 的"前非汉字"允许清单会把 Word 项目符号/私用区字符
# 当成非边界 → 条文被吞进上一条）。`segment_body` 由上方 import re-export，保持本模块可用。


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


def _file_sha256(p: str) -> str:
    """文件内容指纹（F-C02：同日改写检测用）。"""
    import hashlib
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_clause_index(rebuild: bool = False) -> dict:
    """clean → 条文 固定节点（②）。增量：仅对 clean 快照**日期或内容**新于 clause 产物的源抽取。
    rebuild=True 强制全量重建。返回 {src: {date, built, files}}。

    F-C02：原增量仅比日期——同日 cleaned 内容更新（apply 回写/收编）不触发重建，
    条款静默陈旧（M-09）。现同日也比 input_sha（文件内容指纹），任一不同即重建。
    """
    os.makedirs(CLAUSE_DIR, exist_ok=True)
    ci = get_clean_index()
    state = {} if rebuild else _load_state()
    out = {}
    cur_dates: dict[str, str] = {}
    rfn_by_dk, rfn_by_url = _load_rfn_bridge()   # F-D10：条款行 rfn 投影（桥表批量预载）
    for src in _SOURCES:
        cp = ci.latest_jsonl_path(src)
        if not cp or not os.path.exists(cp):
            out[src] = {"error": "no clean jsonl"}
            continue
        # 快照日期 = 文件名末 8 位
        m = re.search(r"_cleaned_(\d{8})\.jsonl$", cp)
        date = m.group(1) if m else "unknown"
        rec_st = state.get(src) or {}
        if (not rebuild and rec_st.get("date") == date
                and rec_st.get("input_sha") == _file_sha256(cp)):
            out[src] = {"date": date, "built": False, "files": rec_st.get("files", 0)}
            cur_dates[src] = date
            continue
        dest = os.path.join(CLAUSE_DIR, f"{src}_clauses_{date}.jsonl")
        mddest = os.path.join(CLAUSE_DIR, f"{src}_clauses_{date}.md")
        tmp = dest + ".tmp"
        mdtmp = mddest + ".tmp"
        n = 0
        with open(cp, encoding="utf-8") as fin, \
                open(tmp, "w", encoding="utf-8") as fout, \
                open(mdtmp, "w", encoding="utf-8") as fout_md:
            for ln in fin:
                if not ln.strip():
                    continue
                try:
                    rec = json.loads(ln)
                except Exception:
                    continue
                body_text = rec.get("body_text") or ""
                # 2026-09-18：`parse_document` = 自动降级解析（law 主模式 + 通知/通报/规划层级体 +
                # 纯段兜底）+ 法条合并/去重/章索引修复（内部自做锚切分，此处传原始正文）。
                stru = parse_document(body_text)
                row = {
                    "dedup_key": rec.get("dedup_key", ""),
                    "source_url": rec.get("source_url", ""),
                    "document_number": rec.get("document_number", ""),
                    "title": rec.get("title", ""),
                    # F-D10（2026-09-13 SSOT 专项）：条款级维度——rfn 桥表投影 + 时效/日期直取
                    "rfn": (rfn_by_dk.get(rec.get("dedup_key", ""))
                            or rfn_by_url.get(rec.get("source_url", ""), "")),
                    "timeliness_status": rec.get("timeliness_status", ""),
                    "publish_date": rec.get("publish_date", ""),
                    "effective_date": rec.get("effective_date", ""),
                    "chapter_count": stru["chapter_count"],
                    "article_count": stru["article_count"],
                    "chapters": stru["chapters"],
                    "articles": [
                        {**a, "number": re.sub(r"\s+", "", a.get("number", "") or "")}  # 条号形态归一（去内部空格）
                        for a in stru["articles"]
                    ],
                    # 2026-09-18 解析适配：模式/评分/诊断/结构/校验（见模块 docstring schema）
                    "parse_mode": stru["mode"],
                    "parse_score": stru["score"],
                    "parse_meta": {
                        "threshold": stru.get("threshold"),
                        "all_scores": stru.get("all_scores", {}),
                        "plain_paragraphs": stru.get("plain_paragraphs", 0),
                        "tail_marker": stru.get("tail_marker", ""),
                        "repair": stru.get("repair", {}),
                        # F3（2026-09-20）：文档级尾部截断留痕 {marker, at, removed}
                        "tail_cut": stru.get("tail_cut", {}),
                    },
                    "is_fallback": stru["is_fallback"],
                    "structure": stru["structure"],
                    "structure_count": stru["structure_count"],
                    # F6（2026-09-20）：条内层级（项/目）；`articles[].body` 原文保真不动。
                    # 条号同步做「去内部空格」归一——与 `articles[].number` 同规则，
                    # 否则源端「第 五十八条」会导致 article_structure 与 articles 键不可对应。
                    "article_structure": [
                        {**n, "number": re.sub(r"\s+", "", n.get("number", "") or "")}
                        for n in (stru.get("article_structure") or [])
                    ],
                }
                row["validation"] = validate_clauses(row)   # V001–V007（在行装配完成后执行）
                fout.write(json.dumps(row, ensure_ascii=False) + "\n")
                # clauses.md 渲染视图（与 internal_policy_base 共享 render_markdown，双产物）
                fout_md.write(render_markdown(stru, title=rec.get("title", "")))
                fout_md.write("\n\n---\n\n")
                n += 1
        os.replace(tmp, dest)
        os.replace(mdtmp, mddest)
        state[src] = {"date": date, "files": n, "clause_path": dest, "md_path": mddest,
                      "input_sha": _file_sha256(cp)}   # F-C02：内容指纹（同日改写检测）
        cur_dates[src] = date
        out[src] = {"date": date, "built": True, "files": n}
    _save_state(state)
    _rotate_clause_history(cur_dates)   # 单版化：旧日期 jsonl/md → history（保留近三版）
    out["_state"] = _STATE_PATH
    out["_dir"] = CLAUSE_DIR
    return out


def validate_schema() -> dict:
    """产物契约自检（字段统一性 + 条号形态 + 解析适配字段 + 结构语义；契约 CLAUSE_*）。

    2026-09-18 增补：解析适配 7 字段的键集/类型/枚举校验，以及 `structure` 节点键集、
    `validation` 严重度闭包——使 3.x 校验器与 5.x 修复件的产物被**门禁级**断言守住。
    2026-09-20 增补（F6/F7）：`article_structure` 键集/level 闭包/条号可对应，
    `structure` 节点 level 闭包，以及**结构语义计数**（title_swallow / tail_contam /
    space_contam / law_items）——后者是"问题一曾被静默放过"的直接堵漏（e2e 断言指标上限）。
    """
    from config.enums import (  # noqa: PLC0415
        CLAUSE_ISSUE_SEVERITY,
        CLAUSE_PARSE_MODES,
        CLAUSE_STRUCTURE_LEVELS,
    )
    from interfaces import contract  # noqa: PLC0415
    from std_lib.scraper_std.document_structure import structure_semantics  # noqa: PLC0415
    problems = []
    stat = {"files": 0, "articles": 0, "chapters": 0, "structures": 0,
            "law": 0, "degraded": 0, "fallback": 0, "invalid": 0, "warned": 0,
            "article_structures": 0, "item_nodes": 0,
            "title_swallow": 0, "tail_contam": 0, "space_contam": 0, "law_items": 0}
    line_f = set(contract.CLAUSE_LINE_FIELDS)
    art_f = set(contract.CLAUSE_ARTICLE_FIELDS)
    ch_f = set(contract.CLAUSE_CHAPTER_FIELDS)
    st_f = set(contract.CLAUSE_STRUCTURE_FIELDS)
    for s in _SOURCES:
        for cl in iter_file_clauses(s):
            stat["files"] += 1
            if set(cl.keys()) != line_f:
                problems.append(f"{s} 行键集漂移: {sorted(set(cl.keys()) ^ line_f)[:4]}")
            if not isinstance(cl.get("chapter_count"), int) or not isinstance(cl.get("article_count"), int):
                problems.append(f"{s} count 非 int")
            if cl.get("article_count") != len(cl.get("articles") or []):
                problems.append(f"{s} article_count!=len(articles)")
            # ---- 2026-09-18 解析适配字段 ----
            mode = cl.get("parse_mode")
            if mode not in CLAUSE_PARSE_MODES:
                problems.append(f"{s} parse_mode 越界: {mode!r}")
            elif mode == "law":
                stat["law"] += 1
            else:
                stat["degraded"] += 1
            if cl.get("is_fallback") is True:
                stat["fallback"] += 1
            if not isinstance(cl.get("is_fallback"), bool):
                problems.append(f"{s} is_fallback 非 bool")
            if cl.get("structure_count") != len(cl.get("structure") or []):
                problems.append(f"{s} structure_count!=len(structure)")
            if not isinstance(cl.get("parse_score"), dict) or "total" not in cl["parse_score"]:
                problems.append(f"{s} parse_score 形态异常")
            v = cl.get("validation") or {}
            if not isinstance(v, dict) or "status" not in v:
                problems.append(f"{s} validation 缺失")
            elif v.get("status") == "FAILED":
                stat["invalid"] += 1
            elif v.get("status") == "PASSED_WITH_WARNINGS":
                stat["warned"] += 1
            for i in (v.get("issues") or []):
                if i.get("severity") not in CLAUSE_ISSUE_SEVERITY:
                    problems.append(f"{s} validation 严重度越界: {i.get('severity')!r}")
            for n in cl.get("structure") or []:
                stat["structures"] += 1
                if set(n.keys()) != st_f:
                    problems.append(f"{s} structure 节点键漂移: {sorted(set(n.keys()) ^ st_f)[:4]}")
                if n.get("level") not in CLAUSE_STRUCTURE_LEVELS:
                    problems.append(f"{s} structure level 越界: {n.get('level')!r}")
            # ---- 2026-09-20 条内层级（F6）----
            art_nos = {(a.get("number") or "") for a in (cl.get("articles") or [])}
            astr = cl.get("article_structure")
            if not isinstance(astr, list):
                problems.append(f"{s} article_structure 非 list")
                astr = []
            for n in astr:
                stat["article_structures"] += 1
                if set(n.keys()) != st_f:
                    problems.append(f"{s} article_structure 键漂移: {sorted(set(n.keys()) ^ st_f)[:4]}")
                if n.get("level") != "条":
                    problems.append(f"{s} article_structure 顶层 level 非「条」: {n.get('level')!r}")
                if (n.get("number") or "") not in art_nos:
                    problems.append(f"{s} article_structure 条号无对应条文: {n.get('number')!r}")
                for it in n.get("items") or []:
                    stat["item_nodes"] += 1
                    if it.get("level") != "项":
                        problems.append(f"{s} 项节点 level 非「项」: {it.get('level')!r}")
                    if it.get("level") not in CLAUSE_STRUCTURE_LEVELS:
                        problems.append(f"{s} 项节点 level 越界: {it.get('level')!r}")
                    for sub in it.get("items") or []:
                        if sub.get("level") != "目":
                            problems.append(f"{s} 目节点 level 非「目」: {sub.get('level')!r}")
            # ---- 结构语义指标（F7：问题一/三/四的堵漏指标）----
            sem = structure_semantics(cl)
            stat["title_swallow"] += sem["swallowed"]
            stat["tail_contam"] += sem["tail"]
            stat["space_contam"] += sem["space"]
            stat["law_items"] += sem["items"]
            for a in cl.get("articles") or []:
                stat["articles"] += 1
                if set(a.keys()) != art_f:
                    problems.append(f"{s} article 键漂移")
                if not isinstance(a.get("no"), int):
                    problems.append(f"{s} article.no 非 int")
                # 条号形态正则与解析侧**同源**（`CN_NUM_CHARS`；含 零/万 与阿拉伯数字）
                if not re.fullmatch(r"第[0-9%s]+条" % CN_NUM_CHARS, a.get("number", "") or ""):
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
