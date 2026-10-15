# regulatory_classifier/retired/ — 退役脚本隔离层

> W4（《项目最优重构方案_20261012.md》§7.2 A 类，2026-10-15）。判据同
> `modules/regulatory_scrapers/retired/README.md`（J9：retired ⟺ 本目录 + 清单登记 + 零可执行引用）。

| 脚本 | 退役日期 | 原因 | 替代物 |
|---|---|---|---|
| `rfn_lookup.py` | 2026-10-15 | 零调用方；RFN 查询 CLI 能力已被统一入口覆盖 | `cli.py rfn lookup` |
| `source_stats.py` | 2026-10-15 | 零调用方；源统计 CLI 能力已被统一入口覆盖 | `cli.py source list` + `cli.py status` |

**观察纪律**：保留一个完整月检周期后删除（git 历史可恢复）；观察期内不得移回生产路径。
