# orchestrator-2 · 每日检索门禁核验（执行历史）

> 仅记录高层执行摘要，不存放完整任务输出/交付物正文。

## 2026-09-12 06:37 (+0800)
- 运行 `modules/regulatory_classifier/recall_audit/run_retrieval_after_checks.py`：
  - 四门禁 clean / validity / contract / schema 全部 **PASS**；
  - 数据签名变化（clean 快照 20260909/10 → 20260912；state 2912 → 2975）→ `action=rerun_success`，
    已自动重跑 scanner → build_outputs → report，交付物刷新；clean_index 无需重建。
- 运行 `python cli.py gates`：14 项门禁全部 `[OK]`，exit 0。
- 无 FAIL，无需人工处置。
- 非阻断观察项：核验覆盖率 2975/4043（73.58%）；matched 底座覆盖缺口 42/1051（4.00%）；
  时效漂移治理项 c2_pending=9、legacy_mismatch=160、layer3(归属表→cleaned) warn=19。
- 环境提示：新仓默认 PATH 无 `python`（WindowsApps 存根），需显式使用
  `C:\Users\wangtierui-lhl\.workbuddy\binaries\python\envs\default\Scripts\python.exe`。

## 2026-09-13 06:00 (+0800)
- 运行 `run_retrieval_after_checks.py`：四门禁 clean / validity / contract / schema 全部 **PASS**；
  签名变化由 `state_mtime` 推进驱动（clean 快照仍为 20260912、state 仍 2975 条）→ `action=rerun_success`，
  已重跑 scanner → build_outputs → report，交付物刷新。
- 运行 `cli.py gates`：**15 项**门禁全部 `[OK]`，exit 0（较昨日新增「密钥/敏感值硬编码扫描 P1-4」）。
- 无 FAIL，无需人工处置。
- 非阻断观察项（与昨日同口径）：核验覆盖率 2975/4043（73.58%）；matched 底座缺口 42/1051（4.00%）；
  时效漂移 c2_pending=9、legacy_mismatch=160、layer3 warn=19。
