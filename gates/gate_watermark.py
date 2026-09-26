# -*- coding: utf-8 -*-
"""gate_watermark —— 产物水位一致性（第 17 道门禁，阶段 1，2026-09-18）

背景（`reports/数据流转与存储交互优化方案_20260917.md` §4.5）：
    本仓原有的"产物新鲜度"判据依赖 **文件 mtime**（`gate_relations` 判据 8：
    `getmtime(产物) >= max(getmtime(输入)) + 2s`），存在两类误差：
      - **假阳**：touch / copy / 跨卷时间精度 → 误报陈旧；
      - **假阴**：`copy2` 保留 mtime 或输入被"原地重写但时间戳未推进"时会**漏报**。
    阶段 1 引入**水位（watermark）**：产物落盘时把"我是在哪个上游版本上算出来的"
    写进治理库（`watermark.inputs_json`），门禁改为**版本比对**——零容差、可解释
    （直接报出缺哪条依赖边），且与 mtime 无关。

判据：
    1) 治理库未启用（`data/governance.db` 不存在）→ **PASS + note**。
       阶段 1 是"只增不减"的增量判据：未运行过刷新链/未建库的环境（含刚克隆的无数据
       仓库）不应因此被阻断——与"数据门禁在无数据时 FAIL 属预期"是不同性质的判据。
    2) 治理库存在 → 逐条检查 `watermark` 表：
       - 每行须有非空 `produced_at` / `version`（缺即 FAIL）；
       - `inputs_json` 须可解析为对象（坏即 FAIL）；
       - 每条声明的依赖边：上游**已登记**且版本不一致 → **FAIL**（上游已推进、产物未重跑）；
         上游**未登记** → 计入 `unregistered_deps` 披露，**不阻断**（过渡期覆盖度不足属预期）。

与既有门禁的关系：**新增、不替换**。`gate_relations` 判据 8（mtime）仍保留；
待阶段 4「判据切换」双判据并行验证无误后再移除旧判据。
"""

from __future__ import annotations

import os
import sys


def run() -> tuple[bool, dict]:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if root not in sys.path:
        sys.path.insert(0, root)

    from std_lib.common_lib import governance_store as gs  # noqa: PLC0415

    db = gs.db_path()
    if not gs.enabled():
        return True, {
            "enabled": False,
            "db": db,
            "problems": [],
            "note": "治理库未启用（阶段 1 未运行）——本判据跳过；"
            "启用：`python cli.py governance init` 后跑生产刷新链",
        }

    try:
        passed, detail = gs.check_dependencies()
    except Exception as e:  # noqa: BLE001  库损坏属实质问题 → FAIL 并给出可执行指引
        return False, {
            "enabled": True,
            "db": db,
            "problems": [f"治理库不可读：{type(e).__name__}: {e}"],
            "note": "治理库损坏时须显式处置（可删除后由刷新链重建；审计链以 exports/ 与 reports/ 为准）",
        }

    detail.setdefault("problems", [])
    return passed, detail
