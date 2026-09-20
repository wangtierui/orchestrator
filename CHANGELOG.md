# Changelog

## [Unreleased] 2026-09-20 — 条款产物六类缺陷修复（F1–F7）+ 三项相邻问题 + 全链刷新

> 用户指令：针对 `nfra_clauses_20260919.{md,jsonl}` 的六类问题排查五源数据、锁定根因并出具修复方案；
> 随后要求**自主推进全部修复至验证通过**（含相邻问题）。排查报告见
> `reports/2026-09-20_nfra条款产物_01_六类缺陷根因排查与修复方案.md`（§9 实施结果）。

### 一、解析层修复（`std_lib/scraper_std/document_structure.py`）

- **F1 锚切分**：新增 `resplit_embedded_anchors`（段内二次切分）——修复「二、基本原则（一）服务…」
  中 `（一）` 紧贴标题末字导致**子节点被吞**（实测五源 577 处 / 182 份）；三重判据（短标题形态 +
  标记后 ≥20 字实质正文 + 非内联引用「项/款/目…」）+ **层级一致性**（只切更深层或同层不同号）
  否证 `负责一、二类口岸…`、`上述(一)、(二)项…`、`本通知第（一）（二）…`；同步修复章标题粘连
  （`总 则第一章 总 则`）与**节标题并入上一条正文**（新增 `_SECTION_RE` 分支：flush + 留痕计数）。
- **F2 标题·正文分离**：`_split_node_title_body`（候选①整段 / ②首个空白前片段，取首个标题形态者）
  ——修复「（一）总体目标 借鉴…」整段进 `title`、「五、发行人应充分…」无标题却被写成 title
  （实测 81,489 处 / 7,923 份）。
- **F3 文档级尾部截断**：`strip_document_tail`（**尾区**判定：`附：`/答记者问/此件发至/发文机关+日期/
  联合发布网页噪声），命中落 `parse_meta.tail_cut` 可追溯；反例（正文内合法生效日期、「附件一」引用）不误截。
- **F5a 解析输入归一**：`normalize_parse_text`（异常空白 → 空格、汉字间异常空白删除、断句注入型空格去除）
  ——**只读**，事实源由 clean 层负责（F5b）。
- **F6 条内层级**：`parse_article_structure`（条 → 项（一）→ 目 1.），新增行级 `article_structure`
  字段（契约 `CLAUSE_LINE_FIELDS` **18 → 19**）+ `render_markdown` 层级渲染。
- **F7 检核增强**：新增 V008 结构层级完整性 / V009 尾部污染 / V010 空白污染（判据唯一实现
  `structure_semantics`，单次遍历）；`validate_schema` 增补 key 集/level 闭包/条号对应与
  **结构语义指标**（`title_swallow`/`tail_contam`/`space_contam`/`law_items`），
  `test_clause_index_schema` 断言指标上限 —— 堵死"子层级被吞静默通过"。

### 二、清洗层修复（事实源保真）

- **F4 段落边界保真**：`pipeline._clean_record_body` 改用 `clean_text(..., keep_newlines=True)`，
  **断句产物不再回写 `body_text`**（旧实现按"断句"目标插入句末 `\n` 与标点后空格，导致"标题行/正文行"
  信号丢失、同段被切成 title+content）。
- **F5b 异常空白归一**：`cleaner._EXOTIC_SPACE(_CJK)` 覆盖 NBSP/EN SPACE/EM/THIN/FIGURE/IDEOGRAPHIC
  （原 `_MULTI_SPACE=[ \t\u3000]+` 未覆盖，五源产物残留 23,974 个）。
- **联动适配（段落化输入暴露的三处隐性依赖）**：
  ①`_parse_law` 遇附件/目录行**不再中断整篇**（原 `^关于印发.*的通知$`/`^附件` 会在首条前 break，
  实测《…证券期货规范性文件的决定》835 → 45 条、全源一度 107,520 → 99,234）；
  ②`score_parse` coverage **计入结构单元续行**（否则 notice 8,154 → 4,950、plain 5,853 → 9,069 退化）；
  ③`_join_para` **标点边界不插空格**（消除段落拼接回流的「， 并履行」类空格 3,622 处）。

### 三、相邻问题

- **章/节粘连**：见 F1（含节边界处理，523 处节标题不再并入条文正文）。
- **nfra 正文重复导语**：新增 `unified_schema.dedup_webpage_lead`（首部同段以「：」结尾重复两次 → 保留一份），
  `map_nfra` 接入；实测 nfra 82/1939 份，其余四源 0 份。
- **`relation_id` 不唯一**：`extract_relations._relation_id` 纳入判别字段（article/action/scope/reason/
  dst_docno/dst_kind/src_key）+ `_ensure_unique_ids` 确定性唯一化；stat 增 `relation_id{rows,distinct,renamed}`；
  **`gate_relations` 新增判据 9（id 唯一性）**；extractor 1.0 → 1.1。实测 5,266 行/4,859 id（366 重复）
  → **5,272 行 / 5,272 id（0 重复）**。

### 四、数据刷新与验收

- `cli.py internal backfill`（877 份，共用解析面）→ `tools/run_production_refresh.py --no-scrape`
  全链 22 步（每轮 1,004~1,010s，失败步骤 0，gates rc=0）；末轮定点重建（clauses `rebuild=True` →
  `base publish` → 水位重登记 → gates）→ 产物换版 `{src}_clauses_20260920.*`。
- **验收**：行数 16,617 = 16,617（仅旧 0/仅新 0）；条文 107,520 → **108,222（+0.65%）**；
  文本量 −0.06%（仅尾部噪声）；**A 577→0、B 81,489→0、C 563→92、D 38,487→40、E 17,805→446、F 23,974→47**；
  新增条内层级节点 17,380；notice 8,154→9,176、plain 5,853→4,843（结构覆盖改善）；
  门禁 **18/18 PASS**、`pytest` **367**（基线 356 + 新增 11）全绿。

## [Unreleased] 2026-09-19 — 三项全链条数据刷新（时效续跑 / clauses 下游 / 内部条文重生成）+ 四处顺序缺陷修复

> 用户指令：①对未完成时效验证的源续跑并刷新下游 ②基于 `regulatory_scrapers/data/clauses`
> 变更按全链条刷新下游 ③用优化解析重生成 `internal_policy_base/data/processed/*_clauses.json`
> 并刷新下游。三项统一走 `tools/run_production_refresh.py`（22 步）全链条。

### 一、任务 1：时效验证续跑与落地

- **环境阻断修复**：`PKULAW_NODE_EXE` 指向的 node 版本目录被运行时升级腾空
  （`…/node/versions/22.22.2-2/node.exe` 不存在，实际在 `22.22.2-3`）→ CLI 静默失联
  （报「未找到 pkulaw-mcp CLI」，**极易误判为未安装**）。新增
  `pkulaw_cli._resolve_node_exe`：环境路径不可用时派生同层 `versions/current` 指针解析，
  **不写死本机路径**（下次运行时升级不再断链）。
- **引擎缺陷修复**：`execute_queries` 原**一次性提交全部候选**、再 `as_completed` 收集 →
  收集/断点落盘被"提交阶段"（`pause=0.2s`×N，gov 1.2 万条 ≈ 42 min）整体推迟，表现为
  **断点长期不落盘**且认证失败门哨（连续 15 次）要 42 min 后才可能触发。
  改为**分块提交/收集**（`_CHUNK=50`）：断点实时、门哨即时（实测重跑后 14 条即触发停止并落盘）。
- **续跑结果**（`run_timeliness_resume.py`，前四源 → gov 门禁）：
  - supp / nfra：无效力缺失记录（已完成）；pbc：2 条 `nomatch`；mof：546 条 `nomatch` + 1 条查询失败
    （**`nomatch` 属正常结论**：该文件在北大法宝无同名记录）；
  - gov：**探活成功**（30 条：1 `valid` + 29 `nomatch`，~1.1 s/条）→ 随后**北大法宝网关阻断**
    （直连 CLI 单条亦报「认证失败」，**非代码缺陷**）→ 按 R13 降级纪律**不写判定**、
    保留断点续跑（`pkulaw_gov_missing_checkpoint.jsonl` 203 条，gov 待核 **12,586** 条）。
- **落地**：`consolidate_timeliness --use-state`（全量清单 3,708 条；`无同名命中` 走
  `skip_downgrade` 不强行升级）→ `apply_timeliness_to_cleaned --source gov|mof|pbc`
  （gov 写回 1 条新判定 `valid`）→ `rfn_backlog --sync-timeliness --apply`
  （SSOT 14,768 → 归属表 **188 行**改动）。
- **⭐ 顺序缺陷修复（新增一环派生刷新）**：`apply_timeliness_to_cleaned` 回写 cleaned 后**同步刷新条款产物**
  （同 F-C05「回写后重建 clean_index」先例）。动因：clauses 由 cleaned 派生，而编排链顺序是
  「阶段 1 clean 尾部建 clauses → 阶段 2 apply 改 cleaned」→ 不刷新则
  `clauses.timeliness_status` **系统性滞后一个 apply 轮次**（实测五源 16,557 条**全为空**）；
  且 `published:external` 声明 `clauses:{src}` 为依赖，水位必须取到终态。

### 二、任务 2：clauses 产物下游刷新（**编排顺序缺陷修复**）

- **暴露**：重跑链时 `gate_relations` 判据 8 与 `gate_watermark` 双双 FAIL ——
  `relations_index 声明 internal_index=c2a9f380d9b67dd6 但当前=dd919a09a9a57f8b`。
- **根因**：`relations:gen`（阶段 2.6）声明 `internal_index` 为依赖，而依赖版本解析走**当前水位表**；
  观察型水位历史上**只在阶段 3 登记** → 若 `internal_index` 在本次链**之前**被链外写方改动
  （如 `cli internal backfill` 回刷章条数），阶段 2.6 解析到的是**上一轮旧版本**，
  阶段 3 再把新版本写进表 → 当场判"产物陈旧"。
- **修复**：新增**阶段 2.55「观察型水位提前登记」**（紧邻 `relations:gen` 之前），阶段 3 之后仍再登记
  一次（终态刷新）；同版本重复登记幂等。
- **结果**：clause 五源产物在链内由 apply 阶段落为终态（时效投影 3,483 条，此前全空），
  `published:external` 取到终态版本；**重跑链 22 步全 rc=0（含 gates 18/18）**。

### 三、任务 3：内部条文产物重生成（优化解析）

- **三写入点收敛为唯一装配**：`indexer.ingest` / `extract.backfill_clauses` / `extract.reocr_backfill`
  原先各自拼 `_clauses.json` 键集 → 统一经
  `internal_policy_base.extract.build_clause_payload`（**唯一实现**）。
- **解析器升级**：`extract_structure`（仅 law）→ **`parse_document`**（自动降级
  `law/notice/bulletin/plan/plain` + 续行收编 + 章索引钳位），**与外部五源条款产物同源**；
  载荷增量扩 3 键（`structure/structure_count/parse_mode`），`chapters`/`articles` 键集与语义不变
  → `build_internal` / drafter 条款对照**零改动**；契约登记
  `interfaces.contract.INTERNAL_CLAUSE_FIELDS`。
- **重生成 877 份**（`cli internal backfill`，55 s）：条文 10,213 → **10,072**
  （内联交叉引用误切消除）/ 章 2,089 → 2,015 / 零条 474 → **470** / 新增 `structure`
  **69 份 543 单元**（非条文体通知/方案/预案；其 `_clauses.md` 由**空**变为可读，经 `render_markdown` 落盘）。

### 四、验收

- 全链条刷新 **22 步全 rc=0**（977 s）；`cli.py gates` **18/18 PASS**；`pytest` **全通过**；
  `clause_index.validate_schema` **consistent=true**（16,617 文件 / 107,520 条 / 12,046 章 / 48,596 结构单元）。
- 发布件同步：`external_clauses.jsonl` **107,520** 行（反洗钱法 65 行）；
  `internal_clauses.jsonl` **10,072** 行（= 重生成结果）。
- 历史两案例复核：`保监发〔2001〕126号` → `parse_mode=notice`、`structure_count=9`、
  `validation=PASSED`、时效 `repealed`；`反洗钱法` → 65 条、条号 1..65 唯一连续、时效 `valid`。
- ⏸ **未完成项（外部阻断，非缺陷）**：gov 待核 12,586 条 —— 北大法宝网关阻断中，
  按断点续跑（`run_timeliness_resume.py --only gov`）；恢复后需再跑一次本链条让判定落地。

## [Unreleased] 2026-09-18 — 条文解析适配（空解析 / 换行截断 / 交叉引用误判三问题修复）

> 问题驱动：`nfra_clauses_20260918` 中 ①`17b6ff89…`（保监发〔2001〕126号）解析结果为空；
> ②`abb0c020…`（反洗钱法）法条被换行截断致编号重复、交叉引用被误判为条首。
> 参考实现：`auto_degrade_parser.py` + `3.1/3.2/3.3`（校验器）+ `5.1/5.2/5.3`（修复件）。

### 一、根因（均带实测证据）

- **空解析**：`保监发〔2001〕126号` 为**通知体**（`一、/1、` 层级序号，通篇无「第X条」），
  旧实现只认 `第X条` → `article_count=0 / articles=[]`。**全量口径**：五源 16,617 份中
  13,878 份（83.5%）零条，其中绝大多数是通知/公告/批复/指引——属**系统性缺口**而非个例。
- **编号重复 + 交叉引用误判**：`clause_index.segment_body` 的条锚判据是"前一字符非汉字/
  字母数字/闭括号即切"，于是 `…本法第五十三条、` + `第五十四条规定的行为…` 被切成两条
  （前者序号 55、后者序号 54 → **重复**）。**全量口径**：五源 631 份文件存在重复条号。
- **顺带查出第三类缺陷**：同判据把正文里的 **Word 项目符号/私用区字符**（`\ue004`）视为非边界
  → 条文被吞进上一条（nfra《个人贷款管理办法》52→35、《流动资金贷款管理办法》51→35）。
- **顺带查出第四类缺陷**：章标题与首条粘连（`第一章 总 则第一条 为了…`，GFM 无关，实为
  章标题以 CJK 收尾被"前非汉字"判据漏切）→ 首条丢失（gov《民用建筑节能条例》起于第 2 条）。

### 二、适配（采纳参考实现，逐条对齐）

- **自动降级解析**（参考 `auto_degrade_parser`）：`law` 主模式 + `notice/bulletin/plan` 层级体
  + `plain` 兜底；`ModeScorer` 权重 `coverage .5 / legality .3 / continuity .2`、阈值 `0.75`。
  非条文体产出 **`structure`**（层级节点，键集恒定 `level/number/title/content/items/children`）。
- **法条合并修复**（参考 `5.1`）：续行残片收编回上一条，句子复原。
- **编号去重**（参考 `5.2` 的去重部分）+ **章节映射钳位**（参考 `5.3`）。
- **条款校验器**（参考 `3.1/3.2/3.3`）：`V001–V007` + `{status, issues:[{rule_id,severity,message}]}`；
  受控枚举 `config.enums.CLAUSE_PARSE_MODES / CLAUSE_ISSUE_SEVERITY`。
- **锚切分统一**：`segment_body`（章/条）+ 新增 `segment_outline`（`一、/（一）/1、/——`）
  上收 `std_lib.scraper_std.document_structure`（`clause_index.segment_body` 保留 re-export）。

### 三、取舍（参考与本项目冲突处，逐条给出依据）

| 项 | 参考做法 | 本项目做法与依据 |
|---|---|---|
| 条号重排 | `ArticleRenumber` 重排为 1..N 并重铸 `number` | **只去重、不重排**：`no` 承载**文中真实条号**（下游按条号引用/展示），重排会篡改法律条号 |
| 层级体重标 | `NoticeParser` 把「一、」重标为「第一条」 | **不重标**，`structure` **原样保留序号**（同"禁止臆造标识"纪律，避免下游生成不存在的条号引用） |
| `plain` 兜底 | 每段变成一条 | **不物化段落**（正文全文已由 cleaned 承载），仅记 `plain_paragraphs` 计数 → 避免冗余 |
| 模式选择 | 全模式 argmax + 阈值 | **law 出条即采用**（保证既有法规文档零回归），仅在 law 为空时降级评分择优；law 低分只记诊断 |
| 重复即合并 | `is_duplicate` 即并入上一条 | 追加**续行判据**（回溯指代/纯分隔符/极短残片）：避免把"一份文件内嵌套另一份完整文件"（修改决定附被修订办法全文，条号重启）**整块并掉**（实测曾并掉 104 条） |
| V002 严重度 | 编号不连续一律 ERROR | **跳号 → WARN**（语料含节选/修正案真实跳号），**重复/非单调 → ERROR**（真解析缺陷） |
| V001 严重度 | 6 个必需字段一律 ERROR | 身份字段（`dedup_key`/`title`）ERROR；日期/文号 4 项 WARN（空值源于**源数据属性**，实测 `effective_date` 空 1,701 份，一律 ERROR 会淹没真缺陷） |

### 四、产物契约（`contract.CLAUSE_LINE_FIELDS` 11 → 18 维）

新增 `parse_mode / parse_score / parse_meta / is_fallback / structure / structure_count /
validation`；新增 `CLAUSE_STRUCTURE_FIELDS`。`articles[]`/`chapters[]` **键集不变**（下游零改动）；
`clause_index.validate_schema` 同步增补新字段与枚举/键集断言（**parse 面纳入门禁级校验**）。
⚠️ 顺带修正既有缺口：条号形态正则漏 `零/万`（「第一百零一条」被误判异常）→ 改为与解析侧
**同源**（`document_structure.CN_NUM_CHARS`）。

### 五、验证（修复效果 + 零回归）

| 指标（五源全量） | 修复前 | 修复后 |
|---|---|---|
| 重复条号文件数 | **631** | **89**（剩余为真·多文档嵌套，已由 V003 报出，正文保全） |
| 非 1..N 序列文件数 | 968 | 238 |
| 解析出条文（law 模式）文件数 | 2,739（含误报） | 2,610（误报消除，误报文件正确降级） |
| 零条文件数 | 13,878 | 14,007（**其中 8,117 降级为 notice、5,853 为 plain**，不再"空解析"） |
| structure 结构单元 | — | **48,596** |
| 合并修复次数 | — | 263（判据加固前为 4,756，含过度合并） |

- **问题 1 已解**：`保监发〔2001〕126号` → `parse_mode=notice`、`structure_count=9`
  （一级 9 单元 + 条目 10）、`parse_score.total=0.88`、`is_fallback=false`、`validation=PASSED`。
- **问题 2 已解**：`反洗钱法` → **65 条、条号 1..65 唯一且连续**，第五十五条正文复原为
  `…第五十三条、第五十四条规定的行为…`（含原文"第五十四条"字样，**零丢字**）；
  第五十七条同款复原。
- `validate_schema` **consistent=true**（16,617 文件 / 107,520 条 / 12,046 章 / 48,596 结构单元
  / 98 份判 FAILED 属多文档嵌套等**真实异常**、已如实报出）。
- 新增用例 **+17**（`tests/test_document_structure.py`，纯函数无 data marker）；
  `pytest` **356 用例**全通过；`cli.py gates` **18/18 PASS**。

## [Unreleased] 2026-09-18 — 阶段 2/3/4：元数据投影 + DAO 收口 + 判据切换（ALL_GATES 17→18）

> 承接同日「阶段 0 止血 + 阶段 1 治理库骨架」。方案见 `reports/数据流转与存储交互优化方案_20260917.md` §6。

### 一、阶段 2 · 元数据迁入（双写期：文件仍是事实源）

- **治理库新增四表**（`governance.db`，schema_version 1.0→1.1，`PRAGMA user_version=2`）：
  `document`（监管+内部统一文档主档）/ `theme_assign` / `relation` / `timeliness_history`。
  `init_db()` 升级时**只 DROP 四张投影表**（纯派生、零代价），五张观测表（run_log/watermark/
  artifact/audit_log/gate_result）承载历史**一律保留**。
- **新增 `tools/governance_sync.py`（投影器，唯一写入口）**：把归属表 / 主题表 / 内部主索引 /
  `relations_index.jsonl` / `verification_state.json` 投影进四表；`--apply` 写库、默认仅比对。
- **比对断言**：`SYNC_TABLES` 声明每表的**断言键**与模式——
  `replace`（document/theme_assign/relation）判**摘要相等**，`append`（timeliness_history）
  判**源行均在库中**（历史累积）。`cli.py governance verify` 退出码即断言结果；
  生产刷新链**新增阶段 3.5 `governance:sync`**，断言失败即计入失败步骤（不静默）。
- **投影语义 = 全量替换**（而非增量 upsert）：从根本上消除"源已删/改名而库残留"的静默分叉；
  时效表则**追加去重**（`(state_key,status,last_checked_at)` 唯一），保留历史观测。
- `row_json` 列保留原始行 → 消费方零损耗取回全部字段（避免类型往返失真）。
- **新增 `cli.py governance sync|verify|export`**；`export` 产出 `exports/`（文本快照 + manifest，
  含水位与 sha；**只导出小表**，`relation` 不导出——已有事实源文件）。`exports/` 已入 `.gitignore`
  （派生只读层；跨机审计链仍由 `reports/` 与 `docs/reports/` 承载）。

### 二、阶段 3 · DAO 收口（`modules/` 跨模块直连 **30 → 0**）

- **实装 `interfaces/clean_index_api.py`**（原四个 `NotImplementedError` 空壳）：
  唯一 `sys.path` 引导点 + `latest_csv_path/latest_jsonl_path/scan_sources/validate_files/is_fresh/…`。
- **新增 `interfaces/clause_index_api.py`**、**新增 `interfaces/timeliness_api.py`**；
  **扩写 `interfaces/rfn_api.py`**（`theme_map/get_index/registry_paths/load_attr_rows/bridge_rows/
  register_doc` 模块级便捷函数）与 `interfaces/internal_policy_api.py`（`paths()/load_index/
  load_merged_view/load_processed`）。
- **改造 22 个文件、30 处直连**：classifier（scanner / build_outputs / run_retrieval_after_checks /
  verification_state_mirror / match_theme_docs / build_detail_tables / build_upper_laws /
  reconcile_clean_drift / filter_clean_relevance / build_theme_report）、ipb（merged / align）、
  drafter（build_draft_clause_view / verify_regulatory_citations / dump_external_articles）、
  base_publish（build_external）、scrapers（supp_ingest / sync_three_modules）。
- **`interfaces/relations_api` 读取路径升级**：`load/by_src/by_dst` **优先查治理库 `relation` 表**
  （索引下推；原实现每次调用重读 7 MB JSONL 再线性过滤），JSONL 退为回退路径（库未建仍可跑）。
- **`base_api.version_chain` 下推 SQL**：发布库 `records` 新增 `docno_norm` 列 + 索引，
  由 `WHERE docno_norm=?` 直接命中（原全表 SELECT + Python 端逐行归一过滤）；
  **旧库（未重建）自动回退**旧路径，行为不变。
- **新增第 18 道门禁 `gates/gate_no_cross_module_import.py`**：静态扫描 `modules/**` 的
  ①越权 `sys.path` 引导（含经同文件变量间接注入）②跨模块裸 import ③`modules.<other>` 全路径 import；
  **基线空集且只减不增**（新增违规即 FAIL）。扫描范围**刻意只含 `modules/`**：
  `gates/`（门禁）与 `tools/`（运维/生成）是横切治理层，其职责即读取全仓事实源，不参与模块依赖图。

### 三、阶段 4 · 判据切换（水位优先，旧判据降为交叉校验）

- 新增 **`interfaces/governance_api.wm_status(artifact_key)`** —— 判据切换的**共用入口**
  （返回 ok/stale/unknown；`stale` 阻段、`ok` 可豁免 mtime 类判据、`unknown` **不得豁免**）。
- **`gate_relations` 判据 8**：水位 stale → FAIL 并直接报出**是哪条依赖边**；
  水位 ok 而 mtime 报陈旧 → **仅交叉校验告警**（`touch/copy/copy2` 误报）；水位不可用 → 退回 mtime。
- **`gate_rfn_drift`**：水位 stale → FAIL（按**内容版本**而非日期）；水位 ok → 通过；
  `unknown` → 退回 `clean_snapshots` 日期比对。
- **`gate_citations`**：水位 stale → FAIL；水位 ok → `inputs` 指纹（含 `名称|size|mtime` 近似签名）
  差异降为交叉校验；`unknown` → 退回指纹判据。

### 四、发现的既有缺陷（本次如实登记，未擅自改数据面）

- **`relations_index.jsonl` 的 `relation_id` 并非唯一**：实测 **5266 行 / 4859 个不同 id**
  （366 个 id 重复、407 行内容互不相同；其中仅 6 行为逐字节完全重复）。与
  `contract.RELATION_FIELDS` 注释"稳定去重键"的实际语义不符，属**抽取侧去重键派生问题**。
  处置：投影表主键改用**合成 `row_key` = `sha256(relation_id|row_json|序次)[:16]`**，
  **保全全部 5266 行**（库行数与事实源严格一致，消费方 `load("all")` 与 `stat.total` 不漂移），
  `relation_id` 降为普通索引列；该缺陷记为**待治理项**，需另立任务修 `relations.py` 的 id 派生。

### 五、验证

- 新增用例 **+12**（`tests/test_governance_store.py` 共 **27 例**，仍无 `data` marker）：
  投影/断言（replace 摘要相等、append 子集、缺失历史行必判分叉）、全量替换无孤儿、
  有效期表追加去重、`row_json` 保真、摘要变更检出、快照导出（路径仓库相对）、
  `relations_api` 优先查库与降级、`gate_no_cross_module_import` 全仓 PASS、`wm_status` 三态。
- **阶段 4 两项验收**（实测）：①`touch` 数据面输入 → `gate_relations` **PASS**
  （旧 mtime 判据会 FAIL，交叉校验如实记录"判为 touch 误报"）②注入陈旧声明（等价跳过
  `relations gen`）→ `gate_relations` **FAIL** 并报出 `cleaned:gov` 声明 `STALE-INJECTED-0000`
  vs 当前 `b53ec289c77708a7`；随后恢复治理库并复跑 PASS。

## [Unreleased] 2026-09-18 — 阶段 0 止血 + 阶段 1 治理库骨架（`reports/数据流转与存储交互优化方案_20260917.md` §6）

### 一、阶段 0 · 止血（5 项，均为"已定位缺陷"的定点修复）

- **`sync_status.json` 改原子写**（`modules/regulatory_classifier/rfn/registry.py::_sync_status_write`）：
  原 `open(...,"w")` 直写 774 KB 状态文件，崩溃/并发即产**截断 JSON**，而 `_sync_status_read`
  对损坏文件直接抛（`json.load` 无兜底）→ 整条 registry 链路不可用。现经
  `std_lib.common_lib.fs_lock.atomic_write_json`（tempfile + fsync + `os.replace`）。
- **`build_fts` 改"临时库 → 一次 `os.replace` 换入"**（`modules/base_publish/build_fts.py`）：
  原实现 `os.remove(db_p)` 后**原地重建**，存在两个致命窗口——①删除与重建之间任何读者
  （`interfaces/base_api`）必然 `FileNotFoundError`；②构建中途崩溃 → 现网库已删、新库未成，
  733 MB 派生索引**彻底丢失**。现改为在 `*.build` 上建库、失败即清理半成品、成功后原子换入，
  并置 **`journal_mode=WAL`**（并发读友好）。FTS 重建纳入同一失败回滚路径。
- **编排器加单实例锁**（`tools/run_production_refresh.py::main`）：此前只有各 collector 自带
  `ProcessLock`，**编排本体无锁** → 调度抖动/人工重入会让两条链同时改写 cleaned / 归属表 /
  published（无锁临界区）。现取仓根 `data/run_production_refresh.lock`，
  `max_age_sec=48h`（须大于采集阶段自身的 ~30h 量级），占用即 `rc=3` 退出。
- **处置失效 `raw_loader.py`**（`std_lib/scraper_std/raw_loader.py`）：①`RAW_FILES` 仍指向旧仓
  扁平形态（`nfra_regulations_scraper/data/raw/...`），实际已拍平到 `modules/regulatory_scrapers/
  data/raw/` → 调用即 `FileNotFoundError`；②`EXPECTED_COUNTS` 冻结 2026-08-28 基线
  （gov 预期 30271，实际 13177）→ 与 R23「数量以事实源为准，禁止文本写死」冲突。
  现修正路径并改为**与 raw 文件自带 `count`/`meta.count` 自校验**，删除冻结基线常量
  （保留同名空 dict 兼容历史 import）。该模块全仓**零消费方**，属技术债定向清理。
- **CLI 帮助文本与 handler 对齐**（`cli.py` / `commands/source.py` / `commands/internal.py`）：
  `source diff`、`internal reocr|refine-identity`、`timeliness sync|summary` 早有实现但
  `build_parser` 的 `choices` 未声明。**顺带纠正一处认知**：`build_parser()` **仅用于 `-h` 文本**，
  实际分发走 `COMMANDS` 注册表（`main()` 直接 `handler(argv[1:])`），故 choices 不一致
  **不会拒绝执行**，只让帮助文本失真——已在 `cli.py` docstring 显式记录该事实。

### 二、阶段 1 · 治理库骨架（`data/governance.db`，五张表）

- **新增 `std_lib/common_lib/governance_store.py`**（唯一读写实现）：`run_log` / `watermark` /
  `artifact` / `audit_log` / `gate_result` 五表 + WAL + 事务；路径经 `paths.DATA_DIR` 派生、
  `REG_ORCH_GOVERNANCE_DB` 可覆盖（测试隔离）；**失败不阻断主链**（旁路观测设施）。
  设计要点：`artifact.path_keys` 为**集合**（原文有跨层硬链接，同 sha 多路径须聚合为一行）。
- **新增 `interfaces/governance_api.py`**（只读消费面）+ **新增命令 `cli.py governance`**
  （`init|status|watermarks|edges|audit|artifacts|gates`）。
- **新增 `gates/gate_watermark.py`（ALL_GATES 第 17 道）**：把"产物新鲜度"从
  **mtime 比较**（`gate_relations` 判据 8：`getmtime` + 2s 容差，受 touch/copy/copy2 干扰，
  既假阳也假阴）升级为**水位版本比较**——产物落盘时登记"我在哪个上游版本上算出来的"，
  门禁比对声明版本 vs 当前版本；不等即 FAIL 并直接报出**是哪条依赖边**。
  未启用治理库 → PASS + note（不加阻断）；**新增、不替换**旧判据（阶段 4 才切换）。
- **编排器写水位**（`tools/run_production_refresh.py`）：新增 `GOV_ARTIFACTS` 声明表
  （§1.4 时序约束的**机器可读形态**）+ `_wm()`/`_wm_observations()`，每阶段 rc==0 后登记
  `clauses:<src>` / `cleaned:<src>` / `classify:products` / `relations_index` / `reconcile:drift` /
  `merged_view` / `published:external|internal` / `analysis:manifest`，并观察登记
  `rfn_attr`/`rfn_theme`/`timeliness:state`/`internal_index`/`clean_index`；
  运行台账 `run_id` 经 `REG_ORCH_RUN_ID` 下传，`cli.py gates` 据此把 17 道结果归档进 `gate_result`。
  **覆盖度取舍（刻意保守，防假阻断）**：`rfn_attr`/`rfn_theme` 仅观察不声明依赖——它们在链内
  被阶段 3 reconcile 二次改写，若在 2.5/2.6 声明版本会同一次运行内自相矛盾（属
  `gate_rfn_sync`/`gate_timeliness_ssot` 职责域）；`clauses:<src>` 不声明 cleaned 上游
  （阶段 2 时效回写会改写 cleaned，阶段 1 版本随即作废）——两条边留待阶段 4 处理。
- **新增 `tools/governance_register_artifacts.py`**：原件注册（内部 879 + 外部附件），
  以 sha 聚合、多路径并入 `path_keys`；**原件本身不入库**（BLOB 化会摧毁跨层硬链接去重与
  Word COM/PaddleOCR 抽取链）。

### 三、验证与文档

- **新增用例 +15**（`tests/test_governance_store.py`，无 `data` marker ⇒ 无数据环境可跑）：
  建库幂等 / 水位三态（ok·stale·unregistered）/ 判据阻断语义 / 未启用全链路降级 /
  审计追加与查询 / path_keys 集合语义 / `gate_watermark` 三态 / run_log 与 gate_result 幂等覆盖。
- 实测：`pytest` **327 用例**（301 代码级 + 26 `@data`）；`cli.py gates` **17/17 PASS**。
- 文档：README（门禁 16→17、命令 10→12、用例 312→327、目录树补 `data/` 与 `common_lib/list`、
  新增 §6.10 治理库用法段）、`.gitignore`（显式注明 `data/` 承载治理库且**勿白名单化**）、
  `cli.py` docstring、`gates/__init__.py`、`paths.py`（新增 `GOVERNANCE_DB` + `ensure_dirs` 纳入 DATA_DIR）。

## [Unreleased] 2026-09-14 — 交付库 sha 口径统一（文件字节）+ 关系类报告随库刷新（链 2.6 + 门禁判据 8）

### 一、交付库 `sha256_16` 口径统一为**文件字节**哈希

- `tools/gen_analysis_deliveries.py::_write`：`hashlib.sha256(content.encode())`（**正文串 LF 归一**）
  → `_sha16(落盘文件)`（**磁盘字节**）。原口径在 Windows 下与文件字节**不符**（`open(...,"w")` 把 `\n` 写成 `\r\n`），
  不能作一致性判据；现平台无关、可直接比对。dry 模式不落盘 → 置空（行为不变）。
- 实测：重新生成后 **17/17 项 manifest sha 与磁盘字节完全对齐**；新增数据级用例 `test_manifest_sha_matches_disk_bytes` 守住。

### 二、关系类报告「随库刷新」（此前只有纳管、缺刷新链与陈旧检测）

- **新增刷新链阶段 2.6 `relations:gen`**（`tools/run_production_refresh.py`）：位于阶段 1/2 写 cleaned **之后**、
  下游消费者（3 reconcile / 4.2 internal merged / 4.5 reports / 6.8 analysis）**之前**。
  动因：本链历史上不接关系重抽取 → `relations_index.jsonl` 静默过时，而消费面
  （merged 引用原语 / drafter 关系素材 / 交付库 2.1.2.4·2.1.2.5 报告）会**反映旧数据**。
  不加 `--report`：报告统一由阶段 6.8 `analysis gen` 产出（**单一写入方**）。编排 docstring 阶段表同步补全（含 16 道门禁）。
- **`gate_relations` 新增判据 8「产物新鲜度」**：关系产物**不得早于其数据面输入**
  （五源 cleaned 最新 JSONL / `internal_policy_index.json` / `processed/*_fulltext.json` / 归属表 CSV），否则 FAIL 并提示
  `cli.py relations gen`。范围纪律：只纳入**阶段 0~2 或链条外操作**推进的输入 —— 阶段 3~6 产物不写这些文件，**不会自造 FAIL**。
  已知取舍（偏严）：归属表仅"时效状态"列变化也会触发，代价一次 ~32s 重抽取。
- **实测生效**：接入后本机即检出 **6 项陈旧输入**（归属表 + 五源 cleaned）→ 重抽取后 gates 恢复 16/16 PASS。

### 三、验证与文档

- 重抽取实测：关系 **2130 条**（依据 1708 / 废止 422）；`dst_class` entity 1044 / corpus 90 / organ 376 / external 620；
  **文件级强解析率 59.5% / 定位率 64.6%**（分母 1754；nfra 1939→1934、pbc 571→539 系"无正文记录被跳过"的既有语义）。
- 交付库 2.1.2.4/2.1.2.5 已随新产物重建（图谱生成时间 → 20:14:51、定位率 64.6%），`analysis status` 17 项全在位。
- 新增用例 **+11**（字节哈希登记/dry 置空/17 项 sha 对齐/单源渲染/缺失跳过 + 门禁新鲜度 4 项）；
  `gates` **16/16 PASS**、pytest 全通过、ruff 0。
- 文档：README（编排阶段表/门禁清单/交付库 sha 口径/关系段「随库刷新」/解析率 64.7%→64.6%）、
  `commands/analysis.py`、`run_production_refresh.py` docstring。

## [Unreleased] 2026-09-14 — F-L01 追加：关系类报告纳入 analysis 交付库（15 → 17 项）

### 一、纳管（报告 `reports/依据与废止关系统一抽取_20260914.md` §10.4）

- **交付库 15 → 17 项**：`docs/reports/监管与制度依据废止关系图谱.md` 与 `RFN补登候选清单.md`
  此前**游离于交付库之外**（不随 `analysis gen` 刷新、不在 `_manifest` 口径内）→ 现登记为
  **2.1.2.4 / 2.1.2.5**（归入 2.1.2 横向整合，原 3 项 → 5 项，**未新开层级**）。
- **4 条纳管纪律**：①**复用单源渲染，禁止分叉**（抽 `extract_relations.render_report()` /
  `rfn_backlog.render_md()` 纯函数，其 CLI 改薄写盘，生成器直接调用 —— 一份渲染、三处消费）
  ②**零重抽取**（从 `relations_index.jsonl` + `relations_stat.json` 重建，`analysis gen` 全量 ~4s）
  ③**沿用既有文件名**（不改两工具 `REPORT_PATH`/`OUT_MD`，避免同名双写分叉）
  ④**事实源缺失即跳过并告警**（不写占位、不登记，防覆盖既有好报告）。
- **追加式**：既有 15 份的内联实现**零改动**（原判断"改动风险高"针对重构；实际为"新增"）。

### 二、验证

- **渲染重构行为等价**：用已落盘产物重建 vs 既有报告 → **逐字节一致**（图谱 2720 字符 / 清单 75 候选）；
  `write_report` / `write_outputs` 已改薄写盘（内容 = 纯函数输出）。
- **生成后内容完整性**：图谱 sha16 **完全不变**（`a1bdf41dc5394f69`）；清单**仅生成时间戳 1 行**变化。
- **交付库**：`analysis status` → **17 项 / 全部交付物在位**；**幂等**（连跑 2 次图谱 sha 稳定、count 稳定）。
- **回归**：`gates` **16/16 PASS**、pytest **305 passed**（+8 用例）、ruff 0；失败安全用例已验证。
- **文档同步**：README ×6、`commands/analysis.py`、`commands/classify.py`、`run_production_refresh.py` 口径 15 → 17。

### 三、遗留观察（未处置，非遗漏）

- `_manifest.json` 的 `sha256_16` 是**正文串（LF 归一）**哈希，非磁盘字节哈希（Windows 落盘为 CRLF）；
  当前**无消费方**（`analysis status` 仅在位校验）且一致适用于 17 项 → 保留既有口径并在 `_write` 注明语义；
  若未来用作一致性判据，需统一切换为文件字节哈希（会使 17 项 sha 全部变更）。

## [Unreleased] 2026-09-14 — 五源时效核验续跑（timeliness_review 家族三脚本）+ 2 处缺陷修复

### 一、续跑执行（报告 `reports/五源时效核验续跑报告_20260914.md`）

- **`verify_missing`（主体）**：mof `--retry-failed` **296 条新增 + 29 条失败重试** → 断点 401→**724 条**，
  判定 valid 103 / amended 6 / repealed 14；gov 15 / pbc 2 复核（零新查询，均 `nomatch`，0 变更）。
- **`classifier_pkulaw_verify`（修复后首次运行）**：归属表 150 待查验 → 实查 78 成功（其后触发网关限流）
  → 判定 6 条 + **归属表同步 3 条** + cleaned 4 处。
- **`authority_backfill --judge-only`（零配额补齐）**：断点 1235 条已成功结果 → **181 条权威落判**
  （nfra 167 / gov 12 / mof 2）+ **434 条核验痕**（防重复查询）。
- **落地链**：`consolidate --use-state`（3707 条）→ `apply_timeliness_to_cleaned` 五源（mof 146 + nfra 5）
  → 归属表键精确同步 18 行 → `classify --all` → `internal merged` → `gates 16/16 PASS`。
- **本轮真实时效状态变更 136 条**：mof 123（空→valid 103 / →repealed 14 / →amended 6）、
  nfra 10（**valid→repealed**）、归属表 3；`verification_state` 3262 → **3428** 条。

### 二、缺陷修复

- **`timeliness_review/verification_state.py` 缺同仓路径引导（阻塞级）**：`sys.path.insert(0, ROOT/"std_lib")`
  是**已失效旧路径**（`std_lib` 已上收仓根）→ ①`scraper_std` 导入必失败并被 `except` **静默降级**为硬编码
  状态集 ②`std_lib.common_lib.norm` 硬失败。后果：`classifier_pkulaw_verify.py` 自迁移后
  **完全无法运行**（`ModuleNotFoundError`）。修复：该文件**自带同仓引导**（不依赖调用方），
  并恢复 `STATUS_SET` 为真实枚举（含 `partially_repealed`）。
- **`authority_backfill_verify.py` 新增 `--judge-only`**：R13 降级态下不发起查询、不依赖 CLI/Token，
  仅按断点已有成功结果落判（幂等、不臆造、零配额）。

### 三、外部阻塞与剩余

- 2026-09-14 ~19:12 起北大法宝 CLI 持续 `认证失败`（阻断前已成功 400+ 次；令牌 2026-09-08 签发），
  属**外部鉴权/配额限制**，本仓无法自解；R13 降级纪律下三脚本**均未误写判定**。
- 剩余**真待续跑 2150 条**（`classifier_pkulaw_verify` 72 + `authority_backfill` 2078）；
  `verify_missing` 余 564 条属**已核验但法宝无同名命中**（设计内，非 backlog）。
- 验证：`gates 16/16`、pytest **297**、ruff 0。

## [Unreleased] 2026-09-14 — R-F01 第二批：三处旧实现收敛 + 未解析率提升（补登 RFN）

### 一、§6 三处旧实现收敛（**行为等价**，三重证据固定）

- **`build_clause_graph.py`**（P1-P5 条款级引用图）：`NUM / ART_IN / P1..P5 / SELF_WORDS` 改引
  `std_lib.common_lib.relations`；**10 个产物字节级一致**。
- **`build_detail_tables.py`**（明细表「立法依据/条款引用」列）：`ART_RE / LAW_RE` 由共享
  「书名号跨度 + 条款链 + 立法词表」构造；**11 张明细表语义级一致**（仅 `generated_at` 变）。
- **`verify_regulatory_citations.py`**（引用核验口径）：`DOCNO_CORE_PAT / TITLE_PAT / ORGAN_PREFIXES`
  迁入 `relations`（`DOCNO_CORE_PATTERN` / `quote_title_re()` / `ORGAN_WORDS`）；`--strict` 结果不变
  （R 62 / 文号 35 全命中，EXIT=0）。
- **共享口径层**（`relations` 二·B）：`CN_NUM_CHARS`/`ARTICLE_NUM`/`ARTICLE_CHAIN(_CAPTURE)`/
  `QUOTE_TITLE_MIN,MAX` + `quote_title_capture|span|re()`/`BASIS_TRIGGER_CORE` + `basis_trigger_alt()`/
  `SELF_REF_WORDS` + `SELF_REF_RE`/`BARE_ARTICLE_RE`/`LAW_SUFFIX_ALT`/`DOCNO_CORE_PATTERN` + `docno_core_re()`/
  `ORGAN_WORDS`。
- 等价性证据：①模式串逐字符比对原字面量；②同文本 `findall` 一致；③产物重跑比对。
  新增 `tests/test_relations.py::TestConvergence`（3 例）**禁止三处再各自定义口径**。

### 二、口径修正：目标性质分层（`dst_class`），真实覆盖度不再被低估

- 实测 1018 条"未解析"中**大量不是文件引用**：`国务院` 182×、`国务院银行业监督管理机构` 23×、
  `本级人民政府`/`其总公司` 等**机关名**（程序性依据目标本就是机关），以及 `条例`/`办法` 等
  **纯类型泛指词**（`《条例》` 原文即泛指）。
- 新增 `RELATION_TARGET_CLASS`（`entity`/`corpus`/`organ`/`generic`/`external`）与
  `relations.classify_target()/is_organ_target()/is_generic_target()`；关系产物增 `dst_class` 字段
  （契约 `RELATION_FIELDS` 26 → 27；`gate_relations` 校验枚举闭包）。
- **抽取侧净化**：`《条例》`/`《办法》` 等泛指词**不再产出关系**（实测过滤 46 条），
  `ExtractionResult.filtered_generic` 透出计数。
- 结果：文件级分母 2152 → **1754**；**文件级强解析率 25.7% → 59.5%**（补登后）、
  **文件级定位率 52.7% → 64.7%**；`organ` 376 条单列。

### 三、提升路径①：RFN 补登（`dst_key` 线索 → 强关联）

- **新增 `tools/rfn_backlog.py`**：从 `dst_class=corpus`（**已采集但未登记 RFN**）聚合补登候选，
  输出 `relations/rfn_backlog.csv` + `docs/reports/RFN补登候选清单.md`；`--apply` 经
  **`rfn.register_doc` 唯一写口**批量登记（幂等 + 自动备份 + 登记后 `rebuild_index`）。
- **主题建议三种依据**（禁臆造）：`law_to_t0`（法律/行政法规 → **T0 上位法锚点**，体系语义；
  此类**不**用投票——实测《商业银行法》会被引用者投成 T1）/ `votes`（引用者主题唯一最高票）/
  `uncertain`（须人工裁决）。
- **处置结果**：候选 170 → **登记 95**（失败 0）→ 归属表 1060 → **1155**；重跑 `relations gen`
  后 `entity` 552 → **1044**、`corpus` 582 → **90**、**`file_resolved_ratio` 31.5% → 59.5%**；
  候选清单收敛到 **75**（余 73 待人工裁决）。
- **⚠️ 踩坑与修复**：`register_doc` 按设计把新行时效置 `pending`，而补登文件**可能已在时效 SSOT 中**
  → `gate_timeliness_ssot` FAIL。首次调用官方 `sync_to_classifier` 修复，但其标题匹配是
  **子串包含**，在 3000+ 记录规模下**误改 1000+ 行既有状态**；改用**与门禁同口径的键匹配**
  （`state_key(发文字号, 文件名称)` 精确查 SSOT）后仅需改 6 行收敛。已封装为
  `rfn_backlog.sync_timeliness()`（`--sync-timeliness`，`--apply` 后自动执行）。
- **附带收益**：`merged_view.associated_rfns` 的 `with_rfn_refs` **321 → 336**；底座链
  （`classify --all`）与 15 项分析交付库自动重算。

### 四、验证

- `gates` **16/16 PASS**（含 `gate_timeliness_ssot` / `gate_citations` / `gate_relations` / `gate_rfn_sync`）；
  pytest **288 → 297**（+9：收敛 3 + 目标分层 4 + 补登 2）；ruff 0。

## [Unreleased] 2026-09-14 — 依据/废止关系统一抽取（R-F01，报告《依据与废止关系统一抽取》）

参照《政府文件依据关系与废止关系通用抽取器》编写**同一套**抽取能力，同时适用于监管文件与内部制度，
输出**三类关系**并接入三个消费方。

- **新增 `std_lib/common_lib/relations.py`（抽取唯一实现）**：七层结构（`RelationConfig` 词表外置 /
  工具 / 三个 dataclass / `RelationExtractor` 六类主模式 + 三类辅助 / `AliasResolver` / `RelationPipeline`
  / 自检），只依赖 `std_lib` 与 `config.enums`（不依赖业务模块）。相对参照实现的适配：
  归一收口 `norm_title_strict`/`norm_docno`、受控值改小写英文、文号形态增法规库式与半角方括号、
  **新增否定/未生效排除**（拟废止/征求意见/草案）、关系带 `offset` 溯源。
- **新增 `tools/extract_relations.py`（编排：实体解析 + 三类产物）**：读 cleaned JSONL `body_text`
  （监管权威轨）与 `processed/*_fulltext.json`（内部）→ 抽取 → 解析到 `RFN`/`IPN` → 落盘。
  产物：`relations_index.jsonl`（**唯一事实源**，一张表三类关系）、`cross_basis.jsonl`（派生视图，
  类别 3 纯依据边）、`relations_stat.json`（两级解析率 + 未解析样例）；`--report` 生成
  `docs/reports/监管与制度依据废止关系图谱.md`。**全量耗时 ~32s**。
- **三类关系实测**（用户要求明确列明）：① 监管依据/废止 **2007** ② 内部依据/废止 **19**
  ③ 内部→监管依据 **126**（纯依据边 **79**）；合计 **2152**（依据 1730 / 废止 422），
  来源 4006 份监管 + 877 份内部制度。
- **两级解析口径**（数据可信度核心）：`dst_ref` 强实体（RFN/IPN）**25.7%** / 含 `dst_key` 弱引用
  （cleaned dedup_key）**52.7%**；未定位者**保留原文 + `unresolved` + confidence 0（禁止臆造）**。
  **跨域匹配强制 `strict`**——实测 `中华人民共和国发票管理办法`（法规）会被 `title_contains`
  误配到内部制度 `…发票管理办法`。
- **程序性依据收紧**（实测反例驱动）：参照实现 `经[…]?同意/批准` 最短匹配产出
  `批准或者未按照`/`依法`/`部门负责人` 等噪声；现要求**法定机关后缀**结尾且不含连接虚词。
- **契约/枚举/门禁/API/CLI**：`interfaces.contract.RELATION_FIELDS`（26 字段，`CROSS_BASIS_FIELDS` 同构）；
  `config.enums` 新增 `RELATION_KIND`/`RELATION_DOC_KIND`/`BASIS_TYPE`/`REPEAL_ACTION`/`REPEAL_SCOPE`/
  `RELATION_MATCH_METHOD`（`assert_enum_bindings` 同步断言）；
  **新增 `gates/gate_relations.py`（ALL_GATES 第 16 道）**：键集⊇契约 + 枚举闭包 + 强引用 0 不可解析 +
  溯源非空 + 统计一致；`gate_flat_layout` 白名单加 `regulatory_classifier: {relations}`；
  新增 `interfaces/relations_api.py`（`load`/`load_cross_basis`/`by_src`/`by_dst`/`stat`）与
  `commands/relations.py`（`gen|status|show`，注册进 `cli.py COMMANDS`）。
- **消费方接入**：①`internal_policy_base/merged.py` 的引用抽取原语**收敛到 relations**
  （删本地 `_DOCNO_REF`/`_TITLE_REF`，改调 `iter_docno_signatures`/`iter_quote_titles`；**行为等价**：
  `with_rfn_refs` 321 / `aligned_ratio` 0.366 前后一致）；②`drafter/build_draft_clause_view.py`
  新增「§2 依据与废止关系」段（本制度作为源/目标，含反向"谁废止了本制度"）；
  ③classifier 侧新增关系图谱报告（`--report`）。
- **新增 `tests/test_relations.py`（26 例）**：抽取器语义（相邻书名号/位阶词连写/条款级/列表头条目/
  部分废止/否定排除/解释权排除/程序性机关后缀/附件告警/offset）+ 共享原语 + 契约与枚举一致性 +
  产物/门禁/API/两级解析口径与"未解析不臆造"。全量用例 **262 → 288**。
- **验证**：`gates` **16/16 PASS**、pytest **288 全绿**（`-m "not data"` 275）、ruff 0；
  `relations gen` 幂等（同输入同输出）。

## [Unreleased] 2026-09-13 — 第二批解析修正 3 例（含抽取链根因，报告 §13）

- **抽取链：pymupdf 提到首位（`crawler_common._extract_pdf`）**。根因：pypdf 对部分嵌入字体
  **逐 token 分行**输出（`…股份有限\n公司\n2\n022\n年\n“\n楼兰\n”`），`normalize_text._drop_junk_lines`
  的水印启发式随即把 **2 汉字短行**（`楼兰`/`公司`）当水印删除 → 正文**缺字**（留下空引号对）。
  实测 **11 份**文档受影响，pymupdf/pdfplumber 均可完整还原。新链：
  **pymupdf → pypdf → pdfplumber**，取首个 `text_layer_ok`（有效汉字 ≥30 **且** 无空引号对）者；
  三库皆不合格时取有效汉字最多者并置 `needs_ocr`。
  新增公开助手 `cjk_count()` / `text_layer_ok()` / `has_extraction_gap()`。
- **文号正则补半角方括号**：`_CONTENT_DOCNO_RE` 原括号形态缺 `[]` → `阳光人寿发[2017]2357号`、
  `阳光人寿发[2014]42号` **完全提取不到**，文号滞留在标题里（"文号重复写入名称"）。
  `normalize_docno` 另增"白名单锚点取**最后一个**"以剥"前缀＋机关全称＋再次前缀"
  （实测 `普通阳光人寿保险股份有限公司阳光人寿发[2014]42号` → `阳光人寿发〔2014〕42号`）。
- **名称截断修正**：弱关键词（`表/单/书`）后随 >8 字时**不得**作为标题结尾
  （实测 `管理类劳动合同书领用及用印管理办法` 不得截成 `…劳动合同书`）；红头正则允许前置修饰词
  （`普通…`）且 `文件` 可缺省。
- **候选择优改用包含关系**（`scan._pick_title_candidate`）：候选须为文件名词干的**子串**，取最长者；
  否则退回最短候选。**曾用 difflib 相似度，实测偏向长串**并把页眉/目录噪声并进标题
  （一次劣化 11 份，如 `首页 事项管理 …关于《…管理办法》`），已废弃该信号。
- **`indexer` 两处身份解析显式传 `fallback_title`**（摄入传文件名词干 / `refine-identity` 传现 title），
  与命名侧共用同一择优先验。
- **版本空格清洗扩面**：`（2017 修订版）` → `（2017修订版）`；`keep_variant_suffix` 增"标题已含该变体
  则不重复追加"（修 `…（2017修订版）…（2017修订版）`）。
- **`reocr` 幂等化 + `--retry`**：原逻辑对"提质尝试过但引擎不可用/文本仍不足"者**每轮重跑**
  （实测某件 cjk=60 反复重抽，永不收敛）。改为 `reocr_force_done` 即跳过；引擎升级后用
  `internal reocr --force --retry`。缺字信号只作**首次**提质触发（无 Unicode 映射的符号字形被清洗后
  **同样**留 `“”`，属原文特征，否则永不幂等）。
- **实测结果**：空引号对文档 **11 → 0**；`reocr --force` 12/12 成功（86,714 字）且复跑 `total=0`；
  三例全部修复（案例 2/3 与用户参照**逐字一致**；案例 1 名称完整 + 清单 score=1.000 兜底文号 429号）；
  索引 **878/878 可解析**、根层 879 文件、子目录 0。
- **新增 7 例回归测试**（半角方括号文号 / 弱关键词 / 词干择优 / 版本空格与变体防重复 / 抽取链顺序 /
  reocr 幂等与 retry / 缺字信号）；全量用例 **255 → 262**。
- **验证**：gates 15/15 PASS、pytest 262 全绿、ruff 0；工具与 reocr 均幂等。

## [Unreleased] 2026-09-13 — 文号/名称解析修正（用户报告 6 例，报告 §12）

用户报告 6 个文号/名称错误并给出两条解析纪律：① **文号只会在文档标题处出现**；
② **公司内部文号均以「阳光人寿」「阳光保险」开头**。

- **文号提取（`scan.py`）**：
  - 新增 `extract_docno_title_area()`：仅在**标题区域**（前 260 字）提取，并做**三重排除**——
    区域限制、引用语境（前 14 字含 根据/依据/按照/同步废止/规定…）、**未闭合括号**
    （`（保监发〔2013〕40号）` 式引用）。原实现是在 `head_chars=1500` 内**全文搜首个文号**，
    实测把正文引用与**文末废止声明**（"同步废止阳光人寿发【2021】673号文件"）当成本文文号。
  - 新增 `normalize_docno()`：剥**红头机关名**（`阳光人寿保险股份有限公司文件`）、从白名单前缀处
    截断（去"特此通知"）、括号归一 `【】[]（）→〔〕`、去"第"。
  - 新增 `is_internal_docno()` + `DOCNO_PREFIX_WHITELIST`：形态 `机关代字〔年〕序号号` **且**
    前缀属 阳光人寿/阳光保险。全库文号非白名单数 **106 → 0**。
- **名称提取（`scan.py`）**：
  - 新增 `_earliest_kw_end()`：取**最早**的"制度类关键词（＋版本括注）"结尾作为标题终点，
    修"标题与正文连写"被吞（实测 `员工周转房入住协议我同意…《周转房管理办法`、
    `保全档案归档清单阳光人寿个险保全扫描清单统计日期：…客户号`）；`的××` 接续例外
    （`…办法》的通知` 整体保留）；版本类括注可吸收、带冒号的长括注不吸收。
  - 新增 `_MAIN_TO_RE`（剥主送机关后缀，修 `…工作指引各分公司：`）、红头机关名剥离、
    标题含句读即拒（正文混入）、关键词表扩面（`协议/清单/问卷/申请表/说明书…`）。
- **附件继承（问题 6）**：新增 `inherit_docno_by_containment()`——以"有文号文档"正文拼检索串并
  记录归属区间，无文号文档取正文前 80 字检索命中即继承（对应规则"附件内容出现在正文结尾处，
  与正文文号相同"）。
- **OCR 判据修正（问题 5）**：某红头文件文字层 461 字符但**有效汉字 0**（全为 `eoa.sinosig.com`
  打印页眉），原判据 `text.strip()` 非空 → 判为"有文字层"→ **跳过 OCR** → 文号/标题漏识别。
  改：`crawler_common.cjk_count()` + `_TEXT_LAYER_MIN_CJK=30`，文本层"有效"按**汉字数**判定；
  `extract._is_scan_pdf` 由"首页总字符"→"**前 5 页有效汉字**"；`reocr --force` 目标由
  `text_chars<100` → **现有正文有效汉字 < 100**（原判据误报 77 份，含 12,482 汉字的正常文档）。
- **命名工具**：`_PREFIX_NOISE` 补 `编号N.`/`红头文件-`/`同步废止`/打印时间戳/孤立序号；
  `keep_variant_suffix()` 并回 `（A类）/（分分2024版）` 等变体后缀（同名对 17 → 12）；
  改名时**回写索引 `docno`/`title` 并同步 `processed`**（否则下次索引重建按 PROCESSED_FIELDS 静默回退）；
  `ipn` 保持**稳定代理键**不重算（防同名对撞键与全链路引用漂移）。
- **新增 `tools/prune_orphan_processed.py`**：清理不在主索引中的孤儿 processed（实测 **179 个 IPN /
  741 文件 / 12.74 MB**，会以 `missing_original` 噪声淹没 reocr 的真实缺口）→ 移入 `backups/`（可回滚）。
- **实测结果**：6 例全部修复 — ①`阳光人寿发〔2020〕404号_关于印发《…讲师技术序列…》的通知.pdf`
  ②`阳光人寿发〔2022〕338号_…银保CSP渠道线上培训平台学习管理办法.pdf`
  ③`阳光人寿发〔2022〕768号_阳光人寿融客事业部录音管理办法.doc`
  ④`阳光人寿发〔2022〕292号_经代渠道销售服务人员执业证管理工作指引.docx`
  ⑤`阳光人寿发〔2020〕199号_关于印发《…资产负债管理办法（2020修订）》等制度的通知.pdf`（OCR 后）
  ⑥附件继承生效（如 `阳光人寿发〔2021〕408号_档案借出申请表.doc`）。
  身份纠正 **705 条**；文号来源 content 173 / registry 486 / 未取得 209；名称来源 content 665 / filename 213；
  `reocr --force` 7/7 成功（9,345 字）；索引 **878/878 可解析**。
- **新增 7 例回归测试**（`TestDocnoTitleAreaRules` 等）固化上述规则；全量用例 **248 → 255**。
- **验证**：gates 15/15 PASS、pytest 255 全绿、ruff 0；工具均幂等（复跑 `需处置 0` / `孤儿 0`）。

## [Unreleased] 2026-09-13 — 内部制度「规范命名 + 归集 + 补摄取」（用户规则落地，报告 §11）

用户规则（2026-09-13）：①命名格式 `文号_名称`，无文号则 `_名称`；②文号与名称**优先从文档内容获取**，
内容未取得文号时按名称匹配 `originals/制度清单.xlsx` 兜底（名称不回退清单）；③规范化后归集至
`originals/`；④执行未闭环任务。

- **新增 `tools/normalize_internal_naming.py`**：制度正文（pdf/doc/docx）规范命名 + 归集根层。
  - 名称：内容优先（`parse_content_identity`）→ 文件名解构回退；**取完整词干**并清噪声
    （编号/附件前缀 `1-` `10：` `6人管-`、后缀 `_盖章` `-含水印` `-定稿` `(1)`、内嵌文号、
    多余空格、康熙部首异体字 `⼈⼼`）。
  - 文号：内容优先 → 清单名称匹配兜底；**两类都过形态校验** `机关代字〔年〕序号 号`
    （实测拦掉 `NEWYXYWFASQ 202503310001` 流转号、`SXLQB201912130013` 档案号）。
  - 冲突消歧 `_2/_3`；dry-run 默认；备份 + `manifest.json`（逐条 from→to + 解析来源）；**幂等**。
  - **实测结果**：878 个制度正文 → **全部规范命名并归集到根层**（文号来源：内容 284 / 清单 400 /
    未取得 194；名称来源：内容 437 / 文件名 441；同名消歧 17 → `_2`）。
- **新增 `tools/split_internal_nonpolicy.py`**：非制度正文件从原件库隔离归置 —— 台账/清单类
  （xls/xlsx）→ `data/ledgers/<部门>/`（保留部门维度）、其余（图片/压缩/数据库/html/rtf/txt、
  `~$` Office 锁文件）→ `data/misc/`；`制度清单.xlsx` 按规则保留原位；清理空目录；
  `--prune-index` 一并剔除已隔离文件的**失效索引记录**（+ 清理 state 游标）。实测：
  隔离 158 个（ledger 97 / misc 60 / temp 1）、清理空目录 131 个、剔除索引 103 条。
- **`indexer` 增强**：
  - `ingest(..., only_paths=...)` + `unindexed_originals()` + `internal index --only-unindexed`
    ——**定向补摄取**（避免全库重扫因"文件名 IPN"与"内容权威 IPN"口径差异产生重复记录）；
  - **`_AUX_CARRY_FIELDS` 防冲**：命名/对账/标记写入的辅助列（`drafting_dept`、
    `path_relocated_*`、`name_normalized_at` 等）不在 `PROCESSED_FIELDS` 白名单内，索引重建会
    静默冲掉（曾致部门信息丢失）——沿用 F-D01 主题列的防冲范式修复。
  - `unindexed_originals()` / `ingest(only_paths=)`：原件库中未被索引引用的制度正文 → 补摄取。
- **`copy_original` 增加"源即目标"保护**：就地补摄取（`--source-dir <originals>`）时原实现会
  同路径覆写抛 `SameFileError`；现检测 `samefile` 直接返回，并保留硬链接写穿防护。
- **门禁收紧**：`gate_original_resolvable.KNOWN_NON_POLICY_BASELINE` **24 → 0**
  （台账已隔离，索引内不应再有非正文件）；`gate_flat_layout` 白名单新增 `ledgers`/`misc`。
- **结果**：`internal_policy_index.json` **878 条 / 100% 可解析（0 失效）**、扩展名全为
  制度正文（pdf 645 / docx 145 / doc 88）；merged 重建 878（with_rfn_refs 317）；
  README/BENCHMARK/`data_migration_manifest.json` 数字同步（制度 957 → **878**）。
- **验证**：gates 15/15 PASS、pytest 全绿（`-m "not data"` 全绿）、ruff 0。
- **`backups/` 处置结论（报告 §11.5）**：8 个备份目录 / 1,083 文件 / **38.88 MB**，**决定保留**
  （含 490 条路径重定位、878 个文件改名、103 条索引剔除三类难逆向动作的回滚依据；源目录已不存在
  无法重放）。清理条件与命令已写入报告，**不自动删除**（删除不可逆，超出"自主提交"授权范围）。

## [Unreleased] 2026-09-13 — 内部制度原件存储治理 P1/P2（同上报告 §10）

- **新增工具 `tools/dedupe_original_storage.py`**（dry-run 默认，三类动作可单独开关，含备份与
  `manifest.json` 可回滚）：
  - ① **内部去冗余**：原件库同内容多份 → 保留被索引引用者，其余**移入**
    `backups/originals_dedupe_<ts>/`（实测移出 **97 个**；无引用组保留路径最浅者并登记为"孤儿待查"）；
  - ② **跨层硬链接**：原件库中内容在 `corpus/dept_policies` 亦有的文件替换为**硬链接**
    （`os.link` → `os.replace` 原子替换）→ 两路径均可用但共享 inode，实测 **967 个 / 释放约 291 MB**
    （同卷 NTFS 生效，跳过 0）；A 侧只读，从不写入；
  - ③ **非正文表格台账显式标记**：24 条 xls/xlsx 失效台账写
    `policy_kind="non_policy_sheet"` + `excluded_reason` + `excluded_at`（不改路径、不删记录，
    957 制度数与下游不受影响），使"排除"从隐式变为**显式可审计**。
- **`copy_original` 硬链接优先**（`internal_policy_base/extract.py`）：源与目标同卷时用 `os.link`
  避免"同批语料两处各占一份"；跨卷自动回退复制。**安全约定**：目标已存在且为硬链接时先移除目录项
  再写，绝不就地覆写（否则会写穿共享 inode 污染 corpus 侧）。
- **摄取侧台账过滤**（`internal_policy_base/scan.py`）：新增 `is_non_policy_ledger()`
  与 `scan_directory(exclude_ledgers=True)`，台账/清单类 xls/xlsx 不再纳入制度索引
  （实测索引 102 条 xls/xlsx 无一为制度正文）；开关可恢复旧行为。
- **新发现（登记，未处置）**：原件库中 **24 个 pdf/doc/docx 真实制度正文未被索引引用**
  （多为 2025 版新制度、办公室/消保部附件），属"源目录在摄取后被补充但未再跑 ingest"的
  **覆盖缺口**（非冗余）。因源目录已不存在，需另立专项（就地补摄取会改变 957 制度数，
  影响 merged/analysis/BENCHMARK，故不在本批次内擅动）。
- **测试**：`tests/test_internal_original_paths.py` 增至 16 例（新增硬链接语义/写穿防护/台账过滤/
  去冗余分类与执行）；全量用例 236 → **242**。
- 验证：gates 15/15 PASS、pytest 242 全绿（`-m "not data"` 229 全绿）、ruff 0；
  `internal merged` 重建（index 变更触发 gate_citations 阻断 → 按提示重建）。

## [Unreleased] 2026-09-13 — 内部制度原件路径治理 P0（`reports/内部制度原件双份存储与索引漂移分析_20260913.md`）

- **修复（P0-A）ingest 幂等键加 `path_key` 维度**（`internal_policy_base/indexer.py`）：
  原幂等键仅内容 `sha256` + `prev.ipn == f.ipn`，同内容**换路径**（源目录重组/改名）被判
  "已摄入"而静默 skip → 索引 `relative_path` 停留在旧布局。现：内容 + 落位路径**双维**判定；
  路径变化走**「路径跟随」= 移动既有原件**（`_follow_path`，而非再复制一份），
  并同步 `processed` 路径字段与 `state.path_key`；`state` schema 升至 2.0（键空间不变，向后兼容）。
- **修复（P0-B）`internal reocr` 不再静默跳过缺原件**（`internal_policy_base/extract.py`）：
  原件不可解析者计入 `stats["missing_original"]` + `missing_details` 并**打印告警与处置入口**
  （原 `continue` 无计数无告警，实测覆盖率曾仅 443/957=46%）。新增可注入 `data_dir` 便于单测。
- **新增（P0-C）门禁 `gate_original_resolvable`**（ALL_GATES 14→15 道）：
  逐条校验 `internal_policy_index.json` 的 `relative_path` 在 `originals/` 可解析——
  制度正文类（pdf/doc/docx）**100%**；非正文表格类（xls/xlsx）失效台账须 ≤ **登记基线 24**
  （只减不增）；索引缺失 → FAIL（对齐 A-07，不静默放行）。
- **新增工具 `tools/reconcile_original_paths.py`**：按**内容 sha256** 对账重定位索引路径
  （`--apply` 前自动备份 index/state/受影响 processed；每条写 `path_relocated_from` 可回滚）。
  首次执行：**重定位 490 条**，可解析率 443/957 → **933/957**（余 24 条为失效表格台账）。
- **数据修复**：`internal_policy_index.json` + 490 个 `processed/*.json` 路径字段修正；
  `_ingest_state.json` 迁移至 v2（966 条含 `path_key`）；`internal merged` 重建（输入签名变化触发）。
- **测试**：新增 `tests/test_internal_original_paths.py`（10 例，覆盖三项 P0）；用例 224 → **236**。

## [Unreleased] 2026-09-13 — 克隆可移植性修复（`reports/克隆可移植性检视报告_20260913.md`）

### 修复（阻断级）
- **clean_index 索引出库 + 加载自愈**：`index.json` 原入库且内嵌本机绝对路径（31 处），
  使克隆副本在同机环境下读到**原仓数据**，把"无数据"伪造成 `timeliness verify` 的 `overall=success`。
  现：加入 `.gitignore` + `_index_is_usable()` 归属/存活性校验，不通过即自动重建（记 warning）。
- **盘符门禁扩面**：`gate_hardcoded_paths` 由"仅 `*.py`"扩至 `*.json/.yaml/.yml/.toml/.cfg/.ini/.md/.txt/.mermaid`，
  并输出 `py_count` / `non_py_count`；派生产物（clean_index 索引、recall output）与构建产物（build/dist/*.egg-info）显式排除。
- **数据清单可移植化**：`data_migration_manifest.json` 由绝对路径（97 处、登记过期 0907 快照）改为
  **相对仓库根路径 + 现行快照 sha256**（schema 1.1，80 条，覆盖 scrapers/classifier/drafter/base）。

### 修复（高）
- **依赖归属**：`pypdf` / `pdfplumber`（PDF **文本层**解析，非 OCR）由 `[ocr]` 上移 base；
  新增 `chardet`；`PyMuPDF` 下界 `>=1.24` → `>=1.24.3`（代码用 `import pymupdf`，该名自 1.24.3 起提供）。
- **删除死声明**：`cryptography`（零引用）、`mcp` extra（零 `import mcp`；真实依赖是 Node 侧 `@pkulaw/mcp-cli`）。
- **打包修复**：`packages` 改 `find`；`config/*.yaml` + `schema/*.json` 补 package-data（原 wheel 缺失，非可编辑安装必 FileNotFoundError）；
  `cli.py` 新增源码树校验——缺 `modules/` 时以 rc=4 显式拒绝并给出运行方式指引。
- **用例分层**：新增 `data` 标记（13 项数据依赖用例），无数据环境可 `pytest tests -m "not data"`（211 项）。

### 修复（中/低）
- README/CHANGELOG/clean_index README 中的本机绝对路径改为占位符；README 新增 §6.0「异机部署先决条件」。
- `.codebuddy/`（含本机路径与过时门禁数量的本地记忆）移出版本控制；`.gitignore` 同步。
- 数字校准：pytest 172 → **224**；README 维护约定 gates 13 → **14**。


本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 结构与语义化版本。
历史以 git log 为底稿归组；重大批次回链 `reports/` 专项报告。

## [Unreleased]

## [2.1.0] - 2026-09-12

### 新增（F-L01 分析交付库）
- 规划 §2.1 五级分析 **15 项交付生成器** `tools/gen_analysis_deliveries.py` + `cli.py analysis gen|status`；交付库 `docs/reports/`（`_manifest.json` sha 登记）。
- **数据重建后自动刷新**：`cli classify` 成功尾部自动 gen（`--no-analysis` 逃生）；编排新增阶段 6.8。

### 新增（其余 F 系列批次，回链报告）
- F-D14 状态文件版本锚点（4 类 `_meta`，副本写入）；F-D07 附件 7 字段契约 / F-D09 双轨权威声明；F-D06 五源字段别名契约化。
- F-O04 nfra 周度增量链入编排；F-O02 `source diff` 变更监听（watch_baseline）；F-O08 match 轻量偏移索引（内存数百 MB→MB 级，T1 回归 193/193 全同）。
- F-L02 条款对照素材 956/957 覆盖；F-L03 明细表 14 列（时效状态/核验来源联入分析层）；F-L04 `external_relations.jsonl`（1508 边）；F-L06 `--order date` 时间线；F-L08 vault 导出截断声明。
- **OCR 通路**：PaddleOCR 3.7.0 + Tesseract 5.4 双引擎、`cli internal reocr` 质量闸门、PDF 零文本清零（702 PDF：654 文本层 + 48 Paddle）。
- **部门制度入 IPB**：摄取 937 文件 → 制度 **107 → 957**（同 IPN 多版本去重透明）；merged 957 / clauses 11626。
- **知识库联动**：Obsidian vault 同步（`<Obsidian vault>`，4985 篇）+ llm_wiki v0.6.11 接入契约 + 主题 MOC。
- **EAST 报送文档排除**（六目录 1677 文件剔除，按指示不纳入）。

### 变更
- README **v2.1.0**（依撰写规范刷新：门禁 13 / 用例 68 / 制度 957 / 交付 15）。
- 门禁 **12 → 14 道**：+`gate_field_aliases`（中文列受控注册）、+`gate_secret_scan`（密钥扫描）。
- `run_production_refresh`：+阶段 6.5 变更监听基线、+阶段 6.8 分析交付库。

### 修复
- F-D14 `_meta` 就地注入污染调用方（indexer 汇总 KeyError 实证）→ 五处状态文件改副本写入。
- 同 IPN 多 sha 三层去重口径统一（index/merged/policies）；发布件 `INSERT OR REPLACE` 防御。
- clause_graph `dst_theme='1'` vs `theme='T1'` 前缀归一（内部边原判=0 误判）。
- `subprocess` 8 处补 `timeout=`（防挂起，审查 P2-5）。

### 工程（审查行动 P1/P2，2026-09-12）
- 依赖声明补全（lxml/numpy/pytesseract/openpyxl/xlrd/python-docx）+ ocr extra 版本区间。
- 单测三组（IPB 纯函数 12 / gen_analysis 10 / cli 门面 9）→ 用例 32 → **68**。
- `tools/ci_check.py`（ruff+pytest+gates 一键本地 CI）。

### 文档
- 新增/更新：`reports/项目全面审查报告_20260912.md`、`F-L01_五级分析交付库专项报告_20260912.md`、`运行手册_编排与定时_20260912.md`、各批次遗留项报告。

## [0.1.0] - 2026-09-08

### 新增
- 四仓合一：本仓为唯一演进点（原四仓只读冻结；P0 骨架 → P1–P7 迁移，来源映射见 README §7）。
- 五源采集清洗（39 列契约）、RFN 分类底座链（base→cluster→match→detail→upper）、时效核验（北大法宝 R13 三态）、内部制度摄取/对齐、起草对照、12 道交付门禁、pytest 20 用例、`data_migration_manifest.json` 数据复制追踪。

[Unreleased]: 见 git log
[2.1.0]: 对比 0.1.0 — 见 reports/ 批次报告（F 系列遗留项全落地 + 工程化 P1/P2）
[0.1.0]: P0 骨架初始（2026-09-08）
