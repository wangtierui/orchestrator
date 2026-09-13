# -*- coding: utf-8 -*-
"""
gates/gate_original_resolvable — 内部制度「主索引 ↔ 原件库」路径可解析性门禁（2026-09-13）

背景（`reports/内部制度原件双份存储与索引漂移分析_20260913.md` · §4.3/§4.4）
--------------------------------------------------------------------------
`internal_policy_index.json` 的 `relative_path` 是**运行期回读原件**的定位依据
（`internal_policy_base.extract.reocr_backfill` 按 `original_path = originals/<relative_path>`
打开文件）。实测曾出现：957 条索引中**仅 443 条可解析**，514 条指向源目录重组前的旧路径，
而 `reocr` 对缺原件**静默 continue** → 提质重建覆盖率事实上只有 46%，却**无任何门禁发现**。
本门禁使该类失配不可能再静默存在。

判据
----
1. **制度正文类记录必须 100% 可解析**（pdf/doc/docx）——任一不可解析即 FAIL；
2. **非制度正文（表格类 xls/xlsx）** 允许存在**已登记**的失效台账，但其数量
   **不得超过登记基线** `KNOWN_NON_POLICY_BASELINE`（只减不增，防继续恶化）；
3. **索引缺失 → FAIL**（对齐 A-07「输入缺失不得空跑放行」，不静默放行）。

处置入口
--------
  python tools/reconcile_original_paths.py            # dry-run 查看漂移分类
  python tools/reconcile_original_paths.py --apply    # 按内容对账重定位（含备份）
"""
from __future__ import annotations

import json
import os

import paths

_IPB = os.path.join(paths.MODULES_DIR, "internal_policy_base")
_INDEX = os.path.join(_IPB, "data", "internal_policy_index.json")
_ORIGINALS = os.path.join(_IPB, "data", "originals")

# 非制度正文（表格类）失效台账的**登记基线**（2026-09-13 首次登记 = 24）。
# 依据：本语料制度正文载体为 pdf/doc/docx；全部 xls/xlsx 均为附表/台账/清单
#     （实测索引 102 条 xls/xlsx 无一为制度正文）。
# 收敛路径：P2（摄取侧按扩展名过滤 + 台账显式排除）落地后，本基线应下调直至 0。
KNOWN_NON_POLICY_BASELINE = 24
NON_POLICY_EXTS = {".xls", ".xlsx"}


def run():
    if not os.path.exists(_INDEX):
        return False, {"error": f"内部制度主索引不存在：{_INDEX}"
                                "；门禁未实检，不得视为通过（先运行 internal index 摄取）"}
    try:
        with open(_INDEX, encoding="utf-8") as fh:
            records = json.load(fh).get("records", [])
    except (OSError, ValueError) as e:  # noqa: BLE001
        return False, {"error": f"索引不可读：{e!r}"}

    ok = 0
    unresolvable_policy, non_policy = [], []
    for r in records:
        rel = (r.get("relative_path") or "").replace("/", os.sep)
        if rel and os.path.exists(os.path.join(_ORIGINALS, rel)):
            ok += 1
            continue
        ext = os.path.splitext(r.get("file_name") or rel)[1].lower()
        (non_policy if ext in NON_POLICY_EXTS else unresolvable_policy).append(r)

    problems = []
    if unresolvable_policy:
        problems.append(f"制度正文类原件不可解析 {len(unresolvable_policy)} 条（应为 0；"
                        "运行 tools/reconcile_original_paths.py 对账重定位）")
    if len(non_policy) > KNOWN_NON_POLICY_BASELINE:
        problems.append(f"非正文表格类失效台账 {len(non_policy)} 条 > 登记基线 "
                        f"{KNOWN_NON_POLICY_BASELINE}（疑似新增漂移；须先对账，再评估是否收紧基线）")

    detail = {
        "records": len(records), "resolvable": ok,
        "unresolvable_policy": len(unresolvable_policy),
        "non_policy_sheets": len(non_policy),
        "non_policy_baseline": KNOWN_NON_POLICY_BASELINE,
        "examples": [f"{r.get('ipn')} | {(r.get('file_name') or '')[:60]}"
                     for r in unresolvable_policy[:10]],
        "problems": problems,
        "note": "判据=制度正文 100% 可解析 + 表格类失效台账不超登记基线（只减不增）",
    }
    return (not problems), detail
