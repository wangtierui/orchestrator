# 长期记忆 · regulatory_compliance_orchestrator（新仓）

## 项目定位
- 新仓（唯一演进点），统一编排原四仓（regulatory_scrapers / classifier / drafter / base，均只读冻结）。
- 工程要求 Python ≥ 3.13；入口 `cli.py`（`orchestrator`），子命令：gates / source / internal / classify / timeliness / draft / rfn / base / ping。

## 环境约定（重要）
- 默认 PATH 中 `python` / `py` **不可用**（WindowsApps 存根，退出码 9009）。
- 可用解释器：`C:\Users\wangtierui-lhl\.workbuddy\binaries\python\envs\default\Scripts\python.exe`（Python 3.13.14）。
  运行脚本建议加 `-X utf8`（Windows 控制台默认 GBK，易 UnicodeEncodeError）。
- 该 shell 为 PowerShell Core；命令输出有时不直接回显，可 `*> 文件` 落盘后再读。

## 每日门禁核验（R11，自动化 orchestrator-2，每日 06:00）
- 编排器：`modules/regulatory_classifier/recall_audit/run_retrieval_after_checks.py`
  - 四门禁：clean（清洗/索引新鲜）/ validity（效力核验）/ contract（链路契约）/ schema（数据 schema 预检）。
  - 幂等：数据签名（五源快照 date+records+sha、state 条数、state_mtime）未变则 `skipped_unchanged`；
    变化则重跑 scanner → build_outputs → report 并刷新交付物；结构化报告 `recall_audit/output/重跑执行报告.json`。
- 交付门禁：`cli.py gates`（GatesRunner），2026-09-13 起共 15 项。
- 已知长期观察项（非阻断，勿误判为 FAIL）：核验覆盖率约 73.6%（state 2975 / 源 4043）；
  matched 底座覆盖缺口约 42/1051（4%）；时效漂移治理项 c2_pending=9、legacy_mismatch=160、layer3 warn=19。
