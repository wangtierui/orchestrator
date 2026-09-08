# 自动化执行记录 · 检索流程自动重跑编排器

## 2026-08-31 06:00 (每日 06:00 调度)
- **action**: `rerun_success`
- **双门禁**: clean=PASS、validity=PASS
- **change_detected**: True（数据签名变化触发重跑，非幂等跳过）
- **重跑链路**: scanner.py → build_outputs.py → report.py 全部 success（returncode=0）
- **签名变化点**: gov/mof/nfra/supp 清洗日期由 20260820/28 前移统一至 20260829；nfra 记录 1930→1929；verification_state 条目 1156→1332（fresh 100%）
- **交付物**: 9 个全部刷新（scan_records.csv、scan_hits_808.jsonl、疑似漏提取文件清单.csv、边界案例清单.csv、808全文提取记录.csv、808无法访问全文清单.csv、关键词库扩充建议.csv、_stats.json、召回复核报告.md）
- **validity 提示**: 63 条「数据层统一清洗」占位符（建议按需补验）+ 63 条「规则判断」回退（既定合规口径，非失败）
- **次要观察**: build_outputs.py:18 与 report.py:15 出现 `invalid escape sequence '\-'` SyntaxWarning（stderr，非阻塞，不影响产出与门禁）
- 结论：本次无需人工介入，重跑正常完成。

## 2026-09-01 06:00 (每日 06:00 调度)
- **action**: `error`（门禁通过但重跑链路崩溃，需人工介入）
- **双门禁**: clean=PASS、validity=PASS；**change_detected=True**（verification_state 条目 1332→1592，fresh=100%）
- **中断点**: scanner.py=success，但 build_outputs.py 在 line 121 抛 `KeyError: '主题'`（returncode=1），report.py 未执行；products={} 空，交付物未刷新
- **根因**: `data/人身保险公司-文件归属表.csv` 于 2026-08-31 23:43 被外部重生成，列数由≥9 减为 8，**「主题」列被删除**；build_outputs.py 硬编码 `a["主题"]` 故崩溃
- **当前列**: 监管文件编号/文件名称/发文字号/发布日期/文件来源/时效状态/判定日期/编号备注（无「主题」）
- **一致性风险**: scan_records.csv、scan_hits_808.jsonl 已被 scanner.py 刷新（新），但其余 7 个产物（含 808 全文记录、召回复核报告.md）停留在旧版本，存在部分更新不一致
- **门禁盲区**: 双门禁未覆盖归属表 schema（主题列存在性），建议补「归属表列校验」门禁
- **validity 提示**: 62 条「数据层统一清洗」占位符 + 63 条「规则判断」回退（同前，非失败）
- 结论：阻塞于归属表 schema 回归，需用户恢复「主题」列或改 build_outputs.py 后重跑。
