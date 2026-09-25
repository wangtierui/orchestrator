# tools/retired — 废弃 / 一次性脚本隔离层

本目录存放**已退役**脚本：保留内容作为历史凭证（迁移映射、复制源清单等），
但**移出可执行面**——不再出现在任何文档的推荐命令中，不被任何代码/配置引用。

纪律（v2 《全链路重构方案·修订版》§3.15，决策 D-11）：

1. 退役充分条件 = **一次性**（有明确的历史时间点、已无触发场景）**且 零仓内引用**；
   仅"很久没跑"不足以退役（按需治理工具本就低频）。
2. `tools/_manifest.json` 中 `category=retired` 的文件**必须**位于本目录（双向由
   `gate_config_integrity` 断言）。
3. 本目录被三道门禁的 `EXCLUDE_DIRS` 排除（`gate_no_duplicate_libs` /
   `gate_hardcoded_snapshots` / `gate_hardcoded_paths`）——隔离层不再参与"增量防漏"，
   其"不再被引用"由 J3 判据保证。
   **例外**：`gate_secret_scan` **不排除**本目录（退役脚本若含 token 应当 FAIL）。
4. 新增退役项须在下表登记：退役原因 + 日期 + 替代物。

## 退役清单

| 文件 | 退役日期 | 原因 | 替代物 |
|---|---|---|---|
| `flatten_collectors.py` | 2026-09-13 | `collectors/` 物理拍平为**一次性迁移**（RENAME 映射 + 幂等 fix_texts）；拍平已完成，无再次触发场景 | 无（历史映射保留在本文件内） |
| `migrate_collectors_p3b.py` | 2026-09-08 | 旧四仓 → orchestrator 的**一次性复制**（`COPY_PLAN` 指向旧仓只读源）；旧四仓已冻结，复制已完成 | 无（其 docstring 已指向 `flatten_collectors.py`） |

## 相关

- 清单与判据：`tools/_manifest.json`、`gates/gate_config_integrity.py`
- 决策与迁移程序：`reports/全链路重构方案_修订版_v2_20260926.md` §3.15
