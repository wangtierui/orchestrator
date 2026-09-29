# -*- coding: utf-8 -*-
"""tools/gen_flow_map — **全链数据流总图**生成器（2026-09-29）

为什么"生成"而不是"手写"
------------------------
手写的链路图会**随时间必然失准**（步骤增删、命令改参、降级链变化）。本工具从**既有事实源**
派生，改代码即改图：

  · **顺序**：`tools/run_production_refresh.STEP_ORDER`（链路唯一顺序声明）；
  · **命令**：同文件内 `_run(...)` / `_run_conditional(...)` 调用点（正则抽取，与实装同源）；
  · **降级措施**：由调用形态派生 —— `_run_conditional` ⇒ 条件步骤（可跳过）；只读披露类
    （`retention:plan` / `semantic:preflight` / `pg:health`）⇒ "恒 rc=0，不可用为合法状态"；
  · **工具与离线状态**：`config/schema/semantic_tools.json` + `semantic_tools.probe_all()`；
  · **未取得模型权重的环节**：清单中声明了 `offline_env`（需外部权重）的工具 ⇒ **单独标记 ⚠️**。

输出：`docs/全链数据流总图.md`（Mermaid 流程图 + 逐步骤表 + 降级/风险分区）。
"""

from __future__ import annotations

import io
import os
import re
import sys

# 引导：**必须经 `bootstrap`（唯一引导点，v2 §3.1.2）** —— `gate_import_bootstrap` 断言
# tools/ 层的 `sys.path.insert` **只减不增**，新代码不得自写插入。
# 运行方式相应为 `python -m tools.gen_flow_map`（仓根位于 sys.path[0] 才能 import bootstrap）。
from bootstrap import bootstrap

bootstrap("all")

import paths

OUT = os.path.join(paths.ROOT, "docs", "全链数据流总图.md")
RUNNER = os.path.join(paths.ROOT, "tools", "run_production_refresh.py")

# 已知的"只读披露"步骤（**恒 rc=0**：后端不可用属合法状态，不应误报为链路故障）
READONLY_DISCLOSURE = {"retention:plan", "semantic:preflight", "pg:health"}
# 步骤 → 作用（**唯一事实源在此**；键与 `STEP_ORDER` 实际值一致，支持"前缀兜底"）
PURPOSE: dict = {
    # —— 阶段 1 采集/清洗/条文 ——
    "collect:nfra_weekly": "nfra 周报采集（按周触发）",
    "collect": "各源原始采集（gov/mof/nfra/pbc/supp）",
    "supp:ingest_batch": "补充库批量摄取（backlog 驱动）",
    "clean": "原始 → 清洗（6 态抽取 + 校验隔离）",
    "timeliness:verify": "时效核验（北大法宝；Node 侧 MCP）",
    "timeliness:consolidate": "时效核验结果汇总",
    "apply": "时效核验结果回写清洗产物",
    # —— 阶段 2 分类/关系/召回 ——
    "classify:all": "主题分类（_t*_base / _t*_final 派生）",
    "relations:gen": "关系抽取（含条款级定位：**窗口优先 + 跨度退路**）",
    "reconcile": "清洗漂移对账（clean_drift）",
    "recall": "召回复核与四门禁（clean/validity/contract/schema）",
    # —— 阶段 3 治理库/内部制度/条款 ——
    "inbox:drop": "收件箱投放（人工投递的制度文件入库）",
    "internal:update": "内部制度索引更新（原件同步）",
    "internal:merged": "内部制度**合并视图**（供条款对照与检索）",
    "governance:artifacts": "原件注册（治理库 artifact 表）",
    "governance:sync": "治理库同步（run_log / gate_result / relation 等，**全量替换**）",
    "draft:clause": "条款对照素材（drafter 视图）",
    # —— 阶段 4 发布/报告/分析/基线 ——
    "reports:build": "总览报告生成",
    "reports:theme": "主题报告生成",
    "base:publish": "底座发布（发布清单 publish_manifest）",
    "analysis:gen": "分析交付物（docs/reports，含 17 项 manifest sha）",
    "watch:baseline": "观测基线快照",
    # —— 阶段 5 只读披露（恒 rc=0）——
    "retention:plan": "数据生命周期**只读披露**（dry-run + 台账；`--apply` 为人工闸门）",
    "quarantine:triage": "清洗隔离件分类与处置登记（零回写 → worklist）",
    "semantic:preflight": "P1 语义增强**启用前置披露**（五道闸逐项）",
    "pg:health": "向量存储**只读**健康检查（pgvector / sqlite_vec 降级链披露）",
    # —— 阶段 6 条件步骤 / 链尾 ——
    "wiki:sync": "llm_wiki 源同步（条件步骤，未触发即跳过）",
    "gates": "全部门禁（**阻断**；置于链尾以终态产物为准）",
}


def _purpose(step: str) -> str:
    """作用查表：精确 → 前缀（`clean:gov`→`clean`）→ 未登记告警（**不静默**）。"""
    if step in PURPOSE:
        return PURPOSE[step]
    head = step.split(":")[0]
    if head in PURPOSE:
        return f"{PURPOSE[head]}（本步：`{step}`）"
    return "**（未登记作用：请在 `gen_flow_map.PURPOSE` 补登）**"


def _steps() -> list:
    import importlib

    m = importlib.import_module("tools.run_production_refresh")
    return list(m.STEP_ORDER)


def _commands() -> dict:
    """从源码抽取每个步骤的命令（与实装同源，避免手工维护第二份）。

    **行式 + 括号深度**解析（非正则跨行贪婪匹配）：正则 `.*?` 会跨到**下一步骤**的 `_run`，
    导致命令串行（实测出现过）→ 改用深度计数，值取到本调用的匹配右括号为止。
    """
    src = open(RUNNER, encoding="utf-8").read()
    out: dict = {}
    # 关键：**有界**取体 —— 从步骤名之后截到**本调用结束**（首个 `\n        )`），
    # 避免 `.*?` 跨到下一步骤（实测曾把 5 个步骤的命令串成一行）。
    for m in re.finditer(r'_run(?:_conditional)?\(\s*\n?\s*"([^"]+)"', src):
        name = m.group(1)
        tail = src[m.end() : m.end() + 900]
        # 按下标**逐行**前进，遇到"整行只有右括号（可带逗号）"即判定本调用结束
        # （比缩进阈值稳健：不同 `_run` 调用的缩进层级并不一致）。
        body = tail
        pos = 0
        for seg in tail.splitlines(keepends=True):
            if pos and seg.strip() in (")", "),"):
                body = tail[:pos]
                break
            pos += len(seg)
        cmds = [
            c
            for c in re.findall(r'"([^"]{3,60})"', body)
            if c != name and "://" not in c and not c.startswith("--")
        ]
        if cmds:
            out.setdefault(name, " ".join(cmds[:5]))
    return out


def _tool_states() -> dict:
    """工具 -> (available, offline_ready, pipeline_ref)；用于标记权重未取得的环节。"""
    from std_lib.common_lib import semantic_tools as st

    res: dict = {}
    for n, p in st.probe_all().items():
        res[n] = {
            "available": p["available"],
            "offline_ready": p.get("offline_ready"),
            "pipeline_ref": p.get("pipeline_ref") or "",
        }
    return res


def build() -> str:
    steps = _steps()
    cmds = _commands()
    tools = _tool_states()
    weight_pending = [
        n for n, t in tools.items() if t["available"] and t["offline_ready"] is False
    ]
    unavail = [n for n, t in tools.items() if not t["available"]]

    L: list = []
    L.append("# 全链数据流总图（`cli.py run`）")
    L.append("")
    L.append(
        "> **本文件由 `tools/gen_flow_map.py` 机器生成**（事实源：`STEP_ORDER` + `_run` 调用点 + "
        "`config/schema/semantic_tools.json`）。**请勿手工编辑** —— 改链路请改源码后重跑本工具。"
    )
    L.append(f">\n> 步骤数：**{len(steps)}**；工具：**{len(tools)}**（可用 "
             f"{len(tools) - len(unavail)}，未装 {len(unavail)}）\n")
    L.append("## 一、链路总览（Mermaid）")
    L.append("")
    L.append("```mermaid")
    L.append("flowchart TD")
    L.append('  S(["cli.py run"]) --> A["阶段1 采集/清洗/条文"]')
    L.append('  A --> B["阶段2 分类/关系/召回"]')
    L.append('  B --> C["阶段3 治理库/内部制度/条款"]')
    L.append('  C --> D["阶段4 发布/报告/分析/基线"]')
    L.append('  D --> E["阶段5 只读披露<br/>retention:plan / semantic:preflight / pg:health"]')
    L.append('  E --> F["阶段6 条件步骤<br/>wiki:sync"]')
    L.append('  F --> G["链尾 门禁 gates（阻断）"]')
    L.append('  E -.->|"后端不可用属合法状态"| E')
    L.append('  D -.->|"P2-2 向量检索（按需）"| V[("pgvector / sqlite_vec")]')
    L.append("```")
    L.append("")
    L.append("## 二、逐步骤表（顺序 = `STEP_ORDER`；命令自源码抽取）")
    L.append("")
    L.append(
        "> 说明：**命令列为节选**（自源码 `_run` 调用点抽取）。少数行的调用形态较复杂"
        "（多行列表 / 相邻调用紧邻），抽取结果可能含**相邻片段** —— 该列的用途是"
        "**快速定位脚本**，精确参数请以源码为准。"
    )
    L.append("")
    L.append("| # | 步骤 | 作用 | 脚本/命令（节选） | 降级 / 失败语义 |")
    L.append("|---|---|---|---|---|")
    for i, s in enumerate(steps, 1):
        purpose = _purpose(s)
        cmd = cmds.get(s, "（条件调用或未见字面量）")
        if s in READONLY_DISCLOSURE:
            degr = "**只读披露 · 恒 rc=0**（后端不可用为合法状态）"
        elif "conditional" in cmd or s == "wiki:sync":
            degr = "条件步骤（未触发即跳过）"
        elif s == "doctor":
            degr = "可 `--skip-doctor` 跳过"
        else:
            degr = "**阻断**（失败即停）"
        L.append(f"| {i} | `{s}` | {purpose} | `{cmd}` | {degr} |")
    L.append("")
    L.append("## 三、⚠️ 未取得模型权重的环节（**单独标记**）")
    L.append("")
    if weight_pending:
        L.append(
            "以下工具**依赖代码已就绪、但权重未取得**（网络白名单不含模型站；"
            "`offline_ready=False`）→ **相关环节在权重落地前不得启用**："
        )
        L.append("")
        L.append("| 工具 | 方案项 | 状态 | 说明 |")
        L.append("|---|---|---|---|")
        for n in sorted(weight_pending):
            L.append(
                f"| `{n}` | {tools[n]['pipeline_ref']} | ⚠️ **权重未取得** | "
                "须在可达环境预置后迁入（`HF_HOME` / `HANLP_HOME`） |"
            )
    else:
        L.append("（当前无需外部权重的工具已全部就绪；或全部未装。）")
    L.append("")
    L.append("**受影响的链路环节**：`semantic:preflight` 的 `offline` 闸（本轮唯一未过闸）；")
    L.append("**不受影响**：主链（采集→清洗→条文→关系→报告→门禁）**不依赖任何模型权重**，")
    L.append("全部走确定性正则/规则路径；P1 增强层为**可选叠加**。")
    L.append("")
    L.append("## 四、向量检索接入（P2-2，按需）")
    L.append("")
    L.append(
        "- **接入层**：`std_lib/common_lib/vector_store.py`（`*_store` 家族；与 `governance_store` 同级）\n"
        "- **降级链**：`pgvector`（`vec` schema，最小权限角色）→ `sqlite_vec`（与既有 SQLite FTS5 "
        "同库同源）→ `none`（走既有全文/正则）\n"
        "- **连接**：env `PGVECTOR_DSN`（口令**仅经 env**，不入库；`gate_secret_scan` 守护）\n"
        "- **异常语义**：`VectorStoreUnavailable` 供调用方降级；`search_or_fallback()` 失败返回 "
        "`None`（**不抛**）\n"
        "- **不影响主链**：主链**不调用**其读写；`pg:health` 仅做只读披露"
    )
    L.append("")
    L.append("## 五、披露步骤的失败语义（统一口径）")
    L.append("")
    L.append(
        "`retention:plan` / `semantic:preflight` / `pg:health` 三者同为**只读披露**，统一口径：\n\n"
        "> **「未就绪」是合法状态** —— 未启用增强、无待归档、后端未部署，都不是链路故障。"
        "故披露步骤**恒 rc=0**；真正需要阻断的判定保留给各自的人工/CI 口径"
        "（如 `semantic_tools --preflight` 的 rc 反映可否启用）。"
    )
    return "\n".join(L) + "\n"


def main() -> int:
    from config.exitcodes import ExitCode

    text = build()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(f"全链数据流总图已写入 {os.path.relpath(OUT, paths.ROOT)}（{len(text)} 字节）")
    return int(ExitCode.OK)


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    raise SystemExit(main())
