# ADR-0013 双轨决策：cleaned CSV 保留 / clauses MD 默认关

- **状态**：已采纳（2026-10-15，W4；对应方案 §6.3.2）
- **决策流程**：方案 §6.3.2 四步法（消费面盘点 → 无消费方默认关 → 有消费方评估按需 →
  登记 ADR）。消费面盘点由 code-explorer 子代理全仓实证（文件:行号 级证据），
  汇总见《双轨消费面盘点》结论（本 ADR 直接引用）。

## 背景

cleaned 快照每轮双轨产出（`data/cleaned/{src}_cleaned_{date}.{csv,jsonl}`，
gov CSV 实测 581MB），clauses 也双轨产出（`{src}_clauses_{date}.{jsonl,md}`，MD 合计
147MB）。每轮全量重写的成本是真实的；删除轨是破坏面动作，必须先有消费面证据。

## 消费面盘点结论（实证）

**cleaned CSV——有真实生产消费面（10 处内容读点 + 3 处存在性/sha 依赖 + 1 条契约）**：

- 内容读点（生产必需）：recall_audit/scanner（召回主输入）、build_detail_tables、
  verify_missing / authority_backfill_verify（时效核验候选）、
  filter_clean_relevance（相关性分层，链内步骤）、reconcile_clean_drift（漂移索引）、
  gate_timeliness_ssot / gate_rfn_drift（门禁判据）、recall_audit/build_outputs
  （快照日期）、semantic_assist（抽样分析）；
- 存在性/sha 依赖：recall Gate 1（latest csv 存在 + sha256 与索引一致）、
  clean_index.validate_files、gate_hardcoded_snapshots 的 lint PAT（含 csv 字面量）；
- 契约：`cleaned_csv_columns`（39 列，unified_schema.CSV_COLUMNS 唯一定义，
  contract_manifest 登记，gate_contract 三元断言 + 单测覆盖）。

**clauses MD——程序读取方 0、契约 0、门禁判据 0、人工报告数据源引用 0**：

- 全部条文消费方走 JSONL：base_publish/build_external、build_theme_report、
  clause_locator、dump_external_articles、clause_index_api、gate_clean_schema、
  gen_benchmark、extract_relations；
- 人工报告（含 17 份 docs/reports 交付物）引条款产物时全部指向 JSONL；
- MD 唯一定位是"人工审阅视图"，且**无任何文档佐证其被实际使用**
  （nfra 条款产物排查报告自述「仅 MD 渲染 + 计数」）。

## 决策

| 轨 | 决策 | 理由 |
|---|---|---|
| cleaned CSV | **保留每轮生成**（不设默认关旗标） | 10 处生产读点全部在链内、读 latest；`cleaned_csv_columns` 是活契约。删除/按需都会直接打断召回、相关性筛选、时效核验、漂移核验四类链内步骤 |
| clauses MD | **默认关闭，保留按需生成能力**（`build_clause_index(with_md=False)` 默认；`run_clean_pipeline --md` 显式开启） | 零程序消费 + 零契约 + 零门禁 + 零人工报告引用；每轮 147MB 渲染落盘无对应收益。可逆：历史 MD 不删，轮转逻辑照旧，开启即恢复 |

**待人工确认项（不阻断本决策）**：MD 的"人工审阅"用途是否真实发生——若有实证
（有人实际打开 data/clauses/*.md 做审查），旗标翻转是一行改动（`--md` 已就位）。

## 后果

- **正面**：链上每轮少写 147MB 渲染视图；MD 从"隐性每轮成本"变为"显式按需能力"；
  CSV 轨的消费面证据入库（此前只存在于人脑）。
- **负面**：若存在未文档化的人工 MD 审阅 workflow，将受影响——缓解：旗标可随时
  开启、历史产物保留在 data/clauses 与 history/。
- **守卫**：`tests/test_w4_data_layer.py::test_clause_md_flag_defaults_off`
  （默认不产 MD / state 如实登记 / `--md` 按需恢复三态断言）。
