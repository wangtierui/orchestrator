# BENCHMARK —— 交付基准登记（回归对照基线）

> 自动生成：tools/gen_benchmark.py @ 2026-09-13 18:23:21 | python 3.13.14
> 用途：数据重建/重构后重跑 `python tools/gen_benchmark.py` 刷新；数值漂移即回归信号。

## 1 门禁（gates/ALL_GATES）

实装 15 道（gates/gate_*.py）：
```
  gate_citations.py
  gate_contract.py
  gate_enum_values.py
  gate_field_aliases.py
  gate_flat_layout.py
  gate_hardcoded_paths.py
  gate_hardcoded_snapshots.py
  gate_no_duplicate_libs.py
  gate_original_resolvable.py
  gate_provenance.py
  gate_rfn_drift.py
  gate_rfn_sync.py
  gate_secret_scan.py
  gate_sources_config.py
  gate_timeliness_ssot.py
```

运行：`python cli.py gates`（GatesRunner 汇总，exit code 非 0 即阻断）

## 2 自动化验收测试（pytest）

用例文件 23：`test_analysis_deliveries.py`、`test_base_publish.py`、`test_clean_index_portability.py`、`test_cli_facade.py`、`test_common_lib.py`、`test_contract_api.py`、`test_crawler_extract.py`、`test_drafter_pure.py`、`test_e2e_pipeline.py`、`test_excel_crawler_pipeline.py`、`test_internal_original_paths.py`、`test_internal_policy_base.py`、`test_ipb_deep.py`、`test_ipb_extract_file.py`、`test_misc_pure.py`、`test_rich_object.py`、`test_scraper_std_core.py`、`test_scraper_std_extra.py`、`test_scraper_std_tables.py`、`test_scrapers_pure.py`、`test_ssot_convergence.py`、`test_std_lib_more.py`、`test_table_structured.py`
运行：`python -m pytest tests -q`

**覆盖率基线（只升不降）**：TOTAL 23%（采自 `.coverage`；刷新：`python -m coverage run -m pytest tests -q`）

## 3 数据基线

### 3.1 外部五源 cleaned（clean_index 快照，SSOT 索引）

| 源 | 快照日期 | 备注 |
|---|---|---|
| gov | 20260912 | 最新 cleaned |
| mof | 20260912 | 最新 cleaned |
| nfra | 20260912 | 最新 cleaned |
| pbc | 20260912 | 最新 cleaned |
| supp | 20260912 | 最新 cleaned |
| 合计 | — | 索引记录 4043 |

### 3.2 classifier 底座/明细/桥（数据血缘 R10 已注入 generated_*）

| 产物 | 数值 |
|---|---|
| 文件归属表行数 | 1060 |
| 主题归属表行数 | 1060 |
| base 底座合计（T1–T10） | 1051 |
| final 底座合计（含 cluster/finalized 血缘） | 1051 |
| 明细表份数 / 行数合计 | 11 / 1060 |
| RFN↔clean 溯源桥行数 | 828 |
| 时效核验 verification_state 记录 | 3157 |
| 各主题 final 记录数 | T1=195、T2=149、T3=75、T4=105、T5=139、T6=60、T7=35、T8=75、T9=78、T10=140 |

### 3.3 internal 制度库

| 产物 | 数值 |
|---|---|
| originals 原始制度 | 108 |
| processed 处理文件（fulltext/main/json/md） | 4481 |
| 条文结构 _clauses.json | 1032 |
| 条文视图 _clauses.md | 1032 |
| merged_view 记录 | 957 |

## 4 运行入口速查

| 命令 | 职责 |
|---|---|
| `python cli.py gates` | 15 道交付门禁（以 ALL_GATES 为准） |
| `python cli.py classify --all --steps base,cluster,match,detail,upper,clause_graph` | 底座强序重建（R8 幂等断点） |
| `python cli.py source list / add --id` | 源目录路由（R15） |
| `python cli.py internal index/align/merged` | 内部制度链路 |
| `python cli.py timeliness verify --source all` | 时效核验三态（R13，需北大法宝 token） |
| `clean\run_clean_pipeline.py --project <源>` | 单源清洗 |
| `recall_audit\run_retrieval_after_checks.py` | retrieval 四门禁编排（幂等） |
| `python tools/gen_benchmark.py` | 刷新本基准 |

## 5 回归说明

1. 归属表/时效数据变更后：重跑 `classify` 底座链 → `reconcile_clean_drift`（桥）→ gates → 刷新本表。
2. internal 源变后：`internal index`（backfill_clauses 幂等）→ `internal align` → merged 视图。
3. 任一基线与上表不符且非预期升级 → 先查对应门禁 FAIL 输出，勿静默覆盖。
4. 本表只登记当前仓产物；历史一次性脚本（旧仓）不在此列。
