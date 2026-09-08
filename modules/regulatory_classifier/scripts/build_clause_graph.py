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
from collections import Counter, defaultdict

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(BASE_DIR, "data")

NUM = r"[0-9零一二三四五六七八九十百千]+"

# P1 条款链：《X》[间隔≤12字] 第N条[款][项] (、|和|及|与|或者 连接的多条并列一并捕获)
P1 = re.compile(
    rf"《([^《》]{{2,40}})》[^。；\n]{{0,12}}?((?:第{NUM}条(?:第{NUM}款)?(?:第{NUM}项)?)"
    rf"(?:[、,和及与或者]{{1,4}}第{NUM}条(?:第{NUM}款)?(?:第{NUM}项)?)*)")
ART_IN = re.compile(rf"第({NUM})条(?:第({NUM})款)?(?:第({NUM})项)?")
PARA_ONLY = re.compile(rf"第({NUM})款")
ITEM_ONLY = re.compile(rf"第({NUM})项")
P2 = re.compile(rf"(?:本办法|本规定|本通知|本指引|本细则|本条例|本规则)[^。；\n]{{0,6}}?第({NUM})条")
P3 = re.compile(rf"(?<![0-9零一二三四五六七八九十百千])第({NUM})条")
P4 = re.compile(r"(?:根据|依据|依照|按照)\s*《([^《》]{2,40})》")
P5 = re.compile(r"《([^《》]{2,40})》")

SELF_WORDS = ("本办法", "本规定", "本通知", "本指引", "本细则", "本条例", "本规则")


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
        if re.match(r"^T(\d+)_.*逐份.*明细表\.csv$", fn):
            theme = re.match(r"^T(\d+)_", fn).group(1)
            with open(os.path.join(DATA_DIR, fn), encoding="utf-8-sig", newline="") as f:
                for r in csv.DictReader(f):
                    idx[norm(r.get("标题"))].append(
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

    meta = {r["监管文件编号"]: r for r in base}   # 2026-08-31 重构：键=RFN（无 seq）
    records, edges = [], []
    stat = Counter()

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
                    edges.append({"src_rfn": rfn, "src_title": title,
                                  "dst_rfn": trfn, "dst_title": tti, "dst_theme": th,
                                  "kind": "article", "art": art, "para": para, "item": item})
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
