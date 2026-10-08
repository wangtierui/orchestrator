# -*- coding: utf-8 -*-
"""tools/gen_flow_map — **全链数据流总图**生成器（2026-09-29 建；2026-09-30 重写 N-165）

为什么「生成」而不是"手写"
------------------------
手写的链路图会**随时间必然失准**（步骤增删、命令改参、降级链变化）。本工具从**既有事实源**
派生，改代码即改图：

  · **顺序**：`tools.run_production_refresh.STEP_ORDER`（链路唯一顺序声明）；
  · **命令**：同文件内 `_run(...)` / `_run_conditional(...)` 调用点 —— **AST 抽取**（非正则），
    故能正确处理 **f-string 步骤名**（`f"clean:{src}"` 在循环中生成）与**多行参数列表**；
  · **降级 / 失败语义**：本模块 `STEP_FAILURE`（**逐步声明**）+ `DEGRADE_CHAINS`（**降级链总表**）；
  · **工具与就绪度**：`config/schema/semantic_tools.json` + `semantic_tools.probe_all()`；
  · **模型选型**：清单 `selection_policy`（**唯一事实源**，本模块不重复定义优先级）。

输出（N-165）
------------
**直接注入 `README.md` 的受管块**（`<!-- BEGIN/END GENERATED: 全链数据流总图 -->`），
**不再单独生成 `docs/全链数据流总图.md`**（用户要求：并入 README，不单独成文件）。

为什么用「受管块」而不是把整篇 README 交给生成器：README 还有大量**人工叙述**（概述/环境/纪律），
若全篇生成，人工内容会被覆盖；受管块让「机器事实」与"人工叙述"**各归其位**，
且**改链路 → 重跑本工具 → README 自动同步**（这正是「代码与文档一致」的落地方式）。
"""

from __future__ import annotations

import ast
import io
import os
import sys

# 引导：**必须经 `bootstrap`（唯一引导点，v2 §3.1.2）** —— `gate_import_bootstrap` 断言
# tools/ 层的 `sys.path.insert` **只减不增**，新代码不得自写插入。
# 运行方式相应为 `python -m tools.gen_flow_map`（仓根位于 sys.path[0] 才能 import bootstrap）。
from bootstrap import bootstrap

bootstrap("all")

import paths
from config.exitcodes import ExitCode

README = os.path.join(paths.ROOT, "README.md")
LEGACY_OUT = os.path.join(paths.ROOT, "docs", "全链数据流总图.md")  # N-165 起废弃
RUNNER = os.path.join(paths.ROOT, "tools", "run_production_refresh.py")

BEGIN = "<!-- BEGIN GENERATED: 全链数据流总图（由 tools/gen_flow_map.py 生成，勿手改） -->"
END = "<!-- END GENERATED: 全链数据流总图 -->"

# 已知的「只读披露」步骤（**恒 rc=0**：后端不可用属合法状态，不应误报为链路故障）
READONLY_DISCLOSURE = {"retention:plan", "semantic:preflight", "pg:health"}

# 步骤 → 作用（**唯一事实源在此**；键与 `STEP_ORDER` 实际值一致，支持「前缀兜底」）
PURPOSE: dict = {
    # —— 阶段 1 采集/清洗/条文 ——
    "collect:nfra_weekly": "nfra 周报采集（按周触发）",
    "collect": "各源原始采集（gov **按子源拆为 xzfgk/zhengceku 两独立步骤**、mof/nfra/pbc/supp）",
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
    # —— 阶段 2 的**只读分析视图**步骤（不写事实源）——
    "semantic:assist": "P1 语义增强**入链调用点**（嵌入→主题辅助裁定；**只产分析视图**）",
    "docreader:extract": "docreader **入链调用点**（版面解析 + 段落级定位；**只产分析视图**）",
    "semantic:preflight": "P1 语义增强**启用前置披露**（五道闸逐项）",
    "pg:health": "向量存储**只读**健康检查（pgvector / sqlite_vec 降级链披露）",
    # —— 阶段 6 条件步骤 / 链尾 ——
    "wiki:sync": "llm_wiki 源同步（条件步骤，未触发即跳过）",
    "gates": "全部门禁（**阻断**；置于链尾以终态产物为准）",
}

# 步骤 → **降级 / 失败语义**（N-165：原实现把非披露步骤一律写作"阻断（失败即停）" ——
# 这与实装不符：采集/清洗在 `--no-scrape` 或「无 raw 变化」时会**快跳**（SKIP 且 rc=0），
# 时效核验无 token 时**降级为 unavailable**，条件步骤**未触发即跳过**。此表为逐步声明的事实源。）
COLLECT_STEPS = {"collect:nfra_weekly", "collect", "supp:ingest_batch"}
CLEAN_STEPS = {"clean", "apply"}
STEP_FAILURE: dict = {
    "collect": "**阻断**（失败即停）；`--no-scrape` → **SKIP**（rc=0，不采集）",
    "collect:nfra_weekly": "**阻断**；未到周度触发 → **SKIP**（rc=0）",
    "supp:ingest_batch": "**阻断**；backlog 为空 → **SKIP**（rc=0）",
    "clean": "**阻断**；**超时按源体量配置**（`max(1800, raw_MB×2)`，上限 6h，N-150；"
             "系数 N-188 重标定）；无 raw 变化 → **SKIP**（rc=0）",
    "apply": "**阻断**；无核验结果可回写 → **SKIP**（rc=0）",
    "timeliness:verify": "**降级**：无 token / 工具缺失 → `verification_state=unavailable`"
                         "（**不误标**）；配额耗尽自动停（断点续跑）",
    "timeliness:consolidate": "**阻断**；无核验输入 → 空汇总（rc=0）",
    "recall": "**阻断**（四门禁不过即 FAIL，留人工）",
    "retention:plan": "**只读披露 · 恒 rc=0**（无待归档为合法状态；`--apply` 才是人工闸门）",
    "quarantine:triage": "**只读产出**（零回写；结果入 worklist，rc=0）",
    "semantic:preflight": "**只读披露 · 恒 rc=0**（增强未启用为合法状态；"
                          "真正的启用判定用 `semantic_tools --preflight` 的 rc）",
    "semantic:assist": "**只读分析视图**（增强层零硬依赖）：未启用（`usage_policy.enabled=false`）"
                       "或嵌入链不可用均为合法状态 → **rc=0**；**绝不写事实源**",
    "docreader:extract": "**只读分析视图**：docreader 不可用/自检不过 → **显式 SKIP（rc=0）**，"
                         "回退既有 6 态抽取；**不替换**抽取事实源（替换须先度量）",
    "pg:health": "**只读披露 · 恒 rc=0**（后端不可用为合法状态；实际读写按 `vector_store` 降级链）",
    "wiki:sync": "**条件步骤**（`triggers.yaml` 判定）：未触发 → **SKIP**（rc=0）",
    "gates": "**阻断**（失败即停；链尾以终态产物为准）",
}
# 未在 STEP_FAILURE 单列的步骤 → 默认语义
DEFAULT_FAILURE = "**阻断**（失败即停；`--resume` 可从该步续跑）"

# 降级链总表（**N-165 新增**：用户要求「补充降级链的完整表述」）。
# 每条链的事实源（SSOT）在括号中注明；此处只做**汇总披露**，不重复定义。
DEGRADE_CHAINS: list = [
    (
        "**嵌入模型选型链**（SSOT：清单 `selection_policy`）",
        "`full`（全量，默认）：`bge_base_zh` → `text2vec`；"
        "`incremental`（增量/显式指定）：`youtu_embedding` → `bge_base_zh` → `text2vec`；"
        "`model=\"…\"` 显式指定**优先级最高**。链中每一项都须先过 `probe`（`available` 且 `model_ok` 实检）。",
        "候选链**逐个尝试**；成功即返回**实际模型名 + 指纹**；链耗尽时 "
        "`strict=False`（默认）返回 `ok=False` 并打印 `fallback_notice`（**可观测**），"
        "`strict=True` 抛 `EnhanceUnavailable`（含每个候选的失败原因）。**任何路径都不吞错。**",
    ),
    (
        "**分句链**（SSOT：`sentence_boundary` + `semantic_enhance`）",
        "ML 增强（`ltp` 分词锚切句，env `REG_ORCH_SEMANTIC_SPLIT=1`）→ "
        "确定性 `sentence_boundary.split_by(text, level)`（受控 SSOT）。",
        "**默认关闭 ML**（改分句口径会改变条文/关系产物 → 须先在可评样本量出 P/R，"
        "v2 纪律「无度量不得上线」）。关闭时与既有实现**逐字节对等**（零回归）；"
        "开启后 LTP 失败**自动回退**并留痕。",
    ),
    (
        "**向量检索链**（SSOT：`std_lib/common_lib/vector_store.py`）",
        "`pgvector`（`vec` schema，最小权限角色）→ `sqlite_vec`（与既有 SQLite FTS5 同库同源）"
        "→ 既有**全文/正则**。",
        "`VectorStoreUnavailable` 供调用方降级；`search_or_fallback()` 失败返回 `None`（**不抛**）。"
        "连接仅经 env `PGVECTOR_DSN`（口令不入库，`gate_secret_scan` 守护）。"
        "**主链不调用其读写**，`pg:health` 仅只读披露。",
    ),
    (
        "**OCR / 文档解析链**（SSOT：`config/ocr.yaml`）",
        "Engine `auto`：`paddleocr` → `tesseract`（`auto_fallback: true`）；"
        "PDF **有文本层先走 `pdfplumber`**，扫描件才走 OCR。",
        "引擎不可用即降级到下一档；两者皆不可用 → 该文件正文抽取为空并**显式告警**"
        "（不静默产出空正文）。",
    ),
    (
        "**版面分析链**（P2-1，N-180 **已部署**：源码模式 + 独立 venv）",
        "`weknora_docreader`（25+ 格式渲染 + **段落级定位**）→ `crawler_common.extract_document_text`"
        "（既有 6 态，**仍是事实源**）。",
        "docreader 不可用/自检不过 → **显式 SKIP**（rc=0）并回退既有 6 态；"
        "**不重做** OCR/正文通路（docreader 自身不做 OCR）。"
        "其产物为分析视图 → 既不影响事实源，也不阻断主链。",
    ),
    (
        "**时效核验链**（SSOT：`timeliness_review`）",
        "北大法宝 CLI 核验（需 `PKULAW_NODE_EXE` + `PKULAW_PKG_DIR` + token）→ "
        "R13 **三态**（`valid`/`invalid`/`unavailable`）。",
        "无 token / 工具缺失 → `unavailable`（**降级不误标**）；配额耗尽自动停并断点续跑。",
    ),
    (
        "**关系抽取链**（SSOT：`std_lib/common_lib/relations.py`）",
        "条款级定位：**窗口优先（±100 字符 `source_snippet`）→ 跨度退路**；"
        "语义档（P1-2，`youtu_embedding@relations`，未接线）→ **既有正则抽取**。",
        "`article_placement` 记录定位来源（`src_offset`/`snippet`/两者）；未定位即留空并计入基线。"
        "`source_span` 为**纯披露字段**（N-98：收窄会劣化定位，故不参与定位）。",
    ),
    (
        "**披露步骤统一口径**（`retention:plan` / `semantic:preflight` / `pg:health`）",
        "**只读**：不写业务事实源。",
        "**「未就绪」是合法状态**（未启用增强、无待归档、后端未部署）→ 三者**恒 rc=0**；"
        "真正需要阻断的判定保留给各自的人工/CI 口径。",
    ),
    (
        "**门禁判定不可执行时**（`cli.py gates`）",
        "判据因缺输入无法执行（如克隆无数据、水位不可用）。",
        "**显式披露**「判据不可执行 ≠ 判据通过」；运行台账 `gate_run_steps` 对"
        "「失败步骤的产物落在该步时间窗内」判 **FAIL**（可经 `acknowledged.json` 人工确认降级为告警）。",
    ),
    (
        "**编排层降级**（`tools/run_production_refresh`）",
        "`--resume` 续跑（按上次步骤结果 + 全库水位）；`--only`/`--no-scrape` 步骤选择。",
        "步骤被跳过时记 **SKIP（rc=0）并写入 `note`（原因）**，不伪装为执行成功；"
        "失败步骤写**稳定运行台账** `data/run_state/last_run_steps.json`（含时间窗），"
        "并触发通知通道。",
    ),
]

# 模型 → 链路节点绑定（**唯一事实源**）。`wired`/`gate` 描述**链路接线状态**（与就绪度分开）：
#   · `wired=False` ⇒ 门面/库已就绪但**主链未调用**（「可选叠加」）；
#   · `wired=True`  + `gate` ⇒ 已接线但**默认关**（env 打开）。
MODEL_BINDINGS: dict = {
    "bge_base_zh": {
        # N-177：绑定**改指真实调用点** —— 原绑 `classify:all` 而该节点**并无嵌入调用点**
        # （绑定指向不存在的调用点 = 纸面接线）。真实调用点在 `semantic:assist`。
        "step": "semantic:assist", "stage": "阶段2", "wired": True,
        "gate": "`usage_policy.enabled=true`（配置开关）",
        "role": "**全量首选嵌入**（P1-1 主题辅助裁定；`mode=full` 首选）",
        "down": "降级 `text2vec`；再退关键词/规则判定（既有主题分类器）",
    },
    "bge_base_zh@relations": {
        # ⚠️ 关系语义档**尚无调用点**（本轮只落地了主题辅助裁定）→ 如实标未接线，不虚报
        "step": "relations:gen", "stage": "阶段2", "wired": False,
        "role": "**关联语义档**（P1-2；`mode=full` 首选嵌入）",
        "down": "降级 `text2vec`；再退既有正则关系抽取",
    },
    "youtu_embedding": {
        "step": "semantic:assist", "stage": "阶段2", "wired": True,
        "gate": "`usage_policy.enabled=true` + `--mode incremental`",
        "role": "**增量/指定场景嵌入**（`mode=incremental` 首选；2B/2048 维）",
        "down": "降级 `bge_base_zh` → `text2vec`；再退关键词/规则判定",
    },
    "text2vec": {
        "step": "semantic:assist", "stage": "阶段2", "wired": True,
        "gate": "`usage_policy.enabled=true`（配置开关）",
        "role": "嵌入**降级备选**（768 维，与 bge 同维等价备份）",
        "down": "优先 `bge_base_zh`（全量）/`youtu_embedding`（增量）；再退关键词规则",
    },
    "bertopic": {
        "step": "classify:all", "stage": "阶段2", "wired": False,
        "role": "**主题内子簇语义化**（P1-5，仅产出分析视图）",
        "down": "不做子簇（仅影响分析视图，不产事实源）",
    },
    "umap": {
        "step": "classify:all", "stage": "阶段2", "wired": False,
        "role": "bertopic 的降维依赖（**不单独使用**，无权重）",
        "down": "随 bertopic 一并跳过",
    },
    "hdbscan": {
        "step": "classify:all", "stage": "阶段2", "wired": False,
        "role": "bertopic 的聚类依赖（**不单独使用**，无权重）",
        "down": "随 bertopic 一并跳过",
    },
    "ltp": {
        "step": "clauses", "stage": "阶段1", "wired": True,
        "gate": "`REG_ORCH_SEMANTIC_SPLIT=1`（**默认关**）",
        "role": "条文**分句增强**（分词锚切句；LTP 4.x 无原生分句 API，`basis_split=ltp_cws`）",
        "down": "回退 `sentence_boundary.split_by`（受控 SSOT，**逐字节对等**，零依赖）",
    },
    "hanlp": {
        # N-181：**绑定改指真实调用点** —— 原绑 `clauses`，而 `STEP_ORDER` **无此节点**
        # （纸面绑定）。真实路径：`relations:gen` → `relations.py` →
        # `semantic_enhance.split_sentences(prefer=…)` → `semantic_models.load_segmenter(prefer)`。
        "step": "relations:gen", "stage": "阶段2", "wired": True,
        "gate": "ML 分句**默认关闭**（`REG_ORCH_SEMANTIC_SPLIT=1` 才开）—— 改分句口径会改变"
                "条文/关系产物，须先在**可评样本**上量出 P/R（v2 纪律：无度量不上线）",
        "role": "条文分句（P1-3 二选一后端之一；`split_sentences` 的 `prefer` **默认 `ltp`**）",
        "down": "用 `ltp`（默认）；两者皆不可用即回退确定性 `sentence_boundary.split_by`",
    },
    "weknora_docreader": {
        # N-180：**绑定改指真实调用点** —— 原绑 `clean`（阶段1）而该节点**没有 docreader 调用点**
        # （纸面接线）；真实调用点在新增链步骤 `docreader:extract`（部署形态＝源码模式 + 独立 venv）。
        "step": "docreader:extract", "stage": "阶段2", "wired": True,
        "gate": "`docreader_bridge.self_test()` 通过（venv 解释器实跑；不可用则显式 SKIP）",
        "role": "文档**版面分析 + 段落级定位**（25+ 格式渲染；补正文抽取的**定位增量**）",
        "down": "回退 `crawler_common.extract_document_text`（既有 6 态，**仍是事实源**）",
    },
    "youtu_embedding@relations": {
        "step": "relations:gen", "stage": "阶段2", "wired": False,
        "role": "**关联语义档**（P1-2：依据/废止关系的语义近似判定）",
        "down": "降级 `bge_base_zh` → `text2vec`；再退正则关系抽取",
    },
    "deepke": {
        "step": "relations:gen", "stage": "阶段2", "wired": False,
        "role": "**知识抽取框架**（NER/关系抽取/属性抽取；P3-1，**替代原 aprcoie**）",
        "down": "回退正则关系抽取（框架已就位但**微调权重未得**，见清单 `weights`；P3 可选，未启用不影响主链）",
    },
    "signalgraph": {
        "step": "relations:gen", "stage": "阶段2", "wired": False,
        "role": "**零 Token 确定性图构建**（P3-2）",
        "down": "回退关系三元组 → 既有图构件",
    },
    "pgvector": {
        "step": "OND", "stage": "阶段4", "wired": False,
        "role": "**向量检索**后端（P2-2；`vec` schema，最小权限角色）",
        "down": "降级 `sqlite_vec`（同库同源）→ 再退 SQLite FTS5 全文",
    },
    "sqlite_vec": {
        "step": "OND", "stage": "阶段4", "wired": False,
        "role": "向量检索**降级后端**（与既有 SQLite FTS5 同库同源）",
        "down": "回退 SQLite FTS5 全文检索（零服务依赖）",
    },
    "paradedb": {
        "step": "OND", "stage": "阶段4", "wired": False,
        "role": "向量+BM25 混合检索（**已被 pgvector 替代**，可选外部后端）",
        "down": "使用 pgvector / sqlite_vec（**不建议随仓分发**：AGPL-3.0）",
    },
}
# 同一工具可挂多个节点（如 youtu_embedding 同时用于主题与关系）→ 以 `<tool>@<用途>` 区分
_OND = "**按需节点**（无独立链步骤；由 `pg:health` 披露、业务按需调用）"
# 阶段 → Mermaid 阶段节点 ID（**必须用节点 ID**，不能取中文首字 —— 实测曾误取为「阶」）
STAGE_NODE = {"阶段1": "A", "阶段2": "B", "阶段3": "C", "阶段4": "D", "阶段5": "E", "阶段6": "F"}


def _purpose(step: str) -> str:
    """作用查表：精确 → 前缀（`clean:gov`→`clean`）→ 未登记告警（**不静默**）。"""
    if step in PURPOSE:
        return PURPOSE[step]
    head = step.split(":")[0]
    if head in PURPOSE:
        return PURPOSE[head]
    return f"⚠️ **未登记作用**（请在 `gen_flow_map.PURPOSE` 补登 `{step}`）"


# --------------------------------------------------------------------------
# 命令抽取（AST）
# --------------------------------------------------------------------------
def _fstr(node) -> str:
    """把 `ast.Constant` / `ast.JoinedStr`（f-string）还原为可读字符串（保留 `{占位}`）。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        out = []
        for v in node.values:
            if isinstance(v, ast.Constant):
                out.append(str(v.value))
            elif isinstance(v, ast.FormattedValue):
                try:
                    out.append("{" + ast.unparse(v.value) + "}")
                except Exception:  # noqa: BLE001  极端表达式 → 占位
                    out.append("{…}")
        return "".join(out)
    return ""


def _argv_parts(argv) -> list:
    """从 `_run` 的 argv 实参抽取**可读片段**（本列用途＝快速定位脚本，非完整参数）。

    归一化规则（N-165）：
      · 跳过**解释器**实参（`PY`）—— 它对"定位脚本"没有信息量；
      · `os.path.join(A, 'x.py')` 这类**路径表达式** → 取其**最后一个字符串常量**的 basename
        （原来整串 `os.path.join(COLLECTORS, 'nfra_weekly.py')` 直接进表，噪音过大）；
      · 变量实参 → `<NAME>`（保留"此处由变量决定"的真相，不猜值）。
    """
    if not isinstance(argv, (ast.List, ast.Tuple)):
        return ["（argv 由变量构造，完整形态见源码）"]
    parts: list = []
    for el in argv.elts:
        s = _fstr(el)
        if s:
            base = os.path.basename(s.replace("\\", "/").rstrip("/")) or s
            parts.append(base)
            continue
        if isinstance(el, ast.Name):
            if el.id in ("PY", "PYTHON", "SYS_EXECUTABLE"):
                continue  # 解释器：无信息量
            parts.append(f"<{el.id}>")
            continue
        # 路径构造表达式（os.path.join(...) 等）→ 取最后一个字符串常量做 basename
        consts = [
            n.value
            for n in ast.walk(el)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
        ]
        if consts:
            base = os.path.basename(consts[-1].replace("\\", "/").rstrip("/")) or consts[-1]
            parts.append(base)
        else:
            try:
                parts.append(f"<{ast.unparse(el)}>")
            except Exception:  # noqa: BLE001
                parts.append("<…>")
    return parts


def _commands() -> dict:
    """从源码 **AST** 抽取每个步骤的命令（与实装同源）。

    N-165：原实现用正则 `_run\\(\\s*"([^"]+)"` + 逐行找右括号 —— 两个真实缺陷：
      ① **不匹配 f-string 步骤名** → `collect:{src}`/`clean:{src}`/`apply:{src}`（循环生成）
         全部退化为「（条件调用或未见字面量）」；
      ② 边界靠缩进/右括号猜测 → 实测把**相邻调用的片段**串进来
         （如 `gates` 行的命令成了 `cli.py run_at %Y-%m-%d %H:%M:%S total_elapsed_s raw`）。
    AST 一次解决两者：调用边界由语法树确定，f-string 原样还原为 `{src}`。
    """
    tree = ast.parse(open(RUNNER, encoding="utf-8").read())
    out: dict = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not isinstance(fn, ast.Name) or fn.id not in ("_run", "_run_conditional"):
            continue
        if not node.args:
            continue
        name = _fstr(node.args[0])
        if not name:
            continue
        if fn.id == "_run_conditional":
            tid = _fstr(node.args[1]) if len(node.args) > 1 else ""
            out[name] = f"（条件步骤；触发项 `{tid}`，判据见 `config/triggers.yaml`）"
            continue
        argv = node.args[1] if len(node.args) > 1 else None
        out[name] = " ".join(_argv_parts(argv))
    return out


def _tool_states() -> dict:
    """工具 → **完整探测结果**（含 offline_ready / service_reachable / pipeline_ref）。

    供「模型节点入图」判定状态：**未就绪也要给出原因**（权重未预置 / 服务未部署 / 未安装）。
    """
    from std_lib.common_lib import semantic_tools as st

    return {n: dict(p) for n, p in st.probe_all().items()}


def _dep_state(name: str, p: dict) -> tuple:
    """**依赖/权重就绪度** → `(标签, 等级)`；等级 ∈ {`ready`,`warn`,`missing`}。"""
    if not p["available"]:
        cfg = MODEL_BINDINGS.get(name) or {}
        extra = "（可选外部后端）" if cfg.get("step") == "OND" else ""
        return f"未装{extra}", "missing"
    if p.get("offline_ready") is False:
        return "**权重未预置**", "warn"
    if p.get("service_reachable") is False or (p.get("kind") == "service" and p.get("service_reachable") is None):
        return "**服务未部署**", "warn"
    if p.get("offline_ready") is True:
        return "就绪（依赖 + 权重实检通过）", "ready"
    return "就绪（依赖可用；无需外部权重）", "ready"


def _wire_state(name: str) -> tuple:
    """**链路接线状态** → `(标签, 类别)`；类别 ∈ {`on`,`gated`,`off`,`na`}。

    与就绪度**分开表述**（旧版把两者混为「已启用/未启用」，实测会让人误以为
    「依赖装好」就等于「链路在用」——例如 `pgvector` 从未被主链调用却被标「已启用」）。
    """
    cfg = MODEL_BINDINGS.get(name) or {}
    if cfg.get("step") == "OND":
        return "不适用（按需节点：无独立链步骤，由 `pg:health` 披露）", "na"
    if cfg.get("wired"):
        gate = cfg.get("gate") or ""
        return f"**已接线**（{gate}）" if gate else "**已接线**", "gated" if gate else "on"
    return "未接线（执行门面已就绪，待业务节点接入）", "off"


def _steps() -> list:
    import importlib

    m = importlib.import_module("tools.run_production_refresh")
    return list(m.STEP_ORDER)


def build() -> str:
    steps = _steps()
    cmds = _commands()
    tools = _tool_states()
    from std_lib.common_lib import semantic_tools as st

    policy = (st.load_manifest().get("selection_policy") or {}).get("modes") or {}

    L: list = []
    L.append("## 8. 全链数据流总图（`cli.py run`）")
    L.append("")
    L.append(
        "> **本节由 `tools/gen_flow_map.py` 机器生成并注入本 README**（受管块；"
        "事实源：`STEP_ORDER` + `_run` 调用点（AST）+ `config/schema/semantic_tools.json`）。"
        "**请勿手工编辑本节** —— 改链路请改源码后重跑 `python -m tools.gen_flow_map`。"
    )
    L.append(
        f">\n> 步骤数：**{len(steps)}**；语义工具：**{len(tools)}**（可用 "
        f"{sum(1 for t in tools.values() if t['available'])}，未装 "
        f"{sum(1 for t in tools.values() if not t['available'])}）\n"
    )
    L.append("### 8.1 链路总览（Mermaid：**含全部接入模型节点**）")
    L.append("")
    L.append(
        "> 图中**每个接入模型都作为节点**挂在所属阶段（虚线=挂接关系，边标签为**具体步骤**）；"
        "**无论就绪与否均已入图**，样式区分：**实线绿=已接线**、**蓝色=已接线·默认关（env 开启）**、"
        "**虚线灰=未接线**（门面就绪，待业务节点接入）、**橙色=依赖或权重未就绪**。"
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
    buckets: dict = {"on": [], "gated": [], "off": [], "na": [], "warn": [], "missing": []}
    for name in sorted(MODEL_BINDINGS):
        tool = name.split("@")[0]
        p = tools.get(tool)
        if p is None:
            continue
        dep, dep_lv = _dep_state(tool, p)
        _wire, wire_lv = _wire_state(name)
        cfg = MODEL_BINDINGS[name]
        node = "M_" + "".join(ch if ch.isalnum() else "_" for ch in name)
        # 图上标签：**就绪度**优先（未就绪必须一眼可见），否则示接线态
        short = "未装" if dep_lv == "missing" else ("未就绪" if dep_lv == "warn" else
                                                    {"on": "已接线", "gated": "已接线·默认关",
                                                     "off": "未接线", "na": "按需节点"}[wire_lv])
        L.append(
            f'  {STAGE_NODE.get(cfg["stage"], "A")} -.->|"{cfg["step"]}"| '
            f'{node}["{tool}<br/>{short}"]'
        )
        key = dep_lv if dep_lv != "ready" else wire_lv
        buckets[key].append(node)
    L.append("")
    L.append("  classDef on fill:#d7f2df,stroke:#2e7d32,stroke-width:1px")
    L.append("  classDef gated fill:#dbe9ff,stroke:#1565c0,stroke-width:1px")
    L.append("  classDef off fill:#f2f2f2,stroke:#8a8a8a,stroke-dasharray:4 2")
    L.append("  classDef notready fill:#ffe8cc,stroke:#e65100,stroke-width:1px")
    for key, cls in (("on", "on"), ("gated", "gated"), ("off", "off"), ("na", "off"),
                     ("warn", "notready"), ("missing", "notready")):
        if buckets[key]:
            L.append(f"  class {','.join(buckets[key])} {cls}")
    L.append("```")
    L.append("")
    L.append(
        f"图例：**已接线 {len(buckets['on'])}** ／ **已接线·默认关 {len(buckets['gated'])}** ／ "
        f"**未接线 {len(buckets['off']) + len(buckets['na'])}** ／ "
        f"**未就绪（权重未预置或未装）{len(buckets['warn']) + len(buckets['missing'])}**"
        " —— **未接线/未就绪的模型同样作为节点存在**，便于在链路上定位其将来挂接的位置（原因见 §8.3）。"
    )
    L.append("")
    L.append("### 8.2 逐步骤表（顺序 = `STEP_ORDER`；命令由 **AST** 自源码抽取）")
    L.append("")
    L.append(
        "> 说明：命令列取自源码 `_run(...)` 的 `argv` **字面量**（f-string 保留为 `{src}` 占位；"
        "`<NAME>` 表示由变量构造的实参）。完整参数以源码为准。"
        "「降级 / 失败语义」为**逐步声明**（事实源：`gen_flow_map.STEP_FAILURE`）。"
        "另见 §8.2.1「编排层通性」与 §六「降级链总表」。"
    )
    L.append("")
    L.append("| # | 步骤 | 作用 | 脚本/命令（AST 抽取） | 降级 / 失败语义 |")
    L.append("|---|---|---|---|---|")
    for i, s in enumerate(steps, 1):
        purpose = _purpose(s)
        cmd = cmds.get(s, "（未见字面量调用；见源码）")
        if s in READONLY_DISCLOSURE:
            degr = STEP_FAILURE.get(s, "**只读披露 · 恒 rc=0**")
        else:
            degr = STEP_FAILURE.get(s, DEFAULT_FAILURE)
        # 命令单元格：**含反引号时不加外层反引号**（否则表格里的内联代码会串行、渲染错乱）
        cell = cmd if "`" in cmd else f"`{cmd}`"
        L.append(f"| {i} | `{s}` | {purpose} | {cell} | {degr} |")
    L.append("")
    L.append("#### 8.2.1 编排层通性（**对所有步骤生效**）")
    L.append("")
    L.append(
        "- **步骤选择**：`--only <步骤>` / `--no-scrape`（跳过采集）→ 未选中步骤记 **SKIP（rc=0）"
        "并写入原因**（`note`），**不伪装为执行成功**。\n"
        "- **续跑**：`--resume` 依据「上次该步 rc=0（且非跳过）」**且**「全库水位无 stale/未登记」"
        "决定跳过；水位有异常即**全量重跑**（宁重跑不跳：重跑的代价是时间，跳错的代价是数据陈旧）。\n"
        "- **超时**：采集与清洗均在编排器侧设超时；**清洗超时按源体量配置**"
        "（`max(1800, raw_MB × 2)`，上限 21600s，N-150；**系数由 N-188 重标定**）——"
        "原固定 1800s 使最大源必然超时；而 N-150 最初的 `15 s/MB` 标定于**被 N-182 缺陷污染**的"
        "数据（同数据在缺陷休眠时仅 0.14~0.19 s/MB）⇒ 虚高约 60×，使真实挂死要 6h 才被发现。\n"
        "- **失败留痕**：每步结果写入**稳定运行台账** `data/run_state/last_run_steps.json`"
        "（含 `started_at`/`ended_at` 时间窗），并由 `gate_run_steps` 判定"
        "「失败步骤的产物是否已落盘并被下游采用」（危险组合 → FAIL，可人工确认降级）。\n"
        "- **通知**：存在失败步骤时触发通知通道（无人值守下「失败无人知晓」是原设计的硬缺口）。"
    )
    L.append("")
    L.append("### 8.3 模型 ↔ 链路节点定位表（**全部接入模型，含未就绪/未接线**）")
    L.append("")
    L.append(
        "> 纪律：**每个接入模型都必须在此表定位到链路节点**；未就绪者须给出**原因**，"
        "未接线者须给出**降级措施**（不得留空、不得写「无」）。"
        "绑定事实源：`gen_flow_map.MODEL_BINDINGS`。"
        "**就绪度与接线状态分开表述** —— 「依赖装好」不等于「链路在用」。"
    )
    L.append("")
    unbound = sorted(set(tools) - {k.split("@")[0] for k in MODEL_BINDINGS})
    if unbound:
        L.append(f"> ⚠️ **未绑定模型的工具**（须在 `MODEL_BINDINGS` 补登）：`{unbound}`")
        L.append("")
    L.append("| 模型 | 挂接链路节点 | 阶段 | 在该节点做什么 | 依赖/权重就绪度 | 链路接线 | 降级措施 |")
    L.append("|---|---|---|---|---|---|---|")
    n_ready = n_gated = n_off = n_bad = 0
    for name in sorted(MODEL_BINDINGS):
        tool = name.split("@")[0]
        p = tools.get(tool)
        if p is None:
            continue
        dep, dep_lv = _dep_state(tool, p)
        wire, wire_lv = _wire_state(name)
        if dep_lv != "ready":
            n_bad += 1
        elif wire_lv == "gated":
            n_gated += 1
        elif wire_lv == "on":
            n_ready += 1
        else:
            n_off += 1
        cfg = MODEL_BINDINGS[name]
        node = _OND if cfg["step"] == "OND" else f"`{cfg['step']}`"
        extra = "" if name == tool else "（`@`：同工具的第二用途）"
        L.append(
            f"| `{name}`{extra} | {node} | {cfg['stage']} | {cfg['role']} | {dep} | {wire} | {cfg['down']} |"
        )
    total = n_ready + n_gated + n_off + n_bad
    L.append("")
    L.append(
        f"合计 **{total}** 个模型节点（**已接线 {n_ready}** ／ **已接线·默认关 {n_gated}** ／ "
        f"**未接线 {n_off}** ／ **未就绪 {n_bad}**）—— 均已入图（§8.1）。"
    )
    L.append("")
    L.append("#### 8.3.1 ⚠️ 未就绪环节（**依赖或权重缺失，单独标记**）")
    L.append("")
    L.append(
        "以下工具**未就绪**：或依赖未装，或依赖已装但**权重未预置**（`offline_ready=False`）"
        "→ **相关环节在其就绪前不得接线启用**："
    )
    L.append("")
    L.append("| 工具 | 挂接节点 | 方案项 | 就绪度 | 处置 |")
    L.append("|---|---|---|---|---|")
    shown = False
    for name in sorted(MODEL_BINDINGS):
        tool = name.split("@")[0]
        p = tools.get(tool)
        if p is None:
            continue
        dep, dep_lv = _dep_state(tool, p)
        if dep_lv == "ready":
            continue
        shown = True
        cfg = MODEL_BINDINGS[name]
        node = _OND if cfg["step"] == "OND" else f"`{cfg['step']}`"
        fix = (
            "在**可达环境**下载权重后**迁入** `external/models/`（清单以 `local_dir` 声明，"
            "`probe` **实检目录**）；`HF_HOME`/`HANLP_HOME` 为可选覆盖"
            if dep_lv == "warn" and p.get("available")
            else "按清单 `pypi`/`extra` 安装依赖（`pip install -e \".[semantic]\"`）"
        )
        L.append(f"| `{name}` | {node} | {p.get('pipeline_ref') or '—'} | {dep} | {fix} |")
    if not shown:
        L.append("| — | — | — | 全部就绪 | — |")
    L.append("")
    L.append(
        "- **受影响的链路环节**：`semantic:preflight` 的 `offline` 闸（**只对「已接线/已装」的后端**"
        "按**职责分组**判定：嵌入三选一、分句二选一——**不是**要求全部就绪）。\n"
        "- **不受影响**：主链（采集→清洗→条文→关系→报告→门禁）**不依赖任何模型权重**，"
        "全部走确定性正则/规则路径；本表全部模型均为**可选叠加**，缺失即按「降级措施」列回退。"
    )
    L.append("")
    L.append("### 8.4 向量检索接入（P2-2，按需）")
    L.append("")
    L.append(
        "- **接入层**：`std_lib/common_lib/vector_store.py`（`*_store` 家族；与 `governance_store` 同级）\n"
        "- **降级链**：`pgvector`（`vec` schema，最小权限角色）→ `sqlite_vec`（与既有 SQLite FTS5 "
        "同库同源）→ **既有全文/正则**（见 §8.6 第 3 条）\n"
        "- **连接**：env `PGVECTOR_DSN`（口令**仅经 env**，不入库；`gate_secret_scan` 守护）\n"
        "- **异常语义**：`VectorStoreUnavailable` 供调用方降级；`search_or_fallback()` 失败返回 "
        "`None`（**不抛**）\n"
        "- **不影响主链**：主链**不调用**其读写；`pg:health` 仅做只读披露"
    )
    L.append("")
    L.append("### 8.5 披露步骤的失败语义（统一口径）")
    L.append("")
    L.append(
        "`retention:plan` / `semantic:preflight` / `pg:health` 三者同为**只读披露**，统一口径："
    )
    L.append("")
    L.append(
        "> **「未就绪」是合法状态** —— 未启用增强、无待归档、后端未部署，都不是链路故障。"
        "故披露步骤**恒 rc=0**；真正需要阻断的判定保留给各自的人工/CI 口径"
        "（如 `semantic_tools --preflight` 的 rc 反映**可否启用**）。"
    )
    L.append("")
    L.append("### 8.6 降级链总表（**完整表述**）")
    L.append("")
    L.append(
        "> 本仓的降级原则：**任何增强能力可缺失，但不可静默**（零硬依赖 + 降级可观测）。"
        "下表汇总**全部降级链**；每条链的事实源（SSOT）在首列注明，此处只做披露、不重复定义。"
    )
    L.append("")
    L.append("| # | 链路（SSOT） | 正常路径 → 降级路径 | 失败/回退语义 |")
    L.append("|---|---|---|---|")
    for i, (name, path, sem) in enumerate(DEGRADE_CHAINS, 1):
        L.append(f"| {i} | {name} | {path} | {sem} |")
    L.append("")
    L.append("#### 8.6.1 模型选型（**当前生效**，读自清单 `selection_policy`）")
    L.append("")
    L.append("| 场景 `mode` | 首选 | 降级链 | 适用 |")
    L.append("|---|---|---|---|")
    for mode in ("full", "incremental"):
        m = policy.get(mode) or {}
        if not m:
            continue
        chain = " → ".join([str(m.get("primary") or "")] + [str(x) for x in (m.get("fallback") or [])])
        label = "`full`（**默认**）" if mode == "full" else "`incremental`"
        L.append(f"| {label} | `{m.get('primary')}` | {chain} | {m.get('when') or ''} |")
    L.append(
        "\n显式 `model=\"…\"` **覆盖 mode**（优先级最高）；分句增强由 env "
        "`REG_ORCH_SEMANTIC_SPLIT=1` 控制（**默认关**，见 §8.6 第 2 条）。"
    )
    L.append("")
    return "\n".join(L) + "\n"


def inject_into_readme(text: str) -> tuple:
    """把生成块注入 README 的**受管标记**之间 → `(是否变更, 说明)`。

    首次运行时标记不存在 → 追加到 README 末尾并建立标记（保持 README 人工叙述不受影响）。
    """
    body = f"{BEGIN}\n\n{text}{END}\n"
    if not os.path.exists(README):
        return False, f"README 不存在：{README}"
    cur = open(README, encoding="utf-8").read()
    if BEGIN in cur and END in cur:
        head = cur.split(BEGIN)[0]
        tail = cur.split(END, 1)[1]
        new = head + body + tail
    else:
        new = cur.rstrip("\n") + "\n\n---\n\n" + body
    if new == cur:
        return False, "内容无变化（README 已是最新）"
    with open(README, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(new)
    n_lines = text.count("\n")
    return True, f"已注入 README 受管块（{n_lines} 行 / {len(text)} 字节）"


def _norm(s: str) -> str:
    """换行归一（R-5 关键）：README 工作副本在本机 `core.autocrlf=true` 下是 **CRLF**，
    而生成文本用 `\\n` → 不作归一会产生"永远不一致"的**假 FAIL**。"""
    return s.replace("\r\n", "\n").replace("\r", "\n").strip("\n")


def check() -> int:
    """核验 README §8 受管块与**当前源码派生结果**是否一致（R-5：入 CI，防生成物漂移）。

    为什么必须入 CI：§8 是**机器生成物**，而"文档漂移不会报错"——链路改了却忘了重跑，
    读者会按**过期**的链路操作。此判据把"忘了重跑"变成**红灯**。
    """
    text = build()
    if not os.path.exists(README):
        print(f"[FAIL] README 不存在：{README}")
        return int(ExitCode.FAIL)
    cur = open(README, encoding="utf-8").read()
    if BEGIN not in cur or END not in cur:
        print("[FAIL] README 缺少 §8 受管块标记 → 运行 `python -m tools.gen_flow_map`")
        return int(ExitCode.FAIL)
    have = cur.split(BEGIN, 1)[1].split(END, 1)[0]
    if _norm(have) != _norm(text):
        have_l = _norm(have).splitlines()
        want_l = _norm(text).splitlines()
        diff = next(
            (i for i, (a, b) in enumerate(zip(have_l, want_l, strict=False), 1) if a != b),
            min(len(have_l), len(want_l)) + 1,
        )
        print(
            f"[FAIL] README §8 与源码派生结果**不一致**（行数 {len(have_l)} vs {len(want_l)}；"
            f"首个差异在第 {diff} 行）→ 运行 `python -m tools.gen_flow_map` 重生成后提交"
        )
        for i in range(max(0, diff - 2), min(len(want_l), diff + 1)):
            print(f"    期望: {want_l[i][:110]}")
        return int(ExitCode.FAIL)
    print(f"[OK] README §8 受管块与源码一致（{len(_norm(text).splitlines())} 行）")
    return int(ExitCode.OK)


def main(argv=None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    if "--check" in argv:
        return check()
    text = build()
    changed, msg = inject_into_readme(text)
    print(f"[gen_flow_map] {msg}")
    # N-165：**不再单独生成 docs/全链数据流总图.md**；若历史文件仍在，提示删除（避免「两处事实源」）
    if os.path.exists(LEGACY_OUT):
        print(
            f"[gen_flow_map] ⚠️ 检测到历史独立文件 `{os.path.relpath(LEGACY_OUT, paths.ROOT)}`"
            " —— 自 N-165 起内容已并入 README，该文件应删除（避免两处事实源漂移）。"
        )
    return 0 if changed or "已是最新" in msg else 1


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    raise SystemExit(main())
