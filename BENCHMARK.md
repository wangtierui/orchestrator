# BENCHMARK —— 交付基准登记（回归对照基线）

> 自动生成：tools/gen_benchmark.py @ 2026-09-29 00:26:25 | python 3.13.14
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
- 体裁分布（`parse_mode`）：`{'empty': 29, 'law': 2599, 'notice': 9140, 'plain': 4843}`　※ `degraded` 旧口径＝非 law 体裁计数，**非质量降级**（N-101）
- 质量信号：`empty 29（0.17%）` / `fallback 29.33%` / `invalid 94`
- 结构语义指标（「曾被静默放过」的直接堵漏项）：`title_swallow=0` / `tail_contam=92` / `space_contam=40` / `law_items=439`
- 但书计数（N-94 度量先行，**只披露不判定**）：宽档 `proviso=4540` 条 / 严档 `proviso_strict=925` 条　※ 用途：为"是否值得改解析器切分逻辑"提供量级证据

### 6.3 关系抽取（`relations_index.jsonl`）

- 行数 **4192**；`relation_id` 唯一性 `True`（distinct 4192）
- 源侧条款定位 **2303/4192**（54.9%）；目标侧 **609**（14.5%）；双侧 **489**
- 定位来源分布：`{'': 1769, 'snippet': 108, 'span': 12, 'src_offset': 1814, 'src_offset+snippet': 445, 'src_offset+span': 44}`
- 引用跨度（N-98 新增 / **N-106 起参与定位**：窗口未唯一命中时的退路）：`source_span` 4192/4192（100.0%）
- **定位正确性边界**（N-106，以跨度为锚；有目标键 1733 行）：一致 191 ／ **冲突 0** ／ 仅窗口 418 ／ 仅跨度 0 ／ 皆无 1124
- **窗口命中位置归因**（N-109，写入侧已**剔除前文**）：引用**之前** 0（**应为 0**，非 0 即有源侧自指残留）／ 引用之后 418 ／ 两处皆有 0 ／ 定位不到出处 0
- **互证分档**（N-112，零人工）：强证据（条号距引用名 ≤40 字符且全窗无竞争者）84 ／ 弱证据 334；距离分布 ≤40 84 ／ 40–80 285 ／ >80 49
  → W2 决议（**保持后向 100 字符**）：抽样证实 `>80` 行的 **88%** 为**长引用列表**（“依据《A》《B》《C》等，制定本法第X条”→ 条号必然偏远）→ 远距离**不是**可疑信号；故**不收窄**（会丢合法长引用命中）**不加宽**（无正确性增益）
- **唯一性不变式**（D5）：竞争者数 >0 的行 `0`（**应为 0**；>0 即「唯一命中才填」守卫被破坏 → 报警）；弱证据细分（供「该先看哪批」）：全窗独一份仅**位置偏远** 418（低可疑）／定位不到出处 0
  → D5 结论：`rivals` **结构上恒为 0**（「唯一命中才填」⇒目标表内本就唯一）→ 该维度**无信息量**，故改判为**不变式校验**而非分档依据；弱证据的真实分档仍以**距离**（≤40/40–80/>80）为准
  → **误定位下界 `0`（0.0%）**、上界 `418`（24.1%）　※ 下界＝窗口与跨度**冲突**（必有一方错）；上界额外计入「仅窗口」——但**前文剔除（N-109）后归因显示「引用之前」已为 0**，即「仅窗口」全部落在引用之后（合法位），故上界已**不宜读作「可能的错」**，宜读作「**未经跨度互证的量**」

### 6.4 召回覆盖（`recall_audit` 既有产物）

- 产物目录存在 `True`；报告生成于 2026-09-28T20:47:00+0800
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

### 6.6 P1 语义增强能力（当前环境）

- 语义增强能力：2/10 可用（hdbscan, umap）
- 指纹：可用 `['hdbscan', 'umap']`；未装 `8` 项

| 工具 | 状态 | 版本 | 对应方案项 |
|---|---|---|---|
| `aprcoie` | 未装 | — | v2 P3-1（可选，须先评估许可与体积） |
| `bertopic` | 未装 | — | v2 P1-5（主题内子簇语义化，可选） |
| `hanlp` | 未装 | — | v2 P1-3（分句/结构增强层） |
| `hdbscan` | 可用 | 0.8.44 | v2 P1-5（随 BERTopic） |
| `ltp` | 未装 | — | v2 P1-3（分句/结构增强层） |
| `paradedb` | 未装 | — | v2 P2-2（**须先做零服务替代评估**；且须同时覆盖 search_internal） |
| `signalgraph` | 未装 | — | v2 P3-2（可选；须先证明其**确定性**：同输入同输出） |
| `text2vec` | 未装 | — | v2 P1-1（主题辅助裁定）、P1-2（关联语义档） |
| `umap` | 可用 | 0.5.12 | v2 P1-5（随 BERTopic，不单独使用） |
| `weknora_docreader` | 未装 | — | v2 P2-1（只补版面分析；OCR 通路已存在，不重做） |

> 未装工具不阻断全链：调用方一律经 `std_lib.common_lib.semantic_tools` 探测后惰性导入，不可用时回退既有正则实现并**记录回退说明**（`fallback_notice()`）。清单：`config/schema/semantic_tools.json`；启用：`pip install -e .[semantic]`。
