# -*- coding: utf-8 -*-
"""
build_clause_graph.py — 主题内「条/款/项级引用关系图」确定性抽取（步骤一 · 分析底座）

定位
----
在 `逐份条款引用与上位法依据明细表.csv` 的稀疏「条款引用」列之外，直接从**五源正文**
（`_t{n}_matched.json`）重新抽取跨文件引用关系，供纵向深化分析报告使用。

抽取模式（2026-08-31 放宽版，提升条级召回）
------------------------------------------
* P1 跨文件条款引用：《X》[间隔≤12字]第N条[第M款][第K项]
* P2 自指条款引用 ：本办法/本规定/本通知/本指引/本细则/本条例 + 第N条
* P3 自身条款结构 ：裸「第N条」（用于统计文件自身条款数，不计入跨文件边）
* P4 上位法依据   ：根据/依据/依照/按照 《X》
* P5 书名号引用   ：《X》（文件级关联网络）

单源纪律：正文取 `_t{n}_matched.json`；元数据取 `_t{n}_base.json`/明细表；
跨主题解析索引取 10 张明细表 + `_upper_laws.json`（均经既有事实源，不硬编码）。
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from collections import Counter, defaultdict

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(BASE_DIR, "data")
# R-F01 收敛（2026-09-14）：同仓引导（独立运行时定位 std_lib）+ 共享口径 import。
# 下列 P1–P5 原为本文件字面量，与 build_detail_tables.ART_RE、verify_regulatory_citations.TITLE_PAT
# 三份重复（口径靠人眼对齐）；现统一取自 std_lib.common_lib.relations，**语义不变**。
_ORCH_ROOT = os.path.dirname(os.path.dirname(BASE_DIR))
if _ORCH_ROOT not in sys.path:
    sys.path.insert(0, _ORCH_ROOT)
from std_lib.common_lib.clause_locator import (
    article_no_to_int,
    load_regulatory_index,
    norm_article_no,
)
from std_lib.common_lib.relations import (
    ARTICLE_CHAIN,
    ARTICLE_CHAIN_CAPTURE,
    ARTICLE_NUM,
    BARE_ARTICLE_RE,
    SELF_REF_RE,
    SELF_REF_WORDS,
    basis_trigger_alt,
    quote_title_capture,
)

NUM = ARTICLE_NUM

# P1 条款链：《X》[间隔≤12字] 第N条[款][项] (、|和|及|与|或者 连接的多条并列一并捕获)
P1 = re.compile(
    quote_title_capture() + rf"[^。；\n]{{0,12}}?((?:{ARTICLE_CHAIN})"
    rf"(?:[、,和及与或者]{{1,4}}{ARTICLE_CHAIN})*)")
ART_IN = re.compile(ARTICLE_CHAIN_CAPTURE)
PARA_ONLY = re.compile(rf"第({ARTICLE_NUM})款")
ITEM_ONLY = re.compile(rf"第({ARTICLE_NUM})项")
P2 = SELF_REF_RE
P3 = BARE_ARTICLE_RE
P4 = re.compile(rf"(?:{basis_trigger_alt()})\s*{quote_title_capture()}")
P5 = re.compile(quote_title_capture())

SELF_WORDS = SELF_REF_WORDS   # 共享口径（原为本文件字面量）


def norm(t: str) -> str:
    """标题归一化：去书名号/空白/效力后缀/印发前缀，用于跨文件解析。"""
    t = (t or "").strip().strip("《》")
    t = re.sub(r"[（(](?:已废止|已失效|废止|失效|修订)[)）]", "", t)
    t = re.sub(r"^(?:关于印发|关于发布|关于印发修订后的|修订后的|关于下发)+", "", t)
    return re.sub(r"\s+", "", t)


def classify_level(title: str, docno: str) -> str:
    """按标题/文号判效力层级，供步骤六『层级缺口』分析。"""
    t, d = title or "", (docno or "").upper()
    if re.match(r"^中华人民共和国.+法$", t) or t.endswith("法") and "条例" not in t and len(t) <= 12:
        return "法律"
    if "司法解释" in t or re.search(r"法释\[", d) or "最高人民法院关于适用" in t:
        return "司法解释"
    if t.endswith("条例"):
        return "行政法规"
    if re.search(r"(银保监令|保监会令|中国保险监督管理委员会令|部令|第\d+号令)", d):
        return "部门规章"
    if re.search(r"(JR/T|行业标准|中国保险行业协会|保险业协会)", t + d):
        return "行业标准"
    if re.search(r"(指引|规程|标准|规范|自律公约|示范)", t):
        return "操作指引/行业标准"
    if re.search(r"(办法|规定)", t) and re.search(r"(试行|暂行)", t):
        return "规范性文件(试行/暂行)"
    if re.search(r"(办法|规定)", t):
        return "规范性文件(办法/规定)"
    if re.search(r"(通知|意见|通报|公告|批复|函)", t):
        return "规范性文件(通知/意见)"
    return "其他"


def load_global_index():
    """归一化标题 -> [(theme, rfn, title, docno)]，覆盖 10 张明细表 + 上位法锚点。"""
    idx = defaultdict(list)
    for fn in sorted(os.listdir(DATA_DIR)):
        if (mt := re.match(r"^T(\d+)_.*逐份.*明细表\.csv$", fn)):   # walrus：一次匹配并收窄
            theme = mt.group(1)
            with open(os.path.join(DATA_DIR, fn), encoding="utf-8-sig", newline="") as f:
                for r in csv.DictReader(f):
                    idx[norm(r.get("标题") or "")].append(
                        (theme, r.get("监管文件编号", ""), r.get("标题", ""), r.get("发文字号", "")))
    up = os.path.join(DATA_DIR, "_upper_laws.json")
    if os.path.exists(up):
        for k, e in json.load(open(up, encoding="utf-8")).items():
            idx[norm(e.get("title"))].append(("UP", k, e.get("title", ""), e.get("docno", "")))
    return idx


# 简称 -> 全称别名表（抽样校验发现的误判修正：《保险法》《公司法》等短名易被模糊匹配错配）
ALIASES = {
    "保险法": "中华人民共和国保险法",
    "公司法": "中华人民共和国公司法",
    "消费者权益保护法": "中华人民共和国消费者权益保护法",
    "消保法": "中华人民共和国消费者权益保护法",
    "个人信息保护法": "中华人民共和国个人信息保护法",
    "个保法": "中华人民共和国个人信息保护法",
    "广告法": "中华人民共和国广告法",
    "数据安全法": "中华人民共和国数据安全法",
    "网络安全法": "中华人民共和国网络安全法",
    "反洗钱法": "中华人民共和国反洗钱法",
    "民法典": "中华人民共和国民法典",
    "保险法解释二": "最高人民法院关于适用《中华人民共和国保险法》若干问题的解释（二）",
    "保险法解释三": "最高人民法院关于适用《中华人民共和国保险法》若干问题的解释（三）",
}


def resolve(name: str, idx):
    """把被引用的书名号名称解析到库内文件；返回 (resolved, theme, rfn, title)。

    精度优先（宁缺勿错，避免报告出现错误发文字号实证）：
      1) 归一化精确匹配；
      2) 别名表展开后精确匹配；
      3) 包含匹配——仅当被引名≥6字 **且候选唯一** 才采信（多候选判为歧义→不解析）。
    """
    n = norm(name)
    if not n:
        return False, "", "", ""
    for key in (n, norm(ALIASES.get(n, ""))):
        if key and key in idx:
            th, rfn, ti, _ = idx[key][0]
            return True, th, rfn, ti
    if len(n) >= 6:
        cands = [k for k in idx if n in k]
        if len(cands) == 1:
            th, rfn, ti, _ = idx[cands[0]][0]
            return True, th, rfn, ti
        pref = [k for k in cands if k.startswith(n)]
        if len(pref) == 1:
            th, rfn, ti, _ = idx[pref[0]][0]
            return True, th, rfn, ti
    return False, "", "", ""


def main():
    ap = argparse.ArgumentParser(description="抽取主题内条/款/项级引用关系图")
    ap.add_argument("--theme", default="T1", help="主题号，如 T1")
    ap.add_argument("--no-write", action="store_true", help="仅统计不写盘")
    args = ap.parse_args()

    n = args.theme.lstrip("Tt")
    base = json.load(open(os.path.join(DATA_DIR, f"_t{n}_base.json"), encoding="utf-8"))
    matched = json.load(open(os.path.join(DATA_DIR, f"_t{n}_matched.json"), encoding="utf-8"))
    citerefs = json.load(open(os.path.join(DATA_DIR, f"_t{n}_citerefs.json"), encoding="utf-8"))
    idx = load_global_index()
    # N-49（2026-09-27）：目标条款表（复核正则抽出的目标条款号是否真存在；进程内单例）
    clause_idx = load_regulatory_index()

    meta = {r["监管文件编号"]: r for r in base}   # 2026-08-31 重构：键=RFN（无 seq）
    records, edges = [], []
    stat: Counter = Counter()

    for rfn, mrow in matched.items():
        b = meta.get(rfn, {})
        title = b.get("title") or mrow.get("title", "")
        body = mrow.get("body", "") or ""

        # 自身条款结构（P3 裸第N条，去重后近似条款数）
        own_arts = set(P3.findall(body))
        # 自指条款（P2）
        self_refs = P2.findall(body)
        # 上位法依据（P4）+ citerefs.basis 兜底
        basis, seen = [], set()
        for nm in P4.findall(body):
            if nm not in seen:
                seen.add(nm); basis.append(nm)
        for nm in (citerefs.get(rfn, {}) or {}).get("basis", []):
            if nm not in seen:
                seen.add(nm); basis.append(nm)

        art_refs = []
        for name, chain in P1.findall(body):
            ok, th, trfn, tti = resolve(name, idx)
            pos = body.find(name)
            ctx = body[max(0, pos - 40): pos + 60].replace("\n", " ") if pos >= 0 else ""
            for art, para, item in ART_IN.findall(chain):
                rec = {"cited": name, "art": art, "para": para, "item": item,
                       "resolved": ok, "target_theme": th, "target_rfn": trfn,
                       "target_title": tti, "ctx": ctx}
                art_refs.append(rec)
                if ok and th != "UP":
                    # N-49/N-59（2026-09-27）目标条款**查表复核**（三态 + 受控理由；见 `_verify_dst_art`）
                    _ver, _chk = _verify_dst_art(trfn, art, clause_idx)
                    edges.append({"src_rfn": rfn, "src_title": title,
                                  "dst_rfn": trfn, "dst_title": tti, "dst_theme": th,
                                  "kind": "article", "art": art, "para": para, "item": item,
                                  "art_verified": _ver, "art_check": _chk})
                    stat["art_verified" if _ver else "art_unverified"] += 1
                    if _ver is None:
                        stat["art_no_table"] += 1
                    # N-59：按**理由**分列统计（`out_of_range` = 必错配的最强信号，独立可观测）
                    stat[f"art_check_{_chk}"] += 1
                stat["art_total"] += 1
                stat["art_resolved" if ok else "art_unresolved"] += 1
                if para: stat["with_para"] += 1
                if item: stat["with_item"] += 1
        # 独立「第N款/第N项」（未挂靠《X》第N条）：单独计数，作为款/项级精度补充证据
        stat["para_only"] += len(PARA_ONLY.findall(body))
        stat["item_only"] += len(ITEM_ONLY.findall(body))

        # 书名号文件级关联（P5）
        name_refs = Counter(nm for nm in P5.findall(body) if len(nm.strip()) > 1)
        for nm, c in name_refs.most_common():
            ok, th, trfn, tti = resolve(nm, idx)
            if ok and th != "UP" and trfn != rfn:
                edges.append({"src_rfn": rfn, "src_title": title,
                              "dst_rfn": trfn, "dst_title": tti, "dst_theme": th,
                              "kind": "title", "count": c})
        stat["book_total"] += sum(name_refs.values())

        records.append({
            "rfn": rfn, "title": title,
            "docno": b.get("doc_no", ""), "year": b.get("real_year") or b.get("year_reported"),
            "eff_status": b.get("eff_status", ""), "file_src": b.get("file_src", ""),
            "cluster": (citerefs.get(rfn, {}) or {}).get("cluster", "") or _cluster_of(rfn, n),
            "level": classify_level(title, b.get("doc_no", "")),
            "body_len": len(body),
            "own_article_count": len(own_arts), "self_ref_count": len(self_refs),
            "basis": basis, "art_refs": art_refs,
            "name_refs_top": [list(x) for x in name_refs.most_common(8)],
        })

    out = {"theme": args.theme, "doc_count": len(records),
           "stats": dict(stat), "records": records, "edges": edges}
    print(f"[{args.theme}] 文件 {len(records)} | 条款级引用 {stat['art_total']} "
          f"(解析成功 {stat['art_resolved']}, 含款 {stat['with_para']}, 含项 {stat['with_item']}) | "
          f"书名号 {stat['book_total']} | 边 {len(edges)}")
    if args.no_write:
        return
    p = os.path.join(DATA_DIR, f"_t{n}_clause_graph.json")
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)
    print(f"✅ 已写盘 -> {p}")


def _verify_dst_art(rfn: str, art: str, clause_idx: dict):
    """目标条款**查表复核**（N-49，2026-09-27）：`art` 来自纯正则（`《X》…第N条`），
    而标题解析含别名/包含匹配（可能错配）；仅当目标文件条款表**真含**该条号才可信。

    返回 `(verified, check)`：
      · `verified` 三态：True=表中确有此条；False=可判定但不匹配；None=无法判定；
      · `check`（N-59，受控字符串）：
        - `verified`            表中确有此条；
        - `out_of_range`        **条号超出目标文件条款总数**（`article_count`）→ **必错配**（最强信号）；
        - `in_range_unmatched`  在总数范围内但表中无该条号（可疑：可能误抽/或该条在修正案中）；
        - `no_table`            目标文件无条款表或未登记 RFN（无从判定，**不等于错**）。
    """
    tab = (clause_idx.get("by_rfn") or {}).get(rfn or "")
    if not tab:
        return None, "no_table"
    try:
        need = norm_article_no(art)
        if need in (tab.get("by_norm") or {}):
            return True, "verified"
        n = article_no_to_int(art)
        # N-62（2026-09-27）：**阿拉伯/中文形态互认**兜底——`art` 常为阿拉伯（如 '133'，
        # 来自《保险法》第133条），而目标条款表键为中文（'一百三十三'）→ 仅比 `by_norm`
        # 会误判为"表内无此条"（实测 24 条可疑边全属此因）。经 `by_no`（`articles[].no` 整数）
        # 归一后比对，与 `clause_locator.locate_dst_article` 的兜底口径一致。
        if n and n in (tab.get("by_no") or {}):
            return True, "verified"
        n_arts = tab.get("n_articles") or 0
        if n and n_arts and n > n_arts:
            return False, "out_of_range"
        return False, "in_range_unmatched"
    except Exception:  # noqa: BLE001  复核失败不阻断图构建
        return None, "no_table"


def _cluster_of(rfn, n):
    """明细表兜底取子主题。"""
    for fn in sorted(os.listdir(DATA_DIR)):
        if re.match(rf"^T{n}_.*逐份.*明细表\.csv$", fn):
            with open(os.path.join(DATA_DIR, fn), encoding="utf-8-sig", newline="") as f:
                for r in csv.DictReader(f):
                    if r.get("监管文件编号") == rfn:
                        return (r.get("子主题") or "").strip()
    return ""


if __name__ == "__main__":
    main()
