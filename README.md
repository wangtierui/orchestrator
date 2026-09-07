# regulatory_compliance_orchestrator

监管合规编排器：五源外部法规采集/清洗 + RFN 监管分类 + 内部制度扫描 + 制度起草的统一工程。
2026-09-08 起为**唯一演进点**；原四仓只读冻结（见 §来源映射）。

## 快速开始（P0 骨架）

```bash
# 托管 python（Windows）：
C:\Users\wangtierui-lhl\.workbuddy\binaries\python\versions\3.13.12\python.exe cli.py ping
C:\Users\wangtierui-lhl\.workbuddy\binaries\python\versions\3.13.12\python.exe cli.py source list
C:\Users\wangtierui-lhl\.workbuddy\binaries\python\versions\3.13.12\python.exe cli.py gates
```

## 目录
- `paths.py` —— 路径唯一事实源（R4）；环境变量 `REG_ORCH_ROOT` 可覆盖。
- `config/` —— `enums.py`（受控枚举唯一源 v3+internal）、`sources.yaml`（源目录唯一源 R15）、`ocr.yaml`（OCR 引擎配置 R17）、`schema/`（数据契约 R24）。
- `interfaces/` —— 跨模块唯一调用层（clean_index_api / rfn_api / theme_api / internal_policy_api + contract.py）。
- `gates/` —— 交付门禁（`python -m gates`），实现/数量以 `ALL_GATES` 为准（R23）。
- `std_lib/scraper_std/` —— 领域共享库（P1 自旧仓复制）。
- `std_lib/common_lib/` —— 跨域通用（fs_lock/io_atomic/norm）。
- `modules/` —— 四子项目（复制进新仓后修改）。
- `reports/` —— 蓝图/检视/回测/报告。
- `data_migration_manifest.json` —— P0 活跃数据复制清单（93 条，含 sha256）。

## 来源映射（原仓 → 新仓）
| 原仓（D:/WorkBuddy，只读冻结） | 新仓 modules/ | 迁移阶段 |
|---|---|---|
| regulatory_scrapers | modules/regulatory_scrapers | P1-P3 |
| regulatory_classifier | modules/regulatory_classifier | P4-P5 |
| internal_policy_base（空） | modules/internal_policy_base | P6 |
| internal_policy_drafter | modules/internal_policy_drafter | P7 |

共享库原 `std_lib/scraper_std` → 新仓 `std_lib/scraper_std`（P1 复制；原 std_lib 冻结）。

## 演进纪律
1. 原四仓与旧 std_lib 不再修改；一切改动在新仓完成。
2. 跨模块调用仅经 `interfaces/`；禁止 sys.path 盘符插入。
3. 枚举一律 import `config.enums`；数据契约以 `interfaces/contract.py` + `config/schema/` 为准。
4. 交付前跑 `orchestrator gates`（pre-commit 接入后自动）。
