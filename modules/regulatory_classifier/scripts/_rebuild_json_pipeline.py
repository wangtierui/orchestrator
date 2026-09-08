# -*- coding: utf-8 -*-
"""
_rebuild_json_pipeline.py —— 保真重排重建（2026-08-31 一次性历史脚本，勿重跑）

⚠️ 2026-09-01 标注：本脚本为 2026-08-31 重构专用的一次性重排工具，
   输入为旧格式（T1-T8、RFN-T{t}-{seq}）JSON，该数据已随重构归档至
   backups/restruct_20260831_233928/data_base_json_*/，现行 40 个 JSON 已按
   新主题归属表直接重建（RFN-<16hex> 键），**本脚本不再需要、勿重跑**。
   如需重排须先按 04_旧新RFN映射表.csv 恢复旧数据。

将 data/ 下现有 T1-T8 四份 JSON（base/final/matched/citerefs）按
「归属表·主题列」权威键重新归组为 T1-T10，保留全部人工标注：
  · final.cluster / src_mark / source_origin（含 u_fix 人工修正，已烘焙进 cluster）
  · matched / citerefs 的正文抽取与条款级引用
仅对主题内 seq 重新连续编号（1..N），对齐已重建 CSV 明细表约定。

设计纪律：
  · 不重新运行聚类 / 五库匹配 → 零丢失既有人工成果，且不为 T9/T10 臆造关键词配置
  · 先全量读入旧 JSON 到内存，再写盘（避免 _t1 同名覆盖导致读空）
  · 原子写：临时文件 + os.replace

用法：
  python _rebuild_json_pipeline.py --no-write   # 仅校验，不写盘
  python _rebuild_json_pipeline.py              # 校验 + 写出 40 个 T1-T10 JSON
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
ATTR = os.path.join(DATA, "人身保险公司-文件归属表.csv")

THEME_CODE_RE = re.compile(r"^T(?:10|[1-9])")
RFN_RE = re.compile(r"^RFN-(T[1-9]|T10)-(\d{3})$")
OLD_T = range(1, 9)              # 现有旧 JSON 仅 T1-T8（按 RFN 前缀）
NEW_T = [f"T{i}" for i in range(1, 11)]


def sort_key(row):
    """主题内规范排序：先按 RFN 前缀编号，再按 3 位序号。"""
    return (int(row["prefix"][1:]), row["rfnum"])


def load_attr():
    """读取权威归属表 → 每 RFN 的 (主题码, 基础字段, RFN 前缀/序号)。"""
    rows = []
    with open(ATTR, encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            rfn = (r.get("监管文件编号") or "").strip()
            m = RFN_RE.match(rfn)
            if not m:
                continue
            theme_col = (r.get("主题") or "").strip()
            tm = THEME_CODE_RE.match(theme_col)
            code = tm.group(0) if tm else None
            pub = (r.get("发布日期") or "").strip()
            year = pub[:4] if len(pub) >= 4 and pub[:4].isdigit() else ""
            rows.append({
                "rfn": rfn,
                "code": code,
                "title": (r.get("文件名称") or "").strip(),
                "doc_no": (r.get("发文字号") or "").strip(),
                "file_src": (r.get("文件来源") or "").strip(),
                "eff_status": (r.get("时效状态") or "").strip() or "valid",
                "year_reported": year,
                "real_year": int(year) if year.isdigit() else None,
                "prefix": m.group(1),
                "rfnum": int(m.group(2)),
            })
    return rows


def load_old():
    """读入现有 T1-T8 四份 JSON，按 RFN 归并成 bundle。"""
    old = {t: {} for t in OLD_T}
    for t in OLD_T:
        base = json.load(open(os.path.join(DATA, f"_t{t}_base.json"), encoding="utf-8"))
        fin = json.load(open(os.path.join(DATA, f"_t{t}_final.json"), encoding="utf-8"))
        mat = json.load(open(os.path.join(DATA, f"_t{t}_matched.json"), encoding="utf-8"))
        cit = json.load(open(os.path.join(DATA, f"_t{t}_citerefs.json"), encoding="utf-8"))
        fin_by_rfn = {x["监管文件编号"]: x for x in fin}
        for x in base:
            rfn = x["监管文件编号"]
            seq = str(x.get("seq"))
            old[t][rfn] = {
                "base": x,
                "final": fin_by_rfn.get(rfn),
                "matched": mat.get(seq),
                "citerefs": cit.get(seq),
            }
    return old


def build(attr_rows, old):
    groups = collections.defaultdict(list)
    for row in attr_rows:
        groups[row["code"]].append(row)

    out = {k: {"base": [], "final": [], "matched": {}, "citerefs": {}}
           for k in NEW_T}
    problems = []
    for code in NEW_T:
        rows = sorted(groups.get(code, []), key=sort_key)
        for i, row in enumerate(rows, start=1):
            rfn = row["rfn"]
            t_old = int(row["prefix"][1:])
            bdl = old[t_old].get(rfn)
            if bdl is None:
                problems.append(f"[{code}] RFN 未命中旧 JSON: {rfn}")
                fin = None
                matched = None
                citerefs = None
            else:
                fin = bdl["final"]
                matched = bdl["matched"]
                citerefs = bdl["citerefs"]

            base_rec = {
                "seq": i,
                "year_reported": row["year_reported"],
                "title": row["title"],
                "doc_no": row["doc_no"],
                "file_src": row["file_src"],
                "eff_status": row["eff_status"],
                "real_year": row["real_year"],
                "监管文件编号": rfn,
            }
            final_rec = {
                "seq": i,
                "year_reported": row["year_reported"],
                "title": row["title"],
                "doc_no": row["doc_no"],
                "src_mark": (fin or {}).get("src_mark", ""),
                "file_src": row["file_src"],
                "eff_status": row["eff_status"],
                "real_year": row["real_year"],
                "cluster": (fin or {}).get("cluster", "U未分类"),
                "监管文件编号": rfn,
                "source_origin": (fin or {}).get("source_origin", ""),
            }
            out[code]["base"].append(base_rec)
            out[code]["final"].append(final_rec)
            if matched is not None:
                out[code]["matched"][str(i)] = matched
            else:
                problems.append(f"[{code}] matched 缺失: {rfn}")
            if citerefs is not None:
                out[code]["citerefs"][str(i)] = citerefs
            else:
                problems.append(f"[{code}] citerefs 缺失: {rfn}")
    return out, problems


def validate(out, attr_rows):
    """后验：计数 / RFN 覆盖 / cluster 零丢失 / seq 连续。"""
    fails = []
    total_attr = collections.Counter(r["code"] for r in attr_rows)
    all_rfn_new = set()
    for code in NEW_T:
        b = out[code]["base"]
        f = out[code]["final"]
        m = out[code]["matched"]
        c = out[code]["citerefs"]
        # 计数
        if len(b) != total_attr.get(code, 0):
            fails.append(f"{code} base 计数 {len(b)} ≠ 归属表 {total_attr.get(code,0)}")
        if len(f) != len(b):
            fails.append(f"{code} final 计数 {len(f)} ≠ base {len(b)}")
        if len(m) != len(b):
            fails.append(f"{code} matched 键数 {len(m)} ≠ base {len(b)}")
        if len(c) != len(b):
            fails.append(f"{code} citerefs 键数 {len(c)} ≠ base {len(b)}")
        # seq 连续
        seqs = [x["seq"] for x in b]
        if seqs != list(range(1, len(b) + 1)):
            fails.append(f"{code} base seq 非连续 1..{len(b)}")
        # matched/citerefs 键连续
        mkeys = sorted(int(k) for k in m.keys())
        if mkeys != list(range(1, len(b) + 1)):
            fails.append(f"{code} matched 键非连续 1..{len(b)}")
        ckeys = sorted(int(k) for k in c.keys())
        if ckeys != list(range(1, len(b) + 1)):
            fails.append(f"{code} citerefs 键非连续 1..{len(b)}")
        # cluster 零丢失
        none_cluster = [x["监管文件编号"] for x in f if not x.get("cluster")]
        if none_cluster:
            fails.append(f"{code} 存在 {len(none_cluster)} 条 cluster 为空")
        # RFN 收集
        for x in b:
            all_rfn_new.add(x["监管文件编号"])
    # 总覆盖
    if len(all_rfn_new) != len(attr_rows):
        fails.append(f"RFN 去重覆盖 {len(all_rfn_new)} ≠ 归属表 {len(attr_rows)}")
    return fails


def write_out(out):
    written = []
    for code in NEW_T:
        for kind in ("base", "final", "matched", "citerefs"):
            path = os.path.join(DATA, f"_{code.lower()}_{kind}.json")
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(out[code][kind], fh, ensure_ascii=False, indent=1)
            os.replace(tmp, path)
            written.append(os.path.basename(path))
    return written


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-write", action="store_true", help="仅校验，不写盘")
    ap.add_argument("--confirm", action="store_true",
                    help="确认为历史一次性重排意图（R11 guard 2026-09-08：防误触覆盖现行 40 JSON）")
    args = ap.parse_args()
    if not args.no_write and not args.confirm:
        print("拒绝执行：本脚本为 2026-08-31 一次性历史工具（勿重跑，现行 40 JSON 已按新归属表重建）。")
        print("确要重排请显式加 --confirm；建议仅用 --no-write 校验。")
        return 1

    attr_rows = load_attr()
    old = load_old()
    out, problems = build(attr_rows, old)
    fails = validate(out, attr_rows)

    print("=== 重建预览 / 校验 ===")
    print(f"归属表 RFN 总数: {len(attr_rows)}")
    for code in NEW_T:
        b = out[code]["base"]
        m = out[code]["matched"]
        c = out[code]["citerefs"]
        print(f"  {code}: base={len(b)} final={len(out[code]['final'])} matched={len(m)} citerefs={len(c)}")
    total = sum(len(out[t]["base"]) for t in NEW_T)
    print(f"合计 base 记录: {total}")

    if problems:
        print(f"\n⚠️ 搬运问题 ({len(problems)} 条):")
        for p in problems[:40]:
            print("   -", p)
    if fails:
        print("\n❌ 校验失败:")
        for f in fails:
            print("   -", f)
        return 1
    print("\n✅ 校验全部通过")

    if args.no_write:
        print("[--no-write] 未写盘")
        return 0

    written = write_out(out)
    print(f"\n✅ 已写出 {len(written)} 个 JSON（T1-T10 × 4）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
