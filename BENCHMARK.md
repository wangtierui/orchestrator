# BENCHMARK —— 交付基准登记（回归对照基线）

> 自动生成：tools/gen_benchmark.py @ 2026-09-28 14:30:35 | python 3.13.14
> 用途：数据重建/重构后重跑 `python tools/gen_benchmark.py` 刷新；数值漂移即回归信号。

## 1 门禁（gates/ALL_GATES）

实装 22 道（gates/gate_*.py）：
```
  gate_citations.py
  gate_clean_schema.py
  gate_config_integrity.py
  gate_contract.py
  gate_enum_values.py
  gate_field_aliases.py
  gate_flat_layout.py
  gate_hardcoded_paths.py
  gate_hardcoded_snapshots.py
  gate_import_bootstrap.py
  gate_no_cross_module_import.py
  gate_no_duplicate_libs.py
  gate_original_resolvable.py
  gate_provenance.py
  gate_relations.py
  gate_rfn_drift.py
  gate_rfn_sync.py
  gate_runtime_hygiene.py
  gate_secret_scan.py
  gate_sources_config.py
  gate_timeliness_ssot.py
  gate_watermark.py
```

运行：`python cli.py gates`（GatesRunner 汇总，exit code 非 0 即阻断）

## 2 自动化验收测试（pytest）

用例文件 33：`test_analysis_deliveries.py`、`test_base_publish.py`、`test_clean_index_portability.py`、`test_cli_facade.py`、`test_common_lib.py`、`test_contract_api.py`、`test_contract_manifest.py`、`test_crawler_extract.py`、`test_dedup_key_uniqueness.py`、`test_document_structure.py`、`test_drafter_pure.py`、`test_e2e_pipeline.py`、`test_excel_crawler_pipeline.py`、`test_gates_matrix.py`、`test_governance_store.py`、`test_governance_watermark.py`、`test_governance_worklist.py`、`test_interfaces_protocols.py`、`test_internal_original_paths.py`、`test_internal_policy_base.py`、`test_ipb_deep.py`、`test_ipb_extract_file.py`、`test_misc_pure.py`、`test_p2_governance_layer.py`、`test_relations.py`、`test_rich_object.py`、`test_scraper_std_core.py`、`test_scraper_std_extra.py`、`test_scraper_std_tables.py`、`test_scrapers_pure.py`、`test_ssot_convergence.py`、`test_std_lib_more.py`、`test_table_structured.py`
运行：`python -m pytest tests -q`

**覆盖率基线（只升不降）**：TOTAL 44%（采自 `.coverage`；刷新：`python -m coverage run -m pytest tests -q`）

## 3 数据基线

### 3.1 外部五源 cleaned（clean_index 快照，SSOT 索引）

| 源 | 快照日期 | 备注 |
|---|---|---|
| gov | 20260928 | 最新 cleaned |
| mof | 20260928 | 最新 cleaned |
| nfra | 20260928 | 最新 cleaned |
| pbc | 20260928 | 最新 cleaned |
| supp | 20260928 | 最新 cleaned |
| 合计 | — | 索引记录 16611 |

### 3.2 classifier 底座/明细/桥（数据血缘 R10 已注入 generated_*）

| 产物 | 数值 |
|---|---|
| 文件归属表行数 | 1162 |
| 主题归属表行数 | 1162 |
| base 底座合计（T1–T10） | 1073 |
| final 底座合计（含 cluster/finalized 血缘） | 1073 |
| 明细表份数 / 行数合计 | 11 / 1162 |
| RFN↔clean 溯源桥行数 | 943 |
| 时效核验 verification_state 记录 | 14797 |
| 各主题 final 记录数 | T1=196、T2=150、T3=76、T4=109、T5=142、T6=66、T7=35、T8=78、T9=78、T10=143 |

### 3.3 internal 制度库

| 产物 | 数值 |
|---|---|
| originals 原始制度 | 879 |
| processed 处理文件（fulltext/main/json/md） | 3793 |
| 条文结构 _clauses.json | 861 |
| 条文视图 _clauses.md | 861 |
| merged_view 记录 | 861 |

## 4 运行入口速查

| 命令 | 职责 |
|---|---|
| `python cli.py gates` | 22 道交付门禁（以 ALL_GATES 为准） |
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

## 6 语义质量基线（铸尺 / 优化方案 v2 · P0-2）

> 口径：全部取自既有事实源（金标准表 / `clause_index.validate_schema` / `relations_index` / `recall_audit`），**不新增模型与依赖**。
> 用途：任何语义增强（分句/结构/主题/关系/关联）**上线前后逐键比对**；任一指标劣化即回退。

### 6.1 主题判定（金标准）

- 样本总数 **1162**；可评样本（剔除 `无正文/registry自动登记`）**1015**（87.4%）
- 依据来源分层：`{'registry自动登记': 103, '上位法锚点': 9, '无正文': 44, '精读裁定': 202, '语义验证': 804}`

| 主题 | 条数 |
|---|---|
| T0上位法锚点 | 89 |
| T10数据治理与信息披露 | 143 |
| T1销售行为与消费者保护 | 196 |
| T2产品与精算制度 | 150 |
| T3资本与偿付能力监管 | 76 |
| T4公司治理与股权关联交易 | 109 |
| T5资金运用与资产负债管理 | 142 |
| T6养老与健康保险专项 | 66 |
| T7反洗钱与反恐怖融资 | 35 |
| T8机构准入与组织监管 | 78 |
| T9风险处置与案件合规 | 78 |

### 6.2 条文结构（既有 `validate_schema` 判据）

- 文件 **16611** / 条 **108409** / 章 **12313**，契约自检 `consistent=True`
- 降级/兜底比例：`degraded 84.35%` / `fallback 29.33%`
- 结构语义指标（「曾被静默放过」的直接堵漏项）：`title_swallow=0` / `tail_contam=92` / `space_contam=40` / `law_items=439`

### 6.3 关系抽取（`relations_index.jsonl`）

- 行数 **4192**；`relation_id` 唯一性 `True`（distinct 4192）
- 源侧条款定位 **2303/4192**（54.9%）；目标侧 **237**（5.7%）；双侧 **147**
- 定位来源分布：`{'': 1799, 'snippet': 90, 'src_offset': 2156, 'src_offset+snippet': 147}`

### 6.4 召回覆盖（`recall_audit` 既有产物）

- 产物目录存在 `True`；报告生成于 2026-09-28T13:16:55+0800
- 四门禁：`{'clean': True, 'validity': True, 'contract': True, 'schema': True}`

### 6.5 清洗体量

| 产物 | 条数 |
|---|---|
| gov_cleaned_20260928 | 13178 |
| mof_cleaned_20260928 | 870 |
| nfra_cleaned_20260926.quarantine | 5 |
| nfra_cleaned_20260927.quarantine | 5 |
| nfra_cleaned_20260928 | 1934 |
| nfra_cleaned_20260928.quarantine | 5 |
| pbc_cleaned_20260928 | 571 |
| supp_cleaned_20260926.quarantine | 4 |
| supp_cleaned_20260928 | 58 |

**纪律**：主题判定新增任何分类器时，须在 §6.1 的**可评样本**上报告 P/R，并**对齐**既有 `判定依据` 的「排名 + margin」留痕格式。
