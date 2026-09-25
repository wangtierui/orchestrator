
## 结论速览

| 问题 | 结论 |
|---|---|
| 1. 统一顶层执行脚本 | **有**：`tools/run_production_refresh.py`（唯一生产编排器）；`cli.py` 只是 12 命令门面。但它**未覆盖 7 类环节**（见缺口表） |
| 2. 脚本/模型边界 | **基本清晰但有一块灰区**：本仓是纯确定性工程（**0 个模型 SDK 依赖、0 处 LLM 调用、无提示词资产**）；模糊点集中在"**助手侧（agent）配置与记忆住在仓内**" |
| 3. 衔接机制 | **进程边界 + 文件/水位 + 文档契约**，不是模型 API：`subprocess(argv/env) → stdout JSON → checkpoint/state/watermark → rc 三态` |

---

## 1. 统一顶层执行脚本

**名称/位置**：`tools/run_production_refresh.py`（唯一主链编排器）。
**入口**：`python tools/run_production_refresh.py [--no-scrape] [--collect …] [--supp-batch <json>] [--stop-on-error]`。包入口 `[project.scripts] orchestrator = "cli:main"`（`pyproject.toml:50-51`）只到命令门面，**不是**全流程入口。

**执行顺序**（`--no-scrape` 实测 22 步）：

```
0  collect（含 gov --source all= xzfgk+zhengceku；nfra-weekly 分支；supp 默认跳过）
1  clean:<src>×5      → 尾部自动 clause_index 条文节点
2a timeliness:consolidate → 2b apply:<src>×5
2.55 观察型水位提前登记（本轮修复引入）
2.5 classify --all → 2.6 relations gen → 3 reconcile → 3.5 governance sync
4  recall → 4.2 internal merged → 4.5 reports build
5.5 base publish（发布件+SQLite/FTS5）→ 6 gates → 6.8 analysis gen → 6.5 watch:baseline
```

**控制面**：单实例 `ProcessLock(data/run_production_refresh.lock, max_age=48h)`，占用即 **rc=3**；`REG_ORCH_RUN_ID` 下传子进程；rc 约定 `0` 全通 / `2` 有失败步 / `3` 锁占用 / `1` 异常；阶段失败**默认继续并汇总**（`--stop-on-error` 仅作用于 collect/clean 两循环）；水位登记 **22 个产物键 / 30 条依赖边**。

**覆盖缺口（需人工或另调脚本）**：

| 链外环节 | 入口 |
|---|---|
| 时效**网络核验**（verify_missing / authority_backfill / classifier_pkulaw_verify） | `cli.py timeliness verify` + 两脚本直调 |
| RFN 补登 + `--sync-timeliness` | `tools/rfn_backlog.py --apply` |
| 内部制度 **index/align/reocr/refine-identity/backfill** | `cli.py internal …`（链内仅 `merged`） |
| **drafter 交付件**（起草/修订稿） | `cli.py draft` + `verify_regulatory_citations.py` |
| wiki / **llm_wiki** 同步 | `tools/sync_wiki_sources.py`、`check_llm_wiki_upstream.py` |
| **graphify 图谱** | git hook → `tools/graphify_sync.py` |
| **supp 本地摄取** | `--supp-batch` / `collectors/supp_ingest_local_dir.py` |

**调度层（不在脚本侧）**：仓内 `<repo>/.codebuddy/automations/` 有两个助手侧定时任务，其 `memory.md` 记录了执行历史——`orchestrator`（周：五源增量采集 → `run_production_refresh.py --no-scrape` → gates → pytest，2026-09-22 01:30–02:38 实测 22 步 rc=0）与 `orchestrator-2`（每日 06:00 检索门禁核验）。⚠️ 与 `reports/运行手册_编排与定时_20260912.md` 的定时表**口径不完全一致**（运行手册：周二 01:00 nfra_weekly、每日 06:30 refresh；automation 记忆：周 01:30 采集、每日 06:00 门禁）→ **调度事实源二义**。

**缺失的入口**：没有"一键覆盖上述 7 类链外环节"的编排器；`tools/ci_check.py` 只串 ruff→pytest→gates。

---

## 2. 脚本与模型的边界

### 2.1 "模型"在本仓的真实形态（事实）

| 检索项 | 结果 |
|---|---|
| 依赖面 | `pyproject.toml` 共 19 条（base 12 / dev 4 / ocr 3），**0 个模型 SDK**；`mcp` extra 已作死 extra 删除（`:46-48`） |
| LLM 调用 | 全仓 `*.py` 对 `openai/anthropic/transformers/langchain/chat/completions/temperature/max_tokens` — **0 命中**（我实测） |
| 提示词资产 | `prompts/`、`*.prompt`、`*.tmpl` **均不存在**（实测 0）；无 `system_prompt`/`few-shot` |
| 模型配置 | `config/` 仅 `sources.yaml`、`ocr.yaml`（后者只配 OCR **权重路径**，非 LLM 参数） |

**"模型"仅以三类进程外工具存在**：
1. **北大法宝 MCP CLI**（`std_lib/scraper_std/pkulaw_cli.py`）——模块头显式声明"纯命令执行、**不经 LLM**、零消耗"；token 经环境变量注入、绝不落盘；
2. **OCR 引擎**（`paddleocr` + `pytesseract`）——**进程内库调用**，权重在 `~/.paddlex/official_models`（不在仓内），`external/PaddleOCR-3.7.0` 是 junction 且被 gitignore；
3. **llm_wiki**（外部桌面应用，自带 LLM Key）——仓内只做**文件协议投喂**（`sync_wiki_sources.py` 导出 md+frontmatter，SHA256 增量）与**上游版本轮询**（`check_llm_wiki_upstream.py` 只调 GitHub Releases）。

**校验链是确定性的**：`gates/gate_citations.py`（水位/RFN 存在性）、`verify_regulatory_citations.py`（纯正则 `R-\d{2}`/文号/书名号）、`recall_audit/scanner.py`（正则+分层置信度）、17 项交付库"**全数据驱动**"（README:106）——**无模型参与判定**。

### 2.2 边界模糊之处（具体表现 → 影响）

| # | 表现（证据） | 影响 |
|---|---|---|
| 1 | 仓内存在 `<repo>/.codebuddy/`：`settings.json`（PreToolUse hooks 调 `graphify.EXE hook-guard`）+ `automations/*/memory.md` + `memory/*.md`；被 `.gitignore:78` 忽略、又被 `gate_hardcoded_paths.py:46` 排除扫描 | **模型侧调度/hook/记忆不入版本控制、不受门禁约束** → 不可复现、无审计链（"排除即盲区"） |
| 2 | `CODEBUDDY.md`（给助手的使用规则）与工程文档同置仓根，且在**全仓 `.md` 扫描**范围内 | 两类内容混放；此前已因该扫描规则受害（automation 记忆 09-14：仓内重定向文件含盘符字面量 → R4 假阳性 FAIL） |
| 3 | 助手侧记忆**两处并存**：`d:\WorkBuddy\.codebuddy\memory`（16 文件/319 KB）与 `<repo>\.codebuddy\memory`（10 文件/14 KB，含环境约定+自动化历史） | 同一项目的约定与历史分裂，**权威性不明**（不同会话可能读到不同"事实"；两处均已记录 node 路径应为 `22.22.2-3`） |
| 4 | 调度定义不在仓内（仓内只有执行历史），且与运行手册定时表口径漂移；automation 记忆自述"09-16/09-17 执行历史缺口，疑为未回写" | 排期二义 + **定时任务可靠性无机器校验** |
| 5 | 叙述性结论靠人工兜底：`rfn/sync_status.json` 标"叙述性分析待人工复核"；交付件**不标 provenance**（人/模型来源） | 权威性不可机判；与"脚本侧确定性判定"形成落差 |
| 6 | 脚本侧也存在显式人工断点：`reconcile_clean_drift.py` C2 人工确认、`cluster_by_keywords.py` u_fix 人工复核 | 属**脚本内人工环节**（非模型），但同样缺"人工决定"的落盘留痕 |

---

## 3. 脚本与模型（及脚本与脚本）的衔接机制

| 维度 | 脚本↔脚本（链内） | 脚本↔模型侧（进程外工具/助手） |
|---|---|---|
| **调用方式** | `subprocess.run` 顺序子进程（`_run` 统一封装，记 `rc/elapsed/tail`）；也支持进程内 `interfaces/*_api` | 同样是**进程边界**：`node.exe + dist/cli.js`（pkulaw）、`graphify.EXE`；助手侧另经 **PreToolUse hook** 门守 Bash/Grep 与 Read/Glob |
| **参数传递** | argv 阶段命令；env `REG_ORCH_RUN_ID`（run_id 下传）；文件参数 `--ledger <最新清单>`/`--backlog` | env `PKULAW_MCP_AUTHORIZATION="Bearer <token>"`（子进程内注入，不落盘）、`PKULAW_NODE_EXE`/`PKULAW_PKG_DIR` 定位 CLI；`--json` 控制输出 |
| **结果回传** | **以产物文件为准**（水位/manifest/台账）；stdout 仅作 tail 证据 | stdout JSON → `json.loads` → `unwrap_cli_response`（兼容 `Data/data/数组/Message` 包裹）；非 JSON 退 `_parse_json_embedded`（剥进度行）；`Url` 取裸链 |
| **状态管理** | ①`ProcessLock` ②治理库 `run_log`（RUN-id 台账）③业务断点 `*_checkpoint.jsonl`（逐条 flush）、`verification_state.json`（SSOT）、`governance.db` watermark（22 产物/30 边）、`clause_index_state.json`（input_sha） | checkpoint 同上（`pkulaw_<src>_<kind>_checkpoint.jsonl`）；配额/认证状态即断点内的 `message` |
| **错误处理** | 阶段失败默认**继续并汇总**（末步落 `生产刷新汇总_*.json`）；rc 分档 0/1/2/3；阶段级 timeout 300–7200 s | 单调用 `timeout=180`；rc≠0 → `[CLI失败 rc=N]`；配额 `90001/积分用尽` 停；**连续 15 次认证失败门哨**；`_CHUNK=50` 分块落盘；**R13 降级：外部不可用/失败项一律不写判定**（不误标） |
| **流程控制** | 阶段串行 + **水位依赖断言**（`_wm` 声明 inputs → `gate_watermark` 校验版本一致）+ 18 道门禁 + 签名幂等跳过（clean/classify/relations/recall 未变即跳）+ `watch:baseline` | 助手侧：automation（周/日）→ 读 automation memory → 跑脚本 → **归因 → 改码 → 回写 memory**（如 09-22 发现并修 `gov_collector.py --source all` 必崩 `KeyError`、`mof_collector` 指纹失效 2 项 P0）；**衔接契约是文档+记忆，不是 API** |

---

## 若要收紧边界（3 条建议，按性价比排序）

1. **调度事实源唯一化**：把 automation 的 cron/命令与运行手册定时表合并为仓内 `config/schedule.yaml`，automation 记忆只存执行结果 → 消除口径二义与"未回写"盲区。
2. **给模型侧状态加"只读披露门禁"**（如 `gate_agent_state`）：不扫 `.codebuddy/` 内容，只断言"存在即登记 + hooks 白名单 + 最近回写时间"，把"排除即盲区"变成"排除但可见"。
3. **`CODEBUDDY.md` 与工程契约分置**（移到 `docs/agent/` 或文件头显式标注"模型侧规则"），并统一助手侧记忆为**单一目录**（当前两处并存 16+10 文件）。

---

# 先给一个总框架：两条"时效"通路的分工

项目里"时效"（文件还有效吗）不是一条流水线，而是**两条职责完全不同的通路**：

| 通路 | 业务角色 | 在哪里跑 | 依赖外部 | 节奏 |
|---|---|---|---|---|
| **取证线**：时效网络核验三脚本 | **"查证/鉴定"** —— 去权威库（北大法宝）逐份核实文件的现行效力，产出**证据** | 链条**之外** | **强依赖**（要配额、会限流、会失效） | 按周/按月，人工或定时触发 |
| **入账线**：2a consolidate + 2b apply | **"归集/入账"** —— 把多轮取证攒下的证据，合并成一份唯一清单，再写进事实源并驱动下游 | 链条**之内**（阶段 2） | **零外部依赖**（纯本地读写） | 每次全链刷新都跑 |

一句话：**核验负责"判定得出对不对"，consolidate/apply 负责"判定进不进系统、进到哪些表"**。两者是"上游产证据 / 下游用证据"的关系，不是替代关系。

---

# consolidate→apply 与 网络核验 的关系

## 1.1 职责边界

| 维度 | 网络核验（三脚本） | 2a consolidate → 2b apply |
|---|---|---|
| **回答的问题** | "这份文件现在还有效吗？被谁替代了？" | "已知的这些结论，怎么变成全系统统一口径？" |
| **碰的数据** | **证据侧**：`pkulaw_*_checkpoint.jsonl`（查询缓存）、`时效核验_*变更台账_*.csv`、`verification_state.json` | **事实源侧**：五源 `cleaned`（正式记录）、`clauses` 条文产物、`clean_index` 索引 |
| **明确不做** | 不改 cleaned、不做下游刷新（**唯一例外**见 1.6） | 不发起任何网络查询、不产生新判定 |
| **产物性质** | 过程证据 + 判定台账（可中断、可续跑） | 权威清单 + 已回写的事实源（每轮重算、幂等） |

## 1.2 触发条件

| 通路 | 触发方式 | 前提条件 |
|---|---|---|
| 网络核验 | 人工或定时（《运行手册》定为**每周一 07:00**） | 北大法宝 token 有效 + 配额可用；否则按 R13 降级**停手不写判定** |
| consolidate | 链条自动（阶段 2a），无开关 | 存在至少一份变更台账 |
| apply | 链条自动（阶段 2b，逐源 5 次） | 存在最新全量清单；**必须发生在 clean 之后** |

## 1.3 数据流向（交接物是关键）

```
【取证线·链外】
 verify_missing ─┐
 authority_backfill ─┼→ ① pkulaw_*_checkpoint.jsonl（查询缓存，逐条落盘，防重复查询）
 classifier_pkulaw_verify ─┘   ② 时效核验_<源>变更台账_<日期>.csv（本轮"结论变更"台账）
                               ③ verification_state.json（★时效 SSOT，键=文号/标题，当前 14,796 条）
                                          │
                                          ▼
【入账线·链内 阶段2】
 2a consolidate_timeliness --use-state
      读：③ SSOT + ② 全部历史台账（22 份）+ 历史全量清单（种子）
      出：时效性标注结果清单_全量_<日期>.jsonl（★唯一清单，3,901 条）
          + _冲突台账_<日期>.csv（多来源结论打架的记录）
          + _合并报告_<日期>.md + _consolidate_manifest.json
                                          │
                                          ▼
 2b apply_timeliness_to_cleaned --source <源> ×5
      读：上面的唯一清单
      出：五源 cleaned 三个字段回写（时效状态/替代文件/核验来源）
          + status 派生（与时效状态同源）
          + 备份 cleaned_before_timeliness_<时间戳>/
          + 派生刷新：clean_index 索引重建 + clauses 条文产物同轮重建
                                          │
                                          ▼
【下游消费·链内 阶段 2.5 之后】
 classify（归属表/明细表）→ relations（依据废止）→ internal merged → 发布件 → 交付库
```

## 1.4 先后顺序（以及"为什么必须是这个顺序"）

1. **核验 → 台账**：先有证据。
2. **台账 → consolidate → 全量清单**：证据先合并成唯一口径。
3. **clean → apply**：链条阶段 1 的清洗是**用原始抓取件重刷 cleaned**，会把时效字段清空；所以 apply 必须排在 clean 之后——这就是它被放在阶段 2 的原因。**若跳过 apply，cleaned 的时效会整体变空**。
4. **apply → classify/明细/发布件**：这些环节都要读 cleaned 的时效字段（明细表的"时效"列、发布件的 `timeliness_status`）。apply 在前，它们才拿到新口径。
5. **apply → clauses**：条款产物是从 cleaned 派生的，也带时效字段，所以 apply 会自动同轮重建它（2026-09-19 起）。

## 1.5 相互依赖

- 入账线**完全依赖**取证线：没有台账与 SSOT，consolidate 只能输出历史清单原样，apply 写不出新判定。
- 取证线**不依赖**入账线：核验只关心"查得对不对"，查完就落台账，不管下游。**这也是它能独立于链条、按周慢跑的原因。**
- 二者共同的**唯一事实源**是 `verification_state.json`——consolidate 用 `--use-state` 与它交叉校验，因此它是两条线的"接口件"。

## 1.6 重叠与冲突（3 处，都是真实存在的）

**① 有一处"双写口"重叠（设计上的例外）**
`classifier_pkulaw_verify` 是个"越界"的脚本：它查完归属表后**自己就写归属表 + 自己就写 cleaned**，不经过 consolidate/apply 这条路。

- 业务影响：若它和链条的 apply 在时间上交错，可能出现"归属表已是新结论、cleaned 还是旧结论"（或相反）的**短时不一致**。
- 现有防线：`gate_timeliness_ssot` 门禁专门守"SSOT ↔ 归属表 ↔ cleaned"三层一致，一旦分叉就报 FAIL。
- 实践口径：**先跑完取证（含此脚本），再跑链条**，不要在链条进行中穿插取证。

**② 有一处"设计内的长期重叠"：`nomatch` 永远留在待核名单**
北大法宝查不到同名记录时（`nomatch`），按"不臆造"纪律**不写任何判定**。

- 业务影响：这些记录会**长期显示为"效力缺失"**。所以 gov 显示"待核 12,394 条"，并不等于"还有 1.2 万份要从头查"——其中绝大部分是**已查过、权威库无同名**（经验值约 94%）。
- 业务含义要区分清楚：**"没查过" ≠ "查过但库里没有"**。前者是待办工作量，后者是既定结论。判断依据看断点文件的"成功"条数，而不是待核条数。

**③ 有一处"顺序性冲突"必须靠编排位置化解**
清洗会覆盖时效字段（1.4 第 3 点）。这不是缺陷，而是"重刷基态"的必然结果；冲突靠"apply 固定排在 clean 之后"化解，靠"apply 后同步刷新 clauses/索引"闭环。

---

# 22 步 与 7 类链外环节 逐项说明

## 2.1 "22 步"= 全链条自动执行清单（`--no-scrape` 实测口径）

链条的业务定位：**把"原始网页/文档"一路加工成"可对外交付、可被下游系统消费的权威数据与报告"**。

| # | 步骤 | 业务含义 | 输入 | 产出物（交付物形式） |
|---|---|---|---|---|
| 1–5 | `clean:gov / mof / nfra / pbc / supp` | **规范化五源监管文件**：把抓来的原始件清洗成统一格式，并抽出条款结构、建快照索引 | 五源原始件 `data/raw/<源>_laws.json`（13,177 / 870 / 1,939 / 571 / 60 份） | ① `cleaned/<源>_cleaned_<日期>.jsonl`（+同口径 csv 双轨）② `clean_index/index.json` 快照索引（含内容指纹）③ **`clauses/<源>_clauses_<日期>.jsonl/.md`** 条文结构（每份文件的章/条/时效） |
| 6 | `timeliness:consolidate` | **时效结论合并**：把多轮核验的结论汇成"唯一权威清单" | 历史变更台账（22 份）+ 时效 SSOT（14,796 条）+ 历史清单（种子） | `时效性标注结果清单_全量_<日期>.jsonl`（3,901 条）+ 冲突台账 csv + 合并报告 md |
| 7–11 | `apply:gov / mof / nfra / pbc / supp` | **时效入账**：把清单写回五源正式记录，并同步刷新其派生 | 上一步的唯一清单 | 五源 cleaned 时效三字段 + 备份目录 + 索引/条文产物同轮刷新 |
| 12 | `classify:all` | **主题分类底座重建**：确定每份文件属于哪个业务主题，形成 RFN 权威台账与逐主题明细 | cleaned + 主题配置 | `人身保险公司-文件归属表.csv`（1,162 行，**RFN 唯一权威台账**）、主题归属表、**11 份逐主题明细表**、上位法索引、条款关系图；并自动刷新第 21 步的交付库 |
| 13 | `relations:gen` | **依据/废止关系全量重抽**：这份文件是根据什么制定的、它废止了什么 | cleaned + 归属表 + 内部制度正文 | `relations/relations_index.jsonl`（5,277 条 / 27 字段）+ 交叉依据表 + 统计 + RFN 补登候选 |
| 14 | `reconcile` | **跨层对账**：语料文号/时效与归属表是否漂移，并给出处置清单 | cleaned + 归属表 + 关系产物 | 漂移状态 + `reports/drift_清单_<日期>.csv`（审计台账） |
| 15 | `governance:sync` | **元数据入治理库**：把归属/主题/关系/时效投影进治理库并当场比对断言 | 上述四类元数据文件 | `data/governance.db` 四张元数据表（文件仍是事实源，库为只读投影） |
| 16 | `recall` | **语料召回与提取质量复核**：有没有该收没收的文件、正文有没有漏提取 | 归属表 + cleaned + 语料 | `召回复核报告.md`、边界案例清单、无全文清单、疑似漏提取清单、关键词扩充建议（csv/md） |
| 17 | `internal:merged` | **制度×监管依据对齐**：我方制度引用了哪些监管依据（供起草与合规核查） | 内部制度索引 + 归属表 | `merged_view.json`（878 条） |
| 18 | `reports:build` | **主题级管理报告** | 归属表 + 明细 + 关系 | `人身保险公司-全景分析报告.md` / `-主题分类报告.md` |
| 19 | `base:publish` | **发布层成型**：把底座整理成契约化的对外发布件 + 检索引擎 | cleaned + clauses + 关系 + 内部制度 | 外部底座：记录/条款/附件/关系 4 个 jsonl + 发布清单 + **检索引擎库（含全文检索）**；内部底座：制度/条款 2 个 jsonl + 发布清单 + 索引库 |
| 20 | `gates` | **质量门禁**：18 项自动校验（契约、一致性、可解析性、水位新鲜度等） | 全部产物 | 门禁结论（PASS/FAIL）+ 治理库归档；**任一项 FAIL 即当轮链条判失败** |
| 21 | `analysis:gen` | **分析交付库**：规划中的 17 项分析成果（纵向深化 5 + 横向整合 5 + 全景分析 7） | 分类/明细/关系/上位法 | `docs/reports/` **17 项 .md** + `_manifest.json`（逐文件字节指纹，供一致性核对） |
| 22 | `watch:baseline` | **快照基线记录**：记下本轮各源快照与内容指纹，供下轮判断"哪些源有变化" | 快照索引 | `data/watch_baseline.jsonl` 追加一条基线 |

## 2.2 "7 类链外环节"= 需人工或另调脚本的业务动作

链条覆盖的是"**从原文到对外交付件**"的确定性加工；链外这 7 类，要么需要**外部资源**（网络/配额/原始文件），要么是**创造性或人工决策**动作，要么**服务外部系统**。

| 类 | 业务含义 | 输入 | 产出物 | 在整体流程中的位置与作用 | 建议频率 |
|---|---|---|---|---|---|
| **1. 时效网络核验**（verify_missing / authority_backfill / classifier_pkulaw_verify） | **取证**：向权威库查证文件现行效力、被谁替代 | ①定性来源：五源 cleaned 的"效力缺失"记录、历史上用非权威来源判过的记录、归属表中未查验的行 | 查询缓存 + 结论变更台账 + **时效 SSOT** + 三态执行摘要 | 入账线（阶段 2）的**唯一证据来源**；不跑它，apply 就无新结论可写 | 每周一 07:00 |
| **2. RFN 补登与时效同步**（`rfn_backlog`） | **补齐监管文件编号台账**：给尚未编号的文件发号，并让台账与时效结论对齐 | 关系产物给出的线索 + 语料 | RFN 注册结果 + 归属表时效列同步 | 归属表是分类/明细/发布件的上游；补登后**必须同步时效**，否则一致性门禁 FAIL | 关系产物更新后 |
| **3. 内部制度摄取系列**（index / align / reocr / refine-identity / backfill） | **把我方制度原件入系统**：扫描入库、OCR 补字、文号身份纠正、条文回补 | 制度原件（含扫描件）、清单台账 | 制度主记录 + 正文 + 条文文件 + 索引 | 链条只做其中的"制度×依据对齐"；**入库/补字/纠身份需人工触发**（含高危操作禁令，需先 dry-run） | 新制度入库时 |
| **4. 起草交付件**（`cli draft` + 引用核验脚本） | **产出制度草案/修订稿**：基于制度与监管依据生成对照片段与底稿 | merged_view + 外部条款 + 素材 | 起草六件套（`docs/*.md`）+ 引用核验结论（严格模式可做门禁） | 属"创造性产出"，链条不自动生成；产出后需人工复核 | 起草/修订任务时 |
| **5. wiki / llm_wiki 同步与上游监测** | **对外知识库投喂与版本跟踪** | 发布件 | 监控源目录的 md（含 frontmatter）+ 上游版本比对结论 | 链条产物（发布件）的**外部消费出口**；不回流本仓 | 每周一 08:00 / 每月 1 日 |
| **6. graphify 代码图谱** | **代码结构图谱**（辅助开发与问答，非业务数据） | 源码 | 图谱产物（不入库，随提交自动刷新） | 与业务链路并行，不参与数据生产 | 每次提交（git hook） |
| **7. supp 本地法规补录** | **补录五源之外的合规文件**（经补充渠道入库，避免"外部权威源"混入交付件） | 本地补录清单（须含正文） | 进入 supp 源原始件 → 触发该源清洗 | 使"五源"口径在缺件时不破；**默认不在链条内跑**，需显式指定 | 有本地补件时 |

**另有"治理/辅助类工具"（不属上述 7 类，按需使用）**：外部语料归集（`ingest_corpus`）、原件库路径对账与命名治理、孤儿文件清理、基准登记（`gen_benchmark`）、提交前校验（`ci_check`）。

## 2.3 一句话总结

- **22 步** = 每日可重复的**确定性生产线**：原始件 → 规范记录 → 条文结构 → 时效入账 → 分类/关系/对账 → 对齐视图 → 报告 → 发布件 → 门禁 → 分析交付库 → 基线。**它的产出就是业务交付物本身**。
- **7 类链外环节** = 生产线的**上游供料、外部取证、外部出口与创造性动作**：取证（1）、补号（2）、制度入库（3）、起草（4）、对外同步（5）、开发辅助（6）、缺件补录（7）。
- 二者的衔接靠**三件"接口物"**：结论台账与 `verification_state.json`（取证→入账）、全量时效清单（入账内部）、发布件与水印（生产线→外部/下游）。**取证线可慢可断，入账线必须每轮跑全**——这是理解整套时效流程的关键。

---

---

# 一、"22 步"逐项展开（`tools/run_production_refresh.py`，`--no-scrape` 口径）

### 第 1 步 · `clean:gov`
- **业务含义**：清洗**国务院口径**法规原文（占全语料约 79%，是上位法/行政法规的主要来源）。
- **输入**：`data/raw/gov_laws.json`（13,178 条 / 1,034.8 MB，含 `xzfgk` 行政法规库 + `zhengceku` 部门文件两子源合并）。
- **产出物**：`data/cleaned/gov_cleaned_20260922.jsonl`（13,178 条，另有同口径 `.csv` 双轨）；`clean_index/index.json` 快照索引；`data/clauses/gov_clauses_20260922.jsonl/.md`（条文 69,350 条，清洗管道尾部自动生成）。
- **位置与作用**：五源清洗第 1 源、链条起点；后续分类/关系/上位法对照的主底座。

### 第 2 步 · `clean:mof`
- **业务含义**：清洗**财政部**规章与规范性文件（财政、税收、会计口径，与人身险交叉）。
- **输入**：`data/raw/mof_laws.json`（870 条 / 115.4 MB）。
- **产出物**：`mof_cleaned_20260922.jsonl`（870 条）+ `clean_index` 快照 + `mof_clauses_20260922.jsonl/.md`（条文 4,057 条）。
- **位置与作用**：五源第 2 源；补齐跨部门依据。

### 第 3 步 · `clean:nfra`
- **业务含义**：清洗**国家金融监督管理总局**（含原银保监会）文件——**人身保险监管的主源**。
- **输入**：`data/raw/nfra_regulations.json`（1,939 条 / 104.5 MB）。
- **产出物**：`nfra_cleaned_20260922.jsonl`（1,939 条）+ 快照索引 + `nfra_clauses_20260922.jsonl/.md`（条文 28,821 条）。
- **位置与作用**：五源第 3 源，主题分类与明细表的绝对主体。

### 第 4 步 · `clean:pbc`
- **业务含义**：清洗**中国人民银行**文件（反洗钱、支付、征信等与人身险交叉的管理要求）。
- **输入**：`data/raw/pbc_laws.json`（571 条 / 2.6 MB）。
- **产出物**：`pbc_cleaned_20260922.jsonl`（571 条）+ 快照索引 + `pbc_clauses_20260922.jsonl/.md`（条文 5,144 条）。
- **位置与作用**：五源第 4 源。

### 第 5 步 · `clean:supp`
- **业务含义**：清洗**补充渠道**文件（本地/官方补录），保证五源缺件时有兜底来源、不把"外部权威源"混入交付件。
- **输入**：`data/raw/supplementary_regulations.json`（62 条 / 8.2 MB）。
- **产出物**：`supp_cleaned_20260922.jsonl`（60 条）+ 快照索引 + `supp_clauses_20260922.jsonl/.md`（条文 1,275 条）。
- **位置与作用**：五源第 5 源、兜底源；链条默认**不触发其采集**（见链外第 7 类）。

### 第 6 步 · `timeliness:consolidate`
- **业务含义**：把多轮外部核验攒下的零散结论，**合并成唯一权威"时效清单"**并处理多来源打架（冲突台账）。
- **输入**：历史变更台账（22 份 `时效核验_*变更台账_*.csv`）+ 时效 SSOT `verification_state.json`（14,796 条）+ 历史全量清单（种子 3,009）。
- **产出物**：`时效性标注结果清单_全量_<日期>.jsonl`（3,901 条）+ `_冲突台账_<日期>.csv` + `_合并报告_<日期>.md` + `_consolidate_manifest.json`。
- **位置与作用**：**时效口径收口点**；下一批 apply 只认它。`无同名命中` 类走"不升级"分支（不臆造结论）。

### 第 7 步 · `apply:gov`
- **业务含义**：把清单中的 gov 结论**写回 gov 正式记录**（三级匹配：文号 → 标题+日期 → 唯一标题）。
- **输入**：最新全量清单 + gov cleaned 当前快照。
- **产出物**：gov cleaned 三字段回写（时效状态/替代文件/核验来源）+ `status` 派生 + 备份 `backups/cleaned_before_timeliness_<时间戳>/` + `clean_index` 重建 + **gov 条文产物同轮重建**。
- **位置与作用**：时效口径进入"事实源侧"（2026-09-21 实测：gov 写回 1 条、跳过 12,585 条）。

### 第 8 步 · `apply:mof`
- **业务含义**：同上，作用于**财政部**源（该源存量多为"已核验但权威库无同名"，故常为 0 写回）。
- **输入 / 产出物**：最新全量清单 → `mof_cleaned` 三字段 + `mof` 条文产物刷新 + 备份目录。
- **位置与作用**：保证 mof 的时效列与统一清单同源（否则明细表/发布件的 mof 行会与全库口径不一致）。

### 第 9 步 · `apply:nfra`
- **业务含义**：同上，作用于**金融监管总局**源（覆盖度最高的源，写回量通常最大）。
- **输入 / 产出物**：最新全量清单 → `nfra_cleaned` 三字段 + `nfra` 条文产物刷新 + 备份目录。
- **位置与作用**：人身险主源的时效口径落地点。

### 第 10 步 · `apply:pbc`
- **业务含义**：同上，作用于**人民银行**源。
- **输入 / 产出物**：最新全量清单 → `pbc_cleaned` 三字段 + `pbc` 条文产物刷新 + 备份目录。
- **位置与作用**：跨领域源的时效口径落地点。

### 第 11 步 · `apply:supp`
- **业务含义**：同上，作用于**补充渠道**源（补录件多为新文件，时效多判 `valid`）。
- **输入 / 产出物**：最新全量清单 → `supp_cleaned` 三字段 + `supp` 条文产物刷新 + 备份目录。
- **位置与作用**：完成五源"时效入账"闭环；此后链条不再触碰时效字段。

### 第 12 步 · `classify:all`
- **业务含义**：**主题分类底座强序重建**（base→cluster→match→detail→upper→clause_graph 六子步，hash 断点幂等），确定"每份文件属于哪个业务主题"。
- **输入**：五源 cleaned + 主题配置 + 归属表既有行。
- **产出物**：`人身保险公司-文件归属表.csv`（1,162 行，**RFN 唯一权威台账**）+ 主题归属表 + **11 份 `T<N>_<n>逐份条款引用与上位法依据明细表.csv`** + `_upper_laws.json` + `_t*_clause_graph.json`；**并自动触发第 21 步**刷新分析交付库。
- **位置与作用**：分类与明细的唯一来源，是关系抽取、对账、发布件、报告的共同上游。

### 第 13 步 · `relations:gen`
- **业务含义**：**依据/废止关系全量重抽**——"这份文件依据什么制定、它废止了什么"。
- **输入**：五源 cleaned + 归属表 + 内部制度索引。
- **产出物**：`classifier/data/relations/relations_index.jsonl`（5,277 行 / 27 字段，依据 4,070 + 废止 1,207）+ `cross_basis.jsonl` + `relations_stat.json` + `rfn_backlog.csv`（补登候选）。
- **位置与作用**：关系图谱、内部制度对齐视图、起草素材的共同事实源。

### 第 14 步 · `reconcile`
- **业务含义**：**跨层对账**——语料文号/时效与权威台账（归属表）之间是否漂移，并产出处置清单。
- **输入**：cleaned + 归属表 + 关系产物。
- **产出物**：`rfn_drift_state.json`（漂移状态）+ `reports/drift_清单_<日期>.csv`（可审计台账，含人工确认项）。
- **位置与作用**：数据一致性防线；其产物是门禁 `gate_rfn_drift` 的判据载体。

### 第 15 步 · `governance:sync`
- **业务含义**：把归属/主题/关系/时效四类元数据**投影进治理库**，并当场做"库 vs 文件"比对断言。
- **输入**：归属表、主题表、关系产物、时效 SSOT。
- **产出物**：`data/governance.db` 四张元数据表（`document` / `theme_assign` / `relation` / `timeliness_history`）。
- **位置与作用**：让治理元数据可查询、可审计（文件仍是事实源，库为只读投影）。

### 第 16 步 · `recall`
- **业务含义**：**语料召回与提取质量复核**——有没有该收没收的文件、正文有没有漏提取。
- **输入**：归属表 + cleaned + 语料原件。
- **产出物**：`召回复核报告.md`、`边界案例清单.csv`、`归属表无全文清单.csv`、`疑似漏提取文件清单.csv`、`关键词库扩充建议.csv`、`scan_records.csv`、`_stats.json`、`重跑执行报告.json`。
- **位置与作用**：语料完整性与提取质量的独立核查面（四道预检门禁 + 检索复核）。

### 第 17 步 · `internal:merged`
- **业务含义**：生成**"我方制度 × 监管依据"对齐视图**——每份内部制度引用了哪些外部监管文件。
- **输入**：内部制度索引 + 归属表 + 关系产物。
- **产出物**：`internal_policy_base/data/merged_view.json`（878 条，含 336 条带 RFN 引用）。
- **位置与作用**：起草/修订与合规核查的素材底座；也是内部发布件的上游。

### 第 18 步 · `reports:build`
- **业务含义**：生成**管理层视角的主题级报告**。
- **输入**：归属表 + 11 份明细表 + 关系产物。
- **产出物**：`classifier/docs/reports/人身保险公司-全景分析报告.md`、`人身保险公司-主题分类报告.md`。
- **位置与作用**：链条内唯一"面向阅读"的中文报告产物；分析交付库（第 21 步）的姊妹件。

### 第 19 步 · `base:publish`
- **业务含义**：把底座整理成**契约化发布件 + 检索引擎**（应用系统只读这一层）。
- **输入**：cleaned + 条文产物 + 关系产物 + 内部制度索引与对齐视图。
- **产出物**：外部底座 `external_records.jsonl`（16,613）/ `external_clauses.jsonl`（108,647）/ `external_attachments.jsonl`（4,022）/ `external_relations.jsonl`（1,821）+ `publish_manifest.json`（含逐文件 sha256 与计数）+ `external_index.sqlite`（全文检索引擎）；内部底座 `internal_policies.jsonl`（878）/ `internal_clauses.jsonl`（10,071）+ manifest + `internal_index.sqlite`。
- **位置与作用**：**对外唯一数据接口层**；下轮门禁校验的对象就是它。

### 第 20 步 · `gates`
- **业务含义**：**18 项自动质量门禁**，任一项 FAIL 即当轮链条判失败。
- **输入**：全部产物 + 治理库水位。
- **产出物**：18 项判据结论（控制台）+ 治理库 `gate_result` 归档。18 项为：①盘符字面量扫描 ②受控枚举一致性 ③目录拍平 ④重复工具/重名定义 ⑤数据契约（列头/键集）⑥硬编码快照日期 ⑦RFN 跨文件一致性 ⑧RFN↔clean 漂移未处置阻断 ⑨时效单源传播一致性 ⑩制度引用门禁（起草严格模式）⑪源目录配置一致性 ⑫血缘 provenance 覆盖 ⑬中文列名受控注册 ⑭密钥/敏感值硬编码扫描 ⑮内部制度索引↔原件库可解析性 ⑯依据/废止关系产物 ⑰产物水位一致性 ⑱跨模块直连扫描。
- **位置与作用**：全链质量闸门；是"链条能不能算成功"的唯一裁决者。

### 第 21 步 · `analysis:gen`
- **业务含义**：生成**规划中的 17 项分析交付库**（全数据驱动）。
- **输入**：分类产物 + 明细表 + 关系产物 + 上位法索引。
- **产出物**：`docs/reports/` **17 项 .md**（2.1.1.1–2.1.1.5 纵向深化 5 项、2.1.2.1–2.1.2.3 + `监管与制度依据废止关系图谱.md` + `RFN补登候选清单.md` 横向整合 5 项、2.1.3.1–2.1.3.7 全景分析 7 项）+ `_manifest.json`（逐文件字节指纹）。
- **位置与作用**：最终业务交付物；第 12 步也会自动触发它（故本步是"兜底/显式重跑"入口）。

### 第 22 步 · `watch:baseline`
- **业务含义**：记录本轮各源快照与内容指纹，供下轮判断"哪些源发生了变化"。
- **输入**：clean_index 快照索引。
- **产出物**：`data/watch_baseline.jsonl` 追加一条（源 / 日期 / 记录数 / 内容 sha）。
- **位置与作用**：链条收尾步；支撑幂等与"增量化"判断（无变化则下轮各阶段快跳）。

---

# 二、"7 类链外环节"逐类：触发场景 + 触发后固定下游流程

### 类 1 · 时效网络核验（三个脚本）
1. **`verify_missing.py`** —— 面向"五源 cleaned 中时效字段为空"的记录做效力核验。
   - **触发场景**：每周定时（每周一 07:00）或人工，需北大法宝 token + 配额；配额/认证受限时自动停手且**不写任何判定**。
   - **固定下游流程**：`consolidate_timeliness --use-state` → `apply_timeliness_to_cleaned --source <源>×5`（链内阶段 2）→ `rfn_backlog --sync-timeliness --apply` → 全链条刷新（分类/关系/对账/发布件/门禁/交付库）。
2. **`authority_backfill_verify.py`** —— 面向"历史上由非权威来源判定过"的记录做**权威复核**（把结论升级为北大法宝口径）。
   - **触发场景**：核验批次后；**配额耗尽时可用 `--judge-only` 零配额补齐**（只消费断点中已成功的查询结果）。
   - **固定下游流程**：与类 1-1 相同（同一入账线：consolidate → apply → 同步归属表 → 全链条）。
3. **`classifier_pkulaw_verify.py`** —— 面向"权威台账（归属表）中未查验/待定"的行核验。
   - **触发场景**：核验批次后（归属表口径需更新时）。
   - **固定下游流程**：**该脚本自身即写归属表与 cleaned**（唯一双写口的例外）→ 随后必须 `consolidate --use-state` → `apply --source <源>` → 全链条（其中 `gate_timeliness_ssot` 校验"SSOT↔归属表↔cleaned"三层一致）。

### 类 2 · RFN 补登与时效同步（`tools/rfn_backlog.py`）
- **业务含义**：给尚未登记的监管文件**补发编号（RFN）**，并让权威台账与时效结论对齐。
- **触发场景**：关系产物更新后（`rfn_backlog.csv` 出现新候选）；按需人工执行 `--apply`。⚠️ 补登属受控写操作（走唯一写口，带备份）。
- **固定下游流程**：`--sync-timeliness --apply`（归属表时效列，键精确）→ `cli.py classify --all`（明细表/主题报告依赖归属表）→ `cli.py relations gen`（新编号提升关系解析率）→ `cli.py base publish` → `cli.py gates`。**漏掉 `--sync-timeliness` 会直接使时效一致性门禁 FAIL。**

### 类 3 · 内部制度摄取系列
1. **`cli.py internal index`** —— 把我方制度原件扫描入库（生成制度主记录、正文、条文）。
   - **触发场景**：新制度原件放入原件库时。⚠️ 高危：`--only-unindexed` 在"待索引清单为空"时会退化为全库重扫，**必须先 `--dry-run`**。
   - **固定下游流程**：`internal align` → `internal merged` → `cli.py base publish`（内部发布件）→ `cli.py gates`。
2. **`cli.py internal align`** —— 制度与主题/依据的对齐。
   - **触发场景**：摄取后、归属表或主题口径更新后。
   - **固定下游流程**：`internal merged` → `base publish` → `gates`（对齐结果直接进 `merged_view.json`）。
3. **`cli.py internal reocr [--force|--retry]`** —— 对扫描件/提取失败件做 OCR 回填（PaddleOCR 优先）。
   - **触发场景**：`gate_original_resolvable` 或基准显示正文缺失/有效汉字过少时。
   - **固定下游流程**：该命令自带写回（正文+条文+索引）→ `internal merged` → `base publish` → `gates`。
4. **`cli.py internal refine-identity`** —— 文号/名称身份精化纠正。
   - **触发场景**：发现文号/名称识别偏差（如括号形态不规范）时。⚠️ 身份变更会导致 IPN 漂移，**须评估全链引用影响**。
   - **固定下游流程**：`internal merged` → `relations gen`（内部依据关系）→ `base publish` → `gates`。
5. **`cli.py internal backfill`** —— 存量制度**条文结构回补**（无需重摄）。
   - **触发场景**：条文解析器升级、或存量制度缺条文结构时。
   - **固定下游流程**：`internal merged` → `base publish`（`internal_clauses.jsonl`）→ `gates`。

### 类 4 · 起草交付件（`cli.py draft` + 引用核验）
- **业务含义**：产出制度草案/修订稿与条款对照片段（创造性产出）。
- **触发场景**：有起草/修订任务时（人工发起）。
- **固定下游流程**：`verify_regulatory_citations.py --strict`（逐条回源核验引用真伪）→ 下一次 `cli.py gates` 中的**制度引用门禁**（`gate_citations`）验真 → 人工复核定稿。

### 类 5 · wiki / llm_wiki 同步与上游监测
1. **`tools/sync_wiki_sources.py`** —— 把发布件导出为外部知识库可读的 Markdown（SHA256 增量）。
   - **触发场景**：发布件更新后（定时：每周一 08:00）。
   - **固定下游流程**：**无仓内下游**（单向输出给外部知识库，不回流）。
2. **`tools/check_llm_wiki_upstream.py`** —— 监测外部知识库上游版本。
   - **触发场景**：每月 1 日巡检，或需确认是否需要适配新版时。
   - **固定下游流程**：无（仅产出比对结论；如上游改版则另立适配任务）。

### 类 6 · graphify 代码图谱
- **业务含义**：生成代码结构图谱（辅助开发与问答，不参与业务数据生产）。
- **触发场景**：每次 git 提交/检出（受管 hook 自动执行），或人工 `python tools/graphify_sync.py`。
- **固定下游流程**：无业务下游；产物随提交刷新（不入库、不参与门禁）。

### 类 7 · supp 本地法规补录
- **业务含义**：把五源之外的合规文件经**补充渠道**入库（避免以"外部权威源"形式进入交付件）。
- **触发场景**：出现本地/官方补录清单时（需显式指定 `--supp-batch <backlog.json>` 或 `collectors/supp_ingest_local_dir.py`；链条默认不跑）。
- **固定下游流程**：`supp_ingest_batch` 自动调用 `clean/run_clean_pipeline --project supp`（含条文节点）→ **必须再跑全链条** `run_production_refresh.py --no-scrape`，否则分类/关系/发布件/交付库仍是旧口径。

---

# 三、治理类与辅助类工具逐项（名称 / 用途 / 适用场景）

| # | 工具 | 类别 | 用途 | 适用场景（触发时机） |
|---|---|---|---|---|
| 1 | `run_production_refresh.py` | 编排 | 全流程生产刷新编排器（本文第一部分的 22 步） | 每日/按需刷新；`--no-scrape` 纯本地 |
| 2 | `ci_check.py` | 编排（校验） | 本地 CI 一键串联 ruff → pytest → gates | 提交前、或定时巡检 |
| 3 | `governance_sync.py` | 治理 | 事实源 → 治理库元数据四表投影 + 比对断言 | 链内阶段 3.5 自动；也可人工 `--apply` |
| 4 | `governance_register_artifacts.py` | 治理 | 把原件（制度/语料）注册进治理库 | 初次建立治理库、或原件库大规模变动后 |
| 5 | `extract_relations.py` | 治理（关系） | 依据/废止关系抽取编排 + 三类关系产物生成（链内阶段 2.6 的被调实现） | 关系口径需单独重抽时（链内已自动覆盖） |
| 6 | `rfn_backlog.py` | 治理（台账） | RFN 补登候选清单生成 + `--sync-timeliness` 时效同步 | 关系产物更新后；补登后必须同步时效 |
| 7 | `normalize_internal_naming.py` | 原件库治理 | 内部制度**规范命名 + 归集**（默认 dry-run） | 新批量入库前、命名不一致时；**执行前自动备份索引** |
| 8 | `reconcile_original_paths.py` | 原件库治理 | 主索引路径 ↔ 原件库对账与重定位（按内容匹配） | 原件目录移动/改名后（可 `--apply`，幂等 + 备份） |
| 9 | `prune_orphan_processed.py` | 原件库治理 | 清理"孤儿 processed 记录"（移入备份，可回滚） | 索引删除后残留派生文件时 |
| 10 | `dedupe_original_storage.py` | 原件库治理 | 原件库冗余处置 + 跨层硬链接 + 非正文标记 | 磁盘治理、发现重复件时 |
| 11 | `split_internal_nonpolicy.py` | 原件库治理 | 把"非制度正文件"从制度库中隔离归置 | 台账/表格类误入制度库时 |
| 12 | `ingest_corpus.py` | 语料接入 | 外部语料归集（清单 + sha256 幂等，保留原目录树） | 新拿到的语料包需要留档时（归集层，业务不直读） |
| 13 | `build_east_backlog.py` | 语料接入 | EAST2.0 表格类监管文件 → supp 收录清单生成 | 需要从 EAST2.0 批次补录法规时 |
| 14 | `gen_analysis_deliveries.py` | 交付生成 | 17 项分析交付库生成（链内阶段 6.8 的被调实现） | 分类等数据重建后需刷新交付库时（`classify` 会自动触发） |
| 15 | `gen_benchmark.py` | 交付/基线 | 聚合数据量/测试/门禁状态写根 `BENCHMARK.md` | 数据量或门禁变化后 |
| 16 | `gen_theme_moc.py` | 交付生成 | 由发布件生成每主题的 MOC 聚合页（外部知识库双链） | 发布件更新后需同步知识层时 |
| 17 | `gen_supp_timeliness_audit.py` | 时效专项 | supp 源时效标注口径只读审计 | 抽查/复核 supp 时效一致性时 |
| 18 | `sync_wiki_sources.py` | 外部接入 | 发布件 → 外部知识库监控源同步（SHA256 增量） | 发布件更新后（定时：每周一 08:00） |
| 19 | `check_llm_wiki_upstream.py` | 外部接入 | 外部知识库上游版本监测 | 每月 1 日巡检 |
| 20 | `graphify_sync.py` | 开发辅助 | 代码图谱跟随提交自动刷新（含 hook 安装） | 每次提交（hook 自动）；`--install-hooks` 仅首次 |
| 21 | `graphify_offline_html.py` | 开发辅助 | 图谱 HTML 依赖本地化（离线可看） | 需要离线查看 `graph.html` 时 |
| 22 | `build_migration_manifest.py` | 迁移（一次性） | 生成数据迁移清单 | 迁移/搬仓时 |
| 23 | `flatten_collectors.py` | 迁移（一次性） | collectors 目录物理拍平 | 目录结构调整时（现已拍平完成） |
| 24 | `migrate_collectors_p3b.py` | **已退役** | 拍平迁移脚本（A 项完成后作废） | 不再使用（保留仅供追溯） |

**一句话定位**：第 1–2 项是"总控"（一个跑业务链、一个跑质量链）；第 3–6 项属"治理与台账"（水位/血缘/编号/时效对齐）；第 7–11 项属"原件库治理"（命名、路径、冗余、归集）；第 12–17 项属"交付与基线"；第 18–19 项属"外部知识层接入"；第 20–21 项属"开发辅助（图谱）"；第 22–24 项属"一次性迁移（含 1 项已退役）"。**这些工具都不在 22 步自动链内**，需按上表场景人工触发。