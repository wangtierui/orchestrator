# 自动化执行记录 · 检索流程自动重跑编排器（门禁式）

## 2026-09-02 06:00 巡检
- **action**: `skipped_gate_fail`（未重跑，符合门禁规则）
- **门禁结果**: clean=PASS / validity=PASS / contract=PASS / **schema=FAIL**
- **阻塞点**: Gate 4 schema 预检失败——明细表 `T1_259逐份条款引用与上位法依据明细表.csv` 列头为 13 列（权威 10 列 + 额外 `子主题NEW`、`子主题分`），与 `build_detail_tables.FIELDS` 严格不等。
- **validity 告警**: 62 条「数据层统一清洗」占位符、63 条「规则判断」回退（非失败），建议按需补验。
- **change_detected**: null（schema 门禁在变更检测前即阻断，属预期）。
- **处置**: 需先修复 T1_259 明细表列头（移除非权威列或同步权威 schema）后再重跑。

## 2026-09-03 06:00 巡检
- **action**: `skipped_gate_fail`（未重跑，符合门禁规则）
- **门禁结果**: clean=FAIL / validity/contract/schema 未执行（clean 门禁短路阻断，JSON 仅含 gates.clean）
- **阻塞点**: Gate 1 clean 预检失败——`scanner_aligned=false`。根因为全仓（非 scanner.py 本身）扫描发现 15 处硬编码快照日期 `*_cleaned_20260829.csv`，全部位于 `theme_analysis/node2_rebuild/` 三脚本（backfill_mismatch_bodies.py / fix_version_collision.py / probe_backfill.py），应改为从 clean_index 动态取 latest。
- **clean 门禁其余子项均 PASS**: 索引加载成功、缺失/哈希失配为 0（26 文件 ok）、index_stale=false、scanner.py 本身 scanner_uses_clean_index=true 且 scanner_hardcoded_dates=[]；五源 index 与 live 的 latest_date/record_count 完全一致（20260829）。
- **change_detected**: null（clean 门禁在变更检测前即阻断，属预期）。
- **对比前次（2026-09-02）**: 上轮 clean/validity/contract 均 PASS，仅 schema 失败（T1_259 明细表 13 列）；本轮 clean 反而 FAIL，表明 node2_rebuild 脚本硬编码日期为新增回归项（与 T1_259 列头问题相互独立）。
- **处置**: 需将 3 个 node2_rebuild 脚本中 15 处硬编码 `*_cleaned_20260829.csv` 改为 clean_index 动态派生，再重跑；validity 层告警未触及（clean 门禁短路）。

### 修复推进结果（continue）
- **已修复**: 对 3 脚本注入 `clean_index` 动态取 latest 范式（复用 scanner.py 同一 import 路径 `D:/WorkBuddy/regulatory_scrapers`，调用 `get_clean_index().latest_csv_path(src)`），移除全部 15 处硬编码 `*_cleaned_20260829.csv`。grep 确认 node2_rebuild 全仓无 `20260829`/`cleaned_20\d{6}`/`SCRAPER_CLEAN` 残留，三脚本 `py_compile` 通过。
- **复跑结果**: 四门禁全 **PASS**（clean/validity/contract/schema），`action=skipped_unchanged`，`change_detected=false`（数据签名未变，幂等跳过，未触发重跑）。门禁阻塞已消除。
- **validity 告警（按需补验）**: 1592 条核验条目全部 fresh（stale=0）；含 62 条「数据层统一清洗」占位符 + 63 条「规则判断」回退（既定合规口径，非失败）。
- **历史遗留 schema 问题已消除**: 本轮 schema 门禁 PASS（11 份明细表 + 40 个底座列头/结构全部符合权威），上轮 T1_259 13 列问题当前未复现。

### 实际重跑结果（`--force` 验证链路）
- 以 `--force` 模式实跑 `scanner.py→build_outputs.py→report.py`，`action=rerun_success`，四门禁全 PASS（clean/validity/contract/schema）。
- 扫描总记录 **33692**（gov 30271 / mof 870 / nfra 1929 / pbc 565 / supp 57）；决策 INCLUDE 311 / BOUNDARY 802 / EXCLUDE 32579；置信 高 126 / 中 1444 / 低 802。
- 交付物 **9 份全部刷新**：scan_records.csv(7.70MB)、scan_hits_808.jsonl(1.51MB)、疑似漏提取文件清单.csv、边界案例清单.csv(218KB)、808全文提取记录.csv(555KB)、808无法访问全文清单.csv、关键词库扩充建议.csv、_stats.json(325KB)、召回复核报告.md(16KB)。
- **结论**：门禁修复后检索→交付物链路端到端健康、可正常产出；后续数据/效力变化时编排器将自动判定 `change_detected=true` 并自然重跑（无需再 `--force`）。

### 62 条占位符「数据层统一清洗」补验筹备（第三次 continue）
- 只读侦察结论：`verification_state.mirror.json` 为**只读镜像**（N-5，单向源→镜像），写侧在 scrapers 侧 `verification_state` 模块；补验写回须落 `D:/WorkBuddy/regulatory_scrapers/timeliness_review/verification_state.json`（跨项目共享权威文件）。
- 62 条占位符构成：规范v3 占位符→pending 57 / 中文残留→英文 4 / P0 1；状态分布 pending 57、valid 3、repealed 1、partially_repealed 1。
- 已产出可执行补验清单：`recall_audit/数据层统一清洗占位符_补验清单.csv`（62 行，含 key/文档标识/状态/占位符子类型/最近核验时间）。
- **执行边界（未执行）**：实际补验=北大法宝 API 核验（pkulaw 连接器）+ 写回共享权威 verification_state，属跨项目共享态变更，按永久记忆规则需显式授权且连接器需就绪；本自动化环境未确认 pkulaw 已连接/认证，故**仅完成只读筹备，未无人值守改写共享态**。待用户显式授权 + 连接器就绪后执行（风控：workers≤2、间隔≥0.2s、连续15次认证失败停止、批量≤500/批、JSONL 断点 checkpoint）。

## 2026-09-07 06:00 巡检
- **action**: `rerun_success`（四门禁通过 + 检测到数据变化，已自动重跑检索→交付物链路）
- **门禁**：clean=PASS / validity=PASS / contract=PASS / schema=PASS（四项全过，无门禁短路）
- **change_detected**: true；签名变化来自 gov/mof/nfra/pbc 四源 latest_date 由 20260905→20260907（supp 维持 20260905），及 verification_state 文件 mtime 更新。
- **重跑链路**：scanner.py(总记录 4042 / INCLUDE 311 / BOUNDARY 795 / EXCLUDE 2936)→build_outputs.py(9 交付物生成)→report.py(报告 170 行) 全部 success（returncode 0）。
- **交付物 9 份全部刷新**：scan_records.csv(1.76MB)、scan_hits_808.jsonl(1.50MB)、疑似漏提取文件清单.csv(5KB)、边界案例清单.csv(215KB)、808全文提取记录.csv(554KB)、808无法访问全文清单.csv(13KB)、关键词库扩充建议.csv(2KB)、_stats.json(322KB)、召回复核报告.md(16KB)。
- **validity 告警（按需补验，非失败）**：1636 条核验条目全部 fresh(stale=0)；含 34 条「数据层统一清洗」占位符 + 63 条「规则判断」回退（既定合规口径）。占位符数由上轮筹备清单 62 降至本轮 34，提示部分占位符可能已被补验清理，建议复核补验清单是否同步。
- **结论**：门禁健康、链路端到端产出正常；本次因四源快照日期前进而自然重跑，符合门禁式编排预期，无需人工介入。

## 2026-09-08 06:00 巡检
- **action**: `skipped_gate_fail`（未重跑，门禁正确拦截）
- **门禁**: clean=FAIL；validity/contract/schema **未执行**（clean 门禁短路，JSON 仅含 gates.clean；控制台显示的 FAIL 为未执行的默认占位，非真实评估结果）。
- **change_detected**: null（clean 门禁在变更检测前即阻断，属预期）。
- **阻塞点（两层，均需修复）**:
  1. **CWD-相对路径解析缺陷（本自动化失败主因）**: clean_index 各快照以相对路径 `data/cleaned/<src>_cleaned_<date>.{csv,jsonl}` 登记；`validate_files` 用 `os.path.exists(p)` 相对**编排器 CWD（recall_audit）**解析 → 30 个文件全判「missing」（ok_count=0）。实测 30 个文件全部存在于 `D:/WorkBuddy/regulatory_scrapers/data/cleaned/`（5 源 × 20260904/05/07 × csv/jsonl 均齐）；改以 scrapers 为 CWD 复跑 validate_files → missing=0，证明缺失为路径解析假阳性。
  2. **sha256 漂移（即便修正 CWD 仍会阻断）**: 以 scrapers 为 CWD 复跑 → missing=0 但 hash_mismatch=6，集中于 **gov/nfra/pbc 三源 20260907（最新）快照**（csv+jsonl 各 1）。说明这三源最新清洗文件在 clean_index 末次构建后被重新生成，索引记录的 sha256 已陈旧；mof/supp 20260907 及全部 20260904/05 共 24 个文件 sha256 一致（ok）。
- **根因小结**: clean_index 现登记 3 个历史快照（20260904/05/07），且其快照路径为相对路径；编排器按任务约定在 recall_audit CWD 下运行致相对路径失配；叠加 gov/nfra/pbc 最新快照 sha256 漂移。两问题任一未解，clean 门禁均不过。
- **自动重建未触发**: 自动重建逻辑仅在 `index_stale`（索引 latest 日期≠磁盘 latest）时触发；本轮日期一致（20260907），故不触发重建，门禁直接判 FAIL。
- **处置建议（待用户授权/介入）**: (a) 修复 `validate_files`/`iter_snapshots` 相对路径按 scrapers 根解析（鲁棒修复）；或编排器以 scrapers 为 CWD 运行；(b) 对 clean_index 执行 `get_clean_index(rebuild=True)` 以刷新 6 条陈旧 sha256（须确保重建期间无并发写入）。修复后 clean 门禁方可通过、进入变更检测与重跑。
- **validity 告警**: 无（validity 门禁未执行）。占位符补验（62→34）待用户显式授权 + pkulaw 连接器就绪后执行，不在本无人值守巡检范围。
