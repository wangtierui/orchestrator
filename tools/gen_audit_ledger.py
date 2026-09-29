# -*- coding: utf-8 -*-
"""tools/gen_audit_ledger — 把审计发现**逐个判定并台账化**（R-3/R-4/R-5 处置，2026-09-30）

为什么需要（而非只写在报告里）
------------------------------
审计会反复产出同一批"中低危"发现（硬编码日期、共享库预留 API、跨模块同类实现…）。
若每次只写进报告，下一轮审计**仍会重新列出**，人工要再判一遍 —— 判定被浪费。
本工具把**判定规则**固化：每条发现按规则归入 `需修 / 接受（附理由） / 待人工`，
产出 **`reports/审计判定台账_<date>.md`**（唯一台账）。

规则即口径（可复算）
--------------------
  · 硬编码-日期 `LOW`（同文件无 `now()/today()`）→ **接受**：属领域字面量（文件名日期规则/示例）；
  · 硬编码-日期 `MED`（同文件有 `now()`）          → **待人工**：可能真是"写死当前时间"；
  · 硬编码-绝对路径 `HIGH`                        → **需修**（破坏克隆可移植性）；
  · 实现重复：同文件/同包 → **需修（下批归并）**；跨模块 → **待人工**（须先核依赖方向纪律）；
  · 代码冗余：位于 `std_lib/**/common|*_utils|artifact_shape` → **接受**（共享库预留 API，
    "零引用"≠"该删"）；其余 → **待人工**；
  · 其余类别 → **需修**。
"""

from __future__ import annotations

import datetime
import io
import json
import os
import sys
from collections import Counter

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUDIT = os.path.join(ROOT, "reports", "_tmp", "audit.json")

SHARED_LIB = ("std_lib/", "artifact_shape", "_utils.py", "/common/")


def classify(x: dict) -> tuple:
    """→ (结论, 理由)"""
    cat, sev, where, det = x["cat"], x["sev"], x["where"], x["detail"]
    if cat == "硬编码":
        if "绝对路径" in det:
            return "需修", "绝对路径破坏克隆可移植性"
        if sev == "LOW":
            return "接受", "领域字面量（同文件无 now()/today()，属文件名日期规则或示例）"
        return "待人工", "同文件存在 now()/today()，需确认是否真为『写死当前时间』"
    if cat == "实现重复":
        files = {w.split(":")[0] for w in where.split(", ") if w}
        if len(files) == 1:
            return "需修", "同文件内重复 → 下批归并（低风险）"
        top = {f.split("/")[0] + "/" + (f.split("/")[1] if f.count("/") > 1 else "") for f in files}
        if len(top) == 1:
            return "需修", "同包内重复 → 下批归并（低风险）"
        return "待人工", "跨模块同类实现 → 须先核『依赖方向纪律』方可归并"
    if cat == "代码冗余":
        if any(k in where for k in SHARED_LIB):
            return "接受", "共享库**预留公共 API**：零引用 ≠ 该删（供门禁/测试/后续接线）"
        return "待人工", "非共享库的零引用公共函数 → 逐个判定『预留 vs 死码』"
    if cat in ("门禁失效", "数据源不唯一", "流程断点", "README 一致性"):
        return "需修", "属结构性问题，必须处置"
    return "需修", ""


def main() -> int:
    fs = json.load(open(AUDIT, encoding="utf-8"))
    rows = [(x, *classify(x)) for x in fs]
    cnt = Counter(r[1] for r in rows)
    today = datetime.date.today().isoformat()
    out_p = os.path.join(ROOT, "reports", f"审计判定台账_{today.replace('-', '')}.md")
    L = [
        f"# 审计发现**判定台账** · {today}",
        "",
        "> 生成：`python -m tools.gen_audit_ledger`（规则见模块 docstring，**可复算**）。",
        "> 目的：把中低危发现的**判定**固化下来，避免每轮审计重复人工判断。",
        "",
        f"- 发现总数 **{len(fs)}**；结论分布："
        + " / ".join(f"**{k} {cnt[k]}**" for k in ("需修", "待人工", "接受") if cnt.get(k)),
        "",
        "| 类别 | 严重级 | 位置 | 结论 | 理由 |",
        "| :--- | :--- | :--- | :--- | :--- |",
    ]
    order = {"需修": 0, "待人工": 1, "接受": 2}
    for x, verdict, why in sorted(rows, key=lambda r: (order[r[1]], r[0]["cat"], r[0]["where"])):
        L.append(
            f"| {x['cat']} | {x['sev']} | `{x['where'][:70]}` | **{verdict}** | {why} |"
        )
    with open(out_p, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")
    print(f"  台账已生成：{os.path.relpath(out_p, ROOT)}")
    print(f"  发现 {len(fs)}；结论：{dict(cnt)}")
    for v in ("需修", "待人工", "接受"):
        ex = [r[0]["where"] for r in rows if r[1] == v][:4]
        if ex:
            print(f"    {v} 例：{ex}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
