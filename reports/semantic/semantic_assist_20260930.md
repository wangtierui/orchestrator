# P1 语义辅助裁定（分析视图）· 2026-09-30 23:39:00

> **只读分析视图**：不写主题事实源（`usage_policy.write_fact_source=false`）。
> 状态：**ok** ｜ 记录 858 条 ｜ 主题 10 个 ｜ 模式 `full` ｜ 限样 200/源

- 说明：最近邻裁定完成：858 条 × 10 主题；嵌入模型 = bge_base_zh（策略链 []）
- 一致性：**n/a**（样本内无确定性基线 `theme_name`）
- **margin（top1−top2）**：中位 0.0268，min 0.0001，max 0.193
- 建议主题分布：T5×236，T1×150，T9×148，T2×88，T10×84，T8×63，T6×42，T4×29，T7×15，T3×3
- 指纹：`{"model": "bge_base_zh", "dep_version": "6.1.0", "weights": "971cc2b8ddcb4eec", "ready": true}`
