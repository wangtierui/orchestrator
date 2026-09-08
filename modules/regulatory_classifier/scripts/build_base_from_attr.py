# -*- coding: utf-8 -*-
"""
build_base_from_attr.py —— 主题清单 base.json 生成方（C-9，2026-08-29）

背景（README 交付物表第 5 行 / 三模块架构审查 P3-C9）：
  `base.json` 是 `scripts/cluster_by_keywords.py --input` 的输入（主题清单，
  含 seq/title 等），但**交付库未留存其生成方**——README 标注「待补充」，
  导致该中间产物不可复现、无法追溯来源。

本脚本从**唯一事实源**「人身保险公司-文件归属表.csv」（1076 条）
按主题生成 base.json，使其中间产物可复现、可审计，且字段与 final.json 同构。

字段契约（对齐 final.json，剔除由聚类工具产出的 cluster / source_origin）：
  seq / year_reported / title / doc_no / file_src / eff_status / real_year / 监管文件编号

数据来源纪律：一切字段取自权威归属表，**不读取 final.json**（避免循环依赖：
base →(聚类)→ final，base 必须独立于 final 复算）。

用法：
  python build_base_from_attr.py --all              # 生成 T1—T10（默认写 data/）
  python build_base_from_attr.py --theme T3         # 仅单主题
  python build_base_from_attr.py --all --dry-run    # 仅预览，不写盘
  python build_base_from_attr.py --all --out-dir <dir>
  python build_base_from_attr.py --check            # 校验：与现有 base.json 逐条比对（差异 exit 1）
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # regulatory_classifier/
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

ATTR_CSV = os.path.join(ROOT, "data", "人身保险公司-文件归属表.csv")
THEME_CSV = os.path.join(ROOT, "data", "人身保险公司-主题归属表.csv")
DEFAULT_OUT = os.path.join(ROOT, "data")
THEMES = [f"T{i}" for i in range(1, 11)]
# 2026-09-08 适配：归属表 8 列（已删「主题」列）+ RFN 为 RFN-hex；主题唯一来源=主题归属表
_THEME_HEAD_RE = re.compile(r"^(T10|T[1-9])(?=\D|$)")
# R10 provenance（2026-09-08）：每条底座记录标注写者/时间/源快照（归属表 mtime），供二期 traceability。
_GEN_BY = "build_base_from_attr"
_PROV_KEYS = ("generated_by", "generated_at", "source_snapshot")


def _attr_snapshot(path) -> str:
    """源快照 = 权威归属表文件 mtime（YYYY-MM-DD HH:MM:SS，本地时区）。"""
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(path)))
    except OSError:
        return ""


def strip_prov(rec: dict) -> dict:
    """去除 provenance 键（供 --check 与数据投影比较用：血缘字段不参与内容一致性判定）。"""
    return {k: v for k, v in rec.items() if k not in _PROV_KEYS}


def load_attr(path=ATTR_CSV):
    """读取权威归属表 + 主题归属表（RFN→主题码），归组 {T1..T10}。

    归组键来自主题归属表「主题」列（权威十主题；T0 上位法锚点不入底座，与 THEMES=T1..T10 一致）。
    主题内保持归属表行序（RFN 稳定），连续编号 seq=1..N（幂等：同输入必同输出）。
    """
    groups = {t: [] for t in THEMES}
    theme_of = {}
    with open(THEME_CSV, encoding="utf-8-sig", newline="") as fh:
        for tr in csv.DictReader(fh):
            tv = (tr.get("主题") or "").strip()
            m = _THEME_HEAD_RE.match(tv)
            code = m.group(1) if m else ""
            if code in groups:
                theme_of[(tr.get("监管文件编号") or "").strip()] = code
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            rfn = (row.get("监管文件编号") or "").strip()
            code = theme_of.get(rfn, "")
            if not code:
                continue
            pub = (row.get("发布日期") or "").strip()
            year = pub[:4] if len(pub) >= 4 and pub[:4].isdigit() else ""
            groups[code].append({
                "seq": 0,
                "year_reported": year,
                "title": (row.get("文件名称") or "").strip(),
                "doc_no": (row.get("发文字号") or "").strip(),
                "file_src": (row.get("文件来源") or "").strip(),
                "eff_status": (row.get("时效状态") or "").strip() or "valid",
                "real_year": int(year) if year.isdigit() else None,
                "监管文件编号": rfn,
            })
    for t in groups:
        gen_at = time.strftime("%Y-%m-%d %H:%M:%S")
        snap = _attr_snapshot(path)
        for i, r in enumerate(groups[t], start=1):
            r["seq"] = i
            r["generated_by"] = _GEN_BY
            r["generated_at"] = gen_at
            r["source_snapshot"] = snap
    return groups


def build(groups, theme, out_dir=DEFAULT_OUT, dry_run=False):
    """生成单主题 `_t{n}_base.json`，返回 (路径, 记录数)。"""
    recs = groups.get(theme, [])
    path = os.path.join(out_dir, f"_{theme.lower()}_base.json")
    if dry_run:
        return path, len(recs)
    os.makedirs(out_dir, exist_ok=True)
    # 原子写：同目录临时文件 + os.replace（N-8 纪律）
    tmp = os.path.join(out_dir, f".tmp_{theme.lower()}_base.json")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(recs, fh, ensure_ascii=False, indent=1)
    try:
        os.replace(tmp, path)
    except OSError:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(recs, fh, ensure_ascii=False, indent=1)
    return path, len(recs)


def main():
    ap = argparse.ArgumentParser(description="由权威归属表生成主题清单 base.json（C-9）")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--all", action="store_true", help="生成 T1—T10（默认）")
    g.add_argument("--theme", choices=THEMES, help="仅生成指定主题")
    ap.add_argument("--out-dir", default=DEFAULT_OUT, help=f"输出目录（默认 {DEFAULT_OUT}）")
    ap.add_argument("--csv", default=ATTR_CSV, help="权威归属表路径")
    ap.add_argument("--dry-run", action="store_true", help="仅预览，不写盘")
    ap.add_argument("--check", action="store_true",
                    help="校验模式：与现有 base.json 逐条比对，差异 exit 1（不写盘）")
    args = ap.parse_args()

    groups = load_attr(args.csv)
    themes = THEMES if (args.all or not args.theme) else [args.theme]

    # ---- 校验模式（C-9 完整性：可复现 + 可核对，防孤儿产物再次漂移）----
    if args.check:
        diffs = []
        for t in themes:
            path, n = build(groups, t, args.out_dir, dry_run=True)   # 不写盘，仅算期望
            if not os.path.exists(path):
                diffs.append({"theme": t, "issue": "文件不存在", "want": n, "have": 0})
                continue
            have = json.load(open(path, encoding="utf-8"))
            # R10：provenance 键不参与一致性（重跑刷新时间/快照属预期）；比较须两侧同构（磁盘旧数据缺键时按无键比）
            want = [strip_prov(r) for r in groups[t]]
            hv = [strip_prov(r) for r in have] if have and isinstance(have[0], dict) and "generated_by" in have[0] else have
            if hv != want:
                diffs.append({"theme": t, "issue": "内容与归属表投影不一致",
                              "want": len(groups[t]), "have": len(have)})
        if diffs:
            for d in diffs:
                print("  ❌", d)
            print("❌ base.json 存在差异，可运行 --all 重新生成")
            return 1
        print("✅ base.json 与归属表投影完全一致")
        return 0

    total = 0
    print(f"来源：{args.csv}")
    for t in themes:
        path, n = build(groups, t, args.out_dir, dry_run=args.dry_run)
        total += n
        flag = "[预览] " if args.dry_run else ""
        print(f"  {flag}{os.path.basename(path)}: {n} 条")
    print(f"合计 {total} 条" + ("（--dry-run 未写盘）" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
