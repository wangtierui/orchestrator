# recall_audit — 召回复核链路（现行链成员）基准说明

## 目录构成（脚本 vs 产物）
| 类别 | 内容 | 处置 |
|---|---|---|
| **脚本（6 个 .py）** | `scanner.py` / `build_outputs.py` / `report.py`（现行三步链）、`run_retrieval_after_checks.py`（四门禁编排器）、`verification_state_mirror.py`（时效镜像读侧）、`__init__` 元数据 | 随 git 版本管理 |
| **产物（csv/jsonl/md/json）** | `scan_records.csv`、`scan_hits_808.jsonl`、`_stats.json`、各清单 csv、`召回复核报告.md`、`重跑执行报告.json`、`verification_state.mirror.json` | 由 scanner/build_outputs/report/编排器**每次运行重新生成**；命名含历史 "808" 字样的文件不得重命名（编排器按文件名校验，见 scanner.py 头注）。产物与脚本同目录为**历史既定事实**（编排器 Gate 按精确路径引用），重构后已入 git 可重建 |

> 目录规范说明（2026-09-08 遗留排查）：本目录为**现行链交付目录**——脚本与其直接交付物共存。
> 与 classifier 其它目录不同（`data/` 纯数据、`scripts/` 纯底座生成器），recall_audit 属
> "脚本 + 其自身产物" 单元，编排器 `run_retrieval_after_checks.py` 依相对路径运行三步脚本并校验产物，
> 故**不以 data/ 收纳其产物**（若迁移产物会破坏 Gate 路径契约）。产物均为确定性可重生成。

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
