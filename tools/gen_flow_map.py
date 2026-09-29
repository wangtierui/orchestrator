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


# 模型 → **链路节点绑定**（唯一事实源；图与表均由它派生）
#   step  ：挂接的链路步骤（须存在于 `STEP_ORDER`，或 `OND` 表示"按需节点、无链步骤"）
#   stage ：所属阶段（用于 Mermaid 连线到阶段节点）
#   role  ：在该节点做什么（一句话）
#   down  ：不可用时的降级措施（**必须显式**，不写"无"）
MODEL_BINDINGS: dict = {
    "hanlp": {
        "step": "clauses", "stage": "阶段1",
        "role": "条文**分句/结构增强**（ML 分句替代正则切分）",
        "down": "回退 `std_lib/scraper_std` 的规则分句（既有 SSOT，零依赖）",
    },
    "ltp": {
        "step": "clauses", "stage": "阶段1",
        "role": "条文分句（**hanlp 的二选一备选**，按质量择优）",
        "down": "同 hanlp；两者皆不可用即用规则分句",
    },
    "weknora_docreader": {
        "step": "clean", "stage": "阶段1",
        "role": "文档**版面分析**（25+ 格式渲染，补正文抽取）",
        "down": "回退 `crawler_common.extract_document_text`（既有 6 态）",
    },
    "youtu_embedding": {
        "step": "classify:all", "stage": "阶段2",
        "role": "**主题辅助裁定**（语义向量近邻投票，P1-1）",
        "down": "回退关键词/规则判定（既有主题分类器）",
    },
    "text2vec": {
        "step": "classify:all", "stage": "阶段2",
        "role": "同上（**已被 youtu_embedding 取代**，保留为备选）",
        "down": "优先 youtu_embedding；再退关键词规则",
    },
    "bertopic": {
        "step": "classify:all", "stage": "阶段2",
        "role": "**主题内子簇语义化**（P1-5，仅产出分析视图）",
        "down": "不做子簇（仅影响分析视图，不产事实源）",
    },
    "umap": {
        "step": "classify:all", "stage": "阶段2",
        "role": "bertopic 的降维依赖（**不单独使用**）",
        "down": "随 bertopic 一并跳过",
    },
    "hdbscan": {
        "step": "classify:all", "stage": "阶段2",
        "role": "bertopic 的聚类依赖（**不单独使用**）",
        "down": "随 bertopic 一并跳过",
    },
    "youtu_embedding@relations": {
        "step": "relations:gen", "stage": "阶段2",
        "role": "**关联语义档**（P1-2：依据/废止关系的语义近似判定）",
        "down": "回退正则关系抽取（既有 `common_lib/relations`）",
    },
    "aprcoie": {
        "step": "relations:gen", "stage": "阶段2",
        "role": "中文**开放信息抽取**（自动生成抽取模式，P3-1）",
        "down": "回退正则关系抽取（P3 可选，未启用不影响主链）",
    },
    "signalgraph": {
        "step": "relations:gen", "stage": "阶段2",
        "role": "**零 Token 确定性图构建**（P3-2）",
        "down": "回退关系三元组 → 既有图构件",
    },
    "pgvector": {
        "step": "OND", "stage": "阶段4",
        "role": "**向量检索**后端（P2-2；`vec` schema，最小权限角色）",
        "down": "降级 `sqlite_vec`（同库同源）→ 再退 SQLite FTS5 全文",
    },
    "sqlite_vec": {
        "step": "OND", "stage": "阶段4",
        "role": "向量检索**降级后端**（与既有 SQLite FTS5 同库同源）",
        "down": "回退 SQLite FTS5 全文检索（零服务依赖）",
    },
    "paradedb": {
        "step": "OND", "stage": "阶段4",
        "role": "向量+BM25 混合检索（**已被 pgvector 替代**，可选外部后端）",
        "down": "使用 pgvector / sqlite_vec（**不建议随仓分发**：AGPL-3.0）",
    },
}
# 同一工具可挂多个节点（如 youtu_embedding 同时用于主题与关系）→ 以 `<tool>@<用途>` 区分
_OND = "**按需节点**（无独立链步骤；由 `pg:health` 披露、业务按需调用）"
# 阶段 → Mermaid 阶段节点 ID（**必须用节点 ID**，不能取中文首字 —— 实测曾误取为"阶"）
STAGE_NODE = {"阶段1": "A", "阶段2": "B", "阶段3": "C", "阶段4": "D", "阶段5": "E", "阶段6": "F"}


def _model_status(name: str, p: dict) -> tuple:
    """模型状态 → `(标签, 原因, 是否已启用)`。**未启用也必须给出原因**（不静默）。"""
    if not p["available"]:
        cfg = MODEL_BINDINGS.get(name) or {}
        extra = ""
        if cfg.get("step") == "OND":
            extra = "（可选外部后端）"
        return "未装 · 未启用", f"依赖未安装{extra}", False
    if p.get("offline_ready") is False:
        return "已接入 · **未启用**", "**模型权重未取得**（白名单网络不含模型站）→ 启用前须预置", False
    if p.get("service_reachable") is False:
        return "已接入 · **未启用**", "客户端可用但**服务不可达**（未部署/未启动）", False
    # 服务型工具：端点未配置（`service_reachable is None` 且 kind=service）→ **未启用**
    # （此前会落入"已启用"分支 —— 实测 `paradedb` 因 psycopg 偶然可用而被误判为已启用，
    #  而其**服务从未部署**。客户端库在 ≠ 服务在，这正是 N-113 三态要区分的事实。）
    if p.get("kind") == "service" and p.get("service_reachable") is None:
        return "已接入 · **未启用**", "**服务未部署 / 端点未配置**", False
    if (name or "").startswith("text2vec"):
        return "已接入 · 备选", "已被 youtu_embedding 取代（保留为二选一备选）", False
    return "**已启用**", "依赖与权重均就绪，可被调用", True


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
    """工具 → **完整探测结果**（含 offline_ready / service_reachable / pipeline_ref）。

    供"模型节点入图"判定状态：**未启用也要给出原因**（权重未取得 / 服务未部署 / 未安装）。
    """
    from std_lib.common_lib import semantic_tools as st

    return {n: dict(p) for n, p in st.probe_all().items()}


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
    L.append("## 一、链路总览（Mermaid：**含全部接入模型节点**）")
    L.append("")
    L.append(
        "> 图中**每个接入模型都作为一个节点**挂在它所属阶段的链路节点上（虚线=挂接关系，"
        "边标签为**具体步骤**）；**无论是否启用均已入图**，并以样式区分："
        "**实线绿=已启用**、**虚线灰=未启用**。"
    )
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
    L.append("")
    # —— 模型节点：按绑定挂到阶段节点；**未启用同样入图** ——
    on_names: list[str] = []
    off_names: list[str] = []
    for name in sorted(MODEL_BINDINGS):
        tool = name.split("@")[0]
        p = tools.get(tool)
        if p is None:
            continue
        label, _reason, enabled = _model_status(tool, p)
        cfg = MODEL_BINDINGS[name]
        node = "M_" + re.sub(r"\W+", "_", name)
        clean_label = label.replace("**", "").replace(" · ", "·")
        L.append(
            f'  {STAGE_NODE.get(cfg["stage"], "A")} -.->|"{cfg["step"]}"| '
            f'{node}["{tool}<br/>{clean_label}"]'
        )
        (on_names if enabled else off_names).append(node)
    L.append("")
    L.append("  classDef on fill:#d7f2df,stroke:#2e7d32,stroke-width:1px")
    L.append("  classDef off fill:#f2f2f2,stroke:#8a8a8a,stroke-dasharray:4 2")
    if on_names:
        L.append("  class " + ",".join(on_names) + " on")
    if off_names:
        L.append("  class " + ",".join(off_names) + " off")
    L.append("```")
    L.append("")
    L.append(
        f"图例：**已启用 {len(on_names)} 个**（实线绿）／**未启用 {len(off_names)} 个**（虚线灰，"
        "原因见 §三）—— **未启用模型同样作为节点存在**，便于在链路上定位其将来挂接的位置。"
    )
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
    L.append("## 三、模型 ↔ 链路节点定位表（**全部接入模型，含未启用**）")
    L.append("")
    L.append(
        "> 纪律：**每个接入模型都必须在此表定位到链路节点**；未启用者须给出**原因**与**降级措施**"
        "（不得留空、不得写「无」）。绑定事实源：`gen_flow_map.MODEL_BINDINGS`。"
    )
    L.append("")
    unbound = sorted(set(tools) - {k.split("@")[0] for k in MODEL_BINDINGS})
    if unbound:
        L.append(f"> ⚠️ **未绑定模型的工具**（须在 `MODEL_BINDINGS` 补登）：`{unbound}`")
        L.append("")
    L.append("| 模型 | 挂接链路节点 | 阶段 | 在该节点做什么 | 状态 | 未启用原因 | 降级措施 |")
    L.append("|---|---|---|---|---|---|---|")
    n_on = n_off = 0
    for name in sorted(MODEL_BINDINGS):
        tool = name.split("@")[0]
        p = tools.get(tool)
        if p is None:
            continue
        label, reason, enabled = _model_status(tool, p)
        cfg = MODEL_BINDINGS[name]
        step = cfg["step"]
        node = _OND if step == "OND" else f"`{step}`"
        if not enabled:
            n_off += 1
        else:
            n_on += 1
        mark = "⚠️ " if p.get("offline_ready") is False else ""
        extra = "" if name == tool else "（`@`：同工具的第二用途）"
        L.append(
            f"| `{name}`{extra} | {node} | {cfg['stage']} | {cfg['role']} | "
            f"{mark}{label} | {reason} | {cfg['down']} |"
        )
    L.append("")
    L.append(
        f"合计 **{n_on + n_off}** 个模型节点（**已启用 {n_on}** ／ **未启用 {n_off}**）—— "
        "两者**均已入图**（§一），未启用者标注了将来挂接的确切位置。"
    )
    L.append("")
    L.append("### 3.1 ⚠️ 未取得模型权重的环节（**单独标记**）")
    L.append("")
    if weight_pending:
        L.append(
            "以下工具**代码依赖已就绪、但权重未取得**（网络白名单不含模型站；`offline_ready=False`）"
            "→ **相关环节在权重落地前不得启用**："
        )
        L.append("")
        L.append("| 工具 | 挂接节点 | 方案项 | 预置方式 |")
        L.append("|---|---|---|---|")
        for name in sorted(MODEL_BINDINGS):
            tool = name.split("@")[0]
            if tool not in weight_pending:
                continue
            cfg = MODEL_BINDINGS[name]
            node = _OND if cfg["step"] == "OND" else f"`{cfg['step']}`"
            L.append(
                f"| `{name}` | {node} | {tools[tool]['pipeline_ref']} | "
                "在可达环境预置后迁入（`HF_HOME` / `HANLP_HOME`） |"
            )
    else:
        L.append("（当前无「已装但权重未取得」的工具。）")
    L.append("")
    L.append("**受影响的链路环节**：`semantic:preflight` 的 `offline` 闸（当前唯一未过闸）；")
    L.append("**不受影响**：主链（采集→清洗→条文→关系→报告→门禁）**不依赖任何模型权重**，")
    L.append("全部走确定性正则/规则路径；上表全部模型均为**可选叠加**，缺失即按「降级措施」列回退。")
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
