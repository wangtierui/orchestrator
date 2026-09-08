# regulatory_compliance_orchestrator

监管合规编排器：五源外部法规采集/清洗 → RFN 监管分类（归属/主题/底座）→ 内部制度扫描/对齐 →
制度起草的**统一工程**（2026-09-08 起为唯一演进点；原四仓只读冻结，见 §来源映射）。

阶段状态：P0–P7 全部落地。全链路命令经统一入口 `cli.py`。

## 1. 快速开始

```bash
# Windows 托管 python 3.13（已装 PyYAML/ruff/pytest/pypdf/pdfplumber/openpyxl/olefile/pywin32）
PY=C:\Users\wangtierui-lhl\.workbuddy\binaries\python\versions\3.13.12\python.exe

%PY% cli.py ping                       # 骨架自检
%PY% cli.py source list                # 五源 + internal 源目录（config/sources.yaml）
%PY% cli.py gates                      # 交付门禁（12 道，ALL_GATES 为准）
```

### 全流程命令（按数据流向）
| 阶段 | 命令 | 说明 |
|---|---|---|
| 外部采集 | （P1–P3 迁移的五源 collectors，经各源脚本 + clean_index） | gov/mof/nfra/pbc/supp |
| 清洗 | `modules/regulatory_scrapers/clean/run_clean_pipeline.py --project <src>` | 统一 39 列双轨产物 → data/cleaned |
| 索引 | `regulatory_scrapers/clean_index` 重建 | clean_index/index.json 最新快照 |
| 召回复核 | `modules/regulatory_classifier/recall_audit/run_retrieval_after_checks.py` | 四门禁 + scanner→build_outputs→report |
| 漂移核验 | `modules/regulatory_classifier/scripts/reconcile_clean_drift.py [--apply]` | RFN↔clean 桥表/漂移（gate 要求快照推进后先跑） |
| 内部摄取 | `cli.py internal index --source-dir <制度目录>` | 107 制度入库（IPN/指纹/正文，幂等） |
| 内部对齐 | `cli.py internal align` | 制度 → T0–T10 主题（R18 UNALIGNED 兜底） |
| 引用视图 | `cli.py internal merged` | 制度 × RFN 引用关联（merged_view.json，D-06 v1.0） |
| 交付门禁 | `cli.py gates` | 12 道全绿方可交付 |

## 2. 架构与数据流向（单向）
```
[外部官网] → scrapers 五源 raw → 统一清洗 → data/cleaned ──┬── clean_index（最新快照索引）
                                                            ├── classifier recall 召回/桥表 reconcile
                                                            ▼
                                            classifier RFN 归属表(8列) + 主题归属表(3列)
                                                   │  rfn/bridge(桥表 9列) / 40 底座 / 11 明细
                                                   ▼
                              merged_view.json（internal 107 制度 × 主题 × RFN 引用）
                                                   ▼
                              internal_policy_drafter 起草/修订（引用门禁 gate_citations）
```
唯一跨模块 import：classifier/scanner → `clean_index`（同仓 scraper 模块）；interfaces 为跨层唯一调用面。

## 3. 数据字典（字段级契约源）

程序可读契约（R24）——字段级定义以以下文件为准：

| 数据集 | 文件 | 字段/列（数量） |
|---|---|---|
| cleaned CSV | `modules/regulatory_scrapers/data/cleaned/{src}_cleaned_*.csv` | `interfaces/contract.py CLEANED_CSV_COLUMNS`（39 列） |
| 文件归属表 | `modules/regulatory_classifier/data/人身保险公司-文件归属表.csv` | 8 列：监管文件编号/文件名称/发文字号/发布日期/文件来源/时效状态/判定日期/编号备注 |
| 主题归属表 | `.../人身保险公司-主题归属表.csv` | 3 列：监管文件编号/主题/判定依据（主题枚举 T0上位法锚点+T1–T10） |
| RFN↔clean 桥表 | `.../data/rfn_clean_bridge.csv` | 9 列：见 `contract.RFN_CLEAN_BRIDGE_FIELDS`（Q1=A） |
| 数据底座 | `.../data/_t{n}_{base\|final\|matched\|citerefs}.json` × 40 | 键集见 `contract.{BASE,FINAL,MATCHED,CITEREFS}_KEYS` |
| 明细表 | `.../data/T{n}_{m}逐份条款引用与上位法依据明细表.csv` × 11 | 10 列（build_detail_tables.FIELDS） |
| RFN 索引 | `.../rfn/监管文件编号索引.csv` | 6 列（registry.INDEX_COLS，归属表派生） |
| 内部制度索引 | `modules/internal_policy_base/data/internal_policy_index.json` | records 字段见 indexer.PROCESSED_FIELDS（含 IPN/主题对齐） |
| 内部制度正文 | `.../processed/<ipn>_fulltext.json` | {ipn, text} |
| merged_view | `.../data/merged_view.json` | schema_version 1.0（D-06 冻结）；records: ipn/title/docno/primary_theme/associated_rfns[] |
| 时效状态缓存 | `modules/regulatory_scrapers/timeliness_review/verification_state.json` | {唯一键: {status/...}}（效力单源，N6/R3） |

### 枚举唯一源（config/enums.py，v3+internal）
- `TIMELINESS_STATUS` 7 值：valid/amended/repealed/partially_repealed/expired/pending/uncertain
- `SOURCE_SET` 5 值：gov/mof/nfra/pbc/supp；`BODY_SOURCE` 3 值
- `INTERNAL_STATUS` 5 值：draft/active/expiring/deprecated/archived
- `INTERNAL_FILE_TYPE`：policy/process/guideline/manual/other
- 编号体系：外部 `RFN-<16hex>`（rfn 派生），内部 `IPN-<16hex>`（D-03 独立空间）

## 4. 门禁（gates/）
hardcoded_paths / enum_values / flat_layout / no_duplicate_libs / contract /
hardcoded_snapshots / rfn_sync / rfn_drift / **citations** / timeliness_ssot ——
数量与实装状态**一律以 `gates/__init__.py ALL_GATES` 为准**（R23，禁文本写死）。
`require_impl=True` 为实装门禁（未实装即 FAIL）；`False` 为待接入占位（P0 骨架放行）。

## 5. 迁移现状与来源映射（原仓只读）
| 原仓（D:/WorkBuddy） | 新仓 modules/ | 阶段 |
|---|---|---|
| regulatory_scrapers | modules/regulatory_scrapers | P1–P3 |
| regulatory_classifier | modules/regulatory_classifier | P4–P5 |
| internal_policy_base（原空） | modules/internal_policy_base（**新实现**） | P6 |
| internal_policy_drafter | modules/internal_policy_drafter（verify/dump 迁入） | P7 |

## 6. 演进纪律
1. 原四仓与旧 std_lib 冻结不改；复用一律"复制进新仓后修改"。
2. 跨模块调用仅经 `interfaces/`；禁止 sys.path 盘符（gate_hardcoded_paths 强检）。
3. 枚举 import `config.enums`；契约以 `interfaces/contract.py` 为准。
4. 数据不入 git（`data/` ignore，D-05）；活跃数据按 `data_migration_manifest.json` 复制追踪；
   internal 107 制度可用 `cli.py internal index --source-dir <源目录>` 重新生成。
5. 交付前 `cli.py gates` 全绿；reconcile 快照推进后须先跑再重建底座（gate_rfn_drift）。
