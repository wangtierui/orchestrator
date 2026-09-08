# 监管合规治理编排仓（regulatory_compliance_orchestrator）

> **项目名称**：监管合规治理编排仓（五源法规采集 · RFN 监管分类 · 内部制度对齐 · 制度起草）
> **文档版本**：`v2.0.0`
> **维护团队**：数据治理与合规工程组
> **最后更新**：2026-09-08
> **演进状态**：本仓为唯一演进点（原四仓只读冻结，见 §7 来源映射）

---

## 1. 项目概述 (Project Overview)

- **背景与痛点**：保险机构合规治理依赖的监管文件散落五源官网（gov/mof/nfra/pbc/supp），采集/清洗/分类链路分布在四个独立仓库、脚本互不统一；内部制度正文（107 份）与外部监管依据之间缺少可核验的对齐关系，起草时引用文号/标题存在"臆造漂移"风险；时效（废止/修订）核验结果在 8 处副本间重复维护，无单一事实源。
- **核心目标**：构建单入口的监管合规编排器——五源采集清洗 → RFN（监管文件编号）唯一实体分类 → 内部制度对齐 → 起草条款对照素材，全部经**程序可读契约**与**交付门禁**收敛为可审计、可重建、可回归的单一数据管道。
- **技术栈全景**：
  - **后端核心**：Python 3.13+（脚本编排式，无常驻服务）；标准库 + PyYAML / requests / beautifulsoup4
  - **数据契约与门禁**：`interfaces/contract.py`（程序可读契约，R24）/ `config/enums.py`（受控枚举）/ `gates/`（ALL_GATES 12 道交付门禁，R23）
  - **测试与静态检查**：pytest（tests/）/ ruff（dev 依赖）
  - **可选外部组件**：PaddleOCR / pdfplumber（OCR，[ocr] extra）；`@pkulaw/mcp-cli` + 托管 Node（北大法宝时效核验 CLI，[mcp] extra，零 LLM 消耗）
  - **依赖管理**：Pyproject.toml（PEP 621，`[project.optional-dependencies]` 分 mcp/dev/ocr）

---

## 2. 目录结构规范与文件用途详解 (Directory Structure & File Manifest)

> **核心原则**：本仓为脚本编排型工程（无常驻服务、无 Web 框架），仍沿用"常规流程脚本 / 特殊工具脚本"分类，明确运维与数据治理责任：**常规流程脚本**（clean/classify/align/reconcile/gates 等）随数据推进被调度执行；**特殊/工具脚本**（flatten_collectors、gen_benchmark、一次性修复）由人工按需执行。

### 2.1 根目录与一级模块总览

```
regulatory_compliance_orchestrator/
├── cli.py                    # 【常规流程·入口】统一命令入口（gates/source/internal/classify/timeliness/draft/ping）
├── paths.py                  # 【常规流程】路径唯一解析（ROOT/MODULES_DIR/SOURCES_YAML…，禁盘符字面量）
├── config/                   # 【常规流程】配置层
│   ├── enums.py              #   受控枚举唯一源（SOURCE_SET/TIMELINESS_STATUS/doc_type/category…，含自检）
│   ├── loader.py             #   sources.yaml/ocr.yaml 唯一读取口（${VAR} 展开、collector 路由 API）
│   ├── sources.yaml          #   源目录唯一事实源（五源 collector/clean_project 字段消费，R15）
│   └── ocr.yaml              #   OCR 引擎配置
├── interfaces/               # 【常规流程】跨层唯一调用面（R4：模块间禁止直接互引）
│   └── contract.py           #   数据契约 SSOT：CLEANED_CSV_COLUMNS / BASE·FINAL·MATCHED·CITEREFS_KEYS /
│                              #   DETAIL_TABLE_FIELDS / RFN_CLEAN_BRIDGE_FIELDS / THEME_FIELDS…
├── modules/
│   ├── regulatory_scrapers/  # 【常规流程·外部域】五源采集/清洗/条文固定节点/时效核验
│   ├── regulatory_classifier/# 【常规流程·分类域】RFN 归属/主题/底座/明细/桥表/召回审计
│   ├── internal_policy_base/ # 【常规流程·内部域】制度扫描/摄取/条文抽取/对齐/merged 视图
│   └── internal_policy_drafter/  # 【常规流程·起草域】条款级对照素材 + 引用核验门禁
├── std_lib/                  # 【共享库单副本】scraper_std（doc_type/category/unified_schema/pkulaw_cli…）
│   └── common_lib/           #   fs_lock / io_atomic / logger（原子写与审计）
├── gates/                    # 【常规流程·门禁】ALL_GATES 12 道交付门禁（gates/__init__.py 为准，R23）
├── tests/                    # 【常规流程·验收】pytest：test_common_lib / test_e2e_pipeline（20 用例）
├── tools/                    # 【特殊工具】flatten_collectors（拍平，幂等 --fix）/ gen_benchmark（基准刷新）
├── reports/                  # 【特殊辅助】蓝图/检视/README 撰写规范/物理目录/drift 清单（权威交付文档）
├── BENCHMARK.md              # 【特殊辅助·基准登记】交付基准（tools/gen_benchmark.py 生成，回归对照）
├── data_migration_manifest.json  # 【特殊工具】旧仓→新仓数据复制追踪（P0 产物）
├── pyproject.toml            # 【元数据】PEP 621 依赖与 optional-dependencies
└── README.md                 # 本文档
```

### 2.2 核心文件用途清单（脚本分类明细）

*按"核心入口 / 各模块通识"分级；全量文件级清单见 `reports/物理目录结构.txt`。*

| 路径 (Path) | 类型 | 脚本分类 (Script Type) | 用途描述 (Description) | 依赖/被调用方 (Caller) |
| :--- | :--- | :--- | :--- | :--- |
| `cli.py` | 文件 | **常规流程（入口）** | 统一命令分发：`gates / source / internal / classify / timeliness / draft / ping` | 由用户/automation 调用；`python cli.py <cmd>` |
| `paths.py` | 文件 | **常规流程** | ROOT/MODULES_DIR/SOURCES_YAML/OCR_YAML 等路径唯一解析 | 被仓内几乎全部模块引用（R4，禁盘符） |
| `config/enums.py` | 文件 | **常规流程** | 受控枚举 SSOT：`SOURCE_SET`(5)/`TIMELINESS_STATUS`(7)/`INTERNAL_STATUS`(5)/G1·G2 文种与位阶；`assert_enum_bindings()` 自检 | 被 `gates/gate_enum_values` 与各清洗/分类模块引用 |
| `config/loader.py` | 文件 | **常规流程** | sources.yaml/ocr.yaml 唯一读取口；`active_source_ids/collector_module/collector_path`（R15 路由） | 被 `cli source`、clean `--project`、编排引用 |
| `config/sources.yaml` | 文件 | **常规流程（配置）** | 源目录唯一事实源：`collector: collectors.<模块>` + `clean_project` | 启动即被 loader 消费（新增源走 `source add` checklist） |
| `interfaces/contract.py` | 文件 | **常规流程** | 数据契约 SSOT（列头/键集/枚举域/别名映射），gate_contract 逐列比对依据 | 被 gates、全部生成脚本引用 |
| `modules/regulatory_scrapers/` | 目录 | **常规流程（外部域）** | 采集(collectors/)、清洗(clean/)、快照索引(clean_index/)、条文固定节点(clause_index/)、时效核验(timeliness_review/) | 被 classifier 经 clean_index 唯一消费（interfaces 面） |
| `.../clean/run_clean_pipeline.py` | 文件 | **常规流程** | 单源统一清洗 → `data/cleaned/{src}_cleaned_{date}.csv/jsonl`（39 列契约 + 空值告警 + 原子写） | 每源采集后人工/automation 执行；`--project` 由 sources.yaml 派生 |
| `.../timeliness_review/verify_missing.py` | 文件 | **常规流程（定时/核验）** | 效力缺失记录北大法宝核验（R13 三态 success/partial/unavailable，摘要落盘，降级不误标） | `cli.py timeliness verify` 子进程；依赖 `@pkulaw/mcp-cli` + token env |
| `modules/regulatory_classifier/` | 目录 | **常规流程（分类域）** | RFN 归属/主题（rfn/registry）、底座/明细生成（scripts/）、召回审计（recall_audit/） | 消费 scrapers clean；供给 internal merged / drafter |
| `.../scripts/classify.py` | 文件 | **常规流程** | 主题底座强序重建编排（R8：base→cluster→match→detail→upper→clause_graph，hash 断点幂等） | `cli.py classify`；数据变更后重跑 |
| `.../scripts/build_base_from_attr.py` | 文件 | **常规流程** | 归属表→各主题 `_t{n}_base.json`（含 R10 provenance：generated_by/at/source_snapshot） | 被 classify base 子步调用；`--check` 校验模式 |
| `.../scripts/build_detail_tables.py` | 文件 | **常规流程** | 逐份条款引用与上位法依据明细表（`DETAIL_TABLE_FIELDS` 契约 12 列，R10 血缘列） | classify detail 子步 |
| `.../scripts/reconcile_clean_drift.py` | 文件 | **常规流程** | RFN↔clean 溯源桥 + 漂移核验（R7：桥表唯一写者；快照推进后须先跑再重建，gate_rfn_drift 强制） | 清洗/快照推进后执行；`--apply` 才刷新归属表展示字段 |
| `.../scripts/cluster_by_keywords.py` | 文件 | **常规流程** | final 子主题聚类（T1–T10；T9/T10 用 RFN 键 u_fix 冻结，R8 修复） | classify cluster 子步 |
| `.../scripts/report_builders/build_theme_report.py` | 文件 | **特殊工具脚本（报告生成）** | 按 THEME_MAP 生成 T0–T10 主题监管文件视图 md → `docs/reports/`（R9，R16 无硬编码） | 手动/发布前执行 |
| `modules/internal_policy_base/` | 目录 | **常规流程（内部域）** | indexer/extract/scan/align/merged：107 制度摄取→条文抽取→主题对齐→merged 视图 | 消费 classifier RFN；供给 drafter |
| `.../indexer.py` | 文件 | **常规流程** | 制度入库（IPN-16hex 指纹、正文、条文结构 `_clauses.json` + 渲染 `_clauses.md`，R21） | `cli.py internal index` |
| `.../merged.py` | 文件 | **常规流程** | 制度 × RFN 引用关联 → `merged_view.json`（D-06 v1.0；associated_rfns + matched_by） | `cli.py internal merged`；被 gate_citations 消费 |
| `modules/internal_policy_drafter/` | 目录 | **常规流程（起草域）** | 起草条款对照素材（build_draft_clause_view）与引用核验（verify_regulatory_citations） | 读 merged_view + clauses |
| `.../scripts/build_draft_clause_view.py` | 文件 | **常规流程** | 条款级端到端对照素材（P8：merged_view × clauses → 每制度 md，自动链接 RFN/⚠待核文号） | `cli.py draft` |
| `.../scripts/verify_regulatory_citations.py` | 文件 | **特殊工具脚本（起草门禁）** | 对齐表 R-01~R-43 + 文档监管引用核验（`--strict` 门禁；旧仓 docs 权威件链路） | 起草/修订制度后人工执行 |
| `gates/` | 目录 | **常规流程（质量门禁）** | 12 道门禁实现（gate_*.py）；数量/实装以 `ALL_GATES` 为准（R23） | `python cli.py gates`；提交/交付前必过 |
| `tests/` | 目录 | **常规流程（验收）** | pytest：`test_common_lib`（8）+ `test_e2e_pipeline`（11 端到端断言：五源快照/归属/底座契约/门禁全绿/主题报告…） | `python -m pytest tests -q` |
| `tools/gen_benchmark.py` | 文件 | **特殊工具脚本（基准登记）** | 聚合当前产物统计渲染 `BENCHMARK.md`（数据重建后重跑刷新，回归对照） | 手动执行 |
| `tools/flatten_collectors.py` | 文件 | **特殊工具脚本（一次性迁移）** | collectors 物理拍平（单层 + 源前缀 + 互引改写；`--fix` 幂等修复） | 仅目录重构时执行 |

---

## 3. 核心数据字典与物理模型 (Data Dictionary & Schema)

> **规范说明**：字段级定义以 `interfaces/contract.py` + `config/enums.py` 为单一事实来源（R24），下表为语义速查；下游一律经契约函数读取，禁止按列号/猜列直接解析。

### 3.1 实体关系总览 (ER Diagram)

```mermaid
erDiagram
    SOURCE ||--o{ CLEANED : "collect(5源快照)"
    CLEANED ||--o{ RFN_ATTR : "归属(reconcile/reconciled)"
    RFN_ATTR ||--|| THEME_ATTR : "监管文件编号"
    RFN_ATTR ||--o{ BASE : "派生(T1-T10)"
    BASE ||--|| FINAL : "cluster(子主题)"
    RFN_ATTR ||--o{ BRIDGE : "RFN-clean锚"
    STATE ||--o{ RFN_ATTR : "时效(SSOT传播)"
    INTERNAL ||--o{ PROCESSED : "107制度"
    PROCESSED ||--o{ CLAUSES : "条文结构"
    RFN_ATTR ||--o{ MERGED : "associated_rfns"
    INTERNAL ||--o{ MERGED : "制度侧"
    MERGED ||--o{ DRAFT : "条款对照素材"
```
- 数据血缘：各底座/明细/桥表/合并视图记录含 **R10 provenance**（`generated_by/generated_at/source_snapshot/finalized_*`），gate_provenance 强制覆盖。

### 3.2 核心数据对象定义

| 数据项 (Field) | 类型 (Type) | 必填 | 枚举/格式约束 (Enum/Format) | 业务含义/示例 | 所属表/模块 (Scope) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `监管文件编号` | `string` | 是 | `^RFN-[0-9a-f]{16}$` | 外部监管文件全局唯一实体标识（文号\|标题派生）例：`RFN-4ab1a1555817bbc8` | 归属/主题归属/底座/桥 |
| `时效状态` | `enum` | 是 | 7 值：`valid/amended/repealed/partially_repealed/expired/pending/uncertain` | 文件效力状态；SSOT=verification_state → 归属表 → 底座（R3 单路传播） | 归属表、base `eff_status` |
| `source` / `文件来源` | `enum` | 是 | 5 值：`gov/mof/nfra/pbc/supp` | 五源标识（子源经 SOURCE_ALIASES 归并） | cleaned/base/file_src |
| `主题` | `enum` | 是 | `T0`(上位法锚点)+`T1..T10`（THEME_MAP） | 主题归属（完整名见 `rfn.THEME_MAP`） | 主题归属表、明细 |
| `cluster` | `string` | 是 | 关键词聚类值或 `U未分类` | final 子主题（T9/T10 以 RFN u_fix 冻结） | final |
| `associated_rfns[]` | `array` | 否 | 元素：`{rfn,title,docno,matched_by}` | 内部制度引用的监管依据（matched_by∈{docno_sig,title}） | merged_view |
| `generated_by/at/source_snapshot` | `string` | 是 | 时间 `%Y-%m-%d %H:%M:%S` | 行级血缘（R10）：写者/批次时间/源快照(mtime) | base/base 派生链 |

### 3.3 枚举值全量清单（关键受控枚举，源=config/enums.py）

- **`SOURCE_SET`**：`gov`（国务院及地方 gov.cn）/ `mof`（财政部）/ `nfra`（金融监管总局）/ `pbc`（人民银行）/ `supp`（补充法规库）——sources.yaml enabled 集合与之双向一致（gate_sources_config）。
- **`TIMELINESS_STATUS`（时效状态，7 值）**：`valid` 现行有效 / `amended` 已修改 / `repealed` 已废止 / `partially_repealed` 部分废止 / `expired` 已失效 / `pending` 核验中(占位) / `uncertain` 不确定。语义：北大法宝等核验结论（fresh≤90 日）→ 归属表人工权威 → 底座派生。
- **`INTERNAL_STATUS`（内部制度，5 值）**：`draft / active / expiring / deprecated / archived`。
- **`INTERNAL_FILE_TYPE`**：`policy / process / guideline / manual / other`。
- **G1 文种（doc_type）**：`FILE_TYPES` 60 项有序列表（命令/通知/条例/办法…）；`DOC_TYPE_GROUP`（规划部署/制度治理/说明解释/文书凭证等）；别名 `DOC_TYPE_ALIAS={令→命令, 法→法律}`。
- **G2 效力位阶（category）**：13 级英文枚举 `constitution(1)…other(12)`，数值型 `AUTHORITY_RANK`（司法解释 2.5）；存量中文映射 `CATEGORY_MAP`（法律解释=司法解释，更正 3）。
- **编号空间**：外部 `RFN-<16hex>`；内部 `IPN-<16hex>`（独立空间不冲突）；`BRIDGE_RELATION`：`self/refresh/supersede`。

---

## 4. 数据流转与拓扑 (Data Flow & Topology)

### 4.1 端到端数据流图（Mermaid）

```mermaid
flowchart LR
    subgraph SRC[五源官网]
        G[gov] ; M[mof] ; N[nfra] ; P[pbc] ; S[supp]
    end
    G & M & N & P & S -->|collectors/run_clean_pipeline| C[(data/cleaned<br/>39列双轨)]
    C --> CI[clean_index 快照索引]
    C -->|recall 召回 + 条文节点| CL[(data/clauses)]
    C -->|reconcile 桥/漂移| B[(rfn_clean_bridge)]
    V[北大法宝核验] -->|verify_missing R13三态| ST[(verification_state)] -->|R3 单路传播| AT[(文件归属表)]
    AT -->|build_base+cluster| BS[(_t*_base/final 40底座)]
    CI --> R[retrieval 四门禁编排]
    AT --> R
    BS -->|match/detail/upper| DT[(明细表 11份)]
    R --->|scanner→build_outputs→report| OUT[recall_audit/output]
    INT[内部制度 originals] -->|indexer R21| PC[(processed<br/>fulltext/clauses.json/md)]
    PC & BS -->|align + merged| MV[(merged_view)]
    MV -->|build_draft_clause_view| DK[(draft_clause 条款对照素材)]
    MV -->|gate_citations| G8{12道交付门禁 gates}
    ST --> G8
    G8 -->|全绿| OK[交付]
    classDef src fill:#e1d5e7
    classDef store fill:#d5e8d4
    class G,M,N,P,S src
    class C,CL,B,ST,AT,BS,DT,PC,MV,DK store
```
单向依赖纪律：scraper → classifier → internal_base → drafter；跨模块仅经 `interfaces/`；同仓唯一模块互引 = classifier→clean_index。

### 4.2 数据处理管道明细

| 阶段 (Stage) | 数据源 (Source) | 目标存储 (Target) | 转换逻辑 (Transform) | 所属脚本/Job | 脚本分类 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Extract 采集** | 五源官网 | `data/raw` → collectors 输出 | 各源 scraper（gov/mof/nfra/pbc/supp，源前缀拍平命名） | `modules/.../collectors/<src>_*.py` | 常规流程（采集） |
| **Transform 清洗** | raw | `data/cleaned/{src}_cleaned_{date}.{csv,jsonl}` | unified_schema 归一（39 列契约 + enum 归并 + 空值告警阈值 3%） | `clean/run_clean_pipeline.py --project <src>` | **常规流程** |
| **时效核验** | cleaned 效力空记录 | `verification_state.json` + 归属表 | 北大法宝 CLI 判定（7 值）；R13 三态摘要；降级不误标 | `timeliness_review/verify_missing.py` | **常规流程（定时/需 token）** |
| **Load 底座** | 归属表（权威） | `_t{n}_base/final.json`（40） | base 投影（R10 血缘）→ cluster 子主题 | `classify --steps base,cluster` | 常规流程（幂等断点） |
| **关联 Load** | base/final + clean | 明细表（11）与桥表（11 列） | 条款引用/上位法抽取；RFN↔clean 漂移判定 | `build_detail_tables` / `reconcile_clean_drift` | 常规流程 |
| **Index 内部对齐** | internal originals | `processed/*_clauses.{json,md}` + `merged_view` | OCR→全文→条文结构（R21 章条抽取+MD 视图）→主题对齐×RFN 引用 | `cli.py internal index/align/merged` | 常规流程 |
| **Export 起草** | merged_view | `drafter/data/draft_clause/*.md` | 条款级对照素材（逐条链接 RFN / ⚠ 待核文号） | `cli.py draft` | 常规流程（P8） |
| **Validate 门禁** | 全仓数据/代码 | gates 报告 | 12 道 ALL_GATES（契约/枚举/拍平/血缘/漂移/时效 SSOT…） | `cli.py gates` | 常规流程（阻断） |

---

## 5. 自动化任务节点与门禁设置 (Automation & Guardrails)

### 5.1 定时与事件驱动任务清单（IDE automation，R11）

| 任务名称 | 触发方式 (Trigger) | 执行动作 (Action) | 脚本/入口位置 | 脚本分类 | 失败处理 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `orchestrator-五源周采集与漂移核验` | Cron 每周二 01:00 | 五源采集→清洗→快照重建→retrieval 编排→reconcile 桥/漂移→`cli.py gates` | `cli.py gates` + 各模块脚本（prompt 编排） | 常规流程（定时批） | 门禁 FAIL 输出清单留人工，不静默通过 |
| `orchestrator-每日检索门禁核验` | Cron 每日 06:00 | retrieval 四门禁编排（签名幂等，变化则重跑）→ `cli.py gates` 全绿确认 | `recall_audit/run_retrieval_after_checks.py` + `cli.py gates` | 常规流程（定时） | 任一 FAIL 输出失败门禁与原因清单，不改数据 |

> 真实核验（北大法宝）运行需环境：`PKULAW_NODE_EXE`/`PKULAW_PKG_DIR`（托管 Node + `@pkulaw/mcp-cli`）与 token 文件 `.pkulaw_token`（git 忽略）。

### 5.2 数据与质量门禁 (Quality Gates)

- **代码门禁**：
  - `ruff check .` 零 Error（手动执行；`[dev]` extra）。
  - 自动化验收 `pytest tests -q`（20 用例：单元 + 端到端流水线断言）。
- **数据门禁（写入/交付拦截）**：
  - 数据契约：归属表/明细/底座/桥 列头与键集须匹配 `interfaces/contract.py`（gate_contract 逐列比对，超集允许、缺必报）。
  - 受控枚举：所有枚举取值 ∈ `config/enums.py`（gate_enum_values）。
  - 时效单源：SSOT `verification_state` → 归属表 → 底座逐层一致（gate_timeliness_ssot；pending/陈旧/时间差容忍语义）。
  - RFN 一致性：归属表与 40 底座/11 明细 RFN 全一致（gate_rfn_sync）；快照推进后未跑 reconcile 即阻断重建（gate_rfn_drift）。
  - 数据血缘：底座/明细/桥表 provenance 字段覆盖 100%（gate_provenance）。
  - 制度引用：merged_view associated_rfns 全部在 classifier 存在（gate_citations）；起草引用核验见 drafter `verify_regulatory_citations.py --strict`（R-01~R-43 对齐表，旧仓 docs 权威件）。
  - 目录拍平：modules data/docs 禁止未经白名单的子目录（gate_flat_layout）。
  - 硬编码零容忍：盘符字面量（gate_hardcoded_paths）与 cleaned 快照日期 N-3 外推（gate_hardcoded_snapshots）扫描。
- **发布门禁**：
  - 交付前 `python cli.py gates` **全绿（PASS）** 方可；ALL_GATES 数量/实装以 `gates/__init__.py` 为准（R23，禁止文本写死）。
  - 快照/归属表/时效数据变更纪律：先 reconcile → 再重建底座链（classify）→ gates → 刷新 `BENCHMARK.md`；禁手动直接改归属表核心字段（应经 reconcile C1/核验路径，C2 一律人工）。

---

## 6. 环境搭建与本地开发 (Quick Start)

1. **克隆代码**：
   ```bash
   git clone <repo-url> regulatory_compliance_orchestrator
   cd regulatory_compliance_orchestrator
   ```
2. **准备 Python 运行时**（本机采用托管 Python 3.13）：
   ```bash
   PY=C:\Users\wangtierui-lhl\.workbuddy\binaries\python\versions\3.13.12\python.exe
   %PY% -m pip install -e ".[dev]"        # 或按需 [ocr] / [mcp]
   ```
3. **数据就绪**：活跃数据（cleaned/归属表/底座/internal 等）在 `data/` 下不入 git——按 `data_migration_manifest.json` 从备份复制，或重新生成（内部 107 制度：`%PY% cli.py internal index --source-dir <制度目录>`）。
4. **骨架自检**：
   ```bash
   %PY% cli.py ping                      # 骨架自检
   %PY% cli.py source list               # 五源 + internal（sources.yaml 唯一事实源）
   ```
5. **外部数据推进（按需）**：
   ```bash
   # 清洗单源（--project 由 sources.yaml 派生；raw 缺省自动探测）
   %PY% modules\regulatory_scrapers\clean\run_clean_pipeline.py --project <gov|mof|nfra|pbc|supp> [--raw <path>]
   # 底座强序重建（R8 幂等断点：base→cluster→match→detail→upper→clause_graph）
   %PY% cli.py classify --all [--steps base,cluster,detail,...]
   # 漂移核验/桥表（快照推进后必跑，gate_rfn_drift 前置）
   %PY% modules\regulatory_classifier\scripts\reconcile_clean_drift.py [--apply]
   ```
6. **时效核验（可选，需 token + CLI）**：
   ```bash
   set PKULAW_NODE_EXE=C:\Users\wangtierui-lhl\.workbuddy\binaries\node\versions\22.22.2-2\node.exe
   set PKULAW_PKG_DIR=C:\Users\wangtierui-lhl\.workbuddy\binaries\.cache\pkulaw-mcp\node_modules\@pkulaw\mcp-cli
   %PY% cli.py timeliness verify --source gov --probe 1    # 冒烟；--source all 全量
   ```
   > R13 三态 exit：`0=success / 2=partial(可续跑) / 3=unavailable(降级不误标)`；token 位于 `modules/.../timeliness_review/.pkulaw_token`（git 忽略）。
7. **内部制度链路**：
   ```bash
   %PY% cli.py internal index --source-dir <制度目录>     # 107 制度摄取（幂等）
   %PY% cli.py internal align                              # 主题对齐（UNALIGNED 兜底）
   %PY% cli.py internal merged                             # 制度×RFN 引用视图
   %PY% cli.py draft                                       # 条款级对照素材（P8）
   ```
8. **交付验证**：
   ```bash
   %PY% cli.py gates                    # 12 道全绿
   %PY% python -m pytest tests -q       # 20 用例
   %PY% python tools\gen_benchmark.py   # 刷新交付基准（数据重建后执行）
   ```
   > 门禁示意输出：`PASS: 全部门禁通过`；任一 FAIL 会给出问题明细，修复后重跑，不静默放行。

---

## 7. 附录与延伸阅读

- **架构与演进文档（ADR 类比）**：参见 `reports/`（整体重构方案评估 v3 / 最终实施蓝图 v1.1 + 检视报告 / 专项评估 v2 / 端到端联动流程图.mermaid）。
- **README 撰写规范与内容大纲**：参见 `reports/README撰写规范与内容大纲.md`（本文件依其结构撰写）。
- **交付基准登记**：参见 `BENCHMARK.md`（`tools/gen_benchmark.py` 生成：门禁/测试/cleaned 快照/归属 1059/base·final 1050/明细 11/桥 823/时效 state/内部 107/merged 107）。
- **来源映射（原仓只读冻结，R19）**：

  | 原仓（D:/WorkBuddy） | 新仓 modules/ | 迁移阶段 |
  |---|---|---|
  | regulatory_scrapers | `modules/regulatory_scrapers` | P1–P3 |
  | regulatory_classifier | `modules/regulatory_classifier` | P4–P5 |
  | internal_policy_base（原空） | `modules/internal_policy_base`（新实现） | P6 |
  | internal_policy_drafter | `modules/internal_policy_drafter`（verify/dump 迁入） | P7 |

- **演进纪律**：
  1. 原四仓与旧 std_lib 冻结只读；复用一律"复制进新仓后修改"。
  2. 跨模块调用仅经 `interfaces/`；禁止盘符字面量（gate_hardcoded_paths 强检）。
  3. 枚举 import `config.enums`；契约以 `interfaces/contract.py` 为准；来源以 `config/sources.yaml` 为准。
  4. 数据不入 git（`data/` ignore）；活跃数据按 `data_migration_manifest.json` 复制追踪。
  5. 交付前 `cli.py gates` 全绿；快照推进先 reconcile 再重建底座（gate_rfn_drift）。
  6. 运行核验/采集等外部依赖任务的执行环境与 token 纪律见 §5.1（automation 词内已内置）。
  7. 远程同步：每次提交后自动 `git push origin main`（本地 `.git/hooks/post-commit`，fast-forward 语义；push 失败不阻断 commit，手工 `git push origin main` 补推）。

---

### 📌 撰写提示（分类与维护约定，与规范文档一致）

1. **脚本分类逻辑**：本仓为数据管道型工程，"常规流程脚本"指随数据推进按 §5 调度执行的链路成员（含完整摘要/审计输出）；"特殊工具脚本"指一次性/运维辅助（迁移、拍平、基准登记、报告生成），运行前建议双人复核。
2. **文件级颗粒度**：本文档 2.2 按"核心入口 + 模块通识"分级列示，全量文件清单见 `reports/物理目录结构.txt`，避免 README 臃肿。
3. **依赖关系链**：2.2 表格"依赖/被调用方"列给出调用层级（如 base 生成器被 classify 子步调用、merged_view 被 gate_citations/draft 消费），助新人理解依赖方向。
4. **Mermaid 渲染**：§3.1/§4.1 图表在 GitHub/GitLab 原生渲染；本地可用 VS Code Markdown Preview Mermaid 支持查看。
5. **保持同步**：本节内容随 R 系列重构持续演进，新增模块/命令/门禁后请同步更新 2.2 与 §5（门禁数量一律以 `gates/__init__.py ALL_GATES` 为准）。
