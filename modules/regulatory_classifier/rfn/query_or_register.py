# -*- coding: utf-8 -*-
"""
rfn.query_or_register — 三级查询 + 注册（C-3/C-4 代码化，单一事实源纪律）

三级查询（命中即返回现有记录，不注册）：
  L1 文号归一化精确相等：idx.by_docno(docno) 命中（同主题优先，无同主题取首条）
  L2 标题归一化精确：idx.by_title(主题全名, title) 精确命中
  L3 标题双向包含 + 文号佐证：归一化标题彼此包含（截断匹配），且
      （a）双方均含有效文号且归一化相等；或
      （b）双方均无文号且「发布日期一致」佐证
        —— 注：归属表无"发布机构"列，docless 文件的唯一键为「归一化标题+文件来源+发布日期」MD5，
           查询侧仅能以发布日期作为可比对佐证（弱于文号，但避免纯标题误判）。

未命中 → 调用 registry.register_doc(...) 新建（action="created"）。

设计纪律：
  - 三级查询**始终要求强佐证**（文号相等 / 发布日期相等），纯标题包含不触发复用，避免误判。
  - 2026-09-01 P0 修复：organ（发布机构）参数已从 query/query_or_register/CLI 移除——
    归属表无该列，register_doc 亦无该入参（传 organ 必抛 TypeError，且 unique_key 第 3 位
    为 source，传 organ 会导致去重键/RFN 派生错位）。
  - 归一化复用 rfn.norm_title 与 registry.norm_docno（单一事实源，禁止本地另写一套）。
  - query_or_register 不修改 register_doc 既有唯一键去重逻辑（避免回归），仅作为更丰富的
    入库前匹配入口；召回/摄入管线应优先经本函数，降低重复登记 RFN 风险。

用法：
  from rfn.query_or_register import query, query_or_register
  level, hit = query(idx, theme="T1", title="...", docno="银保监办发〔2019〕19号")
  res = query_or_register(theme="T1", title="...", docno="...", source="supp")
  # res = {"rfn":..., "action":"reused"|"created", "match_level":..., "unique_key":..., ...}
"""
import os
import sys

_PKG = os.path.dirname(os.path.abspath(__file__))          # regulatory_classifier/rfn/
_PARENT = os.path.dirname(_PKG)                              # regulatory_classifier/
for _p in (_PKG, _PARENT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from registry import norm_docno, register_doc, unique_key  # noqa: E402
from rfn import THEME_MAP, get_index, norm_title  # noqa: E402

LEVEL_DOCNO = "L1_docno"        # 文号归一化精确
LEVEL_TITLE = "L2_title"        # 标题归一化精确
LEVEL_CONTAIN = "L3_contain"    # 标题双向包含 + 文号/发布日期佐证


def _theme_full(theme):
    """主题键(T1) → 主题全名；已是全名则原样返回。"""
    return THEME_MAP.get(theme, theme) if theme else theme


def query(idx, theme=None, title="", docno=None, pub_date=""):
    """三级查询，返回 (level, record) 或 (None, None)。

    record 为归属表 dict 的副本，并附带 "_match_level" 字段标注命中层级。
    未命中返回 (None, None)。

    注：organ（发布机构）参数已于 2026-09-01 移除——归属表无该列，且函数内从未使用
    （L3 佐证仅用文号/发布日期）。
    """
    nd = norm_docno(docno)
    nt = norm_title(title)
    tname = _theme_full(theme)

    # ---- L1 文号归一化精确相等 ----
    if nd and len(nd) >= 5:
        hits = idx.by_docno(docno)          # 已按归一化文号聚合
        if hits:
            same = [h for h in hits if h.get("主题") == tname] if tname else []
            hit = (same[0] if same else hits[0])
            hit = dict(hit)
            hit["_match_level"] = LEVEL_DOCNO
            return LEVEL_DOCNO, hit

    # ---- L2 标题归一化精确（需主题）----
    if tname and nt:
        exact = idx.by_title(tname, title)
        if exact:
            e = dict(exact)
            e["_match_level"] = LEVEL_TITLE
            return LEVEL_TITLE, e

    # ---- L3 标题双向包含 + 文号佐证 ----
    if nt:
        for r in idx.rows():
            rnt = norm_title(r.get("文件名称", ""))
            if not rnt:
                continue
            if nt in rnt or rnt in nt:
                rnd = norm_docno(r.get("发文字号", ""))
                # 佐证 (a)：双方均有有效文号且相等
                if nd and rnd and nd == rnd:
                    rr = dict(r)
                    rr["_match_level"] = LEVEL_CONTAIN
                    return LEVEL_CONTAIN, rr
                # 佐证 (b)：双方均无文号，发布日期一致（归属表无机构列，仅日期可比对）
                if not nd and not rnd:
                    if (pub_date or "") and (r.get("发布日期", "") or "") == (pub_date or ""):
                        rr = dict(r)
                        rr["_match_level"] = LEVEL_CONTAIN
                        return LEVEL_CONTAIN, rr
    return None, None


def query_or_register(theme, title, docno=None, pub_date="",
                      source="", fingerprint="", force_register=False):
    """三级查询命中 → 复用（action="reused"，含 match_level）；
    未命中 → register_doc 新建（action="created"）。

    force_register=True 时跳过查询直接新建（用于明确已知为新文件的场景）。

    2026-09-01 P0 修复：移除 organ 参数——
      ① register_doc 自 2026-08-31 起已无 organ 入参，传 organ 必抛 TypeError；
      ② unique_key 第 3 位为 source，传 organ 会生成错误去重键（RFN 派生错位）。
      无文号文件的唯一键现按「标题+文件来源+发布日期」MD5，与 register_doc 完全一致。
    """
    if not force_register:
        idx = get_index()
        level, hit = query(idx, theme=theme, title=title, docno=docno,
                           pub_date=pub_date)
        if hit:
            rfn = hit["监管文件编号"]
            return {
                "rfn": rfn,
                "action": "reused",
                "match_level": level,
                "unique_key": unique_key(docno, title, source, pub_date),
                "matched": hit,
            }
    res = register_doc(theme=theme, title=title, docno=docno,
                       pub_date=pub_date, source=source, fingerprint=fingerprint)
    return {
        "rfn": res["rfn"],
        "action": "created",
        "match_level": None,
        "unique_key": unique_key(docno, title, source, pub_date),
        "sync": res.get("sync"),
    }


def main():
    """CLI 探针：给定 主题/标题/文号，输出三级查询命中情况（不注册）。"""
    import argparse
    ap = argparse.ArgumentParser(description="rfn 三级查询探针")
    ap.add_argument("--theme", default="")
    ap.add_argument("--title", default="")
    ap.add_argument("--docno", default="")
    # 注：--organ 已于 2026-09-01 移除（归属表无发布机构列，register_doc 亦无该入参）
    ap.add_argument("--pub-date", default="")
    ap.add_argument("--register", action="store_true", help="未命中则注册")
    args = ap.parse_args()
    if args.register:
        r = query_or_register(args.theme, args.title, docno=args.docno or None,
                              pub_date=args.pub_date)
        print(f"[query_or_register] action={r['action']} level={r['match_level']} rfn={r['rfn']}")
    else:
        idx = get_index()
        level, hit = query(idx, theme=args.theme, title=args.title,
                           docno=args.docno or None, pub_date=args.pub_date)
        if hit:
            print(f"[query] 命中层级={level} RFN={hit['监管文件编号']} 标题={hit['文件名称']}")
        else:
            print("[query] 未命中（可注册）")


if __name__ == "__main__":
    main()
