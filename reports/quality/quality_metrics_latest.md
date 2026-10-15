# 数据质量六维度量（2026-10-15）

**健康度评分**：98.84 / 100（均权；缺失维度不计入分母）

| 维度 | 评分 | 数据来源 | 关键指标 |
|---|---|---|---|
| 完备性 | 100.0 | corpus 索引 + watermark 表 | index_total_files=1340；index_entries=0；watermarks=28 |
| 唯一性 | 100.0 | document 表 + gate_result | documents=2023；unique_doc_refs=2023；duplicate_doc_ref_groups=0；unique_docnos_disclosure=1535 |
| 及时性 | 100.0 | watermark 表 produced_at | tracked=17；untracked_keys=['analysis:manifest', 'classify:products', 'clean_index', 'internal_index', 'merged_view', 're |
| 有效性 | 98.21 | quarantine 目录 + gate_result | quarantine_files=0；documents=2023；gate_runs_recorded=1062；gate_failed_records=19 |
| 准确性 | 95.55 | document 表必填字段 | required_fields=['docno_norm', 'title', 'source']；missing_cells=270；documents=2023 |
| 一致性 | 99.26 | gate_result（一致性族） | sampled_records=136；passed=135 |

> 纪律：六维指标**全部取自既有机器判据输出**，本工具只聚合/评分/比趋势，不新造检测逻辑；**评分只披露不阻断**（ADR-0012）。
