# 技术债台账（§13.7，P2）

> 依据：《项目最优重构方案》§13.7。**唯一台账**：本文件。
> 编号纪律：代码债沿用 §7 的 A–E 类与 B1–B9/C1–C8 编号（不重编号，避免两套编号并存）；
> 架构债 `AD-*`、文档债 `DD-*`、测试债 `TD-*` 在本文件新增。

## 一、偿还优先级规则（三轴打分）

`分值 = 影响面（消费方数量）× 风险（数据错误/安全/性能取高）× 耦合度（跨模块数）`

| 轴 | 1 分 | 3 分 | 5 分 |
|---|---|---|---|
| 影响面 | 单点自用 | 2–3 个消费方 | 全链/门禁/发布件 |
| 风险 | 可观测性缺口 | 偶发数据不一致 | **数据错误/安全/合规风险** |
| 耦合度 | 单模块内 | 跨 2 模块 | 跨层（modules↔std_lib↔tools） |

高分先进波次；§7.7 的波次整合表即首批执行依据。

## 二、债务预算（硬约束）

**每个 Wave ≤ 15% 容量用于债务偿还**，超出部分转登记到下一 Wave。
理由：防止"重构变重写"——债务台账若无限膨胀，会挤占功能与合规交付的容量。
W5 自身（约 8 个批次）即该预算的**首次实践**：W0–W4 的 A–E 类清理属**计划内重构**，
不计入此预算；预算专指计划外发现的技术债。

## 三、代码债（§7 系列，已在方案内排期）

| 编号 | 内容 | 状态 | 证据 |
|---|---|---|---|
| A 类 | 零引用脚本（21 个） | ✅ 已清零（第七十批） | `modules/_manifest.json` + 判据 J9 |
| B1–B7 | 原语重复实现（HTTP/文本/日期/哈希/XML…） | ✅ 已收敛（第六十九批等） | `gate_no_duplicate_libs` + SSOT 标注 |
| B8–B9 | 私有名重复模式 | ✅ 已清零（判据 D） | 同上门禁 |
| C1–C8 | 幽灵注册点 / 死声明 / 注释漂移 | ✅ 全关（§7.8 验收） | `gate_config_integrity` 判据族 |
| D 类 | >1200 行文件（4 个） | ✅ 已清零 | `gate_config_integrity` 行数判据 |
| E 类 | 命名与结构不统一 | ✅ 目录 SSOT + collectors 分层 | paths 八常量 + L1 重组 |

**遗留登记（未偿还，故意保留）**

| 编号 | 内容 | 为什么不偿还 | 复发/重议条件 |
|---|---|---|---|
| D-遗留 | 10 个 >500 行的采集脚本（gov/parse 839、gov/collector 825、gov/zhengceku 801、pbc/collector 723、nfra/collector 716、nfra/fetch_attachments 700、mof/collector 658、mof/attachments 641、supp/ingest 603、nfra/attachments_extract 518） | 拆分触及**生产抓取逻辑**，无真实数据环境难以回归验证；风险 > 收益 | 拿到可回归的采集测试夹具（录制-重放）后按层拆分 |
| E-遗留 | `paths.py` 常量返回 `str` 而非 `pathlib.Path` | **破坏面变更**：全部消费方（含生成器）需同步；`versioning.md` §六已登记为 major 待办 | 人工确认后排独立 Wave（走 major + 消费方盘点） |

## 四、架构债（AD）

| 编号 | 内容 | 影响面 | 风险 | 耦合 | 分 | 处置 |
|---|---|---|---|---|---|---|
| AD-1 | 单机脚本链无并发（MCP 并发消费方 <3） | 链 | 性能（可接受） | 1 | 3 | **有意设计**（确定性优先），写死理由（§13.12） |
| AD-2 | 治理库与产物分离（治理库失败降级为告警） | 全链 | 数据（元数据缺失） | 3 | 9 | 有意设计（元数据不得阻断主链），有降级披露 |
| AD-3 | 五源采集器历史层（`std_lib/scraper_std`）体量大、风格不统一 | 采集链 | 维护性 | 2 | 6 | 随接触迁移（boy scout）；per-file-ignores 已隔离风格债 |

## 五、文档债（DD）

| 编号 | 内容 | 影响面 | 风险 | 耦合 | 分 | 处置 |
|---|---|---|---|---|---|---|
| DD-1 | 部分模块 docstring 未说"为什么"（只说"是什么"） | 维护 | 维护性 | 1 | 3 | 随接触补（§13.11 的 docstring 规则已采纳 D 规则子集） |
| DD-2 | 历史批次报告 62 份（`reports/refactors/`）篇幅大、检索成本高 | 审计追溯 | 无 | 1 | 1 | 已分区（refactors/manuals/scratch）+ `reports/README.md` 口径 |
| DD-3 | 生成物与源码的同步依赖人工记得跑（`--check` 已入 CI） | 文档漂移 | 误导读者 | 2 | 6 | ✅ 已解决（flowmap/schedule-doc/lock/sbom 的 `--check` 均入 CI） |

## 六、测试债（TD）

| 编号 | 内容 | 影响面 | 风险 | 耦合 | 分 | 处置 |
|---|---|---|---|---|---|---|
| TD-1 | ~~采集器无录制-重放夹具~~ ⇒ **已处置**（2026-10-15 第七十六批） | 采集链 | 回归风险 | 0 | — | 夹具层 `tests/http_cassette.py` + `tests/cassettes/`（4 合成夹具）+ 17 项回放用例 + 判据 **J13**；**D-遗留（>500 行拆分）的前置阻塞已解除** |
| ~~D-遗留~~ | >500 行采集文件按层拆分 | 采集链 | 可维护性 | 1 | 4 | **第一批 + 第二批完成**（第一批 2026-10-15 第七十七批：pbc 729→541，拆出 constants/listing/detail，夹具回放 + 重构前后等价校验双证；**第二批 2026-10-16 第八十二批：gov/parse.py 843→210，拆出 scrape_config/extract/outputs**；**第三批 2026-10-16 第八十五批：gov/zhengceku.py 802→454，拆出 zhengceku_list/detail/attach**；**第四批 2026-10-16 第八十六批：nfra/collector.py 716→543，拆出 extract/http_api**（解析层零外部依赖⇒ 走纯函数回归 + 重构前后比对双证，17 项新建测试；过程抓出一个**静默给错结果**的真实回归：lxml 未随迁致 html_to_text 退化）（三层夹具回放 28 项 + 重构前后逐字段等价双证，J11 基线 13→11），前置补齐 zhengceku 夹具（第81 批，顺带修复 gov 中文名附件下载必崩缺陷）；剩余 gov/collector 841、nfra/fetch_attachments 710、夹具回放 + 重构前后等价校验双证）；剩余 gov/parse 843、gov/collector 841、gov/zhengceku 802、nfra/collector 716、nfra/fetch_attachments 710mof/collector 664、mof/attachments 641、supp/ingest 603、nfra/attachments_extract 518（**须先补对应夹具**再拆） |
| TD-2 | 待办积压 641 条 open、处置率 2.1% | 治理 | 治理效率 | 638 | 3 | **分批裁决机制已落地**（2026-10-16 第七十九批：`tools/worklist_triage.py` 按裁决动作类型切 8 批、覆盖 641/641；已执行 B2 三条并落审计；其余 4 份裁决包在 `reports/worklist_batches/`）；**剩余 638 条需业务判断**（B4 主题不确定 324 条实测无法机器判定：标题在归属表命中 0）⇒ 需人工裁决或先定「是否纳入 RFN 体系」 |
| TD-3 | 覆盖率门槛 20%（现状基线，非高覆盖） | 全仓 | 漏测 | 1 | 3 | 只升不降；新增代码应带测试（boy scout） |
| TD-4 | 变异测试未做（全仓） | gates | 断言强度未知 | 1 | 2 | 有意不做（§13.12）；以判据负例单测间接保证 |
| TD-5 | dry-run 演练写入生产台账 ⇒ SLI/统计被非生产流量稀释 | 可观测性 | **指标失真** | 中 | 中 | **2026-10-16 实测**：dry-run 占 run_step 行数 **65.4%**（3449/5270）、run 数 **74.5%**（105/141），每次 `cli.py gates` 产生 1 个 dry-run。**已修**：`sli_chain` 排除 dry-run（守卫 5 项，判据= `is_dry_run` 列 OR note 文本兜底）；**根治待决**：① dry-run 不写台账（须先确认链等价 golden 是否依赖它）② 或把 dry-run 标记列化（已落地为 `is_dry_run`）|
| TD-6 | ~~`StepSpec.depends_on` 声明稀疏~~ ⇒ **已处置**（2026-10-19 第一百批） | 链声明面 | 可观测性 | 0 | — | **补至 21/32**（原 2/32），全部据 purpose/produces 的**事实关系**逐条取证，未声明者 11 步写入守卫的 `_NO_UPSTREAM` 表逐个注明理由（采集源/人工投放源/只读披露/链尾门禁）。**过程中 R4-c 报出真实建模缺陷**：`cleaned:{src}` 被 `clean:{src}` 与 `apply:{src}` **双重 produces** ⇒ 一旦 `apply` 声明任何上游（时效台账→consolidate→state→verify→cleaned）就必然成环。**修复**：`cleaned:{src}` 从 `apply` 的 produces 移入 depends_on（语义更准：消费+就地回写；产物主要生产者唯一），**行为影响为零**（resume 读 watermark 的 produced_by，不读本声明）。顺带解决「语义步骤未声明 inputs」那条决策（`semantic:assist` 现 depends_on `classify:products`）。守卫改为「覆盖率只升不降 + 未声明集合必须等于有依据的无上游表 + 有声明者必须能被 topology_api 反查」 |
| TD-7 | ~~rfn 主题不确定剩 123 条规章本体待业务判断~~ ⇒ **已由业务决策闭环**（2026-10-19 第九十九批） | 治理 | 治理效率 | 0 | — | **业务决策**（用户 2026-10-19）：「324 条中仅《关于加强外资保险公司与关联企业从事再保险交易信息披露工作的通知》进入归属表，其余均不纳入」。**已执行**：① 该通知经 `rfn_api.register_doc` 幂等登记为 **RFN-d6063763945de936 / 主题 T4**（文号 保监发〔2006〕116号，来源 nfra，`pub_date` 源为 None 故诚实留空）；② 保留项待办 → `resolved`（附 RFN 与主题）；③ 10-19 新产生的 107 条 → `dismissed`；④ **判据停止产生**：`tools/rfn_backlog.py` 加 `_UN_CERTAIN_SUSPENDED` 闸门（该类已无处置对象 ⇒ 再登记只会每天新增一批、每天被 dismiss）。恢复方式写在代码内（置回 `False`）。**注意**：闸门是**业务口径**而非技术判据——若将来业务指定新的"应入归属表"文件，先办结该文件，闸门无需打开 |
| TD-8 | 清洗隔离区 4 条**源侧无正文**（`body_empty_no_source`） | 采集链 | 交付覆盖率 | 4 | 1 | **2026-10-19 实测**（第九十九批）：`nfra_cleaned_*.quarantine.jsonl` 每日固定 5 条、**连续 9 天同一批**（10-09→10-19），`validation_errors` 全部为 `body_text: required but null/empty`。其中 **1 条已修**（正文空但附件 8727 字 ⇒ 加"三重判据受控回填"，见 CHANGELOG 第九十九批⑤）；**另 4 条 `attachment_content` 也是 0 字** ⇒ 源侧就不提供正文（页面疑似纯索引页/失效页），**不可修**。处置选择：隔离机制**正常工作**（4 条不在 clean 主产物，未污染交付）⇒ 暂按"可接受的数据缺口"处理；若要提升覆盖率须**源侧补抓**（重爬/换来源）或**评估剔除**，属采集策略决策。`tools/quarantine_triage.py` 已能分类并给出处置建议（`body_empty_no_source` → 源侧补抓或评估剔除） |
| TD-10 | D-遗留 **4 个大文件**拆分（`gov/collector` 841 / `mof/collector` 664 / `mof/attachments` 668 / `supp/ingest` 603） | 采集链 | 可维护性 | 2 | 2 | **进度**（2026-10-20 第一百零三批）：`mof/attachments` **668→535**（拆出 `att_constants` 169 + `att_extract` 206，含等价性双证与 11 项守卫）；`supp/ingest` **603→565**（常量/引导下沉 `supp_constants` 52，含 11 项守卫）。**剩余**：① `mof/collector` 664（`split_precheck`：1 class + 13 函数，但 3 个 try-import 别名需每层兜底）；② **`gov/collector` 841 风险最高**（**6 处** try-import 别名 + **2 类咬合** `RobustSession`↔`XzfgkScraper` + 4 处模块级引导 `_GUIDE_ROOT`/`LOG`/`_STD_LIB_ROOT`/`_SCRAPERS_ROOT`）⇒ 应单独一批、按 mof 的三层模式做，且**先补夹具再动** |
| TD-9 | 剩余 open 待办 **311 条**（`relevance_boundary` 211 / `internal_unaligned` 66 / `trigger_manual_breakpoint` 17 / `rfn_clean_drift_c2` 12 / `timeliness_conflict` 3 / `clean_quarantine_triage` 2） | 治理 | 治理效率 | 311 | 2 | **2026-10-19 实测**（第九十九批）：处置率已由 2.58% 升至 **52.58%**（658 条中已处置 347）。① `relevance_boundary` 211 + `internal_unaligned` 66 ⇒ **业务判断**（前者是相关性边界，后者是内部制度未对齐），展示层已修好（每条列示名称/文号/访问地址，见 CHANGELOG 第九十九批②），可在 P4 按 kind 筛选后批量处置；② `trigger_manual_breakpoint` 17 ⇒ 历史技术决策待办（payload 多含"待裁决"）；③ `rfn_clean_drift_c2` 12 ⇒ **RFN 归属表与 clean 的标题/文号漂移**（`matched_by` 有 `docno` 与 `bridge_url` 两类），需逐条核实哪侧为准，**属数据一致性核查**而非批量裁决；④ `timeliness_conflict` 3 ⇒ 同一键 ≥2 个冲突 verdict，需业务定夺；⑤ `clean_quarantine_triage` 2 ⇒ 见 TD-8（信息性汇总项） |

| TD-11 | ~~`collect:mof` 4h 上限用尽仍跑不完~~ ⇒ **已处置**（2026-10-20 第一百零二批） | 采集链 | 每日链可信度 | 0 | — | **根因**（实测推翻上批推断）：`download_attachment` 的**网络异常分支（URLError/TimeoutError）不计入熔断** ⇒ 附件主机超时时每附件走满 6 次退避约 150s 且**永不终止**；502 分支本就有熔断，但两者分开计 ⇒ 任一单独出现都熔不断。**五层修复**（逻辑抽至 `mof/limits.py`，纯逻辑可单测）：① `--max-seconds` 软上限（对齐 gov 既有做法，mof 是唯一缺的；调度已配 7200s）；② 网络异常与 502 **共用熔断计数** + 成功清零；③ 早停**跳过 removed 判定**（「没抓到」不等于「已删除」，实测误判 780/870 条已从备份恢复）+ 兜底补回未处理历史；④ `--no-attachments` **沿用历史 attachments**（传 `[]` 会清空 4 个字段，实测 63.6→36.4MB）；⑤ 详情未拿到或新正文短于历史 20% ⇒ 判「疑似源站截断」并沿用历史，回退计数 `content_text_reverted`（不静默）。`collector.py` 822→785 行（J11 内）。**残留**：`--outdir` 默认值解析到 `collectors/data/raw`（与主库不同）⇒ 裸跑会静默写错位置，已记录未改（影响所有调用方，需单独评估）|

| TD-12 | ~~`REPO_ROOT`/`--outdir` 只上溯一级 ⇒ 写错位置~~ ⇒ **已处置**（2026-10-20 第一百零三批） | 采集链 | 交付位置正确性 | 0 | — | **同类 bug 共 4 处**（全部同一根因：采集器在 `collectors/<src>/` 两级，而 `dirname` 只上溯一级 ⇒ 得到 `collectors`，注释却写 `regulatory_scrapers`）：① mof 的 `--outdir` 默认值（裸跑静默写错位置、白跑不报错）；② mof/attachments 的 `REPO_ROOT`（`local_path` 变 `../../data/…` 脏路径）；③ pbc 的 `REPO_ROOT`（实测 13 条 local_path 里 **10 条已脏**）；④ supp 的 `SCRAPERS_ROOT`（`SRC_DIR`/`RAW_OUT`/`STATE_DIR` 全错位 ⇒ 采集白跑）。**关键区分**：`outdir` 基准 = `regulatory_scrapers/data/raw`（上溯两级）；`local_path` 基准 = **仓根**（实测 mof 246 条以 `paths.ROOT` 拼接后文件真实存在）——两者**不能共用一个常量**。历史脏数据由新工具 `tools/migrate_local_path.py` 迁移（只改一字段、只替换已知脏前缀、逐条验证文件可达、备份后原子替换、默认 dry-run），pbc 10 条已迁完 |

## 七、复核节奏

- **每 Wave 出口**：更新本文件（新增债登记、已偿标记闭环）。
- **每季度**：三轴重评（分值随架构变化漂移），高分项进下 Wave 计划。
- **触发式**：出现生产事故或数据错误时，**必须**回看本文件并新增对应条目（含复发条件）。
