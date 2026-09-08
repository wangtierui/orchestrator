# -*- coding: utf-8 -*-
"""
verify_regulatory_citations.py — 制度文档监管引用强制核验工具（唯一事实源门禁）

用途（2026-08-27 治理决策落地）：
  internal_policy_drafter 后期制定/修订制度时，凡引用政府监管文件，**强制**：
    1) 数据来源 = regulatory_classifier（RFN 唯一事实源，经 rfn.get_index() 解析）；
    2) 发文字号/文件名称 必须能在 classifier 命中（禁止臆造文号/标题）；
    3) R-01~R-33 交叉键必须经 docs/监管文件编号与分类对齐表 → RFN → classifier 逐环可验；
    4) 效力状态以 classifier 时效状态列为准（含北大法宝核验 ≤90 日复用）。

校验维度（对输入 md 文档）：
  - R 引用（R-01~R-33）：对齐表映射 RFN → classifier is_valid + 名称/文号与权威一致（**门禁**）
  - 发文字号引用：提取〔20xx〕N号/[20xx]N号/令20xx年第N号/国务院令第N号 核心 → **文号签名
    （年份+序号）主匹配** classifier（对前缀变体如"废止/替代/由"+机关名免疫；0 候选=疑似臆造→FAIL，
    >1 候选=INFO 列出）
  - 书名号标题引用：包含式匹配（INFO，不参与门禁，内部制度/简称仅提示）
  - 对齐表自检：全部 R 行 RFN 有效、名称/文号与权威一致（防对齐表漂移）

用法：
  python scripts/verify_regulatory_citations.py                    # 校验全部 docs/*.md（非严格，报告）
  python scripts/verify_regulatory_citations.py --strict           # 严格门禁：任一 R/文号未命中 → exit 1
  python scripts/verify_regulatory_citations.py --file docs/<f>    # 单文件
  退出码：0 = 通过（strict 下无未命中）/ 1 = 存在未命中或对齐表漂移
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))           # modules/.../internal_policy_drafter/scripts
ROOT = os.path.dirname(HERE)                                  # internal_policy_drafter/
DOCS = os.path.join(ROOT, "docs")
ALIGN = os.path.join(DOCS, "监管文件编号与分类对齐表.md")

# P7（2026-09-08）：同仓引导（R4 无盘符）——orchestrator 根（paths）+ classifier 模块根（rfn）
# get_index 延迟到 Verifier() 内加载（顶层 import 在部分调用上下文 rfn 解析异常；函数级延迟与 gates 同款）
_MODULES = os.path.dirname(os.path.dirname(ROOT))            # modules/
_ORCH_ROOT = os.path.dirname(_MODULES)
_CLASSIFIER_ROOT = os.path.join(_MODULES, "regulatory_classifier")
for _p in (_ORCH_ROOT, _CLASSIFIER_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

R_PAT = re.compile(r"\bR-(\d{2})\b")
# 发文字号核心：〔20xx〕N号 / [20xx]N号 / （20xx）N号 / 令20xx年第N号 / 国务院令第N号
DOCNO_CORE_PAT = re.compile(
    r"[〔\[(（]\s*(\d{4})\s*[〕\])）]\s*(\d+)\s*号?"
    r"|令\s*(\d{4})\s*年第\s*(\d+)\s*号"
    r"|国务院令第(\d+)号")
TITLE_PAT = re.compile(r"《([^《》]{2,40})》")
# 监管发文机关词表：括号式文号前存在这些词才判定为"监管文件文号"进入门禁；
# 无机关词前缀的裸文号（如〔2023〕687号）为内部制度 OA 文号，仅 INFO 不门禁。
# 新增监管机关时在此扩展（按最长优先匹配）。
ORGAN_PREFIXES = (
    "国家金融监督管理总局办公厅", "中国银行保险监督管理委员会办公厅", "中国保险监督管理委员会办公厅",
    "国家金融监督管理总局", "中国银行保险监督管理委员会", "中国保险监督管理委员会", "银保监会办公厅",
    "中国人民银行等八部门公告", "最高人民法院", "国务院办公厅", "人民银行", "银保监会", "保监会",
    "银监发", "银监办发", "银监通", "银监办通", "银监复", "银监函",
    "保监发", "保监厅发", "保监产险", "保监财会", "保监稽查", "保监消保", "保监厅函", "保监复", "保监函",
    "银保监发", "银保监办发", "金办发", "金办便函", "金规", "银发", "国发", "国办发", "财金", "发改",
    "银监会令", "保监会令", "银保监会令", "国务院令", "证监会", "外汇局", "网信办", "知识产权局",
    "工信部", "市场监管总局", "中保协", "八部门公告",
)


def _norm_docno(s):
    return re.sub(r"[〔\[\]（）()〕\s]", "", s or "").rstrip("号")


def _digits(s):
    return re.sub(r"\D", "", s or "")


def load_align_map():
    """解析 对齐表 → {R号: {rfn, name, docno, timeliness}}。"""
    out = {}
    for ln in open(ALIGN, encoding="utf-8"):
        m = re.match(r"^\|\s*R-(\d{2})\s*\|\s*(RFN-[0-9a-f]{16})\s*\|\s*([^|]+?)\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|", ln)
        if m:
            out["R-%s" % m.group(1)] = {"rfn": m.group(2), "name": m.group(3).strip(),
                                        "docno": m.group(4).strip(),
                                        "timeliness": m.group(6).strip()}
    return out


# 时效状态受控枚举（规范 v3：7 值英文；对齐表时效列必须属于该集合）
TIMELINESS_SET = frozenset({"valid", "amended", "repealed", "partially_repealed",
                            "expired", "pending", "uncertain"})


class Verifier:
    def __init__(self):
        from rfn import get_index  # noqa: PLC0415  延迟加载（见 P7 引导注）
        self.idx = get_index()
        self.align = load_align_map()

    # ---- 对齐表自检 ----
    def check_align(self):
        issues, warns = [], []
        for r, row in sorted(self.align.items()):
            rec = self.idx.by_rfn(row["rfn"])
            if not rec:
                issues.append((f"对齐表 {r}", f"RFN {row['rfn']} 在 classifier 不存在（漂移）"))
                continue
            if row["name"] and row["name"] != rec.get("文件名称", ""):
                issues.append((f"对齐表 {r}",
                               f"名称不一致: 对齐表[{row['name'][:20]}] vs 权威[{rec.get('文件名称', '')[:20]}]"))
            if row["docno"] and row["docno"] not in ("N/A", "—", "-", "") \
                    and _norm_docno(row["docno"]) != _norm_docno(rec.get("发文字号", "")):
                warns.append((f"对齐表 {r}",
                              f"文号样式差异: 对齐表[{row['docno'][:24]}] vs 权威[{rec.get('发文字号', '')[:24]}]（以权威为准）"))
        return issues, warns

    # ---- 对齐表时效受控校验（规范 v3 P3：时效列必须为 7 值英文枚举，且与 classifier 一致） ----
    def check_timeliness(self):
        issues, warns = [], []
        for r, row in sorted(self.align.items()):
            cur = row["timeliness"]
            rec = self.idx.by_rfn(row["rfn"])
            authoritative = (rec or {}).get("时效状态", "").strip()
            if not cur:
                issues.append((f"对齐表 {r}", "时效列为空（应填 7 值英文枚举）"))
                continue
            if cur not in TIMELINESS_SET:
                issues.append((f"对齐表 {r}",
                               f"时效值非受控枚举: {cur!r}（合法: {sorted(TIMELINESS_SET)}）"))
                continue
            if authoritative and cur != authoritative:
                warns.append((f"对齐表 {r}",
                              f"时效与 classifier 不一致: 对齐表[{cur}] vs 权威[{authoritative}]（以权威为准）"))
        return issues, warns

    # ---- 发文字号签名候选 ----
    def _docno_candidates(self, year=None, seq=None, gwy_seq=None):
        rows = self.idx.rows()
        if gwy_seq:
            return [r for r in rows
                    if "国务院令" in (r.get("发文字号") or "")
                    and str(gwy_seq) in _digits(r.get("发文字号"))]
        sig = "%s%s" % (year, seq)
        if len(sig) < 5:
            return []
        out = []
        for r in rows:
            ds = _digits(_norm_docno(r.get("发文字号", "")))
            if ds.endswith(sig):
                out.append(r)
        return out

    # ---- 文档核验（per-file 隔离） ----
    def check_file(self, path):
        base = os.path.basename(path)
        lines = open(path, encoding="utf-8").read().splitlines()
        issues, warns = [], []
        r_ok = d_ok = d_internal = r_miss = d_miss = 0

        for i, ln in enumerate(lines, 1):
            for m in R_PAT.finditer(ln):
                r = "R-%s" % m.group(1)
                row = self.align.get(r)
                if not row:
                    issues.append((f"{base}:L{i}", f"R 编号 {r} 不在对齐表（R-01~R-43 之外）"))
                    continue
                if self.idx.by_rfn(row["rfn"]):
                    r_ok += 1
                else:
                    r_miss += 1
                    issues.append((f"{base}:L{i}", f"{r} → RFN {row['rfn']} 未命中 classifier"))

        for i, ln in enumerate(lines, 1):
            for m in DOCNO_CORE_PAT.finditer(ln):
                year, seq = (m.group(1) or m.group(3), m.group(2) or m.group(4))
                gwy = m.group(5)
                if gwy:
                    # 国务院令 恒为监管文件
                    cands = self._docno_candidates(gwy_seq=gwy)
                else:
                    # 括号式：前缀含机关词 → 监管文件（门禁）；裸文号 → 内部制度 OA（INFO）
                    pre = ln[max(0, m.start() - 10):m.start()]
                    organ = next((p for p in ORGAN_PREFIXES if pre.endswith(p)), None)
                    if not organ:
                        d_internal += 1
                        warns.append((f"{base}:L{i}",
                                      f"裸文号（内部制度 OA 文号，跳过门禁）: {year}年第{seq}号"))
                        continue
                    cands = self._docno_candidates(year=year, seq=seq)
                if not cands:
                    d_miss += 1
                    shown = ("国务院令第%s号" % gwy) if gwy else "%s年第%s号" % (year, seq)
                    issues.append((f"{base}:L{i}", f"发文字号未命中 classifier（疑似臆造/未收录）: {shown}"))
                elif len(cands) == 1:
                    d_ok += 1
                else:
                    d_ok += 1
                    warns.append((f"{base}:L{i}",
                                  f"发文字号多候选（年份+序号同名号）: {sorted(x['监管文件编号'] for x in cands)[:4]}"))

        for i, ln in enumerate(lines, 1):
            for t in TITLE_PAT.findall(ln):
                nt = re.sub(r"[《》\s]", "", t)
                hit = None
                for row in self.idx.rows():
                    rt = re.sub(r"[《》\s]", "", row.get("文件名称", ""))
                    if nt and rt and (nt == rt or nt in rt or rt in nt):
                        hit = row
                        break
                if not hit:
                    warns.append((f"{base}:L{i}",
                                  f"书名号标题未在 classifier 命中（内部制度/简称/上位法缩称）: 《{t[:24]}》"))
        return {"R": r_ok, "R_miss": r_miss, "docno": d_ok, "docno_miss": d_miss,
                "internal_oa": d_internal}, issues, warns

    @staticmethod
    def report(path, stat, issues, warns, strict):
        print(f"\n== {os.path.relpath(path, ROOT)} ==")
        print(f"  R 引用命中 {stat['R']} / 未命中 {stat['R_miss']} | 监管文号命中 {stat['docno']} / "
              f"未命中 {stat['docno_miss']} | 内部 OA 文号(跳过) {stat['internal_oa']}")
        for loc, msg in warns:
            print(f"  [INFO] {loc}: {msg}")
        for loc, msg in issues:
            print(f"  [FAIL] {loc}: {msg}")
        return (len(issues) > 0) and strict


def main():
    ap = argparse.ArgumentParser(description="制度文档监管引用强制核验（唯一事实源门禁）")
    ap.add_argument("--file", default="", help="单文件（默认全部 docs/*.md）")
    ap.add_argument("--strict", action="store_true", help="严格门禁：R/文号未命中或对齐表漂移 → exit 1")
    args = ap.parse_args()

    ver = Verifier()
    if len(ver.align) != 43:
        print(f"[verify] WARN 对齐表解析 R 行数={len(ver.align)}（预期 43，R-01~R-43 主册规模，2026-09-03 扩展），请检查对齐表格式")
    a_issues, a_warns = ver.check_align()
    for loc, msg in a_issues:
        print(f"[FAIL] {loc}: {msg}")
    for loc, msg in a_warns:
        print(f"[INFO] {loc}: {msg}")
    if a_issues:
        print(f"[verify] 对齐表漂移 {len(a_issues)} 项" + ("（strict 门禁拦截）" if args.strict else ""))
        if args.strict:
            return 1
    elif not a_warns:
        print(f"[verify] 对齐表核验：{len(ver.align)} 个 R 行 RFN 全部有效、名称/文号与权威一致")

    # 对齐表时效受控校验（规范 v3 P3）
    t_issues, t_warns = ver.check_timeliness()
    for loc, msg in t_issues:
        print(f"[FAIL] {loc}: {msg}")
    for loc, msg in t_warns:
        print(f"[INFO] {loc}: {msg}")
    if t_issues:
        print(f"[verify] 对齐表时效 {len(t_issues)} 项问题（{'strict 门禁拦截' if args.strict else '非严格，仅报告'}）")
        if args.strict:
            return 1
    elif not t_warns:
        print(f"[verify] 对齐表时效核验：{len(ver.align)} 个 R 行时效均受控且与 classifier 一致")

    files = [args.file] if args.file else sorted(set(glob.glob(os.path.join(DOCS, "*.md")) + glob.glob(os.path.join(DOCS, "**", "*.md"), recursive=True)))
    total_issues = 0
    for f in files:
        if not os.path.exists(f):
            print(f"[verify] 文件不存在: {f}")
            continue
        st, issues, warns = ver.check_file(f)
        total_issues += len(issues)
        if ver.report(f, st, issues, warns, args.strict):
            pass  # 保持累积计数
    if total_issues:
        print(f"\n[verify] ❌ 共 {total_issues} 项门禁问题（{'strict 已拦截' if args.strict else '非严格，仅报告'}）")
        return 1 if args.strict else 0
    print(f"\n[verify] ✅ 全部引用命中 regulatory_classifier 唯一事实源（{len(files)} 文件，R/文号 0 未命中）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
