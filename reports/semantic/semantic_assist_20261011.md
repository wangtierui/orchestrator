# P1 语义辅助裁定（分析视图）· 2026-10-11 06:19:51

> **只读分析视图**：不写主题事实源（`usage_policy.write_fact_source=false`）。
> 状态：**degraded** ｜ 记录 858 条 ｜ 主题 10 个 ｜ 模式 `full` ｜ 限样 200/源

- 说明：嵌入链不可用 → 显式降级（**不抛**）。候选尝试：bge_base_zh, text2vec
- ⚠️ 嵌入降级披露：[semantic_embed] 语义增强 'bge_base_zh' 不可用（本地权重就绪（external/models/bge-base-zh-v1.5；指纹 971cc2b8ddcb））→ 回退既有正则实现；如需启用：pip install -e .[semantic] | [semantic_embed] 语义增强 'bge_base_zh' 不可用（本地权重就绪（external/models/bge-base-zh-v1.5；指纹 971cc2b8ddcb））→ 回退既有正则实现；如需启用：pip install -e .[semantic]
- 抽样策略：**优先取有确定性基线的记录**（扫描上限 4000/源，实扫 5948）→ 一致性在有基线子集上度量
- 一致性：**n/a**（样本内**无确定性基线**）—— 基线来源：`cleaned.theme_name, classifier:_t{N}_final.json`；分类器条目 2138。⚠️ 若分类器条目为 0，说明 `classify:all` 尚未产出 `_t{N}_final.json`（基线不可得，而非一致性为 0）。
- 指纹：`{"model": "bge_base_zh", "dep_version": "6.1.0", "weights": "971cc2b8ddcb4eec", "ready": true}`
