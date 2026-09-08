# recall_audit — 召回复核链路（现行链成员）基准说明

## 目录构成（脚本 vs 产物）
| 类别 | 内容 | 处置 |
|---|---|---|
| **脚本（6 个 .py）** | `scanner.py` / `build_outputs.py` / `report.py`（现行三步链）、`run_retrieval_after_checks.py`（四门禁编排器）、`verification_state_mirror.py`（时效镜像读侧）、`__init__` 元数据 | 随 git 版本管理 |
| **产物（csv/jsonl/md/json）** | `output/` 下：`scan_records.csv`、`scan_hits_attr.jsonl`、`_stats.json`、各清单 csv、`归属表全文提取记录.csv`、`归属表无全文清单.csv`、`召回复核报告.md`、`重跑执行报告.json`、`verification_state.mirror.json` | **代码/产物分离（2026-09-08）**：产物统一落 `recall_audit/output/`，由 scanner/build_outputs/report/编排器每次运行确定性重建；`in_attr` 字段与 `scan_hits_attr`/`归属表全文提取记录` 等命名已完成 808 语义消歧（历史 '808' = 归属表全集 E） |

> 目录规范说明（2026-09-08）：本目录为**现行链单元**——脚本与产物物理分离（产物落 `output/`
> 子目录，脚本保持顶层）。编排器 `run_retrieval_after_checks.py` 经 `RECALL_DIR/output/` 统一读写，
> 产物均为确定性可重生成（git 跟踪 output/ 作为基准交付物；`.retrieval_checkpoint.json` 已 gitignore）。

## 最新更新基准（2026-09-08）
- **数据快照**：五源 cleaned 最新 = `20260907`（gov/mof/nfra/pbc/supp 经 clean_index 动态取，禁硬编码）
- **归属表基准**：`regulatory_classifier/data/人身保险公司-文件归属表.csv` = 1059 条（RFN-hex）
- **最近一次全量运行**：2026-09-08 编排器四门禁 PASS 并自动重跑（见 `重跑执行报告.json`）
  - 扫描 4042 条：EXCLUDE 2936 / BOUNDARY 795 / INCLUDE 311
  - 疑似漏提取 7（nfra）；边界 323；808 集合提取 1013 / 无正文 53
- **效力镜像**：`verification_state.mirror.json`（源 = `modules/regulatory_scrapers/timeliness_review/verification_state.json`，2896 条）

## 重跑方式
```
python modules/regulatory_classifier/recall_audit/run_retrieval_after_checks.py   # 四门禁+条件重跑
python modules/regulatory_classifier/recall_audit/scanner.py                     # 仅重扫
python modules/regulatory_classifier/recall_audit/build_outputs.py               # 生成清单/统计
python modules/regulatory_classifier/recall_audit/report.py                      # 生成报告
```
前置：五源 cleaned 就位（clean_index 可解析）+ 归属表存在 + `regulatory_scrapers/timeliness_review/verification_state.json` 存在（Gate2）。
