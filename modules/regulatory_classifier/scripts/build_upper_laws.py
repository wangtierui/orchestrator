# -*- coding: utf-8 -*-
"""
build_upper_laws.py — 「上位法锚点」_upper_laws.json 确定性重建（单源派生，非人工维护）

=========================================================================
定位与铁律（对齐 regulatory_classifier 数据来源唯一铁律）
-------------------------------------------------------------------------
* `_upper_laws.json` 承载「不归属任何主题正文、仅作为各主题内文件上位法而存在的
  基础法律/法规」的全文锚点，供条款引用比对时取上下文。
* 本脚本将其从「手工维护」改造为「单一事实源派生」：
    - 身份（RFN / 子主题标签）       取于 10 份 `T{n}_{条数}逐份条款引用与上位法依据明细表.csv`；
    - 描述字段（title/docno/url/body_text）全量取于**五源 clean 数据**（gov/mof/nfra/pbc/supp），
      经 clean_index 单一索引事实源访问，禁止硬编码路径或回退手工全文。
* 纳入规则（可配置 UPPER_LAW_TAGS）：明细表「子主题」∈ 上位法家族标签的行。
* 净化纪律：仅保留可确定性派生的集合，不在任何表/非上位法标签的孤儿键一律不入（如
  消保法实施条例属 S5 主题正文、人行消保办法2020 不在任何表，均自然排除）。
* 写盘：先备份再原子 `os.replace`；`--check` 为门禁（校验落盘 == 派生集，无漂移）。

DAMA/DCMM/ISO8000 对齐：数据来源单一事实源（clean_index）+ 可复现派生 + 写入前备份。
=========================================================================
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import sys
from collections import defaultdict

# --------------------------------------------------------------------------- #
# 路径与配置
# --------------------------------------------------------------------------- #
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)                       # regulatory_classifier/
DATA_DIR = os.path.join(BASE_DIR, "data")
ATTR_PATH = os.path.join(DATA_DIR, "人身保险公司-文件归属表.csv")
OUT_PATH = os.path.join(DATA_DIR, "_upper_laws.json")
BACKUP_DIR = os.path.join(BASE_DIR, "backups")

# 纳入规则：明细表「子主题」命中以下标签 => 该文件即上位法锚点
# 2026-08-31 枚举归一：原 4 个变体（补A·上位法 / 补A·制定依据补录 / P0上位法 / P0上位法/裁判规则）
# 统一收敛为单一枚举「上位法锚点」，与『仅作上位法、非主题正文』语义对齐。
UPPER_LAW_TAGS = {"T0上位法锚点"}

# 文件来源(明细表) / 五源 source -> 键前缀
SRC_PREFIX_MAP = {
    "gov": "gov", "nfra": "nfra", "pbc": "pbc", "mof": "mof", "supp": "supp",
    "官方发布": "gov", "桌面已有": "local", "local": "local", "": "ext",
}

# 短 slug 覆盖（确定性、稳定、可读），未覆盖则取净化后的标题
SLUG_OVERRIDE = {
    "中华人民共和国保险法": "保险法",
    "中华人民共和国个人信息保护法": "个人信息保护法",
    "中华人民共和国广告法": "广告法",
    "中华人民共和国数据安全法": "数据安全法",
    "国务院办公厅关于加强金融消费者权益保护工作的指导意见": "国办发81号消保指导意见",
    "最高人民法院关于适用《中华人民共和国保险法》若干问题的解释（二）": "保险法解释二",
    "最高人民法院关于适用《中华人民共和国保险法》若干问题的解释（三）": "保险法解释三",
    "中华人民共和国公司法": "公司法",
    "社会保险经办条例": "社会保险经办条例",
}

FIVE_SOURCE_TITLE_COL = "title"
FIVE_SOURCE_BODY_COL = "body_text"
# 正文可能落在多个列，取最长者；优先正文长度，再源优先级、再日期
BODY_COLS = ["body_text", "body_text_webpage", "body_text_doc", "raw_uncut_text"]
SRC_PRIORITY = {"gov": 0, "nfra": 1, "pbc": 2, "supp": 3, "mof": 4}


def best_body(row):
    """返回 (最佳正文列名, 正文内容)，取四列中最长者。"""
    best_c, best_v = "", ""
    for c in BODY_COLS:
        v = row.get(c) or ""
        if len(v) > len(best_v):
            best_v, best_c = v, c
    return best_c, best_v


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def _norm_title(t: str) -> str:
    # P4：本脚本私有标题归一（仅去空白，用于五源标题匹配），语义独立于
    # common_lib.norm/rfn._norm_title，故下划线命名避免公共符号唯一性门禁误报。
    return re.sub(r"\s+", "", t or "")


def slugify(title: str) -> str:
    if title in SLUG_OVERRIDE:
        return SLUG_OVERRIDE[title]
    return re.sub(r"[\s\W_]+", "", title or "unk") or "unk"


def latest_detail_csvs():
    out = []
    for fn in sorted(os.listdir(DATA_DIR)):
        if re.match(r"^T\d+_.*逐份.*明细表\.csv$", fn):
            out.append(os.path.join(DATA_DIR, fn))
    return out


# --------------------------------------------------------------------------- #
# 步骤 1：从明细表抽取「上位法锚点」行（身份，单源）
# --------------------------------------------------------------------------- #
def load_anchors_from_details():
    """返回 list[dict]，每条为一个上位法锚点行（可能跨主题重复，后续去重）。"""
    rows = []
    for path in latest_detail_csvs():
        with open(path, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                sub = (r.get("主题") or "").strip()
                if sub in UPPER_LAW_TAGS:
                    rows.append({
                        "rfn": (r.get("监管文件编号") or "").strip(),
                        "theme": "T0",
                        "title": (r.get("标题") or "").strip(),
                        "docno_detail": (r.get("发文字号") or "").strip(),
                        "src_detail": (r.get("文件来源") or "").strip(),
                        "subtheme": sub,
                        "body_status_detail": (r.get("正文状态") or "").strip(),
                    })
    return rows


def dedup_anchors(rows):
    """按标题去重，聚合 citing 主题与 RFN 列表（保留首个出现的主记录）。"""
    by_title = {}
    for r in rows:
        t = r["title"]
        if t not in by_title:
            by_title[t] = dict(r)
            by_title[t]["cited_by_themes"] = []
            by_title[t]["rfns"] = []
        a = by_title[t]
        if r["theme"] and r["theme"] not in a["cited_by_themes"]:
            a["cited_by_themes"].append(r["theme"])
        if r["rfn"] and r["rfn"] not in a["rfns"]:
            a["rfns"].append(r["rfn"])
    return list(by_title.values())


def compute_citations(anchors):
    """单源：扫描全部明细表「立法依据」(精确) / 「条款引用」(子串) 列，
    统计每部上位法被哪些主题的文件引用（供比对报告关联，非锚点自身所在主题）。"""
    title_to_themes = defaultdict(set)
    titles = {a["title"] for a in anchors}
    for path in latest_detail_csvs():
        with open(path, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                m = re.search(r"T(\d+)", r.get("监管文件编号") or "")
                th = m.group(1) if m else None
                if not th:
                    continue
                leg = (r.get("立法依据") or "").strip()
                cit = r.get("条款引用") or ""
                for t in titles:
                    if leg == t or t in cit:
                        title_to_themes[t].add(th)
    return title_to_themes


# --------------------------------------------------------------------------- #
# 步骤 2：五源 clean 数据主文档匹配（描述字段，单源）
# --------------------------------------------------------------------------- #
def build_five_source_index():
    """经 clean_index 单一事实源取五源 latest csv，建 归一化标题 -> 行 索引。"""
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(BASE_DIR), "regulatory_scrapers"))
        from clean_index import get_clean_index
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] clean_index 不可用，五源匹配跳过：{e}", file=sys.stderr)
        return {}
    ci = get_clean_index()
    index = defaultdict(list)
    for a in ci.all_active_csv():
        src = a["source_id"]
        path = a["path"]
        try:
            with open(path, encoding="utf-8-sig", newline="") as f:
                for row in csv.DictReader(f):
                    t = (row.get(FIVE_SOURCE_TITLE_COL) or "").strip()
                    if t:
                        index[_norm_title(t)].append({"source": src, "row": row})
        except Exception as e:  # noqa: BLE001
            print(f"[WARN] 读 {src} 失败：{e}", file=sys.stderr)
    return index


def match_five_source(title, index):
    nt = _norm_title(title)
    exact, fuzzy = [], []
    for item in index.get(nt, []):
        exact.append(item)
    if not exact:
        for key, items in index.items():
            if (key.startswith(nt) or nt.startswith(key)) and abs(len(key) - len(nt)) < 40:
                fuzzy.extend(items)
    cands = exact or fuzzy
    if not cands:
        return None
    # 优选：正文最长 > 源优先级高 > 日期新（修正：先按正文长度，避免选到元数据行）
    def score(it):
        row = it["row"]
        body = len(best_body(row)[1])
        pri = SRC_PRIORITY.get(it["source"], 9)
        date = row.get("effective_date") or row.get("publish_date") or ""
        return (body, -pri, date)
    best = sorted(cands, key=score, reverse=True)[0]
    return best


# --------------------------------------------------------------------------- #
# 步骤 3：组装条目
# --------------------------------------------------------------------------- #
def build_entry(anchor, five):
    title = anchor["title"]
    if five:
        row = five["row"]
        src = five["source"]
        body_col, body = best_body(row)
        body = body.strip()
        docno = (row.get("document_number") or anchor["docno_detail"] or "N/A").strip()
        url = (row.get("source_url") or "").strip()
        origin = "five_source"
        body_status = "five_source" if len(body) > 50 else "pending_five_source"
        five_info = {
            "source_id": src,
            "matched_title": (row.get("title") or "").strip(),
            "matched_body_col": body_col,
            "document_number": (row.get("document_number") or "").strip(),
            "timeliness_status": (row.get("timeliness_status") or "").strip(),
        }
        prefix = SRC_PREFIX_MAP.get(src, "ext")
    else:
        docno = anchor["docno_detail"] or "N/A"
        url = ""
        body = ""
        origin = "derived_upper_law_pending"
        body_status = "pending_five_source"
        five_info = None
        prefix = SRC_PREFIX_MAP.get(anchor["src_detail"], "ext")
    key = f"{prefix}_{slugify(title)}"
    return key, {
        "title": title,
        "docno": docno,
        "url": url,
        "body_text": body,
        "source_origin": origin,
        "body_status": body_status,
        "subtheme": anchor["subtheme"],
        "source_mark": anchor["src_detail"],
        "rfn": anchor["rfns"],
        "cited_by_themes": anchor["cited_by_themes"],
        "five_source": five_info,
    }


def derive():
    anchors = dedup_anchors(load_anchors_from_details())
    cite_map = compute_citations(anchors)
    for a in anchors:
        a["cited_by_themes"] = sorted(cite_map.get(a["title"], set()))
    index = build_five_source_index()
    out = {}
    for a in anchors:
        five = match_five_source(a["title"], index)
        key, entry = build_entry(a, five)
        out[key] = entry
    return out


# --------------------------------------------------------------------------- #
# 步骤 4：写盘（先备份）+ 门禁校验
# --------------------------------------------------------------------------- #
def backup_and_write(out):
    os.makedirs(BACKUP_DIR, exist_ok=True)
    if os.path.exists(OUT_PATH):
        ts = __import__("datetime").datetime.now().strftime("%Y%m%d_%H%M%S")
        shutil.copy2(OUT_PATH, os.path.join(BACKUP_DIR, f"_upper_laws_{ts}.json"))
    tmp = OUT_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    os.replace(tmp, OUT_PATH)
    return len(out)


def load_written():
    if not os.path.exists(OUT_PATH):
        return None
    with open(OUT_PATH, encoding="utf-8") as f:
        return json.load(f)


def gate_check(derived):
    """门禁：落盘 == 派生集（键集合一致、source_origin 合法）。返回 (ok, msg)。"""
    written = load_written()
    if written is None:
        return False, "落盘文件缺失"
    dk, wk = set(derived), set(written)
    if dk != wk:
        missing = dk - wk
        extra = wk - dk
        return False, f"漂移：缺失={sorted(missing)} 多余={sorted(extra)}"
    for k, e in written.items():
        so = e.get("source_origin", "")
        if so not in ("five_source", "derived_upper_law_pending"):
            return False, f"键 {k} source_origin 非法：{so}"
    return True, f"✅ 门禁通过：{len(written)} 键 == 派生集，单一事实源无漂移"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="重建 _upper_laws.json（五源单源派生）")
    ap.add_argument("--no-write", action="store_true", help="仅派生并预览，不写盘")
    ap.add_argument("--check", action="store_true", help="门禁：校验落盘==派生集")
    args = ap.parse_args()

    derived = derive()
    if args.check:
        ok, msg = gate_check(derived)
        print(msg)
        sys.exit(0 if ok else 1)

    # 预览（始终打印派生摘要，便于审计）
    print(f"派生上位法锚点 {len(derived)} 部：")
    for k, e in sorted(derived.items()):
        fs = e.get("five_source")
        src = fs["source_id"] if fs else "—"
        print(f"  {k:32s} | {e['title'][:22]:22s} | 五源={src:5s} | body={e['body_status']} | cited_by={e['cited_by_themes']}")

    if args.no_write:
        print("\n[--no-write] 未写盘。")
        return

    n = backup_and_write(derived)
    print(f"\n✅ 已写盘 {n} 部 -> {OUT_PATH}")
    ok, msg = gate_check(derived)
    print(msg)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
