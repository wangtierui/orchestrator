# reports/ — 对内过程文档与机器台账

> 本文件是 `reports/` 目录的**分区口径成文裁决**（W4，方案《项目最优重构方案_20261012.md》
> §7.10 问题 1/3，2026-10-15 落地）。分区目的：职责清晰度——「文档怎么分类、放哪、谁清理」
> 机器可判，不再四类内容混排。

## 分层语义（仓级）

| 目录 | 定位 | 内容 |
|---|---|---|
| `docs/` | **对外**人类文档根 | `adr/`（架构决策记录）+ `deliverables/`（分析交付库 17 项 + `_manifest.json`） |
| `reports/` | **对内**过程文档与机器台账 | 本目录 |

> 历史撞车说明：分析交付库原为 `docs/reports/`，与 `reports/` **同名同级子树**（职责撞车）——
> W4 §7.10 案例一已更名 `docs/deliverables/`；本目录同期按下列三区 + 台账类分区。

## 三区分区（人类文档）

| 子目录 | 类别 | 内容 | 例子 |
|---|---|---|---|
| `refactors/` | 重构过程语料 | 批次执行报告 + 重构方案/审查/评估/转交登记 | `重构执行报告_第<N>批_<date>.md`、`项目最优重构方案_20261012.md`、`全面检视与重构审查报告_*.md` |
| `manuals/` | 长期手册与契约 | 运行手册/撰写规范/数据流图/wiki 适配契约/恢复演练记录 | `运行手册_编排与定时_*.md`（由 `tools/gen_schedule_doc.py` **反向生成**，手改即被 S4 判据判 FAIL）、`脚本数据流_*.md`、`全链路数据流链路图_v2_*.md` |
| `scratch/` | 一次性底稿 | 完成报告/专项审计/历史审查底稿/评估 PoC | `代码审查报告_20260911.md`、`Obsidian_PoC_20260912.md` |

**归档纪律**：scratch 是「读过即存档」层——不删除（git 历史 + 审计价值），新底稿一律落 scratch；
refactors 只收**重构程序**文档；manuals 只收**长期有效**手册（一次性内容不进 manuals）。

## 台账类（留根，机器生成）

根目录的机器产出**不迁入三区**（它们不是「人类文档」，随生成器滚动产生/消费）：

| 形态 | 例子 | 管理方 |
|---|---|---|
| 生成台账（入库） | `retention_<date>.json`、`审计判定台账_<date>.md`、`supp时效口径审计_<date>.md`、`血缘基线_<date>.json`、`评测基线_<date>.json` | 各自生成器 + retention `retention_ledgers`（保留 30 份） |
| 运行期目录（git 忽略） | `_tmp/`（链步骤日志/汇总/临时）、`_snap/`（产物快照 before/after）、`_wiki_sources/`（wiki 同步暂存） | retention `reports_tmp`（7 天）/ 各自工具 |
| 派生视图目录（入库） | `semantic/`、`semantic_search/`、`docreader/`、`corpus/` | 链步骤 + 水位登记 |

> `_snap/` 与 `_wiki_sources/` 的口径确认（原「待人工确认」项）：二者均为**机器运行产物**
> （artifact_snapshot 的 before/after 快照；wiki_sync 的同步暂存区）→ 按台账类留根，
> 不进三区。结案理由：分区治理的对象是「人类文档的职责清晰度」，机器产物按「生成器 +
> 保留策略」管理（retention POLICY 已各自登记）。

## 消费方注意

- 引用本目录文档时用**完整相对路径**（`reports/refactors/...` / `reports/manuals/...` /
  `reports/scratch/...`）——纯文件名提及在分区后不可解析。
- `gate_hardcoded_paths` 的 `EXCLUDE_DIRS` 含 `reports`（整树），本目录文档允许含历史盘符路径
  （历史快照类内容的固有属性）；**新增**文档仍须遵守零盘符纪律。
