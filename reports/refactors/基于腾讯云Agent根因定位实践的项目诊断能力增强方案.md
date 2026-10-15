# 基于腾讯云Agent根因定位实践的项目诊断能力增强方案

> 依据（https://mp.weixin.qq.com/s/TMsyXfEgRUfxetTlQihNHg?color_scheme=light）腾讯云《给270万行代码开“上帝视角”：Agent自主定位根因实践》的核心方法论，结合项目现有事实源（STEP_LAYERS / GOV_ARTIFACTS / worklist / gate_result / run_log）出具完整方案。核心原则：**零新增事实源、零硬依赖、零Token消耗、不破坏主链**。


## 总体判断：方法论与项目的高贴合度

文章解决的核心问题——**“异常沿数据流跨节点级联传播，表象与根因不在同一位置，排查高度依赖经验”**——与项目的处境高度同构。项目主链28步、五源采集、多条条件触发链，一个步骤失败的影响面往往跨越采集→清洗→分类→关系→发布多个环节，根因定位同样依赖对链路拓扑的理解。

文章的核心方法论是：**以系统拓扑图为核心构建统一知识底座，引入问题发现、根因分析、自主修复和Self-Improving四个Agent协同，形成自治闭环**。

**项目已有的全部原材料**：

| 文章要求的要素 | 项目已有的事实源 | 派生方式 |
|---|---|---|
| 节点身份/职责 | `StepSpec.name` / `.purpose`（W1后） | 直接取 |
| 输入输出 | `StepSpec.depends_on` / `.produces` | 直接取 |
| 依赖关系 | `GOV_ARTIFACTS` 的 inputs/produced_by | 直接取 |
| 失败语义 | `StepSpec.fail_semantics` | 直接取 |
| 取证入口 | `step_logs/{step}.log` / `run_step` 表 / `watermark` 表 | 路径约定派生 |
| 条件触发 | `StepSpec.trigger_id` → `triggers.yaml` | 直接取 |
| 调度触发 | `schedule.yaml` 的 `covered_by` | 直接取 |
| 门禁判据 | `ALL_GATES` 的 require_impl | 直接取 |

**方案总览**：文章的四层闭环，逐层映射到项目——**拓扑图（派生视图）→ 问题发现（多源信号关联）→ 根因分析（拓扑遍历+证据聚合）→ Self-Improving（worklist闭环）**，全部复用项目既有机制。


## 第一章：系统拓扑图——从现有事实源派生

### 1.1 节点应表达什么

文章明确：面向Agent的拓扑图必须结构化，每个节点至少要包含**节点身份、业务职责、输入输出、依赖关系、取证入口、治理属性**六要素。

项目的`StepSpec`（W1落地）已覆盖前四要素，缺的是**取证入口**和**治理属性**的显式声明。

### 1.2 数据结构设计

**扩展 `StepSpec`**（在项目W1的`STEP_LAYERS`基础上）：

```python
@dataclass(frozen=True)
class StepSpec:
    # ... W1 已有字段：name/kind/fail_semantics/purpose/timeout/
    #     trigger_id/depends_on/produces/degrade_note
    # 新增（诊断增强专用）：
    evidence_entries: tuple[EvidenceEntry, ...] = ()   # 取证入口
    governance_attrs: GovernanceAttrs | None = None    # 治理属性

@dataclass(frozen=True)
class EvidenceEntry:
    """取证入口（项目现有路径约定派生）。"""
    entry_type: str      # step_log / run_step / watermark / gate_result
    path_or_query: str   # 路径或查询语句
    description: str

@dataclass(frozen=True)
class GovernanceAttrs:
    """治理属性。"""
    owner: str           # 负责人（从CODEOWNERS或MODULE_SPECS派生）
    risk_level: str      # 风险等级（从StepSpec.kind派生：main=high, side=medium, disclosure=low）
    auto_repair_scope: str  # 可自动操作范围（abort=不可自动, warn=可自动跳过）
    verification: str    # 验证项（从produces的门禁判据派生）
    rollback: str        # 回滚方式（从Wave回滚策略派生）
```

**取证入口的路径约定**（全部来自项目现有布局，零新增）：

```python
def _evidence_entries(spec: StepSpec) -> tuple[EvidenceEntry, ...]:
    """从项目现有路径约定派生取证入口。"""
    entries = []
    # 1. 步骤日志（run_production_refresh 已写：reports/_tmp/step_logs/<step>.log）
    entries.append(EvidenceEntry(
        entry_type="step_log",
        path_or_query=f"reports/_tmp/step_logs/{spec.name.replace(':', '_')}.log",
        description=f"{spec.name} 的 stdout/stderr tail",
    ))
    # 2. 运行台账（governance.db 的 run_step 表）
    entries.append(EvidenceEntry(
        entry_type="run_step",
        path_or_query=f"SELECT * FROM run_step WHERE step='{spec.name}' "
                      f"ORDER BY started_at DESC LIMIT 10",
        description=f"{spec.name} 最近10次执行记录（rc/耗时/时间窗）",
    ))
    # 3. 产物水位（governance.db 的 watermark 表）
    for produced in spec.produces:
        entries.append(EvidenceEntry(
            entry_type="watermark",
            path_or_query=f"SELECT * FROM watermark WHERE artifact_key='{produced}'",
            description=f"{produced} 的水位版本与依赖边",
        ))
    # 4. 门禁结果（gate_result 表）
    if spec.kind == "gate":
        entries.append(EvidenceEntry(
            entry_type="gate_result",
            path_or_query="SELECT * FROM gate_result ORDER BY run_id DESC LIMIT 1",
            description="最近一次门禁运行的全部判据结果",
        ))
    return tuple(entries)
```

### 1.3 边的多语义设计

文章强调：**边不能只有“调用”一种语义，应区分数据流、控制流、调用关系、异步通知和配置依赖**。

```python
@dataclass(frozen=True)
class TopologyEdge:
    """拓扑边（多语义）。"""
    src: str
    dst: str
    edge_type: str        # data_flow / control_flow / conditional / side_channel
    artifact_key: str     # 关联产物键（data_flow 时）
    condition_impl: str   # 条件实现（conditional 时，从 CONDITION_IMPLS 派生）
    schedule_id: str      # 调度作业ID（control_flow 时，从 schedule.yaml 派生）
    evidence: str         # 边的证据来源（从 GOV_ARTIFACTS 派生，格式：<源文件>:<行号>）
```

**边的来源映射**：

| 边类型 | 项目事实源 | 派生逻辑 |
|---|---|---|
| **data_flow** | `GOV_ARTIFACTS[artifact].inputs` | 上游产物 → 下游步骤 |
| **control_flow** | `schedule.yaml` 的 `covered_by` | 调度作业 → 被驱动的步骤 |
| **conditional** | `StepSpec.trigger_id` → `triggers.yaml` | 触发条件 → 条件步骤 |
| **side_channel** | `StepSpec.kind in (side, disclosure, enhancement)` | 旁路/披露/增强 → 主链 |

### 1.4 落点（精确到文件）

| 动作 | 文件 | 说明 |
|---|---|---|
| **扩展** | `tools/gen_flow_map.py` | 新增 `gen_topology()`，从 STEP_LAYERS + GOV_ARTIFACTS + schedule.yaml + triggers.yaml 派生 |
| **新增** | `interfaces/topology_api.py` | 只读查询门面：`get_node` / `neighbors` / `shortest_path` / `upstream_chain` / `affected_nodes` |
| **新增** | `gates/gate_topology_completeness.py` | 断言拓扑图与事实源零漂移；取证入口路径可达；生成时间在最近一次全链运行之后 |
| **扩展** | `gates/__init__.py` | 注册新门禁（25道） |
| **扩展** | `config/schema/contract_manifest.json` | 登记 topology 契约 |
| **新增** | MCP 资源 `mcp://orchestrator/topology` | W2 落地，与 `chain_steps` 资源同批 |

### 1.5 质量度量（文章强调“拓扑质量决定闭环上限”）

文章指出：**拓扑缺边Agent会提前终止穿透；节点职责错误会把异常归到错误模块；取证入口过期会拿不到有效证据**。因此拓扑质量需要单独度量。

**新增门禁判据**（`gate_topology_completeness`）：

| 判据 | 断言 |
|---|---|
| T1 | 每个 STEP_LAYERS 步骤在 topology.json 有节点 |
| T2 | 每个节点的 `depends_on` 在拓扑中有对应边 |
| T3 | 每个节点的 `evidence_entries` 路径在项目布局中可达 |
| T4 | topology.json 的 `generated_at` 在最近一次全链运行之后 |
| T5 | 每条边的 `evidence` 字段可追溯到 GOV_ARTIFACTS 或 schedule.yaml 的具体行 |


## 第二章：问题发现——复用现有五类信号源

### 2.1 核心判断：不新建监控系统

文章指出：**问题发现Agent的目标不是“报得更多”，而是周期性监控可疑信号，把多源弱信号组合成少量值得求证的问题**。

项目已有五类信号源，但**互不关联**：

| 信号源 | 现有位置 | 当前用途 | 文章对应 |
|---|---|---|---|
| 步骤失败 | `run_log` / `run_step` | 台账记录 | 监控指标异常波动 |
| 门禁失败 | `gate_result` | 门禁历史 | 错误日志 |
| 告警 | `reports/_alerts/*.json` | 单次告警 | 错误日志簇 |
| 待办积压 | `worklist` | 人工处置 | 数据质量 |
| 水位stale | `watermark` | 门禁判据 | 数据质量 |
| 步骤日志 | `step_logs/` | 排查时查阅 | 错误日志 |

### 2.2 信号关联规则

**新增 `tools/problem_discovery.py`**：

```python
def scan(days: int = 7) -> list[ProblemCandidate]:
    """周期性扫描多源信号，关联生成候选问题。"""
    signals = []
    signals += _scan_run_failures(days)       # run_step: rc≠0 的记录
    signals += _scan_gate_trends(days)        # gate_result: 近N次失败率
    signals += _scan_watermark_stale()        # watermark: stale边
    signals += _scan_worklist_aging()         # worklist: 超期未处置
    signals += _scan_step_log_errors(days)    # step_logs: 错误指纹聚类
    return _correlate(signals)

def _correlate(signals) -> list[ProblemCandidate]:
    """关联规则（示例，全部基于项目现有数据）：
    - 步骤连续N次耗时上涨>50% + 该步骤产物stale → step_regression
    - 门禁近N次失败率>阈值 + 相关步骤近期有变更（git log）→ gate_trend
    - worklist某kind积压>阈值 + 该kind产生方近N次运行频繁触发 → worklist_anomaly
    - 某步骤step_log错误指纹新增 + 上游run_step显示异常 → error_cluster
    - 多条watermark边同时stale + 共同上游步骤 → cascade_stale
    """
```

**候选问题的数据结构**：

```python
@dataclass(frozen=True)
class ProblemCandidate:
    candidate_id: str
    signal_type: str           # step_regression / gate_trend / watermark_stale / ...
    time_window: tuple[str, str]
    affected_nodes: tuple[str, ...]   # 从 topology_api.affected_nodes() 派生
    impact_summary: str
    sample_evidence: tuple[str, ...]
    severity: str
    suggested_diagnosis: str          # 建议的排查入口（拓扑图上哪条链）
```

### 2.3 拓扑判断级联异常

文章强调：**如果多个下游服务同时异常，逐服务报警会制造多个工单；基于拓扑可以寻找它们共同的上游依赖，把多个症状聚合为一个根问题**。

项目的对应实现：`_correlate` 中的 `cascade_stale` 规则——当多条 watermark 边同时 stale 且共享同一个上游步骤时，聚合为一个候选问题，`affected_nodes` 取共同上游的 `affected_nodes` 集合。

### 2.4 落点

| 动作 | 文件 | 说明 |
|---|---|---|
| **新增** | `tools/problem_discovery.py` | 多源信号扫描与关联 |
| **新增** | `commands/discover.py` | CLI 第20命令：`scan` / `list` / `show` |
| **扩展** | `cli.py` | 注册 `discover` 命令 |
| **扩展** | `config/enums.py` | WORKLIST_KIND 增 `discovered_problem` |
| **扩展** | `config/schedule.yaml` | 增 `problem_discovery` 作业（kind=quality，周一07:00） |
| **扩展** | `gates/gate_config_integrity.py` | J7 判据覆盖新 kind |


## 第三章：根因分析——基于拓扑图的受控推理

### 3.1 核心判断：不引入LLM Agent，用拓扑遍历+证据聚合

文章描述的主/子Agent架构，对项目而言，**根因分析的输入是结构化的拓扑图和台账**，不需要LLM做规划——拓扑图的 `upstream_chain` 就是排查路径。

### 3.2 五环节流程（对应文章的五个核心环节）

文章明确根因分析的五个核心环节：**统一入口、主Agent规划归因、子Agent单点取证、根因报告输出、经验异步回写**。

**新增 `tools/root_cause.py`**：

```python
def diagnose(failure_step: str, run_id: str | None = None) -> RootCauseReport:
    """基于拓扑图的反向追溯 + 证据聚合。

    五环节（对应文章）：
      1. 统一入口：接受步骤名/告警/时间窗
      2. 规划归因：从 topology_api 取上游链，规划排查路径
      3. 单点取证：沿链逐跳取证（step_log/run_step/watermark/gate_result）
      4. 报告输出：根因候选 + 排除项 + 置信度
      5. 经验回写：候选经验入 worklist
    """
    # 1. 规划：取上游链
    upstream = topology_api.upstream_chain(failure_step)
    # 2. 取证：沿链逐跳收集证据
    evidence = []
    for step in upstream:
        evidence += _collect_step_evidence(step, run_id)
    # 3. 聚合：证据关联与根因候选
    candidates = _aggregate_evidence(evidence)
    # 4. 双证据验证
    verified = _verify_with_second_source(candidates)
    # 5. 输出报告
    return RootCauseReport(
        failure_step=failure_step,
        upstream_chain=upstream,
        candidates=verified,
        ruled_out=_get_ruled_out(evidence),
        confidence=_compute_confidence(verified),
    )

def _collect_step_evidence(step: str, run_id: str | None) -> list[Evidence]:
    """单点取证（对应文章的'子Agent'角色，用规则实现）。"""
    entries = topology_api.get_node(step)["evidence_entries"]
    evidence = []
    for entry in entries:
        if entry["entry_type"] == "step_log":
            evidence += _read_step_log_tail(entry["path_or_query"])
        elif entry["entry_type"] == "run_step":
            evidence += _query_run_step(entry["path_or_query"])
        elif entry["entry_type"] == "watermark":
            evidence += _query_watermark(entry["path_or_query"])
        elif entry["entry_type"] == "gate_result":
            evidence += _query_gate_result()
    return evidence
```

### 3.3 四条硬约束（对应文章的“用强约束控制幻觉”）

文章设置四条硬约束：**服务名必须有来源、时间窗必须可追溯、调用链必须穿透、关键判断至少双证据**。

| 约束 | 项目落地 |
|---|---|
| **步骤名有来源** | `topology_api.get_node()` 查不到即拒绝 |
| **时间窗可追溯** | `Evidence.ts` 全部来自 `run_step.started_at` / `step_log` 时间戳 |
| **依赖链穿透** | `_aggregate_evidence` 拒绝在中间步骤停止，必须追到 `raw:*` 或明确输入异常 |
| **双证据** | `_verify_with_second_source` 要求每个候选至少来自两类证据源 |

### 3.4 两阶段分析（对应文章的“广度扫描→定向深挖”）

文章明确完整分析分为两阶段：**阶段一广度扫描先缩圈，阶段二定向深挖再穿透**。

项目的规则实现：

```python
def _aggregate_evidence(evidence):
    # 阶段一：广度扫描——快速检查全链，排除明显无关节点
    candidates = _breadth_scan(evidence)
    # 阶段二：定向深挖——对高嫌疑节点，沿调用链持续向下游穿透
    for candidate in candidates:
        if candidate.suspicion > THRESHOLD:
            candidate.evidence += _deep_dive(candidate.step)
    return _rank_candidates(candidates)
```

### 3.5 落点

| 动作 | 文件 | 说明 |
|---|---|---|
| **新增** | `tools/root_cause.py` | 根因分析核心逻辑 |
| **新增** | `commands/diagnose.py` | CLI 第21命令：`diagnose <step>` / `diagnose last` / `diagnose history` |
| **扩展** | `interfaces/topology_api.py` | 提供 `upstream_chain` / `affected_nodes` |
| **新增** | `reports/diagnosis/` | 诊断报告目录（入 `.gitignore`） |


## 第四章：Self-Improving——复用worklist闭环

### 4.1 核心判断：候选区就是worklist，晋升走既有resolve流程

文章强调：**候选经验不会立即进入权威经验库，而是经过旁路Agent复现和人工确认后再沉淀复用**。

项目**已有这套机制**——`worklist` 的 `status` 就是候选→权威的状态机（open → resolved）。

### 4.2 经验的六要素（对应文章的“经验不是标准答案”）

**新增 `std_lib/common_lib/memory.py`**：

```python
@dataclass(frozen=True)
class Experience:
    """可复用经验（必须回答六个问题）。"""
    scope: str                # 适用范围（节点/异常类型）
    symptom: str              # 什么现象下
    evidence: tuple[str, ...] # 依据哪些证据
    action: str               # 采取什么动作
    result: str               # 得到什么结果
    not_applicable_when: str  # 何时不适用
    source_diagnosis: str     # 来源诊断报告ID
    verified_at: str
    confidence: float

class ExperienceStore:
    """权威经验库（governance.db 的 experience 表）。"""
    def query(self, scope: str, symptom: str, top_k: int = 5) -> list[Experience]: ...
    def promote_from_worklist(self, item_id: str) -> Experience: ...
```

### 4.3 governance.db 扩展

```sql
CREATE TABLE experience (
    exp_id TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    symptom TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    action TEXT NOT NULL,
    result TEXT NOT NULL,
    not_applicable_when TEXT NOT NULL,
    source_diagnosis TEXT,
    verified_at TEXT NOT NULL,
    confidence REAL NOT NULL,
    promoted_from_worklist TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_experience_scope ON experience(scope, symptom);
```

### 4.4 经验注入点

诊断前先查经验（以“建议和约束”形式注入，不作唯一判据）：

```python
def diagnose(failure_step: str, run_id: str | None = None) -> RootCauseReport:
    exp_store = ExperienceStore()
    experiences = exp_store.query(
        scope=f"step:{failure_step}",
        symptom=_initial_symptom(failure_step),
        top_k=5,
    )
    report = _do_diagnose(failure_step, run_id)
    report.experience_hints = experiences  # 建议和约束，不作判据
    if report.candidates:
        _enqueue_experience_candidate(report)  # 新经验入 worklist 候选
    return report
```

### 4.5 落点

| 动作 | 文件 | 说明 |
|---|---|---|
| **新增** | `std_lib/common_lib/memory.py` | 经验库 |
| **扩展** | `std_lib/common_lib/governance_store.py` | 增 experience 表 |
| **扩展** | `config/enums.py` | WORKLIST_KIND 增 `root_cause_report` / `experience_candidate` |
| **扩展** | `commands/worklist.py` | `promote` 子命令：从候选晋升权威 |
| **扩展** | `tools/root_cause.py` | 经验注入 |
| **扩展** | `gates/gate_config_integrity.py` | J7 判据覆盖新 kind |


## 与现有Wave/章节的整合

| 阶段 | 落点文件 | Wave | 依赖 |
|---|---|---|---|
| **拓扑图** | `gen_flow_map.py` 扩展 + `interfaces/topology_api.py` + `gate_topology_completeness` | W1~W2 | STEP_LAYERS（W1） |
| **问题发现** | `tools/problem_discovery.py` + `commands/discover.py` + schedule 作业 | W2~W3 | 拓扑图 + notify |
| **根因分析** | `tools/root_cause.py` + `commands/diagnose.py` | W3~W4 | 拓扑图 + 问题发现 |
| **Self-Improving** | `memory.py` + `governance_store` experience 表 + worklist 3 kind | W4+ | 根因分析 + worklist |

**与第8章确认台的整合**：确认台新增 **P9 诊断台**（与P0-P8同构）：拓扑图可视化（可交互）+ 候选问题列表 + 诊断报告查看 + 经验库浏览；P4待办闭环台增3个新kind的处置动作。

**与MCP门面的整合**（第4章）：MCP资源 `mcp://orchestrator/topology`（与 `chain_steps` 同批）；MCP工具 `discover_candidates` / `diagnose_step`（只读，不进首批写操作）。


## 关键纪律

| 纪律 | 落地 |
|---|---|
| **零Token** | 四阶段全部基于规则和拓扑遍历，不调用LLM |
| **唯一事实源** | 拓扑图从 STEP_LAYERS/GOV_ARTIFACTS 派生；经验候选复用 worklist；权威经验入 experience 表 |
| **零硬依赖** | 全部新增为项目内模块，不引入外部服务 |
| **可观测降级** | 拓扑图不可用→诊断明示；经验库为空→诊断照常 |
| **不破坏主链** | 全部为旁路分析能力；不修改 `run_production_refresh.py` 的执行路径 |
| **人工闸门** | 经验晋升走 worklist resolve；根因报告只出建议不执行修复 |


## 总结

文章的核心价值不是某个具体工具，而是**“把系统的全局关系结构化，让Agent基于证据自主推理”**的方法论。项目的 `STEP_LAYERS` / `GOV_ARTIFACTS` / `worklist` / `gate_result` / `run_log` 已经构成了拓扑图的全部原材料，**缺的是把它们组织成可推理的图谱，以及基于图谱的自主诊断能力**。

**四阶段全部落在项目已有路径约定和表结构上**：

- **拓扑图** = `STEP_LAYERS` + `GOV_ARTIFACTS` + `schedule.yaml` + `triggers.yaml` 的派生视图，由 `gen_flow_map.py` 同批生成
- **问题发现** = `run_log` + `gate_result` + `watermark` + `worklist` + `step_logs/` 的多源关联，候选进worklist
- **根因分析** = 拓扑图反向遍历 + 现有证据源聚合，报告落 `reports/diagnosis/`
- **Self-Improving** = 候选复用worklist，权威区新增 `experience` 表，晋升走既有resolve流程

**不新增事实源、不新增外部依赖、不调用LLM、不破坏主链**——这是与项目高贴合度的核心。

# 完整方案补充：诊断能力的使用方法

> 前文四章（拓扑图/问题发现/根因分析/Self-Improving）是**能力建设**，本章是**能力使用**——两者缺一不可。以下按"入口→场景→衔接→机制→起步"五层给出完整使用方法。


## 第五章：使用入口

### 5.1 三个入口的分工

| 入口 | 适用场景 | 命令/位置 | 使用者 |
|---|---|---|---|
| **CLI** | 日常巡检、故障排查、脚本调用 | `orchestrator discover` / `orchestrator diagnose` | 运维/值班/合规工程师 |
| **确认台 P9 诊断台** | 人工查看、多人协作、可视化 | `cli.py serve` → P9 页 | 团队/负责人 |
| **MCP 工具** | Agent 自动调用、集成到其他流程 | `mcp://orchestrator/topology` + `discover_candidates` / `diagnose_step` | Agent |

**起步建议**：先用 CLI 熟悉，再上确认台可视化，最后按需接 MCP。

### 5.2 CLI 命令清单（新增 2 命令，19→21）

```bash
# 问题发现
orchestrator discover scan [--days 7]      # 扫描多源信号，生成候选问题
orchestrator discover list [--kind ...]     # 列出候选问题
orchestrator discover show <candidate_id>   # 查看候选详情（含拓扑影响面）
orchestrator discover dismiss <id> --reason # 标记误报

# 根因诊断
orchestrator diagnose <failure_step> [--run-id RUN-xxx]  # 诊断指定步骤
orchestrator diagnose last                                 # 诊断最近失败的步骤
orchestrator diagnose history [--limit 20]                 # 历史诊断报告
orchestrator diagnose show <report_id>                     # 查看诊断报告详情
orchestrator diagnose --save-experience                    # 诊断后沉淀经验到 worklist
```

### 5.3 确认台 P9 诊断台（与 P0-P8 同构）

| 区块 | 数据源 | 内容 |
|---|---|---|
| 拓扑图 | `topology.json` | 可交互编排拓扑（点击节点看 StepSpec + 取证入口） |
| 候选问题 | `worklist kind=discovered_problem` | 列表 + 影响面 + 证据 |
| 诊断报告 | `reports/diagnosis/` | 根因候选 + 排除项 + 建议动作 |
| 经验库 | `experience` 表 | 权威经验 + 候选经验 |
| 一键诊断 | CLI 触发 | 输入步骤名 → 展示诊断进度与结果 |

### 5.4 MCP 资源与工具（W2 落地）

```python
# MCP 资源（只读）
mcp://orchestrator/topology              # 拓扑图（节点+边+取证入口）
mcp://orchestrator/diagnosis/latest      # 最近诊断报告

# MCP 工具（只读，不进首批写操作）
discover_candidates(days: int) -> list    # 扫描候选问题
diagnose_step(step: str) -> report        # 诊断指定步骤
```


## 第六章：典型场景的完整操作

### 场景 A：每日/每周例行巡检

**何时用**：每日 06:00 主链跑完后，或每周一早上。

**完整操作**：

```bash
# 1. 扫描最近 7 天的多源信号
orchestrator discover scan --days 7

# 输出示例：
# [discover] 扫描完成，发现 3 个候选问题：
#   PC-20261015-001  step_regression   clean:gov 耗时连续上涨 62%（3次）
#     影响面：clean:gov → clauses:gov → classify:all → ...（8 个下游节点）
#     证据：run_step 表 3 条记录 + watermark stale
#     建议：diagnose clean:gov
#   PC-20261015-002  gate_trend        gate_relations 近 5 次失败 2 次
#   PC-20261015-003  watermark_stale   relations_index 水位 stale 2 天

# 2. 逐个查看候选详情
orchestrator discover show PC-20261015-001
# 输出：候选的时间窗、影响面（拓扑图下游节点）、样本证据、严重度

# 3. 确认值得排查的 → 诊断
orchestrator diagnose clean:gov
# 输出：根因候选 + 排除项 + 建议动作（详见场景 B）

# 4. 处理结果：
#    - 确认为真问题 → worklist 自动生成 discovered_problem 条目，走处置闭环
#    - 误报 → orchestrator discover dismiss PC-xxx --reason "xxx"
```

**产出**：`reports/diagnosis/` 下的诊断报告 + `worklist` 的 `discovered_problem` 条目。

**建议频率**：每周一早上跑一次，10 分钟看完。

### 场景 B：主链失败后快速定位

**何时用**：收到 `notify` 的 `failed_step` 告警，或 `gates` 转红。

**完整操作**：

```bash
# 1. 确认失败步骤（从告警或台账）
orchestrator status --last-run
# 输出：最近一次运行 run_id/失败步骤/失败时间/失败 note

# 2. 一键诊断（自动沿拓扑图反向追溯）
orchestrator diagnose last
# 或指定步骤
orchestrator diagnose relations:gen --run-id RUN-20261015-060000

# 输出示例：
# [diagnose] 根因分析报告 RC-20261015-001
# 失败步骤：relations:gen
# 上游链：relations:gen ← cleaned:nfra ← collect:nfra ← raw:nfra
#
# 根因候选（双证据验证）：
#   1. [高置信] collect:nfra 抓取不完整
#      证据：step_log 显示 3 条超时 + raw:nfra 记录数比上次少 12%
#      链路：raw:nfra 记录减少 → cleaned:nfra 缺记录 → relations 抽取失败
#   2. [中置信] relations:gen 自身超时
#      证据：run_step 显示 1800s 恰好用尽
#
# 已排除：
#   - cleaned:nfra 清洗失败（清洗 rc=0，记录数正常）
#   - internal_index 缺失（水位 ok）
#
# 建议动作：
#   - 重跑 collect:nfra（--resume 会从该步续跑）
#   - 若反复超时 → 检查 nfra 源站响应
#
# 经验提示（来自 experience 表）：
#   - [E-20261010-003] nfra 源站在月末最后3天响应变慢，建议避开此窗口
#     置信度 0.85；不适用条件：非月末

# 3. 执行建议动作
orchestrator run --resume --from collect:nfra

# 4. 若确认经验有效 → 沉淀
orchestrator diagnose --save-experience
```

**产出**：根因报告 + 排除项 + 建议动作 + 经验提示。

**建议**：把 `orchestrator diagnose last` 作为失败后的**第一个动作**，而不是直接翻日志。

### 场景 C：门禁告警后的追溯

**何时用**：`gate_result` 表新增 FAIL，或确认台 P0 门禁状态卡转红。

**完整操作**：

```bash
# 1. 看门禁失败详情
orchestrator gates --last
# 或指定运行
orchestrator gates --run RUN-20261015-060000
# 输出：24 道门禁逐道结果 + 失败判据 + 证据

# 2. 门禁失败往往指向具体产物，用诊断追溯
orchestrator diagnose gate:relations
# 诊断会自动：
#   - 从 topology 找到 gate:relations 的上游
#   - 检查门禁判据的产物（relations_index 等）的水位
#   - 追溯该产物的生产者（relations:gen）的执行记录
#   - 给出"是产物问题还是门禁判据问题"

# 3. 分支处理：
#    - 若是产物问题 → 重跑；诊断报告会指明重跑哪步
#    - 若是门禁判据过严 → 提 ADR 调整判据（走既有 ADR 流程）
```

### 场景 D：反复出现的问题 → 沉淀经验

**何时用**：同一个问题第 2 次出现时。

**完整操作**：

```bash
# 1. 诊断后，从报告的 candidates 中提取经验
orchestrator diagnose clean:gov --save-experience

# 2. 经验进 worklist 候选区
orchestrator worklist list --kind experience_candidate
# 输出：经验候选列表

# 3. 人工复核（确认六要素齐全）
orchestrator worklist show <item_id>
# 输出：
#   scope: step:clean:gov
#   symptom: 耗时 >1800s 且 raw 无增长
#   evidence: run_step 耗时 + watermark + step_log
#   action: 检查 N-182 类死循环（jieba 高频词典）
#   result: 定位到 ocr_correction 候选剪枝问题
#   not_applicable_when: raw 确实大幅增长时
#   source_diagnosis: RC-20261015-003

# 4. 旁路复现验证（在测试环境重现该症状，确认经验有效）
# 5. 晋升权威
orchestrator worklist resolve <item_id> --option confirm
# 或拒绝
orchestrator worklist resolve <item_id> --option reject --reason "xxx"

# 6. 之后每次 diagnose clean:gov，系统自动注入该经验作为"建议"
```

**产出**：`experience` 表新增一条权威经验，后续诊断自动参考。

### 场景 E：批量排查（月度评审前）

**何时用**：月度评审、季度审计前。

**完整操作**：

```bash
# 1. 扫描过去 30 天
orchestrator discover scan --days 30

# 2. 批量诊断所有候选
orchestrator discover list --kind discovered_problem --format json \
  | jq -r '.[].failure_step' \
  | xargs -I {} orchestrator diagnose {}

# 3. 汇总诊断报告
orchestrator diagnose history --since 2026-09-15 --format md > reports/monthly_diagnosis.md

# 4. 评审用：
#    - 高频根因 TOP5
#    - 重复问题列表
#    - 经验库增长情况
#    - 建议的系统性改进
```


## 第七章：与现有工作流的衔接

### 7.1 与 `schedule.yaml` 衔接

**扩展 `config/schedule.yaml`**（新增 1 个作业）：

```yaml
- id: problem_discovery
  kind: quality              # E-3 标注：质量类
  when: "0 7 * * 1"          # 每周一 07:00（verify 之后）
  argv: ["cli.py", "discover", "scan", "--days", "7"]
  on_miss: skip
  desc: "问题发现：多源信号扫描，候选入 worklist"
  covered_by: ["discover:scan"]   # E1 判据断言
```

**效果**：每周一自动扫描，候选进 worklist，人工早上看。

### 7.2 与 `worklist` 闭环衔接

诊断能力产出的 **3 个新 kind** 都进 worklist，走**既有五环闭环**：

```
discover/diagnose 产生 → worklist open
    → 确认台 P4 查看（或 CLI worklist show）
    → 人工处置（confirm/reject/defer/escalate/need_info）
    → kind→action 注册表执行回写
    → 验证灯亮 → resolved
```

**新增 3 个 kind 的 action 注册**（`config/worklist_actions.yaml`）：

```yaml
discovered_problem:
  action: none              # 纯提示，处置即 dismiss 或转 diagnose
  options: [confirm, reject, escalate]
root_cause_report:
  action: none              # 纯报告，处置即确认或标记误判
  options: [confirm, reject, need_info]
experience_candidate:
  action: promote_to_experience   # 晋升写 experience 表
  options: [confirm, reject, defer]
```

**不需要新工作流**——复用既有 worklist 五环。

### 7.3 与确认台衔接

确认台新增 **P9 诊断台**（与 P0-P8 同构），展示：

| 区块 | 数据源 | 内容 |
|---|---|---|
| 拓扑图 | `topology.json` | 可交互编排拓扑 |
| 候选问题 | `worklist kind=discovered_problem` | 列表 + 影响面 + 证据 |
| 诊断报告 | `reports/diagnosis/` | 根因候选 + 排除项 + 建议动作 |
| 经验库 | `experience` 表 | 权威经验 + 候选经验 |
| 一键诊断 | CLI 触发 | 输入步骤名 → 展示诊断进度与结果 |

**P4 待办闭环台**增 3 个新 kind 的处置动作（`kind→action` 注册表扩展）。

### 7.4 与 `notify` 衔接

**扩展 `notify.py`**：在 `failed_step` 告警消息中追加诊断提示：

```
[告警] failed_step=relations:gen, run_id=RUN-xxx
建议：orchestrator diagnose last
（诊断会自动沿拓扑追溯根因，预计 5 秒出报告）
```

**效果**：告警直达诊断入口，运维第一时间用上。

### 7.5 与 MCP 门面衔接（W2）

MCP 资源与工具（只读）：

```python
# 资源
mcp://orchestrator/topology              # 拓扑图
mcp://orchestrator/diagnosis/latest      # 最近诊断报告

# 工具（只读，不进首批写操作）
discover_candidates(days: int) -> list    # 扫描候选问题
diagnose_step(step: str) -> report        # 诊断指定步骤
```

**Agent 消费场景**：
- 「现在该跑什么」→ Agent 读拓扑图 + run_log，给出建议命令
- 「这步失败了影响什么」→ Agent 读 topology.affected_nodes，给出影响面
- 「这个现象之前出现过吗」→ Agent 读 experience 表，给出历史经验


## 第八章：持续用起来的三个机制

### 8.1 角色与时机

| 角色 | 何时用 | 用什么 |
|---|---|---|
| **运维/值班** | 主链失败后立即 | `diagnose last` |
| **合规工程师** | 每周一巡检 | `discover scan --days 7` |
| **制度起草人** | 需要溯源时 | 确认台 P7 检索 + P9 诊断 |
| **Agent** | 自动巡检 | MCP `discover_candidates` |
| **团队负责人** | 月度评审 | P9 经验库 + BENCHMARK |

### 8.2 衡量有效性的指标

| 指标 | 数据源 | 目标 |
|---|---|---|
| 诊断覆盖率 | 失败步骤中被 `diagnose` 覆盖的比例 | >80% |
| 根因命中率 | 诊断候选与实际根因的吻合率 | >70% |
| 平均定位时间 | 从失败到根因确认的时长 | 下降 50% |
| 经验复用率 | 诊断中命中已有经验的次数 | 逐月上升 |
| 重复问题率 | 同一根因二次出现的比例 | 逐月下降 |

**这些指标加入 `BENCHMARK.md`**（项目已有的回归基线），从 `run_log`/`gate_result`/`worklist`/`experience` 表算出。

### 8.3 持续优化的闭环

```
每周 discover → 候选问题 → diagnose → 根因报告
    ↓
人工确认 → 修复 → 重跑验证
    ↓
第 2 次出现 → 沉淀经验 → 晋升权威
    ↓
后续 diagnose 自动注入经验 → 定位更快
    ↓
经验失效（环境变化） → worklist 标记 deprecated → 重新验证
```


## 第九章：起步路径（从 0 到 1，四周）

**不要一次性全用上**——诊断能力本身需要"被使用才能验证有效"。

| 周 | 目标 | 动作 | 验收 |
|---|---|---|---|
| **第 1 周** | 理解链路 | 打开确认台 P9，看拓扑图；对照 `STEP_LAYERS` 理解主链/旁路/条件 | 能说清"哪个步骤失败影响哪些下游" |
| **第 2 周** | 跑通发现 | 手动 `discover scan --days 7`；看候选；对 1-2 个候选 `diagnose` | 产出 1 份诊断报告 |
| **第 3 周** | 真实场景 | 下次主链失败时，**第一个动作**用 `diagnose last`；对比"手动翻日志"与"诊断"的耗时 | 定位时间下降 |
| **第 4 周** | 沉淀经验 | 把第 3 周的问题沉淀为经验；晋升；验证下次 diagnose 是否注入 | `experience` 表首条权威经验 |

**四周后**：
- 把 `discover scan` 加入 `schedule.yaml`，形成例行巡检
- 把 `diagnose last` 写入值班 SOP
- 把 `discover` / `diagnose` 注册到 MCP，供 Agent 调用


## 第十章：最小可用清单

**如果只看一件事，做这件**：

```bash
# 主链失败后的第一个动作
orchestrator diagnose last
```

**为什么**：它把"翻 5 个日志 + 查 3 个表 + 人工推理"压缩为"一条命令 + 一份结构化报告"。这是诊断能力最直接的价值兑现点。

**推广方式**：把这条命令写进值班手册、写进 `notify` 的告警消息、写进确认台的失败页面——**让它在失败时被第一个看到**。


## 关键纪律（与项目既有纪律完全一致）

| 纪律 | 落地 |
|---|---|
| **零 Token** | 四阶段全部基于规则和拓扑遍历，不调用 LLM |
| **唯一事实源** | 拓扑图从 STEP_LAYERS/GOV_ARTIFACTS 派生；经验候选复用 worklist；权威经验入 experience 表 |
| **零硬依赖** | 全部新增为项目内模块，不引入外部服务 |
| **可观测降级** | 拓扑图不可用→诊断明示；经验库为空→诊断照常 |
| **不破坏主链** | 全部为旁路分析能力；不修改 `run_production_refresh.py` 的执行路径 |
| **人工闸门** | 经验晋升走 worklist resolve；根因报告只出建议不执行修复 |


## 完整方案总览

| 章 | 内容 | 落点 | Wave |
|---|---|---|---|
| **第一章** | 系统拓扑图（能力建设） | `gen_flow_map.py` 扩展 + `topology_api.py` + `gate_topology_completeness` | W1~W2 |
| **第二章** | 问题发现（能力建设） | `problem_discovery.py` + `commands/discover.py` + schedule 作业 | W2~W3 |
| **第三章** | 根因分析（能力建设） | `root_cause.py` + `commands/diagnose.py` | W3~W4 |
| **第四章** | Self-Improving（能力建设） | `memory.py` + experience 表 + worklist 3 kind | W4+ |
| **第五章** | 使用入口 | CLI（2 命令）+ 确认台 P9 + MCP（资源+工具） | 随能力建设同步 |
| **第六章** | 典型场景操作 | 5 个场景（巡检/失败/门禁/沉淀/批量） | 随能力建设同步 |
| **第七章** | 与现有工作流衔接 | schedule/worklist/确认台/notify/MCP | 随能力建设同步 |
| **第八章** | 持续使用机制 | 角色时机/有效性指标/优化闭环 | 上线后持续 |
| **第九章** | 起步路径（四周） | 从理解链路到沉淀经验 | 上线后执行 |
| **第十章** | 最小可用清单 | `diagnose last` 一个动作 | 立即 |


## 总结

**前四章是"建能力"，后六章是"用能力"——两者缺一不可。**

- **建**：拓扑图 → 问题发现 → 根因分析 → Self-Improving
- **用**：CLI + 确认台 P9 + MCP 三入口 → 5 个典型场景 → 与现有工作流衔接 → 持续使用机制 → 四周起步 → 最小可用清单

**核心原则**：诊断能力的价值不在"搭完了"，而在"失败时第一个用它、每周定时用它、重复问题时沉淀它"——三件事坚持四周，它就成为团队的免疫系统。