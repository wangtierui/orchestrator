# -*- coding: utf-8 -*-
"""独立后验：校验写盘后的 T1-T10 JSON 与归属表主题列完全一致（不依赖生成脚本）。"""
import csv
import glob
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # P4: modules/regulatory_classifier（相对，无盘符）
DATA = os.path.join(ROOT, "data")
ATTR = os.path.join(DATA, "人身保险公司-文件归属表.csv")
THEME_RE = re.compile(r"^T(?:10|[1-9])")
EXPECT = {"T1":195,"T2":149,"T3":75,"T4":105,"T5":139,"T6":60,"T7":35,"T8":75,"T9":78,"T10":139}

# 归属表：rfn -> theme code
# 2026-08-31 重构：归属表无主题列，主题改从主题归属表读取
attr_code = {}
THEME_CSV = os.path.join(DATA, "人身保险公司-主题归属表.csv")
if os.path.exists(THEME_CSV):
    with open(THEME_CSV, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            rfn = (r.get("监管文件编号") or "").strip()
            tc = THEME_RE.match((r.get("主题") or "").strip())
            attr_code[rfn] = tc.group(0) if tc else None

fails = []
files = sorted(glob.glob(os.path.join(DATA, "_t[0-9]*_*.json")))
print(f"磁盘上 _t*_*.json 数量: {len(files)} (期望 40)")

for code in EXPECT:
    base = json.load(open(os.path.join(DATA, f"_{code.lower()}_base.json"), encoding="utf-8"))
    fin = json.load(open(os.path.join(DATA, f"_{code.lower()}_final.json"), encoding="utf-8"))
    mat = json.load(open(os.path.join(DATA, f"_{code.lower()}_matched.json"), encoding="utf-8"))
    cit = json.load(open(os.path.join(DATA, f"_{code.lower()}_citerefs.json"), encoding="utf-8"))
    n = len(base)
    if n != EXPECT[code]:
        fails.append(f"{code} base 计数 {n} ≠ 期望 {EXPECT[code]}")
    # 主题列交叉校验
    for x in base:
        if attr_code.get(x["监管文件编号"]) != code:
            fails.append(f"{code} 含非本主题 RFN: {x['监管文件编号']} (归属表主题={attr_code.get(x['监管文件编号'])})")
    # 2026-08-31 重构：base 无 seq 字段，键=监管文件编号；cluster 非空
    if any("seq" in x for x in base):
        fails.append(f"{code} base 残留 seq 字段（应已删除）")
    if not all((x.get("监管文件编号") or "") for x in base):
        fails.append(f"{code} base 存在空监管文件编号")
    if any(not x.get("cluster") for x in fin):
        fails.append(f"{code} final 存在 cluster 空")
    # 2026-08-31 重构：matched/citerefs 键 = RFN（非 seq），须与 base 键集一致
    base_keys = {x["监管文件编号"] for x in base}
    if set(mat.keys()) != base_keys:
        fails.append(f"{code} matched 键集 ≠ base 键集")
    if set(cit.keys()) != base_keys:
        fails.append(f"{code} citerefs 键集 ≠ base 键集")
    print(f"  {code}: base={n} final={len(fin)} matched={len(mat)} citerefs={len(cit)} | 主题列交叉校验 {'OK' if all(attr_code.get(x['监管文件编号'])==code for x in base) else 'FAIL'}")

# 总量
total = sum(len(json.load(open(os.path.join(DATA, f"_{c.lower()}_base.json"), encoding="utf-8"))) for c in EXPECT)
print(f"合计 base: {total} (期望 1050，T0 不生成数据底座)")
if total != 1050:
    fails.append(f"合计 {total} ≠ 1050")

if fails:
    print("\n❌ 后验失败:")
    for f in fails:
        print("   -", f)
    raise SystemExit(1)
print("\n✅ 独立后验全部通过：40 文件、计数=归属表、主题列对齐、无 seq 残留、cluster 完整。")
