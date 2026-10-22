# Changelog

## [Unreleased] 2026-10-16 — 工作台三缺陷修复 + rfn 判据从源头减产（第九十批）

> 用户报告三处确认台缺陷并决策「rfn 人工处置项均不入 rfn，改判据从源头减产」。
> 三处缺陷**全部是第七十五批我自己埋的**，本批逐一定位根因并修。

### 三处缺陷的根因（实测定位，非猜测）

| 现象 | 根因 | 修法 |
|---|---|---|
| **P1 数据流转未呈现** | `flow_graph` 的边权读 `run_step` 的 `raw_size`/`record_count`/`output_count` —— 而该表**根本没有这些列**（实测列：run_id/step/rc/exit_code/elapsed_s/skipped/note/recorded_at/started_at/finished_at/is_dry_run）⇒ `weights` 恒空 ⇒ **一条边都不画** | 改用**产物侧权威** `interfaces/clean_index_api.record_count(src)`（ADR-0014 的 C3 决策本就写明"产物侧才是权威"）；实测 weights 由 0 → **15 条**（`collect:gov`=13178 等） |
| **P1 点击无反应** | SVG 节点**没有任何事件绑定**（前端只给 `#stepbtn` 下拉绑定了抽屉） | 节点 `<rect>` 加 `class="fnode" data-step=…`；新增 `openStep()` 复用既有抽屉；`render()` 绑定 `rect.fnode` 的 click |
| **P3 指标渲染异常** | `barChart` 按**位置**取值（`Object.values(data)[i]`），而 JSON 对象键序**无语义保证**（实测 `worklist_aging` 键序是字典序 `0-3,15+,4-7,8-14`，labels 是语义序 `0-3,4-7,8-14,15+`）⇒ **两处图表标签与数值整片错位**（时效三态曾把 valid 显示成 pending 的值、uncertain 显示成 valid 的值） | 改为**按 label 取值** `data[lb]`——不依赖键序 |
| **P4 列表仅 200 条** | `worklist_items(limit=200)` 硬编码，而真实 open **641** 条 | `limit` 默认 0（全部）+ `offset` 分页 + 回填 `total`；实测 count=641/total=641，`?limit=50&offset=100` 可用 |
| **P4 无多选批量** | 每行只有一个「处置」按钮 | 表头全选 checkbox + 行内 checkbox + 计数 + 批量处置条（**理由必填**，进审计台账）；批量**逐条 POST** 唯一写路径 ⇒ 每条各自落审计（判据 C1），单条失败不阻断其余 |

### rfn 判据从源头减产（用户决策：这些文件均不入 RFN）

**取证**（324 条全量实测）：
- `_law_shape` 正则只认「中华人民共和国…法/条例/细则」「最高人民法院关于…」「…条例$」三种形态
  ⇒ **命中率 0/324**（样本是《公司债券发行与交易管理办法》《…办法》废止、
  `关于废止&lt;…&gt;的决定`——带书名号、含 HTML 实体、尾随「废止」）；
- 324 条 payload 的 `reason` **完全相同**（"无票 / 票数并列 / 形态不明"）⇒ 成因不可区分。

**减产方案**（判据**前置过滤**，不是 dismiss 存量）：
1. **形态清洗** `clean_shape()`：解码 HTML 实体、剥离书名号、剥离「废止/修改/修订/施行」尾缀；
2. **不纳入 RFN 形态** `excluded_shape()`：废止/修改/决定/征求意见/通告/公告/复函/批复/
   **通知/适用意见** 等**过程性文件**——不是现行有效的监管文件本体，入体系会污染主题分布；
3. **判定顺序修正**：`excluded_shape` **先用原标题判"已废止/失效"**再清洗——
   否则 `《X办法》废止` 会在清洗时被剥掉尾缀，**丢失"已废止"这一关键信息**。

**实测减产**：**324 → 123 条（减产 62%）**。剩余 123 条为**规章本体**
（如《公司债券发行与交易管理办法》）——它们**确实是现行有效的监管文件**，
是否纳入 RFN 属**业务判断**，本批不擅自排除（已登记 TD-7）。

### 过程中的两次事故（记录以自省）

① 按行号批量替换 `web/api.py` 时**多切 2 行**，破坏 `handle_request` 定义 ⇒ 服务起不来；
② `fix_console_fe1.py` 因末尾锚点失败抛异常 ⇒ **整个文件未写入**，导致 barChart 与
P1 节点的修复"看起来成功、实际没生效"。
**两者都是批量改写脚本的固有风险**（记忆里的铁律第三次翻车）——
现象都是"不报错、看起来做了、其实没做"。

### 守卫
`tests/test_confirm_console_fixes.py`（9 项）：边权走产物侧 + **不得再读不存在的列** +
**实测 weights 非空**、barChart 按 label 取值、limit 默认 0 + offset + total、
多选/批量/理由必填、P1 节点可点击、P4 显示真实总数。
其中"不得再读 `raw_size`"这条用 **tokenize 剥离注释与 docstring** 后再匹配——
说明性文本里引用旧写法是正常解释，不能被当成"仍在用旧写法"。

### 验证
30 道门禁 PASS、ruff 0、mypy 0；确认台契约 26 项 + 新增守卫 9 项全绿。

## [Unreleased] 2026-10-16 — 诊断第 2 波：步级失败取证 `tools/step_triage.py`（第八十九批）

> 承接第 1 波（门禁归因）。本波解决"某一步失败了，怎么快速定位"。

### 新增 `interfaces/topology_api.py`（只读门面，零写入）
**解决五阶段审查的 B1 阻断级陷阱**：链拓扑是**双层标识**——
声明面 `STEP_ORDER` 32 项含 4 个 `{src}` 模板，台账 `run_step.step` 48 个**互异值全是展开形态**
（无模板形态）。若照字面实现"步骤名有来源、查不到即拒绝"，会在**每个真实失败场景**下自我拒绝
（`clean:nfra` 在声明面不存在，只有 `clean:{src}`）。
门面的职责是把两层**显式接上**，并如实返回 `matched_as`（`instance`/`spec`/`both`），
不让调用方误以为二者同形。另提供 `neighbors` / `upstream_chain` / `affected_nodes` /
`Topology.coverage`。

### 新增 `tools/step_triage.py`（J1 登记）
```
python -m tools.step_triage --list-failures      # 近 N 天失败步骤概览
python -m tools.step_triage <step>                # 取证某步（最近一次失败）
python -m tools.step_triage <step> --run-id RUN-xxx --json
```
取证包七项（每项标注是否取到）：拓扑双层匹配 / `run_step` 执行事实 / `note` **原文** /
步骤日志 tail（经统一取证入口）/ **产出物水位** / 耗时基线对照 / 拓扑邻接。

### 关键取舍：**不做自动根因断言**
原方案要"沿链自动归因 + 置信度"。本仓**无记录数列**（`run_step` 无 `record_count`），
"穿透深度"无法量化 ⇒ 自动断言会产出**看似精确实则臆测**的结论。
本波只做**证据聚合 + 怀疑排序**，每条怀疑**必须挂依据**，并**显式列出证据缺口**
（缺口非空 ⇒ 结论须标「待人工确认」）。沿用 `argument-strength-evaluation` 的纪律。

### 实测输出（真实失败 `collect:mof`）
```
拓扑：声明名 `collect:{src}`（匹配=instance，层=L1_ingest，kind=main，失败语义=abort）
失败：run_id=… exit=TIMEOUT at=2026-10-14T05:00:17
取证（7 项）：拓扑匹配 / rc=-1 exit=TIMEOUT耗时=14403.5s / note 原文 /
             日志尾部 / 产出物水位 raw:mof@29ee8c5f / 耗时基线对照 / 拓扑邻接
怀疑排序：①撞硬超时被强杀（依据：exit_code=TIMEOUT）②链首无上游（依据：上游链为空）
          ③重跑该步即可修复产出（依据：raw:{src} 由本步生产）
⚠ 证据缺口（1 项）：该次无执行时间窗（第八十三批新增列，老数据按口径不回填）
```

### 过程中即时修复的缺陷
| 现象 | 根因 | 性质 |
|---|---|---|
| 产出物水位恒查不到，误报"均无水位记录" | 水位键是**展开形态**（`raw:mof`），`produces` 是**模板形态**（`raw:{src}`）⇒ **拓扑双层标识问题在水位侧同样存在** | 真实缺陷（与 B1 同源）。改为按实例名展开后查（规则与 `run_production_refresh.py:1301` 一致） |
| 链首步骤输出"上游 0 层（无）已完成" | 上游为空时该怀疑项**不成立** | 自相矛盾。改为链首单列"无上游，直接查本步自身" |
| 耗时基线 n=1 时报"显著高于 p90" | p90≈p50 时"高于 p90"是**噪声** | 误导。改为样本 <3 时明确标注"不足以做分位数对照" |
| 门禁 `gate_enum_values` 拦下新测试 | 测试里写了 `SRC_SET = {"gov",...}` 字面量 | 判据正常工作。改 `from config.enums import SOURCE_ORDER` |

### 新发现的声明面稀疏（已如实锁定，未擅自修）
32 步里只有 **3 步**声明了 `depends_on`（`reports:theme` / `draft:clause` / …），
其余全为空 ⇒ `upstream_chain` / `affected_nodes` 的覆盖面天然有限
（`clean:gov` 的下游本就是空集——**事实，非缺陷**）。
守卫 `test_dependency_declarations_are_sparse_known_limitation` 锁定该现状并写明：
若日后有意补全，需同步更新守卫与台账。**补声明需逐个取证且会改变 R4-c 无环判定**，故不在本批。

### 守卫
`tests/test_step_triage.py`（17 项）：双层标识四类（实例反查/模板命中/未知返回 None/
**台账每种实例形态都必须能反查**）、邻接与上游链**在有环图上终止**、取证证据齐全、
证据缺口可见、怀疑项**必带依据**、链首不得声称上游完成、无失败记录不冒充成功、
拓扑查不到不臆造依赖、产物归属闭合、JSON 结构稳定。

## [Unreleased] 2026-10-16 — 治理缺口补齐：8 个产物的步骤归属 + 判据双向闭合（第八十八批）

> 第 1 波实测发现：`GOV_ARTIFACTS` 19 个产物键只有 **11 个**在 `StepSpec.produces`
> 声明了生产步骤 ⇒ 其余 8 个在门禁归因时**拿不到"重跑哪步"的建议**
> （`produced_by` 只是模块路径，不是步骤名）。

### 补齐的 8 处（每条都有取证，非按语义猜）
| 产物 | 步骤 | 取证 |
|---|---|---|
| `raw:{src}` | `collect:{src}` | watermark 5 行 `produced_by=collectors.<src>.collector`，stage=0 |
| `clean_index` | `apply:{src}` | 它是 cleaned 的**按需旁路索引**（produced_by 是 **getter** `get_clean_index`），而 `cleaned:{src}` 已归本步 |
| `timeliness:state` | `timeliness:verify` | watermark `verification_state.save_state`；本步 purpose 即"时效核验" |
| `rfn_attr` / `rfn_theme` | `classify:all` | watermark produced_by 均为 `rfn.registry._save*`（stage=3） |
| `internal_index` | `internal:update` | watermark `internal_policy_base.indexer.ingest`；本步 purpose 即"内部制度索引更新" |
| `reports:classifier` | `reports:build` | watermark `build_overview_report+build_theme_report`（概览为主） |
| `wiki:sync` | `wiki:sync` | 步骤名与产物键同名 |

### 补声明前先做的**影响面评估**（不盲改）
`produces` 全仓仅 4 处消费：R4-c 无环检测、R4-d 血缘闭合、MCP 门面输出、
以及 `run_production_refresh.py:1266` 的 `--resume` 跳过决策。
**水位/血缘门禁（`gate_watermark` / `gate_provenance`）不读它** ⇒ 门禁语义不变。
唯一行为变化：某步被判定可跳过后会额外核对其产物水位，未跟进则**取消跳过**（更严格= 更正确）；
无水位记录者（`wiki:sync`）走 `get_watermark() → None → continue`，零影响。

### 判据（双向闭合）
`tests/test_artifact_step_ownership.py`（5 项）：与既有 R4-d（`produces ⊆ GOV_ARTIFACTS`）
互补的**反向**判据——`GOV_ARTIFACTS` 的每个产物都必须有步骤归属；
另锁：归属必须是链上**真实步骤名**（不是模块路径）、门禁归因与声明面**同源**（不得各写一份映射）、
本批补的 7 处归属不被"顺手改错"。

### 顺带修掉一处**静默降级**
`tools/gate_triage._artifact_keys()` 的 `except Exception: return {}` 会把任何内部错误
伪装成"无数据"（实测踩过：monkeypatch 造成的递归被它吞掉，症状是"产物未命中"，
排查成本极高）⇒ 改为降级留痕（`LAST_ERROR`）并写进证据缺口，让
「工具故障」与「产物无归属」可区分。

## [Unreleased] 2026-10-16 — 诊断第 1 波：门禁失败归因 `tools/gate_triage.py`（第八十七批）

> 承接第八十批的第 0 波（信号源可用性自检）。诊断最优方案里"成本最低、收益最直接"的一项：
> 近 90 天门禁FAIL **21 次**（实测），且 `gate_result.detail_json.problems` 已是
> **结构化的中文判据 + `文件:行号`**（FAIL 样本 95% 带 `:` 指针）⇒ 归因原料齐备，
> 不需要建监控系统，也不需要 LLM 推理。

### 能力
```
python -m tools.gate_triage --list                # 近 N 天 FAIL 概览（含 problems 条数）
python -m tools.gate_triage <gate>                  # 归因某门禁（最近一次 FAIL）
python -m tools.gate_triage <gate> --run-id RUN-xxx  # 归因指定运行的那次
python -m tools.gate_triage <gate> --json           # 机器可读
cli.py gates --triage <gate> / --triage-list        # CLI 接线（**不新增命令**）
```
输出五段：判据身份 → 归因类别（五类）→ 建议动作 → **判据证据原文** → 指针与重跑建议。

### 三条硬约束（把方案第三章「用强约束控制幻觉」按门禁场景落地）
1. **门禁名有来源**：只接受 `ALL_GATES` 登记 ∪ 治理库历史出现过的名字（支持短名解析）；
   查不到 ⇒ 报"未知门禁"并列候选，**不臆测**。
2. **无证据不出结论**：`problems` 为空 ⇒ **不给归因类别**（只给 `category_hint`
   说明"若补齐证据属哪类"）。实测 3/30 门禁不产出 `problems`
   （`gate_import_bootstrap` / `gate_rfn_drift` / `gate_runtime_hygiene`）。
3. **重跑命令只给有事实源声明的产物**：`StepSpec.produces` 里声明过的（11/19）
   才给 `cli.py run --resume --from <步骤名>`；其余 8 个只有 `produced_by`
   **模块路径**（不是步骤名）⇒ **不给命令**，报为**治理缺口**（血缘声明面与链声明面未对齐）。

### 归因类别：按**门禁身份注册表**判定，不猜problems 文本
`artifact`（重跑生产者）/ `structure`（改代码）/ `semantics`（补登记或走 ADR）/
`environment`（修环境）/ `review`（人工判断）。
**不猜文本的理由**：problems 文案会变、门禁身份稳定；且文本可被误读
（`gate_secret_scan` 的 problems 里写"这看起来像产物水位问题"时，类别仍应是 environment）。

### 实测输出示例（真实 FAIL）
```
[triage] gates.gate_secret_scan
  归因类别：**environment** — 密钥或敏感值硬编码
  判据证据（detail_json.problems 原文，1 条）：
    1. …\std_lib\common_lib\vector_store.py:284 [CRITICAL] 数据库连接串明文口令
       位置：…\vector_store.py:284
```

### 过程中即时修复的缺陷
| 现象 | 根因 | 性质 |
|---|---|---|
| 重跑建议产出**跑不起来的命令**（`--from internal_policy_base.indexer.ingest`） | 把 `produced_by` **模块路径**当步骤名 | 真实缺陷。改为只认 `StepSpec.produces` 的声明 |
| 注册表只对上 **10/30** 门禁 | 我按语义**猜** module 名（`gate_drafter`/`gate_column_registry`…） | 真实缺陷。按真实 `ALL_GATES` 重建，并把「注册表⇄ALL_GATES 名称一致」做成测试项 |
| 报告**自我否定**（一边给类别、一边说"不给类别"） | 无 `problems` 时仍填 `category` | 一致性缺陷。改为 `category` 留空 + `category_hint` |
| 门禁 N-3 拦下**我自己**的新测试 | 测试里写了 `gov_cleaned_20260930.jsonl` 完整字面量 | 判据正常工作。改运行期拼接 |
| `main()` 只能连真库 ⇒ JSON 输出结构无法验证 | 缺连接注入 | 可测性缺陷。加 `con` 参数（同 `signal_availability` 款） |

### 守卫
`tests/test_gate_triage.py`（14 项，含 6 条失态用例）：注册表⇄`ALL_GATES` 双向一致、
类别受控、未知门禁拒绝、无证据不给类别、非 JSON 不崩、无 FAIL 记录不冒充成功、
有声明才给命令（且断言重跑目标是**链上真实步骤名**）、类别不被 problems 文本带偏、
problems 原文不被改写、文件指针抽出、短名解析、JSON 结构稳定。

### 验证
30 道门禁PASS、ruff 0、mypy 0；新增守卫 14 项。

## [Unreleased] 2026-10-16 — E4：nfra/collector.py 按层拆分（extract/http_api）（第八十六批）

> D-遗留第四批。选址依据（AST 实测）：十个候选中 `nfra/collector.py` 是**唯一**
> 「19 个顶层函数 + 0 个类 + 16 个模块常量」的——`gov/collector.py`（841 行）有两个类
> （`RobustSession`/`XzfgkScraper`）互相咬合，`pbc/collector.py` 同理，不宜先动。

### 结构（依赖单向）
```
nfra/extract.py    解析层 115 行：html_to_text / extract_document_no /
                           extract_effective_date / make_summary / build_detail_url
                           —— **零外部依赖的纯函数层**（不触网/不落盘/不依赖全局缓存）
nfra/http_api.py   HTTP 层 136 行：make_opener / http_get_json / _cache_path /
                           _init_cache / set_cache_dir / set_offline + 8 项端点常量
nfra/collector.py  编排层 543 行（原 716）：get_child_items / get_list_for_item /
                           get_detail / load_attachments / download_original_doc /
                           scrape / main + 全部迁出符号的显式 re-export
```

### 为什么这次**不需要补网络夹具**（比 E1–E3 的路径更强）
解析层五个函数全是零外部依赖的纯函数 ⇒ 等价性可用「**重构前后逐字段比对**」锁死，
不必依赖网络夹具形态假设。而 nfra 是**逆向前端 JS 接口**
（`DocInfo/SelectDocByItemIdAndChild` 等），按真实接口形态造夹具成本高且极易猜错——
这条路径在 E1 的 gov/pbc 上已付出过三次"想当然全错"的学费。
⇒ 新建 `tests/test_nfra_extract.py`（17 项）把纯函数锁进长期回归。

### 等价性证据
- **重构前后逐字段比对**：5 个解析函数 × 共 17 组输入（含 script 剥离、空输入、
  表格嵌套、半角括号、超长文本）+ 7 项外部调用面符号 + 10 项常量 ⇒ `DIFF: none`；
- **回归测试**：17 项（`tests/test_nfra_extract.py`）；
- 夹具回放 34 项 + 双形态启动全绿（未受影响）。

### 过程中即时修复的缺陷（**含一个会静默给错结果的真实回归**）

| 现象 | 根因 | 性质 |
|---|---|---|
| **`html_to_text` 行为变了**：`<script>` 内容不被剥离、正文多出换行 | `lxml.html` 未随迁 ⇒ 函数内 try 抛 NameError ⇒ **静默落到正则兜底** | 🔴 **真实回归**：函数不报错，只安静地给错结果。比对脚本当场抓出 |
| `ImportError: cannot import name 'paths'` | 我按印象写 `from std_lib.scraper_std import paths`，实际 `paths` 是**仓根模块**；`bind_source_cache`/`docs_root` 来自 `cache_store` | 凭印象写导入路径的教训：**迁移 import 以备份原文为准** |
| `NameError: BASE`（http_api 导入期） | 层间 import 放在文件末尾，而 `API = BASE + "/cbircweb"` 在**常量区** | **硬规则**：层间 import 必须放在**所有使用点之前**（Python 导入期即求值常量） |
| `NameError: ssl` / `urllib.error` | stdlib 导入未随迁 | 常态 |
| 校验脚本 4 项误报 | 我按"看起来应该"写断言（以为返回空串、以为日期返回原文） | 实测真实行为后修正：`None`/ISO 形态/补句号/半角归一 |

**两条应升为硬规则的教训**（本次与 E3 各踩一次，同源）：
① 层间 import 必须置于使用点之前；② **重构前后比对脚本本身也会错**
（对象身份比较、签名假设、参数结构），它报 DIFF 时要先自证再改产品代码。

### 验证
30 道门禁 PASS、ruff 0、mypy 0；新增回归 17 项；全量测试见下。

## [Unreleased] 2026-10-16 — E3：gov/zhengceku.py 按层拆分（list/detail/attach）（第八十五批）

> D-遗留第三批。zhengceku 夹具已在第八十一批（E1）就位，故本批可直接拆分。
> 依赖图（AST 实测）：`detail → list`；`attach` 独立；`zhengceku.py`（编排）依赖三者。

### 结构
```
gov/zhengceku_list.py    列表层166 行：分页 URL / 停翻页判据 / 总页数 / 列表抓取与条目抽取 /
                              HTML 清洗 / 摘要截取（纯函数 + 一次 HTTP，无详情、无落盘）
gov/zhengceku_detail.py  详情层 108 行：文号抽取 / meta 字段 / 正文选择 / parse_detail
gov/zhengceku_attach.py  附件层 191 行：Word COM 抽取 / 表格层级判定 / CJK 阈值 / 附件下载
gov/zhengceku.py         编排层 454 行（原 802）：ZhengcekuScraper（暂存/断点/run/fetch_one）
                              + build_record + main + re-export
```
- 常量随**主要消费层**下沉；路径常量（`_SRC_ROOT` / `_REPO_ROOT`）在各层**重复定义**
  （值相同、都是 `__file__` 派生）——为共享一个路径常量而让最底层依赖别的层不值得。
- `LOG` 随其唯一消费者迁入 attach 层，但**logger 名保持 `zhengceku` 不变**（handler 的
  level/name 匹配依赖它）。
- **外部调用面零变更**：`gov_collector.py` 的 `from…zhengceku import ZhengcekuScraper`、
  `tools/run_production_refresh.py` 的 `zhengceku.should_stop_paging`（N-190 判据）
  一行未改（经re-export保持）。

### 等价性证据（两层）
1. **夹具回放**：`test_collector_replay_zhengceku.py` + `test_collector_replay.py` 共 28 项全绿；
2. **重构前后逐字段比对**：8 个函数（各多组参数，含 `scan_list` 两种调用形态与
   `parse_detail(html, url)`）+ 7 项符号归属 + 10 项常量⇒ `DIFF: none`。

### 过程中即时修复的缺陷（均为"搬代码忘搬依赖"类，只在运行期暴露）
| 现象 | 根因 | 处置 |
|---|---|---|
| `NameError: _HERE` | 引导块 `del … _HERE`，而各层常量用它算路径 | 引导块保留 `_HERE`（只删循环变量） |
| `NameError: _SRC_ROOT`（list 层） | `_REPO_ROOT` 由 `_SRC_ROOT` 派生，而后者被我归给 attach | 路径常量各层重复定义（list 是最底层，不该依赖 attach） |
| `NameError: _SRC_ROOT`（编排层） | 引导块 `for _p in (_SRC_ROOT, _REPO_ROOT)` 依赖**后面才 re-export** 的常量 | 引导块改为自包含（就地现算路径） |
| `AttributeError: CONTENT_SELECTORS` | 该常量只被 `pick_content_html` 用（详情层），我错配到 list | 归位到 detail + 修 re-export 指向 |
| `NameError: LOG` / `logging` | `LOG` 原定义在编排层未随迁；`import logging` 被 ruff 当未使用删了| 迁入 attach（logger 名不变）+ 置于使用点之前 |

**可复用的教训**：AST 抽取顶层定义时，**模块级的引导块与 logger/路径常量不属于任何函数闭包**，
必须单独盘点——否则拆分后必现NameError，且只在运行期炸。

### 台账
- **J11 基线维护**：`gov/zhengceku.py` 已不超限 ⇒ 从 `file_budget.baseline` 移除
  （13 → 12 → **11** 条，`config/integrity_policy.yaml` 仅 1 行删除）。
- `docs/tech_debt.md` 的 D-遗留进展同步（第一批 + 第二批 + 第三批完成）。

### 全量回归暴露的系统性脆弱点：**同名模块的裸名导入**（新判据 J14）

E3 拆分后的全量回归在**收集期**报错
（`tests/test_internal_policy_base.py`: `cannot import name 'normalize_text' from 'extract'`）。
根因不是 E3 引入的 bug，而是一个**长期潜伏、被拆分触发**的脆弱点：

- 仓内有**13 组同名模块**（`gov/extract.py` vs `internal_policy_base/extract.py`、
  `pbc/constants.py` vs `config/constants.py` …）；
- 采集器层为支持"可独立脚本直调"，脚本形态走**裸名** import（`import extract`），
  把顶层模块名注册进 `sys.modules`；
- 裸名 `from extract import X` 解析到哪一份，取决于**谁先被导入** ⇒
  **全套件的导入结果依赖测试执行顺序**：同一天全绿、换个顺序就ImportError。

处置：
1. 三处测试改为**显式按路径加载**目标模块（`importlib.util.spec_from_file_location`，
   与 `test_resource_hygiene._load` 同款）：`test_internal_policy_base.py`、
   `test_ipb_deep.py`、`test_ipb_extract_file.py`；
2. **新判据 J14**（`gates/gate_config_integrity/_homonym.py`）：测试中不得裸名导入
   同名模块（AST 扫描 `tests/`，`refactor/` 属其他用户目录不在范围），须显式按路径加载。

**教训**：采集器的"可独立脚本直调"纪律（裸名 import 双形态）与"仓内同名模块"是两个
各自合理的约定，**组合起来**会产生依赖执行顺序的静默失效——这类交互缺陷只有门禁能防。

### 验证
30 道门禁 PASS（含新判据 J14）、ruff 0、mypy 0、夹具回放 28 项 + 等价性校验全绿。



## [Unreleased] 2026-10-16 — F1（§13.8）：步骤耗时性能基线（第八十四批）

> 方案 §13.8 的最后一项。动机：`style_report`/J11 管住了**规模**，而**耗时**此前无任何基线
> ——"这一步变慢了"只能人肉对比某次输出。

### 工具 `tools/step_duration_baseline.py`（J1 登记）
- 读治理库 `run_step`（只读）⇒ `data/run_state/step_duration_baseline.json`：
  每步 `n / p50 / p90 / max / last / mean / skip_rate / fails` + 执行时间窗 + 判据来源披露。
- **两条关键口径**（都是实测踩出来的）：
  1. **dry-run 排除**——其 `elapsed_s` 是"列 argv"的进程耗时（<0.1s），混进分布会压平统计；
  2. **resume 跳过样本不入分布**——`--resume` 跳过的步骤 `elapsed_s≈0`（未执行），
     首版实现下 18 个步骤里**多数 p50=0.00**，基线失去判读价值；改为只统计 `skipped=0`
     的样本，跳过率单列（它是"续跑效率"的另一项指标，不该混进耗时分布）。
- **连接可注入**（`_collect_from(conn)`，`collect()` 只负责开真库）⇒ 口径可被内存库测，
  不必"对真库跑一遍看起来对"。守卫 `tests/test_step_duration_baseline.py`（11 项）。

### 首份基线（实测数据，30 次生产运行）
| 观察 | 数据 | 判读 |
|---|---|---|
| 最慢单步 | `collect:mof` p50=**14401s**（4 小时）、失败 1 次 | 采集类步骤是长尾来源；后续优化优先级最高 |
| 波动最大 | `clean:gov` p50=187s 但 p90=**6481s**、max=**15526s**（4.3 小时） | 耗时分布**长尾极重** ⇒ 单点对比会误判，须用分位数 |
| 续跑效率 | 多数步骤 `skip_rate` **73–92%** | `--resume` 有效；但也说明"p50 低"可能是"没跑"而非"快" |
| 时间窗 | **0 个步骤**有 started/finished_at | 全部为迁移前老数据（按口径不回填）⇒ 新列需下一次真实运行才有数据 |

**结论**：耗时判读必须用**分位数 + 跳过率**双维度，且当前数据说明 `clean:gov` 与
`collect:*` 是唯二值得做性能优化的步骤族（其余步骤 p90 均 < 230s）。

### 验证
30 道门禁 PASS、ruff 0、mypy 0；新增守卫 11 项全绿。

## [Unreleased] 2026-10-16 — C3 + TD-3：run_step 增三列（时间窗 + dry_run 标记）（第八十三批）

> 负责人 2026-10-16 决策：**C3 = 只扩 `started_at`/`finished_at`**（明确不扩 `record_count`
> 与 `argv`）；**TD-3 = 加 `is_dry_run` 列**（根治 dry-run 台账污染）。

### 三处改动（零主链控制流变更）
- `governance_store/_db.py`：`run_step` DDL 增 `started_at REAL` / `finished_at REAL` /
  `is_dry_run INTEGER`，并在 `_OBSERVATION_MIGRATIONS` 登记（**`_SCHEMA_INT` 5 → 6**）；
- `governance_store/ledger.py::step_record`：接受并落库三字段（幂等 upsert，旧调用方不传即 0）；
- `tools/run_production_refresh.py::_record_step`：传入 rec 里**本已算好**的时间窗
  （`:581-582` / `:670` 一直在算，只是没落库）与 `_DRY_RUN` 标记。

### 关键口径
- **老数据不回填**（实测 5448 行三列均为 0）：`recorded_at ∓ elapsed_s` 反推出的时间窗
  是**估算值**，混进实测列会让「产物是否落在执行窗内」的判定失真而**无从察觉**；
  故时间窗分析须显式过滤 `started_at != 0`。
- **`is_dry_run` 优先于 note 文本**：`sli_chain` 判据改为 `is_dry_run=1 OR note LIKE '%dry-run%'`
  ——列为主（不受文案变更影响）、文本兜底（兼容未迁移的老库），并在结果里披露判据来源
  （`dry_run_predicate` 字段）。老数据滚出统计窗口后可删文本项。
- 实测：修后 `sli_chain_success_rate` 的 `n=7` 为**真实生产运行**，
  `excluded_dry_run_total=109`（此前窗口内多为 dry-run，成功率恒 100%、SLO 永不触发）。

### 过程缺陷（记录以自省）
- **迁移键算错**：`误按「现有键最大 +1」取 4，而键语义是**目标 `_SCHEMA_INT`**（当时为 5）
  ⇒ `v > cur` 判定跳过，**迁移静默不执行**（真库 user_version 停在 5、列未加）。
  已修正为 6 并同步 `_SCHEMA_INT`；注释里写明该坑。
- 守卫 `tests/test_run_step_schema_ext.py`（9 项）：迁移幂等、**不篡改数据**、
  不回填、写入方三字段齐备、默认值为 0、SLI **列判据/文本兜底双态**各自验证、
  列判据下真实失败仍被计入。

### 验证
30 道门禁 PASS、ruff 0、mypy 0、全量 923 passed/ 2 skipped。

## [Unreleased] 2026-10-16 — E2：gov/parse.py 按层拆分（config/extract/outputs）（第八十二批）

> D-遗留第二批。承接第八十一批的 zhengceku 夹具（E1），本批拆分 `gov/parse.py`
> （843 行 → 210 行）。拆分判据与 pbc 同构：**纯函数层可被夹具驱动 + 分层边界内聚**。

### 结构（依赖单向，两块闭包经 AST 分析**无交集**）

```
gov/config.py    ScrapeConfig（采集配置契约）+ CSV_COLUMNS（产物列契约）+ DEFAULT_HEADERS
gov/extract.py   8 个纯抽取函数 + 3 个抽取常量（无网络/无落盘/无全局状态）
gov/outputs.py   13 个落盘函数 + MasterView + load_resume + 5 个落盘常量
gov/parse.py     引导 + SOURCES（子源登记表**唯一事实源**）+ build_config + 缓存 +
                 全部迁出符号的显式 re-export（外部调用面不变）
```

- **依赖方向**：config ← outputs ← parse；extract 独立（无横向依赖）。
- **re-export 沿用 2026-09-13 P3 写法**（模块对象 + 显式赋值），规避 F401/F405 与
  mypy `no-redef`；`collector.py` 的导入点一行未改。
- 规模：parse.py 843 → 210 行；新增 config.py 84 / extract.py 175 / outputs.py 512 行。

### 等价性证据（两层，均可复跑）

1. **夹具回放**：`tests/test_collector_replay.py` + `test_collector_replay_zhengceku.py`
   共 34 项全绿（gov 抽取 + zhengceku 纯函数 + 附件编码）。
2. **重构前后逐字段比对**：加载拆分前的备份模块与拆分后三层，在同一夹具输入上比对
   7 个抽取函数（各多组参数）× 7 个常量 + 11 个符号的**归属确认**
   ⇒ `DIFF: none`。

### 顺带清理

- **死定义**：`CONTENT_SELECTORS` / `TITLE_SELECTORS` 在 parse.py 定义但**代码零引用**
  （仅 docstring 提及），实际使用方是 `collector.py`（其 L391 遍历）⇒ 删parse.py 副本，
  消除同仓重复定义。
- **J11 基线维护**：`gov/parse.py` 拆分后已不超限，门禁明示「不得留已偿条目」⇒
  从 `config/integrity_policy.yaml` 的 file_budget.baseline 移除（13 → 12 条）。
  用**文本定点删除**而非 yaml 重写——`yaml.safe_dump` 会吞掉全部注释与格式
  （实测 -77 行），配置文件的注释是判据语义的一部分，不能用解析器回写。

### 拆分过程中被及时发现并修复的缺陷（记录以自省）

| 现象 | 根因 | 处置 |
|---|---|---|
| `ScrapeConfig` 报无 `max_seconds` | 按 AST `end_lineno` 取块，少取了末行 | 改为「从定义行到下一个顶层定义前一行」逐字重取 |
| `parse.py` 报 `invalid syntax`（病灶在更早处） | 搬走 `ScrapeConfig` 后其`@dataclass` 装饰器孤立 | 清理孤立装饰器 + 压缩空行 |
| `NameError: _unified_doc_number` | 该名字定义在 `try` 块的 import alias 里，AST 闭包**未捕获** | 迁到真正使用方 `extract.py` |
| `AttributeError: DEFAULT_HEADERS` | 漏搬该块 | 补入 config.py |
| `NameError: dataclass` / `csv` / `datetime` | ruff 在使用点缺失期间把导入当"未使用"删了；补回使用点时不会自动补回 | 补齐导入并复验 |

**教训**：**自动修复必须在校验之后**——ruff 按"当前文件状态"判断未使用，
若中途手工删了使用点，会连锁删掉导入；而恢复使用点时 ruff 不会补回导入。

### 全量回归暴露的两个结构性问题（当场发现并修复）

| 问题 | 根因 | 处置 |
|---|---|---|
| **模块同名遮蔽**（`gov/scrape_config.py` 原名 `config.py`） | 仓根已有真实包 `config/`（含 `__init__.py`）。脚本形态下 `sys.path` 中仓根在先⇒ `from config import CSV_COLUMNS` 解析到 `config/__init__.py` ⇒ `ImportError: cannot import name 'CSV_COLUMNS' from 'config'`。**同类名在同仓共存是真实的歧义** | 改名 `config.py` → `scrape_config.py`（与类名 `ScrapeConfig` 对齐）。三个候选（改名 / `importlib` 动态加载 / 把常量留原层）中**只有改名是根治**——另两者都是在绕过歧义 |
| **脚本形态引导缺本目录** | 单文件时代只需仓根与上层包；拆出兄弟模块后，脚本形态的裸名 `import outputs` 需要 `collectors/gov/` **自身**在 `sys.path`。三个新文件的引导块补入 `_HERE` | 引导块统一为「本目录 + 仓根 + 上层 `collectors`」三条路径 |

另修三处**测试跟随分层**：`test_resource_hygiene.gp` / `test_raw_jsonl` /
`test_gov_incremental` 用 `importlib` 按路径加载 `parse.py` 并监视其 `json` /
`_COLLECT_STATS_REL` 属性——这些实现已随落盘逻辑迁到 `outputs.py` ⇒ 加载目标更新为
`outputs.py`，**断言的性质不变**（仍是"走流式而非整表解析"），只是不再依赖分层位置。

### 验证


## [Unreleased] 2026-10-16 — 诊断第 0 波：信号源可用性自检 + SLI 口径修正（第八十批）

> 承接第七十九批与五阶段审查的最上游结论：**「告警从未真正送达」**——`reports/_alerts`
> 目录不存在（此前从未生成），意味着任何以告警为输入的诊断能力都建立在**未被验证的通道**上。
> 本批把「信号源可用性」变成**可实测、可复跑**的事实，并顺带修掉一个被实测暴露的
> **指标失真**缺陷。

### 新增 `tools/signal_availability.py`（J1 登记）

- **信号源实测表**：治理库各表行数 + 最后活动时间、step_logs 文件数/最后写入、_alerts 状态；
  输出 `reports/diagnosis/signal_availability.json`（供门禁与确认台 P3 消费）。
- **notify 通道可达性探针**：写一个探针文件再删（**不触发真实告警**，避免污染外部
  watcher 消费面）；实测结论 =通道**现在**可达（目录可创建可写），但**此前从未落盘过告警**
  （`exists_before=False` 时显式披露）。
- 实测输出示例：`audit_log 3 行（C1 落地后首次有数据）`、`run_step 5270`、
  `step_logs 424 文件`、`_alerts 0 文件`（探针创建目录后）。

### 修`sli_chain` 口径缺陷（TD-3，本批最重要的修复）

- **问题**：`sli_chain` 取"最近 N 次 run_log 运行"判failed 占比，但 **dry-run
  （`--dry-run` 只列 argv 不执行）也写run_log/run_step**，且每步 rc=0：
  实测 dry-run 占 run_step 行数 **65.4%**（3449/5270）、run 数 **74.5%**（105/141），
  每次 `cli.py gates` 产生 1 个 ⇒ 只要窗口内多为 dry-run，
  `sli_chain_success_rate` 恒为 100% ⇒ **SLO ≥98% 永不触发，指标形同虚设**。
- **修法**（零 schema 变更、零主链改动）：SQL 侧直接取「不含任何 dry-run 步骤的运行」
  = 生产运行，在其上取窗口；同时把排除量写入 `excluded_dry_run_total` + `note` 披露。
  ⚠️ 中途踩过一次坑并修正：**不能「先取最近 N 个再逐个过滤」**——最近 N 个常全是 dry-run
  （gates 产物），过滤后为空 ⇒ SLI 直接报"无数据"（比虚高更糟）；改为 SQL 侧筛选后
  实测 `n=7` 为真实生产运行，note 显式披露"全库 105 次 dry-run 已排除"。
- **为什么这不是"改口径"而是"修 bug"**：ADR-0012 定义的是**链成功率（生产运行的成功比例）**，
  dry-run 不执行任何步骤，本就不是"一次运行"；把它计入执行成功率在定义上就错。
- 守卫 `tests/test_sli_dryrun_exclusion.py`（5 项）：窗口全dry-run ⇒ 不产出 SLI（防虚高）、
  dry-run 排除但生产运行照常计入、**真实失败仍被计入**（防修口径时漏判失败）、
  混合窗口比率只按生产运行算、排除量必须可见（不许静默过滤）。
- **根治方案待决**（登记 TD-3）：① dry-run 不写台账（须先确认链等价 golden 是否依赖它）
  ② 或加 `is_dry_run` 标记列（schema 变更，与 ADR-0014 同等决策）——本批只做**指标侧纠正**，
  不动写入面。

### 验证

30 道门禁 PASS、ruff 0、mypy 0；新增守卫 5 项全绿。

## [Unreleased] 2026-10-16 — C1 审计覆盖人工处置 + C2 分批裁决 + C3 schema 决策书（第七十九批）

> 处置五阶段审查遗留的三项「待人工确认」：C1 与 C2 由负责人决策并本批落地；
> C3（是否扩展 `run_step` schema）按负责人要求**出具优劣说明待确认**，本批不实施。

### C1：审计面覆盖人工处置（已落地）

- `governance_store.worklist_resolve()`（`std_lib/common_lib/governance_store/worklist.py:152`）
  内补 `_audit_resolution()`：落`audit_log`（action=`worklist_resolved`/`worklist_dismissed`，
  target=worklist、target_key=item_id、field=status、old=open→new、basis=处置理由、actor=处置人）。
- 三条设计依据：
  · **写在唯一写口**而非 `commands/worklist.py`/`web/snapshots.py` —— 后者只覆盖单一入口会漏；
    CLI、确认台 P4、未来 MCP 全通道自动覆盖（表级唯一写方纪律）；
  · **actor 与 `resolved_by` 同源**（同一份处置人，杜绝「台账说A、审计说B」双口径）；
  · **审计失败不阻断处置**（旁路留痕：裁决是已发生的事实，不能因留痕失败而"当作没发生"，
    否则会诱导调用方重试造成重复处置）。
- 守卫`tests/test_c1_human_audit.py`（4 项 contract）：正例 resolve/dismiss 各写一条且要素齐全；
  负例 ①未命中不得写审计（否则审计出现幽灵裁决）②审计不可用时处置仍须成功。
- **端到端实证**：执行 B2 批次后 `audit_log` 从 **0 行 → 3 行**，处置数与审计数一致（3=3）。

### C2：641 条 open 分批裁决（工具 + 首批执行）

- 新工具 `tools/worklist_triage.py`（J1 登记）：`scan`（分批总览）/ `bundle`（导出裁决包）/
  `apply`（受控执行）。
- **按「裁决动作类型」切批，而非按数量平均切**（实测641 条的真实结构）：

  | 批次 | kind | 条数 | 机器可判 | 需人工 | 依据 |
  |---|---|---|---|---|---|
  | B1 | trigger_manual_breakpoint | 23 | 5 | 18 | payload/subject 自证闭环（弱证据，须显式 opt-in） |
  | B2 | relevance_boundary（汇总） | 3 | 3 | 0 | `summary:` 前缀，无裁决对象（明细在 relevance/*.jsonl） |
  | B3 | relevance_boundary（明细） | 208 | 0 | 208 | 带 hits/snippet，证据齐全但需业务判定 |
  | B4 | rfn_theme_uncertain | 324 | 0 | 324 | payload 无候选主题与票数；标题在归属表命中 0 |
  | B5 | internal_unaligned | 66 | 0 | 66 | 补关键词或确认无对应主题 |
  | B6/B7/B8 | drift_c2 / timeliness / quarantine | 17 | 0 | 17 | 数量小，产出裁决包或走既有工具 |

  覆盖 **641/641 闭合**（批次是归口单位，机器可执行范围是批内子集——两者混同会漏归口）。
- **三条安全硬约束**：默认只读（`apply` 须 `--execute`）；机器只判机器能判的
  （业务判断批次一律拒绝代劳）；人工闸门（`--by` + `--reason` 必填，逐条落审计）。
- **已执行B2（3 条 dismissed）**，依据入审计台账；其余批次产出 4 份裁决包
  （`reports/worklist_batches/*.md|json`，含逐条证据摘要与建议动作）。
- 守卫 `tests/test_worklist_triage.py`（12 项，含 6 条失态用例：默认只读 / 谓词边界 /
  覆盖闭合 / dry-run / 缺 by·reason 拒绝 / 业务批次拒绝代劳 / 动作名受控域）。
- **开发过程中被门禁与测试当场抓出并修复的缺陷**（4 处，均为受控域/分类逻辑类）：
  ① B2 动作名写成 `dismiss`（受控域为 `dismissed`，写口 ValueError 拒绝——正面案例）；
  ② B1 动作名同样越界（`resolve` ⇒ 由自建测试 `test_auto_actions_are_within_controlled_domain` 抓出）；
  ③ B1 谓词只扫 payload漏扫 subject ⇒ 自证闭环条目假阴性（实测 5 条）；
  ④ 分类按 batch_id 而非 kind 分支 ⇒ 汇总项被重复计入 B3（分批总数 214 > 实际 211）。

### C3：`run_step` 是否扩展 schema（决策建议书，本批不实施）

- 出具 `docs/adr/ADR-0014-run_step-时间列扩展决策.md`，结论 **分项决策**：
  · **建议扩** `started_at` / `finished_at` 两列——主链**手上已有这两个值**
    （`tools/run_production_refresh.py:563` `t0=time.time()`、`:670` `rec["ended_at"]` 已算出
    但未落库），迁移机制现成（`_db.py:374` `ALTER TABLE ADD COLUMN`），写入点已是旁路
    （`:1159-1173`「登记失败不得中断主链」）⇒ 约 6 行 / 3 文件，主链控制流零改动；
    且 `recorded_at` 的误差 = 该步耗时（实测 `clean:gov` elapsed 15526s ≈ 4.3 小时），
    缺这两列则根因分析没有时间窗基础；
  · **建议不扩** `record_count`（可从 `clean_index_api.record_count()` 派生，实测五源 0.067s，
    且产物侧才是权威，落库即制造第二事实源）；
  · **建议不扩** `argv`（权威来源是代码调用点，可AST 抽取——`gen_flow_map` 已在做；
    落库会随代码漂移）。
- 待负责人确认的一点：老数据（5132 行）两列为空后，**建议不反推填充**
  （反推值会被误当实测值引用，污染数据语义），并要求时间窗分析显式过滤 `started_at IS NOT NULL`。

### 顺带修复：跨日失败的时间炸弹（执行途中发现即修）

全量回归首次运行（2026-10-16 凌晨，日期刚从 10-15 跨到 10-16）报出
`tests/test_w4_data_layer.py::test_clause_md_flag_defaults_off` 失败。根因是该用例
**输入侧**已按门禁 N-3 要求改用动态日期（`datetime.date.today()`），**断言侧却仍硬编码
按日快照文件名** ⇒ 写当天不可见、**跨日必现**。

- 修复：断言侧改用同一个 `stamp` 变量贯穿（输入与断言同源）。
- **判据收窄（防复发）**：`gates/gate_hardcoded_snapshots.PAT` 原只覆盖 `_cleaned_` 一族，
  故 `_clauses_` 族的同类缺陷从未被拦下 ⇒ 模式扩到 `cleaned|clauses|raw` 三族
  （这三族都是按日命名的快照产物，硬编码即定时炸弹；自检确认能命中当时的雷，
  且仓内已无该形态残留）。
- 守卫 `tests/test_n3_snapshot_date_literals.py`（5 项）：三族正例命中 +
  **非快照族负例不命中**（保证零噪声——门禁一旦噪声化就会被整体忽略）+
  模式结构断言 + 仓内无残留 + 动态写法为约定解法。
- **插曲（记录以自省）**：该守卫首版在parametrize 里写了完整字面量，**被刚扩展的判据
  当场拦下**——门禁对自己的测试文件生效，这正是判据可信的证据；已改为运行期拼接。

### 顺带修复：跨日失败的时间炸弹（执行途中发现即修）

全量回归首次运行（2026-10-16 凌晨，日期刚从 10-15 跨到 10-16）报出
`tests/test_w4_data_layer.py::test_clause_md_flag_defaults_off` 失败。根因是该用例
**输入侧**已按门禁 N-3 要求改用动态日期（`datetime.date.today()`），**断言侧却仍硬编码
按日快照文件名** ⇒ 写当天不可见、**跨日必现**。

- 修复：断言侧改用同一个 `stamp` 变量贯穿（输入与断言同源）。
- **判据收窄（防复发）**：`gates/gate_hardcoded_snapshots.PAT` 原只覆盖 `_cleaned_` 一族，
  故 `_clauses_` 族的同类缺陷从未被拦下 ⇒ 模式扩到 `cleaned|clauses|raw` 三族
  （这三族都是按日命名的快照产物，硬编码即定时炸弹；自检确认能命中当时的雷，
  且仓内已无该形态残留）。
- 守卫 `tests/test_n3_snapshot_date_literals.py`（5 项）：三族正例命中 +
  **非快照族负例不命中**（保证零噪声——门禁一旦噪声化就会被整体忽略）+
  模式结构断言 + 仓内无残留 + 动态写法为约定解法。
- **插曲（记录以自省）**：该守卫首版在 parametrize 里写了完整字面量，**被刚扩展的判据
  当场拦下**——门禁对自己的测试文件生效，这正是判据可信的证据；已改为运行期拼接。

### 验证

## [Unreleased] 2026-10-15 — 诊断能力五阶段审查 + 取证缺陷修复（第七十八批）

> 对 `reports/refactors/基于腾讯云Agent根因定位实践的项目诊断能力增强方案.md`（878 行）
> 做**五阶段实证审查**（现状 → 选型 → 用途 → 需求 → 偏差），出具
> `reports/refactors/项目诊断能力增强方案_五阶段审查与最优方案.md`。
> 方法论沿用腾讯云《Agent 自主定位根因实践》，但**不采信原方案的任何断言**，
> 逐条以代码与真实数据重验。

### 审查结论（三处阻断级偏差，均源于未核对真实数据）

| # | 偏差 | 代码/数据证据 |
|---|---|---|
| B1 | **拓扑单层节点设计错误**——未识别「声明模板步 ↔ 运行实例步」双层结构 | `tools/run_production_refresh.py:1100` STEP_ORDER 32 项含 4 个 `{src}` 模板（`collect/clean/relevance/apply:{src}`）；最近一次真实运行台账 **40 实例步、0 个模板形态**；`GOV_ARTIFACTS` 19 键同样模板化（`raw:{src}` 等）⇒ 原方案「`get_node()` 查不到即拒绝」的硬约束在真实失败场景（`clean:nfra`）下**必然自我拒绝** |
| B2 | **取证入口路径错误** | 写入方 `run_production_refresh.py:611-613` 产出 `{全字符归一}_{时间戳}.out.log` / `.err.log`（实测 424 个文件）；原方案按 `{step}.log` 取证**恒不可达** |
| B3 | **schema 臆断** | `run_step` 实际列为 `[run_id, step, rc, exit_code, elapsed_s, skipped, note, recorded_at]`——**无 `started_at`、无记录数列** ⇒ 原方案的 `ORDER BY started_at` 与「记录数少 12%」类规则不可实现 |

### 现状基线（供后续判定的锚点）

- 已有诊断类设施：doctor 22 项环境体检（`commands/doctor.py:392`）、audit_health 12 项静态审计、
  3 个 SLI（`tools/check_slo.py:58,72,98`）、30 道门禁、确认台 P0–P8。
  **唯独没有「运行期症状 → 归因」这一环** ⇒ 原方案方向正确。
- **诊断需求频次实测**：近 90 天步级失败 **35 次**、门禁 FAIL **19 次**、告警落盘 **0 次**
  （`reports/_alerts` 目录不存在）；而 **worklist open 641 条 / 已处置 14 条（处置率 2.1%）**、
  其中 82% 集中在 `rfn_theme_uncertain`(324) + `relevance_boundary`(211)。
  ⇒ 项目是「**低故障率 + 高积压**」系统，原方案把主要投入放在步级根因属**优先级错配**。
- `audit_log` 0 行的真实原因：**有**写入方（`governance_store/projections.py:47`，
  由 MCP 写级审计 `mcp_server/registry.py:258` 调用）但 **MCP 门面从未被真实调用**。

### 本批修复（B2 的代码落地）

- `web/snapshots.py`：新增模块级公开函数 `latest_step_log(step, tail_lines=40)`——
  按**写入方逐字同规则**归一（`re.sub(r"[^0-9A-Za-z_.-]", "_", step)`，全字符而非只换冒号），
  glob 取时间戳最大者；`step_detail()` 改用它。此前确认台 P1 节点抽屉的「日志 tail」**恒为空**，
  修复后实测可取到真实日志（如 `clean_nfra_1792015532.out.log`）。
- `tests/test_step_log_evidence.py`（4 项守卫，contract 标记）：
  ① 写入方命名三要素可识别；② **归一规则与写入方逐字一致**（从两侧源码提取正则比对，
  并对含冒号/斜杠/点/中文的步骤名断言归一结果相同）——防漂移；
  ③ 对磁盘真实文件能取回；④ 负例静态断言：不得退回固定 `{step}.log` 拼接。

### 待人工确认（审查中标记，不猜测）

1. **C1** `audit_log` 是否应覆盖人工处置路径（现仅 MCP 写级审计）——治理决策
2. **C2** 641 条 open 的处置策略（批量 dismiss / 分批裁决 / 改判据从源头减产）——业务判断
3. **C3** 是否扩展 `run_step` schema（记录 argv/开始时间/记录数）以支撑量化归因——架构决策
   （与「不改主链」约束冲突，需权衡）

### 验证

30 道门禁 PASS、ruff 0、mypy 0、新增守卫 4 项 + 确认台契约 26 项全绿。

## [Unreleased] 2026-10-15 — D-遗留第一批：pbc 采集器按层拆分（第七十七批）

> 承接 TD-1（上一批处置的录制-重放夹具）：夹具就位后才动大文件内部结构。
> 拆分判据（按此挑选本轮目标）：**行数大 + 解析入口已被夹具覆盖 + 分层边界内聚**。
> 十个≥400 行采集文件中，`pbc/collector.py` 命中 6 个夹具覆盖入口、13 处分层线索 ⇒ 首选。

### 结构变更（依赖单向、无环）
```
pbc/constants.py   共享常量：BASE / ISSUING_AUTHORITIES / ATTACH_EXT / RE_TAG / RE_WS
pbc/listing.py     列表层：RE_PAGING / RE_ENTRY / parse_listing / listing_page_urls
pbc/detail.py      详情层：RE_*（7 条）/ parse_detail / extract_doc_number /
                   extract_issuing / extract_effective
pbc/collector.py   采集层：引导 / 缓存 / 配置 / UA / Fetcher / 主流程 / CLI
                   729 → 541 行；**P3 写法显式 re-export** 保持外部调用面不变
```
- **为什么这么切**：`BASE`（列表层要）与 `ISSUING_AUTHORITIES`（详情层要）同被两层需要，
  各复制一份必漏改其一 ⇒ 站点常量静默分叉（不报错，只是抓错/抽不出）⇒ 取公共上层；
  `Fetcher`/缓存/主流程互相咬合，不宜再切（拆分是为了让解析层能被夹具直接驱动，不是为了行数好看）。
- 函数体**逐字搬运**（含原注释），仅改依赖来源；每层文件保留「可独立脚本直调」的三行引导。

### 等价性证据（两层，均可复跑）
1. **夹具回放**：`tests/test_collector_replay.py` 17 项全绿——`pbc_list_page` / `pbc_detail_page`
   夹具经**重构后**的 listing/detail 层解析，字段级断言不变；
2. **重构前后逐字段比对**：加载拆分前的备份模块与拆分后的模块，在同一夹具输入上比对
   `parse_listing` / `listing_page_urls`（hex 栏目 + 单页两种形态）/ `parse_detail` /
   `extract_doc_number` / `extract_issuing` / `extract_effective` 共 7 组输出，
   并校验 21 项迁出符号存在性与常量一致性 ⇒全等（DIFF: none）。

### 顺带修正（三处「搬代码忘搬依赖」类缺陷，均只在运行期暴露）
- `listing.py` / `detail.py` 补 `import re`（正则随块迁入、导入未随之迁）；
- `listing.py` 补 `import urllib.parse`（`parse_listing` 用它解析绝对 URL）；
- `detail.py` 去掉未用的 `RE_TAG` / `RE_WS`（二者仍是 constants 的公开常量，由 collector re-export）。

### 验证
30 道门禁 PASS、ruff 0、mypy 0、夹具回放 17 项 + 门禁与契约 91 项全绿。

## [Unreleased] 2026-10-15 — TD-1 处置：采集器录制-重放夹具 + 判据 J13（第七十六批）

> 处置 `docs/tech_debt.md` 最高优先技术债（TD-1），并**解除**「>500 行采集文件按层拆分」
> （方案 §7.8 D-遗留）的**前置阻塞**——没有可回放的稳定输入，就不敢动那些文件的内部结构。

### 夹具层（`tests/http_cassette.py` + `tests/cassettes/`）
- **回放优先、严格模式**：默认只回放夹具，未命中即 `CassetteMiss`（**绝不静默联网**——
  联网会让回归网时通时不通）；`origin` ∈ {`recorded`, `synthetic`} 显式披露来源，不冒充真实抓取。
- **URL 归一**：小写 host、去默认端口、去 fragment、剔易变参数（时间戳/追踪 id）、query 排序
  ⇒ 同一逻辑请求无论线上带什么抖动都命中同一夹具（TD-1 的核心价值）。
- **四类网络入口覆盖**（与调研一致——采集器并非全部经单栈）：`urllib.request.urlopen` /
  `build_opener().open`（nfra 形态）/ `requests.Session.send`（gov 会话级 RobustSession，
  ADR-0009 豁免）/ 单栈 `robust_get`。
- **密钥脱敏**：Authorization/Cookie/token 类头落盘即 `***`（对齐 gate_secret_scan）。
- **夹具不腐化**：逐条 `body_sha256` 校验 + `assert_all_used`（留死条目即报）；base64 支持二进制附件。
- **可测性**：`CASSETTES_ROOT` 参数化根目录（运行期默认 `tests/cassettes`），门禁与负例测试共用
  **同一校验实现**（`load_cassette`）——不复制校验逻辑，杜绝门禁与运行期口径漂移。

### 回放用例（17 项，`tests/test_collector_replay.py`）
- 夹具机制：URL 归一、严格未命中、脱敏、篡改检测、base64、空夹具拒绝、manifest 闭合；
- 纯解析器（无 HTTP，最稳）：`pbc.parse_listing`（分页隐藏域 + /tiaofasi/ 条目 + 去重）、
  `pbc.parse_detail`（ArticleTitle + #shijian + #zoom）、`listing_page_urls`（hex 栏目取
  moduleid 前 8 位的历史 404 坑）、`gov` 文号/机关/日期抽取（**按各函数真实契约取形态**：
  机关抽取只覆盖「X令第N号」令文、日期抽取返回原文不归一——两处差异均写入用例防「顺手统一」误改）；
- 端到端回放：`urlopen` 与 `build_opener().open` 两形态各一条，跑真实采集器解析路径。

### 门禁与工具
- **新判据 J13**（`gates/gate_config_integrity/_cassette.py`，5 条）：manifest 双向闭合 /
  摘要一致（防「手改夹具不更新摘要」）/ 结构与逐条摘要自校验 / 无凭据明文残留 / origin 受控。
  **判据会失败才值得信任** ⇒ `tests/test_j13_cassette_gate.py` 7 项正负例（漏登记/幽灵登记/
  摘要漂移/正文篡改/凭据残留/非法 origin）。
- `tools/record_cassettes.py`（J1 登记为独立工具）：manifest 唯一重建入口。

### 顺带修正
- `request_key` 口径：请求体**只在非空时**参与（原实现把响应体计入⇒GET 变体不命中）。
- `OpenerDirector.open` 是**实例属性**（`__init__` 内 `self.open = self._open`）⇒ 补丁须打
  `_open`，且调用方在补丁生效后构造 opener（此点写入 harness docstring 契约）。

### 验证
30 道门禁 PASS、ruff 0、mypy 0。

## [Unreleased] 2026-10-15 — H 轨：本地 Web 确认台（P0–P8）+ 判据 J12（第七十五批）

> 方案 §8（人机交互层）。`cli.py serve` 一个本地界面同时承载**待办闭环管理**（操作面）与
> **项目数据总览**（观测面）；**零新增依赖**（纯标准库服务端 + 单文件原生 JS 前端）。

### 结构与纪律
- `web/` 新顶层包（服务端与页面资源）+ `commands/serve.py` CLI 薄壳（与 `commands/run.py`
  薄壳 + `tools/run_production_refresh.py` 实现同构）：
  · `web/snapshots.py` —— 卡片数据层（每源一 provider，统一 `available/source/fetched_at`
    降级形状 + 2s TTL 防抖缓存），**一切取数经 `interfaces/` + `governance_store`**；
  · `web/api.py` —— 15 条路由的薄层（参数校验 + 委派 + `_scrub` 密钥脱敏 + 请求体 4KB 上限）；
  · `web/sse.py` —— SSE Hub（服务端 2s diff 指纹，有变更才推；前端断线自动降级 5s 轮询）；
  · `web/server.py` —— `ThreadingHTTPServer`（loopback-only，非回环打印**无鉴权**告警；
    安全头 nosniff/ DENY / no-referrer；静态资源路径穿越拒绝）；
  · `web/assets/index.html` —— 单文件 SPA：9 个页签 + **手绘 SVG**（桑基图/折线/柱状/环形），
    **零 CDN**（出口网络有过滤，外链即不可用风险）。
- 页面：P0 总览六卡 / P1 桑基（边宽 ∝ √记录数，节点抽屉含 argv·rc·日志尾）/ P2 资产与水位 /
  P3 指标（含 **DAMA 六维**与 **SLI/SLO** 卡片，直接读 W5 度量产物）/ P4 待办闭环（处置必填理由）/
  P5 治理审计 / P6 FTS5 检索（external→internal 自动回退）/ P7 投放看板 / P8 发布事件流。
- **唯一写路径** = `governance_store.worklist_resolve`（处置登记；确认台**不执行任意动作**，
  §8.6 安全核心）；P4 动作注册表显式声明该边界。

### 门禁与测试
- **新判据 J12**（`gates/gate_config_integrity/_console.py`，5 条）：禁裸 SQL 写 / 禁自写
  `sys.path` / 禁外链 CDN / `serve` 必须注册 / 响应必须脱敏。
- `tests/test_confirm_console.py` 26 项契约测试（随机端口真实起服务，不 mock HTTP）：
  13 条路由 200+JSON、未知路由 404、参数非法 400、路径穿越被拒、处置缺理由 400、
  脱敏生效、以及三条**静态纪律断言**（无裸 SQL 写 / sqlite 只读 / 无自写引导）。
- 修复 `gate_import_bootstrap._code_hits` 的**自指误报**（2026-10-15 亲历）：原口径只跳注释，
  门禁/测试**自己代码里的字符串字面量**（含 3.12+ 的 f-string 字面量片段）被计成注入
  ⇒ 「只减不增」纪律被自己的判据文本顶破。现改为 `tokenize` 剥离注释**与字符串**（含
  `FSTRING_MIDDLE`）后再匹配，无法 tokenize 时回退保守计数。副作用：计数更准
  （剔除散文与字面量，modules 由 111 → 109 真实下降），基线无需重标。

### 验证
30 道门禁 PASS、ruff 0、mypy 0（含新引入的 `web` 与被 J12 拉进图的 `cli.py`）。

## [Unreleased] 2026-10-15 — W5 三批：版本管理 + 技术债台账 + 测试矩阵 + 风格报告 + 行数预算判据 J11（第七十四批）

> 方案 §13.6（版本管理 P1）、§13.7（技术债台账 P2）、§13.8（测试深度 P1）、§13.9（现代特性 P1）、
> §13.11（PEP 8 显式采纳 P1）。**W5 出口判据至此全部「已完成或已登记」**。

### §13.6 版本管理与兼容性
- `docs/versioning.md` 成文：semver **三层**（契约版本 = 消费方承诺 / 发布件 `schema_version` =
  产物现实 / STEP_LAYERS 资源版本 = 链内记账——混层会导致"承诺了不存在的能力"）、兼容规则
  （minor 只增不改不删 / major 附等价性证明）、**版本矩阵**（7 类消费方 × 受支持区间 × 守门判据）、
  弃用三台阶（与 §7.2 退役同节奏，观察期 2 个 Wave）。遗留登记：`paths.py` 常量形态 str→Path
  属破坏面变更，须走 major + 消费方盘点，**待人工确认后排独立 Wave**（不在本波隐式改动）。

### §13.7 技术债台账
- `docs/tech_debt.md` 成文：三轴打分（影响面 × 风险 × 耦合度）+ **债务预算硬约束**
  （每 Wave ≤15% 容量，超出转登记，防"重构变重写"）+ 四类台账（代码债沿用 §7 的 A–E/B/C 编号
  **不重编号**；新增 AD/DD/TD 三类）+ 复核节奏（每 Wave / 季度 / 事故触发式）。
- **TD-1 标记为最高优先技术债**：采集器缺录制-重放夹具 ⇒ 抓取逻辑改动难回归，这也是
  10 个 >500 行采集文件不敢拆的**前置阻塞**（诚实登记，而非假装可以拆）。

### §13.8 测试深度
- `tests/README.md` 成文：**契约测试矩阵**（8 类：字段契约 / 产物形态 / import 面 / 传输行为 /
  资源版本 / 门禁判据 / 配置一致性 / 链等价 × 具体用例位置）+ 运行方式 + 属性不变量登记。
  首轮自检即发现矩阵里 **5 个"凭印象写的文件名"在仓内不存在**（test_contract_surface /
  test_publish_outputs / test_imports / test_http_contract / test_property_date）→ 按实际
  文件全部修正。**教训：文档声称与实际不符，本身就是一类债。**
- 属性不变量三组以**确定性用例**实现（清洗规则 / resume 幂等 / 水位判据稳定）；
  `hypothesis` 库裁定**不引入**（理由：确定性优先 + 现有断言强度等价；复发条件已写死）。

### §13.9 现代特性（报告先行，结论推翻方案假设）
- `tools/modern_features_report.py` 出报告。**关键发现：方案假设不成立**——本仓 72 个受控域
  是 `frozenset`/`tuple` 字面量 + `gate_enum_values` 的**全仓字面量面校验**，**零个 Enum 类**
  ⇒ 无可迁对象；且该形态**比 Enum 类更严**（Enum 无法被全仓字面量面扫描覆盖，会绕开判据）。
  故 StrEnum 迁移裁定为**不适用**（而非"暂不实施"）。
- 现状基线（298 文件）：PEP 604 `X | None` 78 文件、`from __future__ import annotations`
  257 文件、dataclass 16 文件、`match/case` **0**、type alias 0。

### §13.11 PEP 8 显式采纳 + 新判据 J11（行数预算，只减不增）
- `tools/style_report.py` 出量化报告：C901（复杂度>15）**169 个函数**、D103 缺 docstring
  **376 处**、D101 **18 处**、≥800 行文件 **13 个**。
- **裁定分层**：D 规则与 C901 **先不全局启用**——394 条 docstring 与 169 条复杂度告警当前
  **无害**，全局启用等于训练忽略（告警疲劳＝没有告警）；改为分层（owned 层启用 D）+ 定点量
  （重构某文件时用报告看该文件）。
- **新判据 J11（行数预算，只减不增）**：`gates/gate_config_integrity/_filebudget.py` +
  策略 `integrity_policy.file_budget`（max_lines=800 + baseline 13 条）。**首建即打脸**：
  §7.8 的 D 类「>1200 行 4→0 已完成」是**一次性核对**，其后 `run_production_refresh.py`
  随 W0–W5 新增步骤又长到 **2338 行**——**规模在无人盯防的情况下回涨**。基线单调收缩
  （拆一个删一个），不要求立刻清零（强拆风险高于收益）。

### 验证
30 道门禁 PASS、ruff 0、mypy 0、bandit 0 问题。

## [Unreleased] 2026-10-15 — W5 第二批：SLI/SLO + DAMA 六维质量 + 灾备演练 + ADR-0012（第七十三批）

> 方案 §13.3（可观测性 P1）、§13.4（灾备 P0）、§13.5（数据质量 P0）、§13.12（明确不做清单）。
> 三件度量工具 + 两个新告警事件 + 首次灾备演练；门禁 30 道全绿、用例 817+ 全绿。

### SLI/SLO 与错误预算（§13.3）
- `tools/check_slo.py`：三 SLI 全部取自治理库既有台账（不新造检测逻辑）——
  `sli_chain_success_rate`（最近 7 次运行 failed=0 占比，SLO ≥98%）/
  `sli_data_freshness`（关键族水位 age ≤ 阈值占比，SLO ≥95%）/
  `sli_gate_pass_rate`（最近 7 轮 gates 全绿占比，SLO = 100%，准入门非渐进）。
- 阈值唯一事实源 = `config/schedule.yaml` 的 `notify.sli` 段；快照入
  `data/run_state/slo_history.jsonl`（趋势可查）。**连续 2 个周期**低于 SLO ⇒ 发
  `slo_breach`（防单点抖动误报）。首跑：三 SLI 均 100%，错误预算余额 2.00pp。
- 事件 `slo_breach` 已登记入 `NOTIFY_EVENTS` + `notify.on` + `integrity_policy.notify_on`
  三处（门禁 S3/E2 双向守护：声明必须有发送方、策略与受控域不得漂移）。

### DAMA 六维 + 健康度评分（§13.5）
- `tools/gen_quality_metrics.py` → `reports/quality/`：完备性/唯一性/及时性/有效性/准确性/
  一致性六维，**全部指标取自既有机器判据输出**（corpus 索引、watermark、gate_result、
  document 表、quarantine 区）。**缺失维度不计入分母**——不以 0 充数，否则数据源一断
  评分就变成谎言。首跑：健康度 **98.84**（六维全部可用）。
- 两处口径陷阱（实测修正，勿回退）：① `FRESH_DAYS` 键是模板 `raw:{src}`，真实水位键是
  `raw:gov` ⇒ 必须按**族前缀**匹配（精确匹配只命中 1 条水位，及时性成假高分）；
  ② 唯一性**不能用 `docno_norm` 判重**——同文号多源转载是业务常态，按文号判重把正常
  数据误报为质量问题（实测假低分 75.88）；唯一性 = `doc_ref` 主键唯一率。
- 任一维环比劣化 >10% ⇒ `quality_regression`（发送点在本工具，门禁 E2 要求）。
- **评分只披露不阻断**：质量是渐变指标，用它当准入门会把夜间批处理挡在门外。

### 灾备演练（§13.4）
- `tools/dr_drill.py`：治理库与 raw 各一次「恢复 → 服务验证 → 全程计时」，记录落
  `data/run_state/dr_drill_log.jsonl`（含 `rto_target_h` 与 **honesty 字段**：本地演练
  验证的是**恢复路径可行性**与实操耗时，不等价于灾难现场 RTO 达标证明）。
- 首跑实测抓到两处自我欺骗：① 治理库校验原本读的是**生产库**（未验证恢复副本）→
  改用 `REG_ORCH_GOVERNANCE_DB` 注入并 reload，台账记 `api_is_restore_copy: true`；
  ② raw 演练原本把 **480MB** 产物复制进 `reports/_dr/`（演练自身成了仓库负担）→ 改为
  **原地只读**校验 + 备份源优先（无备份时显式标 `source_kind: working_copy`，不谎称验过备份）。

### ADR 与纪律
- **ADR-0012 入库**（`docs/adr/0012-SLI-SLO与灾备.md`）：SLI/SLO 定义与阈值、错误预算规则
  （预算耗尽 → **建议**冻结非必需变更，裁决权在人）、RTO/RPO 分级表（对齐 §6.3.4 产物分级）、
  季度演练制度、六维口径陷阱、**明确不做清单**（OpenTelemetry/atheris/TaskGroup/全仓变异
  测试/uv-poetry/文档门户/质量评分自动阻断——各带复发条件）。
- 三个新工具按**引导纪律**实现：`from bootstrap import bootstrap`（不自写 `sys.path.insert`
  ——门禁 `gate_import_bootstrap` 判据「tools/ 注入数只减不增」当场拦下），运行方式为模块方式。
- 手册定时表随 `notify.sli` 段变更同步（`python -m tools.gen_schedule_doc`；S4 判据发现漂移）。

### 验证
817 passed / 2 skipped、**30 道门禁 PASS**、ruff 0、mypy 0、bandit 0 问题。

## [Unreleased] 2026-10-15 — W5 首批：供应链安全（锁/CVE/SBOM/登记簿）+ 新门禁 + J10（第七十二批）

> 方案 §13.2 依赖管理与供应链安全（P0）+ §9.2 的 pip-audit/bandit 接线；ADR-0011 入库。
> 门禁 29 → **30**（新增 `gate_dependency_audit`）；新增判据 J10（README 门禁计数 ≡ ALL_GATES）。

### 依赖治理四件套
- **`requirements.lock` 入库**（115 条）：`tools/gen_lock.py` 从 `pip freeze --all` 生成、
  稳定排序（可评审）、剔除本地路径/VCS 直连；`--check` 供门禁 D1 与 CI 作业。
  **锁强制纯 ASCII**——第三方工具（pip-audit）以 **locale 编码**读 requirements 文件，
  含 CJK 的锁在 GBK/cp936 下直接 `UnicodeDecodeError`（rc=1 且**零行输出**：若不察会被
  误读成"扫描通过、无漏洞"，**比不扫更坏的失效模式**）。
- **漏洞扫描**（门禁 D4）：`pip-audit -r requirements.lock`，high/critical **零容忍**、
  medium 披露；工具缺失或网络不可达 ⇒ **SKIP 披露而非 FAIL**（不把网络故障误报成供应链
  问题）。首跑结论：**115 包零漏洞**。
- **SBOM**（门禁 D5）：**裁定改由 lock 直生 CycloneDX 1.5**（`tools/gen_sbom.py`）——
  syft 未安装、需外部二进制、且会扫全盘（含 `external/` 的 13GB 权重与 venv），而依赖
  证据的权威来源就是锁；lock 直生可离线可复现，SBOM↔lock 用 sha256 断言。
- **直接依赖登记簿** `config/dependency_policy.yaml`（门禁 D3 双向断言）：每个直接依赖
  绑定 ADR 编号或 why。**首建即查出 3 个影子依赖**——`pytest-timeout`（pytest 入口
  `--timeout=900`）、`bandit`、`pip-audit` 长期在用却从未声明（换机即失配、审计无从下手）；
  已补进 `pyproject` dev extra 并重新冻结 lock。非 pip 安装的语义工具（DeepKE/SignalGraph/
  docreader/ParadeDB）单列 `non_pip` 记录"为何不在依赖里"。

### SAST（bandit）分层：真问题修、良性类带理由跳过
- 首跑 444 项里绝大多数是噪音，但**挖出两个真问题**：
  ① 编排器以裸 `python` 自重启（PATH 可能先命中另一解释器 ⇒ 台账命令与真实运行体不一致）
  → 改 `sys.executable`，并加**门禁 D7**（AST 断言：子进程 argv 可执行位不得是裸
  `python`/`py`）机器守护，防止回潮；
  ② `vector_store` 的 SQL 串带**无插值 f 前缀** → 去掉 f 前缀。
- 逐处核实后做**类级跳过**（每条附理由 + **复发条件**）：B101/B102/B103/B105/B110/B112/
  B314/B310/B311/B324/B404/B405/B406/B603/B607/B608。未列出的检查项保持阻断。
- **`trust_remote_code`（B615）受控风险**：自定义模型架构（Youtu UTUConfig）必需，但会执行
  模型目录内的建模代码 ⇒ 风险面收敛三条并写死复发条件（本地已审核目录 + `local_files_only`
  + manifest 登记许可状态）；改为直接 Hub 加载即失效并须改为阻断。
- 实测坑：`[tool.bandit] exclude` **不被 bandit 解析**（只认 `skips`）——排除项的权威落点
  是命令行 `--exclude`（已写入 ci_check），配置里明确标注"不视为生效配置"。

### 新门禁与新判据
- **`gates/gate_dependency_audit.py`（第 30 道）**：判据 D1 锁与环境一致 / D2 依赖可解析 /
  D3 无影子依赖（双向）/ D4 CVE 扫描（分级）/ D5 SBOM 同步 / D6 锁的卫生 / D7 解释器路径。
- **判据 J10**（`gate_config_integrity`）：README 里所有「N 道门禁」声明 ≡ `len(ALL_GATES)`
  ——此前 8 处手写「29 道」且**无任何机器校验**，漏改即文档漂移。
- `ci_check` 新增三作业：`lock` / `sbom` / `bandit`（工具缺失自报跳过，不产生假红）。
- 新增测试 `tests/test_dependency_audit_gate.py` 9 项（D3 双向/缺 why、D7 正负例、
  SBOM 同步、锁 ASCII、真实仓全绿）。

### 验证
808+ 用例全绿（本批 +9）、**30 道门禁 PASS**、ruff 0、mypy 0、bandit 0 问题 0 告警。

## [Unreleased] 2026-10-15 — W4 L1：collectors 按源建子包（源内四分层）（第七十一批）

> 方案 §7.9.2 L1：`modules/regulatory_scrapers/collectors/` 由「单层拍平 + 源前缀命名」
> 改为**按源建子包**，源内按职责分层命名。**纯结构变更**（git mv + import/argv 重写），
> 零业务逻辑改动；边界纪律见 §7.9.4（结构思想借鉴 refactor/，代码全部来自本仓现行脚本）。

### 结构
- 25 个采集脚本 `git mv` 入 5 个源子包：`gov/`（collector/parse/attachments/zhengceku）、
  `mof/`（collector/attachments）、`nfra/`（collector/common/fetch_lists/fill_details/
  fetch_attachments/attachments_extract/weekly）、`pbc/`（collector/parse/backfill/recover）、
  `supp/`（ingest/ingest_local_dir/ingest_batch/normalize/parser/ocr/sanitize/extract_doc_text）。
- **双形态 import 纪律**：每个脚本既要能包形态加载（`…collectors.<src>.<role>`），又要能
  脚本形态独立启动（编排器以子进程调用的真实方式）⇒ 兄弟 import 一律「相对导入 + 裸名兜底」；
  多符号 re-export 改为「模块对象 + 显式赋值」（名单只维护一份，杜绝两形态名单漂移）。
- **接线同步**：sources.yaml collector 字段改两级点号（`collectors.gov.collector`）+
  `config.loader.collector_module/collector_path` 解析（点号→路径）；编排器 8 处 argv；
  triggers.yaml ingest argv；`produced_by` 模板；`build_east_backlog` importlib 路径；
  `modules/_manifest.json` 5 条 independent 登记路径；门禁 `_chain_invoked`/
  `_manifest_independent` 改点号逐段比对（五源同名 `collector.py` 不互相冒名）。
- 删除零消费方配置字段 `weekly_orchestrator`（自 ee3e535 引入后**零读者**，幽灵注册点；
  weekly 编排身份以链上 STEP 为唯一事实源）。

### 顺带修复的实质缺陷（非纯结构）
- **`nfra/fetch_attachments.py` 潜伏断裂**：`sha256_of` 在 B2 批（死代码清理、与 SSOT
  `io_atomic.sha256_bytes` 重复）已从 `attachments_extract` 删除，但**本文件两处活调用点与
  re-export 名单未同步** ⇒ 包形态与脚本形态**均** ImportError/NameError（链上 nfra 附件
  步骤一跑即炸）。现直接绑 SSOT 实现。此前**双侧均无测试覆盖**，故未被发现。
- **`pbc/recover.py` 幻影路径**：数据基准原为 `collectors/`（其 `data/`、`backups/` 子目录
  **早已不存在**⇒ 输入输出均为幻影，脚本在「备份不存在」检查处即退出）。现基准改为模块
  数据根 `regulatory_scrapers/`，且恢复产物**另存** `pbc_recovered.jsonl`（不覆写在线
  `pbc_laws.jsonl`——本脚本重写整份记录，误指向活库即数据事故）。
- **仓根引导深度**：迁子包后上溯层数 3→4（23 处 `_GUIDE_ROOT`）、模块根 dirname 2→3 级
  （18 处）；原写法迁一层即静默失效（`import paths` ModuleNotFoundError）。
- **生成器环境噪声入文档**：`gen_flow_map` 把运行时探针（语义工具可用性）写进 README 受管
  块，环境漂移被误读为回归 ⇒ 标注为「生成时点实测快照」并指明接线事实以 8.2 命令列 +
  `semantic_tools.json.wired` 为准。

### 新增测试（36 项，此前该维度零覆盖）
- `tests/test_collectors_dual_form.py`：19 项包形态（模块可导入 + re-export 符号真实存在）
  + 15 项脚本形态（`--help` 可起）+ yaml↔文件↔编排器 argv 三方接线一致性 2 项。
  **死符号与引导层数错位只在此暴露**（两者都曾真实发生）。
- golden 重录（`chain_dry_run.txt`：7 行差异**仅路径形态**，零行为变更）。

## [Unreleased] 2026-10-15 — W4 §7.10 目录 SSOT 收口 + §7.2 A 类死代码清零 + J9 门禁（第七十批）

> 接第六十九批，收口 W4 最后两项结构性任务（方案 §7.10 目录体系 / §7.2 死代码专项）。
> 验证：全量 pytest 待本轮末复跑、**29 道门禁 PASS**、ruff 0、mypy 0（CI 口径）。

### W4 §7.10 目录体系 SSOT 收口（问题 1/2/3/4/5/6/7）
- **paths.py 补八个顶层常量**（EXTERNAL/TESSDATA/LOGS/BACKUPS/ARCHIVE/EXPORTS/GRAPHIFY_OUT/DELIVERABLES）：
  全仓自拼点改引（retention/audit_health/graphify_sync/graphify_offline_html/governance_sync/
  gov_collector/nfra_collector 等）——「目录一调整漏改方」的静默分叉风险消除。
- **tessdata 迁入 `external/tessdata/`**（同卷 rename，字节级核对一致）：ocr.yaml 默认值 +
  doctor 检查项同步；**顺带修复两处潜在缺陷**——supp_ocr_pdf/pbc_recover 原指向
  「模块内 tessdata」（该目录从不曾存在，内联 Tesseract 回退实际从未找到语言包）。
  四道门禁跳过清单的仓根 `tessdata` 死条目清理（external 已覆盖）。
- **交付库更名 `docs/reports/` → `docs/deliverables/`**（案例一命名撞车消除；docs/= 人类文档根，
  与 docs/adr/ 并列）：15 处代码引用 + README/ADR-0013/manuals 引用同步；`.gitignore`
  仓根 `tessdata/` 死规则清理。
- **reports/ 三区分区**（refactors/manuals/scratch + 台账类留根）：140 个 .md 迁移
  （62 批次报告 + 程序文档 → refactors；6 手册 → manuals；41 底稿 → scratch；机器台账留根），
  284 处引用重写，**零残留校验通过**；分区口径成文 `reports/README.md`
  （_snap/_wiki_sources「待人工确认」项按机器产物结案）；`gen_schedule_doc.MANUAL`
  同步（S4 判据derive 点）。
- 修复链等价 golden 的**数据依赖泄漏**：timeliness 步骤按日期选最新清单令 dry-run 指纹
  每日漂移（10-15 实证 FAIL）——归入「汇总文件名 → 占位符」归一化类。

### W4 §7.2 A 类死代码清零（21 → 0）+ supp_ingest 语义降级 + 新判据 J9
- **退役 13**（三台阶之「移入」）：supp_mapper（幽灵注册点）/supp_downloader/supp_fix_raw_factual/
  pbc_merge_scrape/pbc_serve/pbc_run_batched/pbc_fix_doc/nfra_seed_cache/nfra_prefetch/
  nfra_validate_cache/rfn_lookup/source_stats/build_clean_index（纸面产生方——
  `produced_by` 改指实际生产者 `get_clean_index`；README/脚本数据流/包 docstring 三处
  「被 __init__ 调用」失实断言修正）。入 `<module>/retired/` + README 备案（原因/日期/替代物），
  观察一个月检周期后删除。
- **独立登记 8**（`modules/_manifest.json` 新建）：supp_parser/supp_sanitize/supp_ingest_local_dir/
  pbc_recover/sync_three_modules/dump_external_articles/dump_related_systems（各带 independent_why）。
- **supp_ingest 语义降级**（R-8）：sources.yaml 标 `collector_kind: ingest`（原为纸面 collector——
  登记 13 天零执行）；`config.loader.collector_kind` 受控两类；`gate_sources_config` 判据 2
  **分级**：collector 须有编排器执行证据（argv/produced_by），ingest 只断言模块存在 + 独立登记。
- **新判据 J9**（gate_config_integrity）：modules 脚本三态归属闭合——chain（生产引用：
  import/argv 形态，tests 不计）/ independent（清单登记）/ retired（零可执行引用 + README）。
  9 项正负例单测（`tests/test_j9_modules_manifest.py`，临时目录猴子补丁构造失态场景）。
  audit_health 与 J9 的分工成文（函数级死代码披露 vs 脚本级归属阻断）。
- pytest 新增 `contract` 标记注册（§9.3 契约类用例分层）。

## [Unreleased] 2026-10-15 — W4 数据层强化 + pgvector 接线 + ADR 制度化（第六十九批）

> 接第六十八批（W0-W3 + W4a）落地 W4 剩余出口判据（方案 §6.3~§6.5）与 ADR 制度化（§9.4 部分）。
> 验证：**754+ 用例全绿**（W4 新增 11 项）、**29 道门禁 PASS**、ruff 0、mypy 0、`ci_check --fast` 5/5。

### W4 §6.3.1 raw 纳入水位体系 + resume 第三条件（D-16）
- `GOV_ARTIFACTS` 增 `raw:{src}`（观察型，stage 0 采集后登记——**先于 clean 的输入解析**，
  否则 cleaned 声明的是上轮 raw 版本、边恒 `ok_within_round` 噪声）；`cleaned:{src}` 声明
  `inputs: ["raw:{src}"]`——「链外 raw 更新 → clean 不跳」成为机器可判面。
- `_resume_plan` 第三条件**并行计算、只披露不生效**（方案风险控制：判据切换先并行验证一周）：
  文件级新算 depends_on 版本 vs 上次执行记录版本（watermark.inputs_json），新增信号只有
  一类——**文件已变但水位未更新** = 链外写方；差异入 `audit_log` 台账；
  `REG_ORCH_RESUME_V2=1` 显式开启（默认关）。合法再生产（水位已跟进）不误判。
- 新增 `_artifact_paths()` 产物→路径**唯一映射**（登记与第三条件核验同源，防两处定义漂移）；
  全部 `_wm` 调用点与 `_wm_observations` 改引。
- 台账实证测试：`tests/test_w4_data_layer.py`（链外改写 → v2 剔除 clean:gov + 证据披露）。

### W4 §6.3.2 双轨决策（ADR-0013）
- 消费面盘点（code-explorer 全仓实证，文件:行号 级证据）：**cleaned CSV 有 10 处生产读点
  + 3 处存在性/sha 依赖 + 1 条活契约**（recall/相关性/时效核验/漂移核验全在链内读 latest）
  → **保留每轮生成**；**clauses MD 程序读取方 0、契约 0、门禁 0、人工报告数据源引用 0**
  → **默认关闭**（`build_clause_index(with_md=False)`；`run_clean_pipeline --md` 按需开启，
  可逆不删历史）。「人工审阅是否真实发生」列为待人工确认项（旗标翻转是一行改动）。

### W4 §6.3.3 .tmp 治理 + 发布版本史
- `fs_lock.sweep_stale_tmp()`：链启动清理超过 24h 的崩溃残留 `.tmp_*`（删除前打印清单；
  写入侧 except 清理 + os.replace 保证正常路径零残留——实测全仓 0 个）。
- retention 增 `published_tmp_ext/int`（keep_days=1 + delete；`_plan_age` 增 include_glob
  收窄——无收窄会误删发布目录正式件）。
- `base_publish.append_publish_history()`：每次发布追加一行到 `publish_manifest_history.jsonl`
  （manifest 自身 sha + 各件 sha/计数 + 时间）——条款级变更追踪与审计回溯的凭据。

### W4 §6.3.4 / E-1 产物分级存储（ADR-0007）+ ADR 制度化
- 新建 `docs/adr/` + README 索引（13 条）；落地 **ADR-0007**（四级形态 L-a 文件事实源 /
  L-b SQLite 索引 / L-c 治理库 / L-d PG 可选门槛 + 四条引入门槛；治理库 28.8MB 远低于
  50MB ⇒ 无需 PG 迁移的证据记录在案）；README §3.4 数据字典补对应章节。
- 补录 ADR-0001~0006 / 0008~0010（既有选型，证据取 CHANGELOG 与批次报告）；
  ADR-0011/0012 随 W5 落地。

### W4 §6.4 pgvector 接线（语义检索视图）
- 新工具 `tools/semantic_search.py`：17 项交付物 + 发布件记录（限量）嵌入 →
  `vec.delivery_docs/delivery_records`（只读消费；`vector_store.ensure_table` 表自供给）；
  降级链 pgvector→sqlite_vec→none 全部 rc0 披露（增强层纪律）。
- **链步骤 `semantic:search`**（STEP_ORDER 31→32，插在 analysis:gen 之后——索引含交付物
  终态）；`doctor` 增 pgvector 服务检查项；BENCHMARK §6.7 披露「可用性 + 与 FTS5 的
  A/B overlap（同索引子集，mean_jaccard）」；`semantic_tools.json` pgvector `wired: true`。
- A/B 公平性修正：FTS5 侧必须**过滤到同一索引子集**再取 top-k（否则全库 vs 子集的
  overlap 恒低、度量失真）。

### 本轮发现并修复的回归/缺陷（均已固化）
- **P-5 回归（mypy 双模块名）**：新测试 `from tools import …` 使 `mypy .` 报
  "Source file found twice" 中止全仓检查 → 改裸名导入（conftest bootstrap 已注入）。
- **gate_config_integrity 环状导入**：五拆后子模块 `from . import ROOT` 形成环，mypy
  `[has-type]` 阻断 → 新建 `_common.py` 承载共用件（子模块一律 `from ._common import …`）。
- **golden 盘符大小写**：regen 时 cwd 小写盘符 vs 子进程输出大写盘符 → 归一化漏匹配，
  golden 混入机器路径（gate_hardcoded_paths 拦下）→ `_normalize` 改大小写不敏感。
- **N-3 回归**：新测试含 cleaned 快照日期字面量 → 改动态生成。
- **HTTP 方向校正（诚实记录）**：W4a 实作与方案 §6.1 原文相反（http.py/urllib 成为单栈，
  robust_get 为其门面）——按「移动+适配不重写」纪律，小面是吸收 robust_get 而非搬迁
  http.py 的 382 行差异化能力；方案 §6.1/权衡 #6/ADR-0009 已同步校正。
- **refactor/ 归属方转交登记**：远程提交（其他用户）引入的明文数据库连接串（2 处
  CRITICAL）被 gate_secret_scan 拦下 → 按项目纪律排除出检查范围（与 mypy/ruff 同口径）
  + `reports/refactors/refactor归属方转交登记_20261015.md` 转交（不代改）。

## [Unreleased] 2026-10-13 ~ 10-15 — 架构重构五波落地（W0 缺陷修复 → W3 领域层 + W4 共享库收敛启动，依据 `reports/refactors/项目最优重构方案_20261012.md`）

> 两条主线：**① W0-W3 架构波次全部落地**（编排分层 / MCP 门面 / 领域层扩展 / 三处静默失效修复）；
> **② W4 共享库收敛启动**（HTTP 单栈 / 公共原语 SSOT / 三个千行级大文件包化拆分）。
> 验证：**754 用例全绿**（2 skipped）、**29 道门禁 PASS**、ruff 0、mypy 0 error（全仓 CI 口径）、`ci_check --fast` 5/5。

### W0 · P0 缺陷修复（正确性止损，24 → 26 道门禁）
- **修复三处「静默失效」实证缺陷**（门禁/单测全绿但真实运行仍断）：
  ① `modules` standalone 脚本的 `from config.exitcodes import ExitCode` 须在**仓根引导之后**（曾致 apply 5 源全 FAIL）；
  ② `source_url` 校验语义分级（带 scheme 非 http(s) → error；无 scheme 来源标注 → warning）——supp 源合法承载本地路径，严格 `^https?://` 会误判阻塞数据；
  ③ `token` 移出 `doctor` QUICK_IDS（核验步骤级依赖，非 run 主链必要条件；`run --no-scrape` 不被 token 缺失阻断）。
- **新增 2 道门禁**：`gate_condition_observers`（T4：条件触发观察对象闭合/可达/同源）、
  `gate_dynamic_step_coverage`（R3：`{src}` 模板步骤运行时五源覆盖 + 连续成段，台账断言）。

### W1 · 编排层重构（STEP_ORDER → STEP_LAYERS）
- 31 步平铺链升级为**分层声明式编排**：步骤的执行性质/失败语义/依赖/条件/增强标记全部机器可读，
  作为 README §8、MCP 编排描述、多体系频度调度的**单一事实源**；STEP_ORDER 由 STEP_LAYERS **派生**。
- `tests/test_step_layers.py` + `tests/test_chain_equivalence.py`（golden 逐行比对 `--list-steps`/`--dry-run`，防漂移）。

### W2 · MCP 门面（26 → 27 道门禁）
- 新建 `mcp_server/`（schema 生成 + 传输 + 鉴权 + 审计四件事，**零 SDK 依赖**）+ `commands/mcp.py`；
  12 个只读 tool + 3 个资源，写能力审计绑定。门面必须薄——业务逻辑仍在 interfaces/modules。
- `gates/gate_mcp_schema.py`：schema 派生一致性 + 写能力审计 + E-4 资源齐备 + 零硬依赖。

### W3 · 领域层扩展（27 → 29 道门禁）
- 新增 `modules/reg_frameworks/`（多监管数据体系建模：数据表/数据项/条款↔数据项映射）与
  `modules/implementation_audit/`（落地执行检视：执行记录 × 作证材料 × 制度条款的差距）——
  完全遵循既有模块范式（MODULE_SPECS 5→7 + interfaces api + 契约 + 门禁 + worklist 联动）。
- 7 契约锚点（模块级 `*_FIELDS` 元组）+ contract_manifest 登记；7 个新受控域；cli 19→21 命令；
  `std_lib/common_lib/tabular.py` 共享表格原语（消除跨模块 import）。

### W4a · 共享库收敛启动
- **HTTP 单栈**（**方向校正**，见 ADR-0009 与方案 §6.1 校正注）：最终形态为
  `http.py`（urllib `AdaptiveHttpClient`）为**无会话 GET 唯一实现**——原
  `crawler_common.robust_get` 实现并入此处、crawler_common 改 re-export 门面，
  `common/ratelimit.py` 退役（退避公式 SSOT 落 http.py）。动因：http.py 已承载全部
  差异化能力（UA 轮换/冷却/代理池/流式，382 行成熟代码 + 自检），"移动 + 适配不重写"
  的小面是吸收 robust_get（约 60 行函数）而非反向搬迁；分层保留 `gov_collector.
  RobustSession`（requests Session + 连接池 + POST——urllib 无连接复用对应能力，
  gov 千页级采集改 urllib 是性能退化）。默认参数逐项对齐 + 两处加固向差异
  （UA 池升 8 条 / decode_bytes 解码）由 `tests/test_http_unification.py` 对等锁定。
- **公共原语 SSOT**：sha256/normalize_date/norm_docno/write_jsonl/atomic_write 全仓改引唯一实现；
  `gate_no_duplicate_libs` 扫描模式扩展 `def _?(norm_title|...)` 堵私有名绕过 + 实现体相似度检测（>0.8 告警不判死）。
- **千行级大文件包化**（与 governance_store 拆分同构：包 + 门面，import 路径不变）：
  `governance_store.py`（1507 行）→ `governance_store/`；`relations.py`（1081 行）→ `relations/`；
  `gate_config_integrity.py`（1407 行）→ `gate_config_integrity/`（按判据族五拆）。
- **拆分类门禁的环状导入修复**：`gate_config_integrity` 新建 `_common.py` 承载公共基础设施
  （子模块一律 `from ._common import …`，禁止回头 import 门面）——环状导入运行期可解但 mypy `[has-type]` 阻断。

### 本轮发现并修复的回归（均已在测试/门禁中固化）
- **P-5 回归**：新测试 `from tools import run_production_refresh` 使 mypy 全仓走查双模块名冲突
  （"Source file found twice"）直接中止检查 → 改裸名导入（conftest bootstrap 已注入 tools）。
- **flowmap 漂移**：架构变更后 README §8 链路总图未重生成 → `gen_flow_map` 重注入（173 行受管块）。

## [Unreleased] 2026-10-08 ~ 10-12 — 桌面干扰根治 + 声明面/死代码清剿（第五十六~六十五批，回链 `reports/refactors/重构执行报告_*.md`）

> 两条主线：**① 用户体感故障（复制粘贴失效/被抢占）的真凶定位与根治**；
> **② “声明面先进、执行面缺失”的系统性清剿**（未接线脚本 / 死代码 / 未接线模型 / 调度失实 / README 失实）。

### 真凶定位（第五十六~五十八批）
- 新建 **Win32 活体监控器**（剪贴板序列号 + 持有者 + 前台窗口归属，250ms 采样）⇒ 实测**系统剪贴板每 2.5~3.5s 被清空**
  （持有者 `Idle(0)`/`soffice.bin`）✗；**A/B 对照**证明「每次 LibreOffice 转换 = 确定性 1 次 `EmptyClipboard()`」。
- **假设被证伪并如实记录**：`SAL_USE_VCLPLUGIN=svp` 无法阻止该行为（Δ 仍为 1）✗ ⇒ 转机制隔离路线。
- 排除性结论：全仓**剪贴板 API 0 处、输入自动化 0 处** ⇒ 真凶在**外部程序**；前三轮修复（COM 门禁/窗口隐藏/进程误杀）全部打偏 ✗。
  自查纠正：批 56 引入的「PID 差集强杀」会**误杀用户 LibreOffice**（剪贴板内容由源进程持有）⇒ 改**按我方隔离 profile 精确归属**。

### 桌面干扰**四重机制**（第五十八~六十五批）
- **独立窗口站隔离**（`std_lib/scraper_std/win_station.py`）：`CreateWindowStation`+`CreateDesktop`+桌面继承式启动；
  自证 `--selftest` 私有站内 `EmptyClipboard()` ⇒ 全局 **Δ=0**；fail-safe（隔离不成立即**放弃转换**）。
- **剪贴板保全护栏**（`std_lib/scraper_std/clipboard_guard.py`）：转换前快照、转换后**原样恢复**（仅文本/文件列表；
  未保格式日志明示）⇒ 用户内容**永不丢失**；验收以“金丝雀文本原样存活 = True”。
- **内容哈希缓存**（`doc_convert`）：键 = 输入字节 sha256；命中即**不启动 LibreOffice** ⇒ 零窗口
  （7.3MB 含表 `.doc`：102s/Δ=194 → **0.02s/Δ=0**）。
- **用户活动让路**（`std_lib/scraper_std/user_activity.py`）：`GetLastInputInfo`；用户在用则不开始转换
  （上限 `RCO_QUIET_MAX_WAIT`；**fail-open** 绝不阻塞管线）；与批 58 优先级让路共用 `RCO_YIELD_TO_USER`。
- 配套：全局隐藏子进程（`proc.py`，唯二引导点 `bootstrap.py`/`scraper_std/__init__.py`）、链内 `BELOW_NORMAL` 让路、
  `PYTHONUNBUFFERED=1`（更正“mof 停滞”＝子进程 stdout 块缓冲导致的日志假死 ✗）。

### 声明面与接线清剿（第五十九~六十三批）
- **新增第 24 道门禁「执行者存在性」**（`gates/gate_declaration_executor.py`）：E1 `kind=event` 作业 `covered_by` ∈ `STEP_ORDER`｜
  E2 `notify.on` 每类告警有发送方｜E3 `triggers.resume` 解析到真实文件｜E4 manifest 归属明确。
- 补齐：`{vault}` 供给（`ctx_vars()` + 缺值**显式失败**⇒步骤跳过）／`resume` 消费（`resume_path()`）／
  `notify.on` **四类告警全部有发送方**／`run --triggers` **只跑链外**（`CHAIN_TRIGGER_IDS` SSOT）／`schedule.yaml` event 作业补 `covered_by`。
- **T7 死代码清剿**：28 项零引用符号/常量/`__all__` 条目/未用 import 清理（逐条 AST 校验，0 跳过）；5 个游离脚本标注特殊工具。
- **W-AI 闭环**：legacy `.doc` 表格轨恢复并实证（7.3MB 含表样本：**115 张表** / `table_structured` 115 项 / 321,233 字符）。

### 一致性守卫（第六十六批）
- `tools/audit_health.check_readme_consistency` 增验：**步骤数**（复用门禁 `_step_order` SSOT）、**env 反向核验**
  （代码用到 ⇒ README §6.3 必须记载）、关键路径补 `.githooks/post-commit` ⇒ 文档漂移**由机器拦截**。
- 本轮修正 README 漂移：门禁数 4 处 **23→24**、步骤数 **30→31**、新增 §5.4/§6.3（原 env 仅记 1/18 ✗）。
## [Unreleased] 2026-09-24 ~ 09-26 — v2 全链路重构（九批，回链 `reports/refactors/重构执行报告_*.md`）

> 依据《全链路重构方案_修订版_v2_20260926.md》落地，把采集→清洗→分类→对齐→起草→交付全链路收敛为
> 可审计、可重建、可回归的单一数据管道。九批提交 `16e3c4f`…`427516b`。

### 第一批 · P0 骨架 + P1-1 校验假成功修复
- P0-1 清单/退出码 SSOT、P0-2 `bootstrap.py`（两类路径注入）、P0-3 `paths.py` 派生、P0-4 依赖声明补全、
  P0-5 `tools/_manifest.json` + `tools/retired/` 退役隔离、P0-6 新门禁（**18→22**）。
- **P1-1 校验假成功修复**：清洗校验失败记录隔离落盘（`.quarantine.jsonl`）+ 失败率阈值（rc=2），不再"照常交付"。

### 第二批 · 水位/待办队列/契约消费/语料分层
- P1-2 水位 `round/stage`、P1-6 `worklist` 待办队列（8 类 kind）、P1-4 契约清单消费、P2-3a 语料分层、R2/R7/R8 清理。

### 第三批 · interfaces 收口 + 日志与失败语义
- **P1-3 interfaces 收口（I-1~I-7）**：`theme_api` 实装（空壳归零）、`protocols.py` 四协议、
  `gate_no_cross_module_import` 判据 D（兄弟模块路径拼接）、接口空壳判据转阻断；timeliness_review/base_publish
  改经 interfaces 读输入。
- **P1-5 日志与失败语义（X3/X4）**：`common_lib/logging.py` + 生产脚本 print→LOG；`_run` 增 `exit_code`（语义化）/
  `stderr_tail_len`；生产脚本日志化判据④。
- 修复 `bootstrap("all")` 缺 modules/ 父目录注入（包形态导入死代码）、门禁计数改只数代码行。

### 第四批 · 调度/触发事实源 + 统一执行入口 + 投放区
- P2-2 `config/schedule.yaml`（调度唯一事实源）+ `gen_schedule_doc.py` 反向生成运行手册；P2-1 `config/triggers.yaml` + TriggerRunner。
- P2-6 统一执行入口 `cli.py run/doctor/status/schedule`；P2-3b `data/inbox/` + `inbox_registry.yaml` + `inbox_scan.py`；
  P2-5 六类决策项接入 worklist。

### 第五批 · 数据生命周期 + 测试/CI + 退役收尾 + 顺序解耦
- P2-3 `retention.py` + `archive/`；P2-4 测试与 CI（覆盖率门槛/用例分层）；P2-7 退役收尾判据 J6 转严；
  P3-1/3-3/3-4/3-5 顺序解耦（**gates 移链尾**，`analysis:manifest` 可声明依赖边）；判据 **U/V/R** 升为门禁。

### 第六批 · 审查驱动收口（P3-2/R3）
- `from __future__` 100% 补齐、owned 层退出码/异常收敛、`dedup_key` 唯一性校验接线、`ruff format` 自有层；
  `.git-blame-ignore-revs` 登记机械改造提交（R7）。

### 第七批 · D6 接入 + dedup_key 接线 + tools 层退出码收敛
- D6 `relevance_boundary` 接入 `filter_clean_relevance`（worklist 九类 kind 全部有产生方，J7 双向闭合 9/0）；
  `run_clean_pipeline` dedup_key 唯一性校验；tools 层裸整数退出码收敛。

### 第八批 · gate_runtime_hygiene 五判据全阻断 + N-38 + dedup_key 转严格
- **判据①退出码 ②静默异常转阻断**（语义收窄：只统计有顶层引导文件的裸 return、只抓宽泛吞异常）；
  71 处宽泛吞异常加意图声明归零；`common_lib/logging.py` 相对导入修复（N-38）；dedup_key 默认重复即 rc=2（逃生阀）；
  mypy 首跑（owned 层 78 错误基线）。

### 第九批 · 遗留收口
- **mypy 转阻断**：`follow_imports="silent"` 收窄到 owned 层，19 处错误归零（0 error）；`NONBLOCKING=∅`。
- **`gate_clean_schema` 转严格**：删 `LEGACY_WITH_ERRORS`，交付文件含 validation_errors 即 FAIL；隔离率改只披露。
- **计划任务安装**（`cli.py schedule install` 5/5）；N-44（DayOfWeek 大小写）、N-45（`_expand` 五源展开）修复。
- 新发现 N-42（supp 源 4 条草稿，数据治理待办）、N-43（隔离率阈值对小源过严）。

### 口径变更（v2 重构累计）
- 命令 **12 → 18**（新增 doctor/run/status/schedule/triggers/worklist/relations/governance）；
- 门禁 **18 → 22**（新增 gate_watermark/gate_relations/gate_no_cross_module_import/gate_config_integrity/gate_import_bootstrap/gate_runtime_hygiene/gate_clean_schema）；
- 用例 **356 → 487**；交付 **15 → 17**；覆盖率 **45%**；**mypy owned 层 0 error（转阻断）**；
- `tools/ci_check.py` 本地 CI **5/5**（ruff/mypy/pytest/gates/coverage）。

## [Unreleased] 2026-09-21（第二轮）— 时效验证续跑增量 + 全链条刷新

> 承接同日晚间首轮（见下条）。本轮引入**自适应冷却续跑循环**，在网关配额窗口下最大化推进。

### 一、续跑（自适应冷却，41 min / 4 批）

- 日窗口重置后首批放行 **286** 次新查询（gov 断点 645→**973 行 / 成功 463→749**）；
  随后窗口关闭 → 连续 3 批零增益自动收口（`[stop] 3 bursts with no new successful queries`）。
- `authority_backfill` 以**已缓存成功结果**补判 **37 条权威判定**（零新查询——降级态下的有效产出路径）。
- **限流节奏实测**：单窗口 ≈ **286** 次；**240 s 冷却不足以回补**（次批 0 增益），900 s 亦仅部分回补
  → 有效冷却需 **~10 min 级**；据此推算 gov 存量（约 1.24 万）在每日 ~300 次放行下需
  **~40 天**级窗口才能清空 → 建议改为**纳入既有每周一定时任务**（《运行手册_编排与定时》07:00 项），
  而非人工反复触发。

### 二、落地与下游刷新

- `consolidate_timeliness --use-state` → 全量清单 3,727 → **3,901 条**（+174；
  种子 3009 / 新增 892 / 应用 57 / 确认 1192）；状态 valid 3,093 / amended 307 / repealed 379 /
  expired 87 / pending 35；`state 对齐 63`；
- `rfn_backlog --sync-timeliness --apply` → 归属表 **15 行**（键精确）；
- **全链条 22 步全 rc=0**（1,065 s）；`cli.py gates` **18/18 PASS**；`pytest` 全通过；`gen_benchmark` 已刷新；
- **条款产物时效投影**（`apply` 同轮刷新 hook）：gov `valid` 339→**496**、`repealed` 1→**11**、
  `amended` 268→274；五源时效「空」13,116→**12,943**（**-173**，与待核缺口下降一致）。

### 三、残余（外部配额约束，断点在手）

| 项 | 本轮前 | 本轮后 |
| :--- | ---: | ---: |
| `verify_missing` gov | 12,567 | **12,394** |
| `authority_backfill` | 15,258 | **15,084** |
| `classifier_pkulaw_verify` | 7 | 7 |

## [Unreleased] 2026-09-21 — 时效验证续跑（五源）+ 全链条下游刷新

> 任务：对**尚未完成时效验证**的五源数据续跑，并完成该模块数据/脚本变动所需的下游刷新。
> 承接 2026-09-19（当日 gov 遭外部网关阻断）——本轮**网关恢复**，核验家族三脚本并行推进。

### 一、续跑结果（`timeliness_review` 三脚本 + 断点续跑）

| 脚本 | 续跑前 | 续跑后 | 本批新判定 |
| :--- | ---: | ---: | --- |
| `verify_missing`（gov） | 断点 203 行 / 成功 63 | 断点 **645 行 / 成功 463** | **18 条**（valid 14 / amended 3 / repealed 1）+ `nomatch` 442 |
| `classifier_pkulaw_verify`（归属表未查验） | 待查验 72 | 待查验 **7** | **2 条**（`法释〔2013〕14号` valid→amended、`国务院令第765号` pending→valid） |
| `authority_backfill`（非权威来源权威复核） | — | — | **31 条**（nfra 28 = valid 24/repealed 4；mof 1 / pbc 1 / supp 1） |

- 其余四源：nfra / supp 待核 **0**（已清）；pbc 2、mof 547 属**已核验但法宝无同名命中**
  （`nomatch` 为正常结论，**非 backlog**，按设计不写判定）。
- `classifier_pkulaw_verify` 产出经**统一单点写口**落库：归属表同步 **2 条** + 五源 cleaned 三字段 **2 处**。
- **限流特征（外部约束，非代码缺陷）**：单批次放行约 150–260 次新查询后即进入限流，
  由「连续 15 次认证失败自动停止」风控红线收口 → **全程未误写任何判定**（R13 降级纪律）。

### 二、落地与下游刷新

- `consolidate_timeliness --use-state` → 全量清单 **3,727 条**（种子 3009 / 新增 718 / 应用 57 / 确认 819）；
- `rfn_backlog --sync-timeliness --apply`：SSOT **14,796** 条 → 归属表 **3 行**（键精确）；
- 编排链阶段 2 `apply_timeliness_to_cleaned` 五源回写，并经 **2026-09-19 新增的"条款产物同轮刷新"**
  把判定投影进条款产物（gov `valid` 325→**339**、`repealed` 0→**1**；五源时效投影合计 **3,501** 条）；
- **全链条 22 步全 rc=0**（1,374 s）；`cli.py gates` **18/18 PASS**；`pytest` 全通过；
  `gen_benchmark` 已刷新（`state=14797`、`merged=878`）；关系表 5,266 → **5,272** 行
  （时效判定带动废止关系重算）。

### 三、残余（外部配额约束，断点在手可续跑）

| 项 | 残余 | 性质 |
| :--- | ---: | :--- |
| `verify_missing` gov | **12,567** | 真 backlog（其中约 94% 判为 `nomatch` 型） |
| `authority_backfill` | **15,258**（gov 12,577 / mof 708 / nfra 1,429 / pbc 535 / supp 9） | 待配额续跑 |
| `classifier_pkulaw_verify` | **7** | 待配额续跑 |

**一键续跑**（配额恢复后；断点自动续、幂等）：

```powershell
python modules\regulatory_scrapers\timeliness_review\run_timeliness_resume.py --only gov   # 五源编排（gov 最低优先级）
python modules\regulatory_scrapers\timeliness_review\classifier_pkulaw_verify.py --retry-failed
python modules\regulatory_scrapers\timeliness_review\authority_backfill_verify.py --source all --retry-failed
python modules\regulatory_scrapers\timeliness_review\authority_backfill_verify.py --source all --judge-only  # 零配额补齐
python tools\rfn_backlog.py --sync-timeliness --apply
python tools\run_production_refresh.py --no-scrape
```

> 时效运行产物（`verification_state.json` / `*_checkpoint.jsonl` / `时效核验_*变更台账_*.csv` /
> `时效性标注结果清单_*`）按 `.gitignore` **刻意不入库**（数据以本地文件 + 门禁校验为准）。

## [Unreleased] 2026-09-20 — 条款产物六类缺陷修复（F1–F7）+ 三项相邻问题 + 全链刷新

> 用户指令：针对 `nfra_clauses_20260919.{md,jsonl}` 的六类问题排查五源数据、锁定根因并出具修复方案；
> 随后要求**自主推进全部修复至验证通过**（含相邻问题）。排查报告见
> `reports/scratch/2026-09-20_nfra条款产物_01_六类缺陷根因排查与修复方案.md`（§9 实施结果）。

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

> 承接同日「阶段 0 止血 + 阶段 1 治理库骨架」。方案见 `reports/refactors/数据流转与存储交互优化方案_20260917.md` §6。

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

## [Unreleased] 2026-09-18 — 阶段 0 止血 + 阶段 1 治理库骨架（`reports/refactors/数据流转与存储交互优化方案_20260917.md` §6）

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

### 一、纳管（报告 `reports/scratch/依据与废止关系统一抽取_20260914.md` §10.4）

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

### 一、续跑执行（报告 `reports/scratch/五源时效核验续跑报告_20260914.md`）

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

## [Unreleased] 2026-09-13 — 内部制度原件路径治理 P0（`reports/scratch/内部制度原件双份存储与索引漂移分析_20260913.md`）

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

## [Unreleased] 2026-09-13 — 克隆可移植性修复（`reports/scratch/克隆可移植性检视报告_20260913.md`）

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
- 新增/更新：`reports/scratch/项目全面审查报告_20260912.md`、`F-L01_五级分析交付库专项报告_20260912.md`、`运行手册_编排与定时_20260912.md`、各批次遗留项报告。

## [0.1.0] - 2026-09-08

### 新增
- 四仓合一：本仓为唯一演进点（原四仓只读冻结；P0 骨架 → P1–P7 迁移，来源映射见 README §7）。
- 五源采集清洗（39 列契约）、RFN 分类底座链（base→cluster→match→detail→upper）、时效核验（北大法宝 R13 三态）、内部制度摄取/对齐、起草对照、12 道交付门禁、pytest 20 用例、`data_migration_manifest.json` 数据复制追踪。

[Unreleased]: 见 git log
[2.1.0]: 对比 0.1.0 — 见 reports/ 批次报告（F 系列遗留项全落地 + 工程化 P1/P2）
[0.1.0]: P0 骨架初始（2026-09-08）
## [Unreleased] 2026-10-16 — E5 第一层：nfra 附件**纯判定层** + 修 E4 遗留的两处 NameError（第九十二批）

> D-遗留第五批第一步。选址依据（AST 实测）：710 行、**10 函数 0 类**、22 个顶层块、
> 依赖闭包天然分层且**零缺失导入**（用新工具一次扫出，见上一批）。

### 拆出：`nfra/attachment_quality.py`（纯判定层）
`_COMMON_CN`（247 字白名单）+ `quality_flags`。**零外部依赖**（不触网/不落盘/不读磁盘）
⇒ 与 E4 的 `nfra/extract.py` 同构，可用纯函数回归完整锁住，**不需要网络夹具**。
函数体**逐字搬运一字未改**（含原注释）——这是能被完整回归锁住的前提。

### 等价性三重锁定
1. **逐字段比对**：9 组输入（含乱码阈值两侧、空输入、超长文本、**异常组**）⇒ DIFF: none；
2. **异常行为也比对**：`page_lens=None` 抛 TypeError，而 `text=None` 因 `if text:` 为假
   **返回 `[]`**——两者**行为不对称**，是既有行为，写进断言防日后被顺手"容错"掉；
3. **源码逐字一致**：`inspect.getsource` 比对函数体完全相同。
外加白名单集合相等（247 字）、**乱码阈值 1%**、**多标志顺序固定**两条口径入断言。
守卫 `tests/test_nfra_attachment_quality.py`（11 项，含 3 项异常/边界契约）。

### 途中修掉 **E4 拆分的两处真实遗漏**（静态检查才暴露）
| 现象 | 根因 | 性质 |
|---|---|---|
| `nfra/http_api.py:87` 用 `_OfflineMiss`、`:94` 用 `USER_AGENTS` 但**均未导入** | E4 拆分时漏搬（`cache_store` 只搬了 `bind_source_cache`；UA 池的 try-import 未迁） | 🔴 **脚本形态下必 NameError**——回放测试未覆盖该路径，静态检查零命中 |
| `nfra/collector.py:89` F401 `lxml.html` unused | `html_to_text` 已随 E4 迁走，但该 import 是**有意的启动时依赖预检**（缺 lxml 即给安装提示） | ⚠️ 误报。若按 lint 删掉，友好提示会退化成下游深栈 NameError ⇒ 加注释保留 |

**教训**：E4 的回放测试覆盖了 `extract`/`http_api` 的**包形态**，但**脚本形态**下
这两处才会炸——回放测试恰好没覆盖到。
⇒ 拆分后除跑回放，还必须让**静态检查覆盖到被拆出的每个文件**（本次是 ruff F821 抓到的）。

### 规模
`fetch_attachments.py` 710 → **690** 行（第一层）。其余取数/落盘/报表层待第二层，
因其依赖 `sniff_kind`/`_is_ole2_word` 等兄弟模块与磁盘读，**不是纯判定**
⇒ 需按 E1–E4 方式（夹具回放 + 重构前后比对）处理。

### 验证
30 道门禁 PASS、ruff 0、mypy 0；新增守卫 11 项 + 双形态 47 项 + 回放 34 项全绿。
## [Unreleased] 2026-10-16 — 拆分前置检查工具 `tools/split_precheck.py`（第九十一批）

> 动机（实测代价驱动）：E1–E4 拆了四个采集文件（pbc/collector 729→541、
> gov/parse 843→210、gov/zhengceku 802→454、nfra/collector 716→543），每次都在**运行期**
> 才发现「搬代码忘搬依赖」，累计 20+ 处。本批把这些**全部变成运行前的静态检查**——
> 继续拆剩下 6 个大文件前，先把踩过的坑固化。

### 五类历史故障全部覆盖
1. **import 未随迁**（stdlib / 仓内 / 三方**分类**报告）；
2. **模块级引导块与 logger 与路径常量**——**AST 依赖闭包捕获不到**（它们不在任何
   `def` 里）⇒ 拆分后运行期 NameError（E2 的 `LOG` 漏迁即此类）；
3. **try-import 别名**——赋值在 try 内，闭包同样捕获不到（E2 的
   `_unified_doc_number` 即此类）；
4. **跨层共享常量**——复制两份必漏改其一，**不报错只抓错**；
5. 非顶层定义名——须报「不在顶层定义里」，不得静默跳过。

### 可信度证据不是「能跑」而是「能复现真实故障」
用**已拆文件的备份**（`reports/_tmp/*.py`）作输入，实测复现：
- E4 的 `html_to_text` 缺 `lxml`（即那个静默降级为正则兜底、
  `<script>` 不被剥离的隐蔽故障）；
- E2 的模块级 `LOG`、try-import `_unified_doc_number`、
  路径常量 `_GUIDE_ROOT`/`_ROOT`/`_ATT_DIR`。
守卫 10 项，其中 3 项直接断言这三条真实故障可复现。

### 工具自身修正三处判据缺陷（否则不可信）
① `missing_*` 原用 `symbols` 排除，而 Attribute 主名（`lxml.html` 里的
`lxml`）本身就在 `symbols` 里 ⇒ **最隐蔽的一类故障恰检测不到**；
② 未排除本文件顶层名 ⇒ `_PAT.sub` 的 `_PAT` 被当成缺失模块名；
③ 未计入模块级已 import 的名字 ⇒ `import re` 后块内用 `re.escape` 被误报。
修正后仍能复现 `lxml`，且 `re` 误报归零（**零噪声是工具可用的前提**）。

### 设计取舍
**只读、不改任何文件、不代劳判断**——只出「人工检查清单」（缺失 import 分类与行号、
模块级风险、共享常量及其使用方）。守卫含「工具绝不修改源文件」一条。
入口：`python -m tools.split_precheck <file.py> --move <名,...> | --survey`；
J1 登记（清单 54 条）。

### 验证
30 道门禁 PASS、ruff 0、mypy 0、新增守卫 10 项全绿。
## [Unreleased] 2026-10-19 — 修三天连续门禁失败 + gate_fail 告警报文增强（第九十四批）

> 接手时发现 10-17/18/19 **三天各 2 条判据失败**（`reports/_alerts/` 里三条 gate_fail），
> 且告警载荷只有 `{{step, rc}}` ⇒ 收到告警仍要手动跑 gates 才知道挂在哪。
> 本批逐一定位根因并修，同时把告警变成「可直接归因」。

### 修的两处真实失败（都有代码证据）

**① J13.3 `http_cassette 不可导入`（同名包遮蔽，三天连续 FAIL）**

- 现象：`ImportError: cannot import name 'http_cassette' from 'tests'`
- 根因：本仓 `tests/` 是**命名空间包**（无 `__init__.py`），而
  `refactor/collectors/tests/` 是**真包**（有 `__init__.py`）。
  Python 导入规则**真包优先于命名空间包** ⇒ 一旦 `refactor/collectors` 进入
  `sys.path`（生产跑的 bootstrap 会扫到它），`import tests` 就解析到
  那个没有 `http_cassette.py` 的目录。
- 这与判据 **J14（同名模块裸名导入禁令）是同一类问题**，只是发生在**门禁自身**。
- 修法：`_load_cassette_module()` **按文件路径显式加载**
  （`spec_from_file_location`）——**不是** sys.path 注入（不违反
  gate_import_bootstrap 的注入纪律），也完全绕开同名歧义。
- **两级策略**：路径优先；`TESTS_DIR` 下找不到时**回退包导入**
  （测试注入的临时树里只有 `cassettes/`、没有 `http_cassette.py`，
  回退才能保持既有负例语义）。

**② D1 `requirements.lock 与当前环境不一致`（锁从未入库）**

- 现象：三天连续 FAIL，但本机 `gen_lock --check` 返回 0（一致）。
- 根因：`.gitignore:45` 的通配符 `*.lock`（本意为忽略 Python 的
  `.lock` 缓存文件）**把供应链关键件 `requirements.lock` 一并排除了** ⇒
  `git log -- requirements.lock` **无任何记录**（从未入库）⇒
  各环境各生成各的、无共同基准 ⇒ 环境一变 D1 必失败。
  而 **ADR-0011 第 1 条明确要求「`requirements.lock` 入库」**
  （换来换机可复现）⇒ 这是 **ADR 与 .gitignore 的直接冲突**。
- 修法：加精确例外 `!requirements.lock`（不动通配符原意，其他 `.lock` 仍被忽略），
  并把 lock 纳入版本控制。

### gate_fail 告警报文增强（诊断第 1 波的落地延伸）

原载荷 `{{step, rc}}` 无法归因。现在携带：失败判据清单（`gate`/`module`/
`problem_count`/前 2 条 `problems`）+ `run_id` + 一条可直接执行的
`gate_triage` 命令。

**关键实现决策（实测纠偏）**：最初想从 `run_step.note` 解析 FAIL 明细，
实测 note **只有归档提示 + 汇总行**（93 字，无明细）⇒ 改为**主路径读治理库
`gate_result`**（结构化，`commands/gates.py:_archive` 已按 run_id 归档），
note 解析仅作兜底。

### 守卫
`tests/test_j13_shadow_safe_import.py`（7 项）——**在同名遮蔽环境下**验证：
- 环境**真的**被遮蔽（否则后面几条毫无意义）；
- **旧写法必失败**（失态对照，证明修复必要性）；
- 新写法仍成功、判据不报 J13.3、加载器**不做 sys.path 注入**；
- 路径不存在时回退包导入、两级都不可用才返回 None。

过程中修掉一个**测试顺序依赖**：`tests` 是命名空间包，被导入过之后
`find_spec` 从缓存返回旧位置 ⇒ 单独跑绿、整套跑红。fixture 改为
**清 `sys.modules` 里的 `tests` 及其子模块**。

### 验证
30 道门禁 PASS、ruff 0、mypy 0；J13 相关 14 项（新 7 + 既有 7）全绿。
## [Unreleased] 2026-10-19 — T1：定位并消除三天门禁失败的真实根因 + E5 第二层（第九十五批）

> 第九十四批修好了 J13.3/D1 的**表现**，但根因只确认了一个（D1 的 lock 未入库）。
> 本批把 J13.3 的根因查到底，并继续推进 E5 第二层。

### T1：J13.3 的真实根因是**第三方 `tests` 包遮蔽**（不是 refactor）

第九十四批的假说是「`refactor/collectors/tests/` 真包遮蔽」。本批**逐条否证**：
`bootstrap` 不注入 refactor、cwd 无影响、`sys.modules` 无占位、子进程不复现。

真正的证据在历史失败的**错误串**里：它含一个**我没用过的解释器路径**
（`<用户目录>/.workbuddy/binaries/python/`）—— 生产调度用的是 **uv 管理的另一个解释器**
（`.workbuddy/binaries/python/versions/3.13.12`，
而我交互用的是 `Python314`）。用它一跑即复现：

- 该解释器的 `site-packages/tests/` **真实存在**（`test_crf.py` + `__init__.py`）；
- Python 导入规则**真包优先于命名空间包** ⇒ `import tests` 解析到 site-packages 那个，
  它没有 `http_cassette` ⇒ `cannot import name 'http_cassette' from 'tests'`（与历史逐字一致）；
- 该目录是**孤儿**：无 dist-info 声称拥有、`pip show tests` 报未找到、无已装包 import 它
  （典型的误装残留），且**不在 requirements**。

**处置（两层，缺一不可）**：
1. **消除根因**：删除该孤儿目录（已确认无包依赖、内容仅一个无关的 `test_crf.py`）；
2. **保留防御**：第九十四批的按路径加载（`_load_cassette_module()`）不动——
   它让判据对**任何**同名遮蔽免疫（含未来再出现同名包）。
   两招并用：一个治当下的病，一个防复发。

验证：用生产解释器跑 J13 判据 ⇒ `problems: none`（6 个夹具）；
且该解释器下**连旧写法也能导入**（证明根因已除）。

### E5 第二层：`fetch_attachments.py` 690 → 287 行

```
attachment_paths   路径常量层（新）  ATT_DIR / CACHE / DETAIL_GLOB / QUALITY_REPORT / HERE
attachment_fetch   取数层   124 行  http_get_bytes / build_attachment_url + BASE/_SSL_CTX/DEFAULT_HEADERS
attachment_process 落盘层   222 行  process_attachment / process_doc（触网+触磁盘）
attachment_report  报表层   193 行  build_and_write_report / refresh_stats
fetch_attachments  编排层   287 行  main / retry_doc_legacy_offline + classify_entry + 全部 re-export
```

**设计取舍（`ATT_DIR` 跨三层，三个候选）**：
① 留编排层、各层反向 import ⇒ **成环**（编排层 import 各层做 re-export，实测果然炸）；
② 每层各复制一份 ⇒ **必漏改其一**（路径改了不报错、只写错地方）；
③ **下沉为最底层常量模块** ⇒ 依赖单向、单一事实源。✅ 选③。

**`classify_entry` 的处理**：它依赖磁盘读 + 兄弟模块 `attachments_extract`，
**不是纯判定** ⇒ 留编排层，report 层以**形参注入**（调用方显式传），
并留延迟兜底（未注入时运行期 import）⇒ 既不破坏既有 `--report-only` 路径，也不成环。

### 等价性证据
① `build_attachment_url` 6 组输入逐字段比对（含空/特殊字符/中文/缺字段）⇒ none；
② 14 项符号归属 + 编排层 re-export 可取（同一对象）；
③ 8 项常量一致（`_SSL_CTX` 按类型比、`HERE` 按**定义语义**校验——
   备份文件的 `HERE` 值是 `reports/_tmp`，与拆分无关）；
④ 6 个函数源码 `difflib` 比对 ⇒ 5 个逐字一致，
   `build_and_write_report` 的 diff **仅 4 处我有意新增**（形参/docstring/延迟兜底），
   无任何意外变更。

### 过程中的两次自我纠偏（都是校验脚本的错，不是产品的错）
1. 等价性脚本先报 `HERE` DIFF —— 实为备份文件位置导致，改用**定义语义**校验；
2. 报 4 个函数源码 DIFF —— 实为我的 `_body_lines()` 通用剥离逻辑**误删了内容**
   （遇到 `if x is None:` 就进入跳过模式），改为仅对
   `build_and_write_report` 生效；且判定从字符串相等改为 `difflib`
   （`inspect.getsource` 跨模块的行边界细节会让 == 失真）。

### 验证
30 道门禁 PASS、ruff 0、mypy 0；双形态 42 项 + 既有 J13 14 项全绿。
## [Unreleased] 2026-10-19 — 诊断第 3 波：待办积压治理回路（第九十六批）

> 四波诊断里前三波都面向**低频故障**（第 0 波信号源、第 1 波门禁、第 2 波步级），
> 而项目实况是「**低故障率 + 高积压**」：open 641 条、处置仅 3 条、
> 产生速率约 330 条/月 ⇒ **净增长**。本波面向高频积压，核心不是"判得更准"，
> 而是"**让回路持续转动**"：监控 → 判定 → 处置 → 度量。

### 新增 `tools/backlog_governance.py`（J1 登记，清单 55 条）

| 子命令 | 职责 | 写库 |
|---|---|---|
| `growth` | 积压增长监控（open/处置率/按月产生与处置/近 30 天净增/数据跨度），超阈值返回码 `FAIL` 供调度消费 | ❌ 只读 |
| `selfclosed` | 机器判定「自证闭环」项（保守三条件 + 一护栏） | ❌ 只读 |
| `apply` | 受控执行（默认 dry-run；须 `--execute --by --reason`） | ✅ 走唯一写口 |

### 机器判定的边界（关键设计，实测定形）

**只判「自证闭环」一类**，判据是**三条件 + 一护栏**，全满足才判：
1. subject **含**闭环词（`已修复/已处置/已补齐/已修/已落地/已解除`）——
   **句中匹配**，因为实测真实 subject 的宣告不在开头
   （`N-208 已修复并完成历史数据补救`、`写入侧「正文载体混入 attachments」已修复`）；
2. subject **不含**否定语境（`部分完成/未执行/未改码/改法已定/待执行/未清/残余`）；
3. payload **无**待决键（`待裁决/待确认/待人工/待转交/待产品决策/建议`）。

**宁可漏判，不可错判**：漏判的代价是留在堆里；错判是把真待办关掉、
且审计显示"已处置"——**不可逆**。

明确**不**判：主题归属（`rfn_theme_uncertain`，标题不在归属表内）、
相关性边界（需业务口径）、"建议"类（payload 只有 `建议`、无闭环证据）。
这些由 `worklist_triage bundle` 出裁决包人工处理。

### 首批执行（真实数据）

`dry-run` 列出 6 条 ⇒ 执行后 **6 条 dismissed、逐条落 `audit_log`**
（`basis` 记录完整判据，可回溯），`open` **641 → 635**，
`trigger_manual_breakpoint` open **23 → 17**。

### 途中修的 4 处（两处是真实缺陷，两处是我的误判）

| 现象 | 性质 |
|---|---|
| 时区比较：`created_at` 是 `+0800`（无冒号）而 `isoformat()` 给 `+08:00` ⇒ 字符串比较全中 | ⚠️ **我的误判**——实测两种格式都正确（1/2）。真相是**本仓待办历史只有 23 天**（`MIN(created_at)`=09-26），"近 30 天"天然≈全部。已加 `history_span` 披露防后人重犯 |
| `selfclosed` 判 0 条 | 🔴 **真实缺陷**——原判据要求"subject **开头**是闭环词"，而真实宣告在句中 ⇒ 放宽为句中匹配 + 加否定护栏 |
| 测试库缺 `resolved_by` 等列（`OperationalError`） | 🔴 测试自身问题——`_mkdb` 按**真实 DDL** 重建 |
| `_connect()` 不认 `REG_ORCH_GOVERNANCE_DB`（与 `governance_store` 不同源） | 🔴 **一致性缺陷**——两处路径解析已统一，否则测试注入 env 只对一半生效 |

另修 3 处**新工具自身的门禁合规**（判据正常工作）：J1 未登记、
测试内 `sys.path.insert` 顶破注入基线（conftest 已 bootstrap，测试不需自注入）、
裸 `int` 退出码（改 `config.exitcodes.ExitCode`）。

### 守卫 `tests/test_backlog_governance.py`（15 项，含 6 条失态）

判定边界（闭环词只在 payload 不算 / 有 `待裁决` 不算 / 否定语境不算 /
`建议` 不算 / 非 open 不算 / payload 非 dict 不崩）、
cutoff 格式与库内同构、窗口真实生效、数据跨度披露、阈值双向、空库不除零、
dry-run 不写库、`--by/--reason` 必填、执行后逐条落审计。

### 验证
30 道门禁 PASS、ruff 0、mypy 0；新增守卫 15 项全绿。
## [Unreleased] 2026-10-19 — 诊断第 4 波：能力出口（确认台 P9 + MCP 门面）（第九十七批）

> 前三波把「能诊断」做出来了（门禁归因 / 步级取证 / 积压治理），但能力只在 CLI 里。
> 本波解决**出口**问题：让诊断出现在人每天看的地方（确认台）与模型能调的地方（MCP）。

### 第 4 波-A：确认台 P9

新增两个**只读**端点：
| 端点 | 内容 |
|---|---|
| `GET /api/diagnose?kind=gate&name=…` | 门禁归因（复用 `gate_triage`）；无 `name` ⇒ 近 90 天失败清单 |
| `GET /api/diagnose?kind=step&name=…` | 步级取证（复用 `step_triage`），含拓扑双层 `matched_as` |
| `GET /api/diagnose/backlog` | 积压健康度（复用 `backlog_governance`）+ 阈值判定 |

**不复制判据**：三个端点直接调 `mcp_server` 侧的同一份实现 ⇒
CLI / MCP / 确认台**口径必然一致**（这是本仓反复强调的"单一事实源"）。

前端：P1 步骤抽屉里加「诊断这一步」按钮，展示 `matched_as` / 证据项数 /
**怀疑（每条附依据）** / **证据缺口** / 渲染全文。
**降级可辨**：诊断不可用时显示"⚠ 诊断不可用：<原因>"，
**不伪装成"无数据"**——这条坑本仓踩过（曾把内部异常吞成"无数据"，
导致排查困难翻倍）。

### 第 4 波-B：MCP 门面

新增两个**读级**工具 + 一个资源：
| 名称 | 类型 | 说明 |
|---|---|---|
| `diagnose_gate` | tool（`tags=("diagnosis",)`, access=read） | 门禁归因；**无证据不出结论**（未知门禁 `found=False` 且 `category` 为空） |
| `diagnose_step` | tool（同上） | 步级取证；`matched_as` 如实说明台账实例 vs 声明模板 |
| `orch://backlog_health` | resource | 积压健康度（内容派生版本号，可作缓存键） |

**明确不暴露处置入口**：MCP 工具面**没有** `apply`/`dismiss`/`resolve`
类接口——处置必须由人显式发起（对齐 C1「人工处置须留痕可追」）。
守卫里有一条负例专门断言这件事。

### 途中修的 6 处（全是「按印象猜接口」）

| 猜错的 | 真实形态 | 后果 |
|---|---|---|
| `tr.pointers` | 是 `problems: list[Problem]`，指针在 `p.evidence` | AttributeError |
| `tr.gaps` | 叫 `evidence_gaps` | AttributeError（被 except 吞成"无数据"） |
| `tr.rerun` | 叫 `rerun_steps`（tuple） | 同上 |
| `StepTriage.evidence` 当 dict | 是 `list[Evidence]`（含 kind/ok/detail/data） | `.keys()` AttributeError |
| `@tool` 挂 `__mcp_tags__` | 注册在 `registry._REGISTRY`（`dict[str, ToolSpec]`） | 守卫断言失败 |
| `recent_failures` 返回 dataclass | 返回 `sqlite3.Row` | 全空字段 |

**教训**：与本仓记忆里那条一致——**接口以源码为准，不凭印象**。
这 6 处全部由「实测验证脚本」抓出，没有一处是靠读代码发现的——
说明"写完必须真跑"比"写前认真读"更可靠。

### 守卫
`tests/test_mcp_diagnosis.py`（`12` 项）：
工具注册与 `access=read`、**结果与直接调 triage 一致**（不复制判据）、
未知门禁不给类别、证据是原文列表、双层标识受控、怀疑项必带依据、
资源返回 `contents.metrics/verdict`、版本派生、
**MCP 不得暴露处置入口**（负例）。

### 验证
30 道门禁 PASS、ruff 0、mypy 0；P9 三端点实测（`environment` 归因到
`vector_store.py:284`、`matched_as=instance` 7 项证据 3 条怀疑、
`open=635` 2 条告警）；MCP 资源与工具实测可取。
## [Unreleased] 2026-10-19 — rfn 324 条决策处置 + 积压回路入调度 + 工作台三处修复（第九十八批）

### ① rfn 324 条：按业务决策处置 323 条、保留 1 条

用户决策（2026-10-19）：324 条 `rfn_theme_uncertain` 中**仅**
《关于加强外资保险公司与关联企业从事再保险交易信息披露工作的通知》进入归属表，
其余均**不纳入**。

**执行**（新 `decide` 子命令，按 kind 批量 + `--keep-subject` 保留项）：
- `dry-run` **第一次报「保留 0 条」** —— 我的匹配子串
  `关于再保险交易信息披露工作的通知` 与实际标题**不连续**
  （中间有"加强外资保险公司与关联企业从事"）。
  **若直接执行会把用户明确要保留的那条也关掉**。改用唯一短子串
  `再保险交易信息披露` 后 dry-run 正确显示"保留 1 条"。**这是 dry-run 的典型价值**。
- 执行结果：`dismissed 323` / 保留 1 / 未命中 0，逐条落 `audit_log`
  （`basis` 完整记录业务决策原文）。
- `open` **641 → 312**；`rfn_theme_uncertain` open **324 → 1**；
  处置率 **2.58% → 52.58%**。
- **保留项仍未登记进归属表**：登记需要**主题**，用户未指定 ⇒ 本批不代填（见遗留风险）。

### ② 积压回路入调度（`backlog_growth`）

三处同步（门禁 S3 双向守护）：
| 位置 | 内容 |
|---|---|
| `config/schedule.yaml` `jobs` | `id: backlog_growth` / 每日 07:30（06:00 refresh 之后）/ `argv: ["cli.py","backlog","growth","--json"]` |
| `config/schedule.yaml` `notify.on` | 加 `backlog_growth` |
| `std_lib/common_lib/notify.py` | `NOTIFY_EVENTS` 加 `backlog_growth` |
| `config/integrity_policy.yaml` | `notify_on` 白名单同步 |
| `commands/backlog.py`（新） | 薄壳转发（**含裸 int 退出码已改受控枚举**） |

**为什么单独一个事件而不复用 `worklist_aged`**：口径不同——
`worklist_aged` 问"这条放了多久"（单条超龄），
`backlog_growth` 问"越积越多吗"（总量净增）。缺后者，积压增长无人知晓。

**实测**：默认阈值（open≥500 / 净增≥100）下当前 312 条**不告警**（rc=0）；
强制低阈值时 rc=1 且**告警落盘 4 个文件**（md+json × 2 次运行）。
告警发送失败**不改**返回码（旁路留痕，不能让"没送达"伪装成"没超阈值"）。

### ③ 工作台三处修复（P2/P3/P4）

**P3 主题分布（真实根因：字段根本不存在）**
`theme_dist` 原从 `attr_rows()` 取"主题"列，而归属表**没有该列**
（实测 8 列：监管文件编号/文件名称/发文字号/发布日期/文件来源/时效状态/判定日期/编号备注）
⇒ 1162 条全落 `?` ⇒ **图表只剩一根 `?` 柱**，看起来像渲染坏了。
**易混点**：`theme_map()` 是 `T0..T10 → 主题名` 的**受控枚举**（11 条），
拿它查编号 **0/1162 命中**。
修法：新增 `rfn_api.theme_by_doc()`（主题归属表派生的 **编号→主题**，1162/1162 命中），
`theme_dist` 改用它；未归类用可读名 `未归类` 而非 `?`。
实测：**11 个主题、合计 1162**（T1=196 最多）。

**P4 kind 筛选（用户报「不同 kind 不能批量处置」）**
表头加 kind 下拉（带计数），选中后只拉该 kind ⇒ 可整批全选处置。
**关键口径**：`kind_counts` 保持**全量**（若跟随筛选，切到某 kind 后
其它 kind 显示 0，用户会以为"条目没了"）。实测：筛 `relevance_boundary`
得 211 条、`kind_counts` 仍全量。

**P2 契约状态（我第一版判断错了，如实记录）**
我先据"键列表前 10 项"断言 `contract` 没有 `consistent/problems/stat`，
把前端改成按统计字段渲染。**复核发现 `consistent: True` 其实存在**
（第一次只打印了前 10 个键就下结论）。改动保留是因为**信息更丰富**
（覆盖文件/条文/章节/表格结构/降级/兜底/无效/告警/解析方式），
但**不再声称"修复了 bug"**——P2 的原始异常若有，另有原因（见遗留风险）。

### 守卫
`tests/test_p2p3_p4_fixes.py`（13 项）：`theme_map` 是 T 编号（易混点）、
`theme_by_doc` 键是 RFN 编号、**归属表无主题列**（根因守卫）、
`theme_dist` 合计等于归属表条数且无 `?`、kind 筛选收窄、**筛选后计数仍全量**、
`filter.kind` 回显、未知 kind 返空不报错、`contract` 统计字段、
`parse_modes` 存在、调度作业已声明、**argv[0] 必须是 cli.py**（S2 同款判据）、
告警事件三处同步。

### 验证
30 道门禁 PASS、ruff 0、mypy 0；新增守卫 13 项全绿。
## [Unreleased] 2026-10-19 — 前端瘫痪根因 + 待办字段列示 + 入 T4 + 判据停产生 + 正文回填（第九十九批）

### ① 工作台「SSE 连接中 / 页面加载中」——根因是**我上一批埋的前端语法错误**

**症状极具误导性**（这也是它值得判据化的原因）：
- 页面停在 HTML 初始态：`加载中…` + `SSE 连接中…`
- 而服务端**一切正常**：HTTP 200、全部 API 200、
  `/api/events` 有 `event: hello` + `event: ping` + `Connection: keep-alive`

**根因**：在 `web/assets/index.html` 的**模板字符串内部**插入了一段 `//` 注释，
而注释里用反引号标注字段名（``c.consistent``）⇒ **第一个反引号提前闭合了
模板字符串** ⇒ 后面的内容被当成代码 ⇒ `Unexpected identifier` ⇒
**整段 `<script>` 不执行** ⇒ 两个 badge 都停在初始文本。

**定位过程（关键：不猜）**：先写了一个**词法扫描器**，但它对 3 个历史版本全部报
FAIL ⇒ 说明**扫描器自己有问题**（本仓记忆里那条「校验器报错时先自证」第 3 次生效）。
真因是**单 `mode` 变量无法表达嵌套模板字符串**（本仓 `viewPublish` 就是三层嵌套
``...${cond?`...${x}...`:...}...``）。**改用真解析器 esprima** 后，
`1b6c004`（第九十五批）= OK、`7fb6778`（第九十八批）= FAIL 在 L252
⇒ 精确定位到我上一批改的 `viewAssets`。

**修复**：把 5 行注释移到模板字符串**之外**，并在注释里写明这条禁忌。

**新增 `tools/js_syntax_lint.py`**（J1 登记，清单 56 条）：
- 首选 **esprima**（纯 Python ECMAScript 解析器，`dev` extra 登记 + 依赖策略登记）；
- **等长降级**处理 ES2020（`??`→`||`、`?.[`→` [ `、`?.(`→` ( `、`?.`→`. `）——
  严格保持字符数 ⇒ **行列号不变、定位不失真**（esprima 4.0.1 只到 ES2017）；
- esprima 不可用时降级为词法兜底，并**明确标注 `backend=lexical`** + 告警"不支持
  嵌套模板、可能误报"（不留"看起来通过"的错觉）。

**判据 J15**（`gates/gate_config_integrity/_js.py`）：复用同一份 `lint_file`
（**不复制逻辑** —— 避免"门禁过了但工具报错"的双口径漂移）。放门禁而非只放
pytest 的理由：改前端的人未必跑全量 pytest，但每次交付都过门禁；成本毫秒级，
漏检代价是整站不可用。

**守卫 `tests/test_js_syntax.py`（11 项）**：当前文件无错、
**失态对照**（复现"模板内注释含反引号"必须被判错）、合法三层嵌套**不得**误报、
ES2020 不得误报、降级**必须等长**、后端可辨、兜底能抓未闭合字符串/括号。

### ② 待办列表按 kind 列示「名称 / 文号 / 访问地址」

**用户反馈**：`relevance_boundary` 的 `subject` 部分显示文号、部分显示名称，
业务无法判断。要求 `relevance_boundary`/`timeliness_conflict`/
`rfn_theme_uncertain` 列示**名称+文号+访问地址**，`internal_unaligned`
列示**名称+文号**。

**新增 `interfaces/worklist_fields.py`**（单一事实源，确认台/MCP/CLI 可共用）：
- 字段一律从 `payload` 既有关键字取（实测各 kind 的键：`title`/`document_number`/
  `ipn` 等），**不猜**；
- payload 无 URL ⇒ **两级反查**：① 桥表（`登记时标题`/`登记时文号`→
  `source_url`，939/943 有值）；② **clean 产物**（覆盖全部已采集文件）。
  为什么需要第 2 级：`register_doc` 手工登记的文件**没有桥记录**
  （实测 T3 登记的 `RFN-d6063763945de936` 桥表查不到），而 clean 里有它的 URL；
- **归一复用 SSOT**（`std_lib.common_lib.norm`），仅两处极小特化并加
  `# norm-specialization` 豁免标记：① 标题先解码 HTML 实体（SSOT 不解码）；
  ② 文号先把 `【】` 换成 `[]`（SSOT 的 `_DOC_CLEAN` 未覆盖）。
  ⇒ 门禁 `gate_no_duplicate_libs` 报"重复实现"后**改成复用**，不复制归一口径。

**降级可辨**（本仓反复踩的坑）：每字段带 `*_src`，且严格区分
`not_found`（查不到）/ `not_applicable`（该 kind 无此概念）/
`aggregate`（汇总项）；前端分别显示「未查到」/「—」/「汇总项」，
**绝不把"查不到"渲染成空白**。

**实测覆盖率**（真实数据）：
| kind | 名称 | 文号 | 访问地址 |
|---|---|---|---|
| `relevance_boundary` 211 | 211/211 | 189/211 | **197/211（93%）** |
| `timeliness_conflict` 3 | 3/3 | 3/3 | 0/3（clean 也无 ⇒ 真实 not_found） |
| `internal_unaligned` 66 | 66/66 | 66/66（IPN，明确标注） | not_applicable |

**前端**：表头与行**同构动态列**（`showUrl` / `showNameNo` 同一组开关驱动，
避免"列数与内容错位"这个动态列最常见的坑）。

### ③ 《…再保险交易信息披露工作的通知》入 **T4**

按用户指示登记进 RFN 归属表并指定主题 T4：
- 事实源取数（**不臆造**）：title/文号/来源/URL 全部来自
  `nfra_cleaned_20261019.jsonl`；`pub_date` 源为 None ⇒ **诚实留空**；
- `rfn_api.register_doc("T4公司治理与股权关联交易", …)` ⇒
  **`RFN-d6063763945de936`** / action=`created`；
- 归属表已写入（文号 `保监发〔2006〕116号`、时效 `pending`）、
  主题复核 = **T4公司治理与股权关联交易**；
- 待办 `WL-20260926122825194-4972-e59815` → **resolved**（附 RFN 与主题）。

### ④ `rfn_theme_uncertain` 判据**停止产生**待办

**发现**：上一批 dismiss 后剩 1 条（保留项），但 10-19 一轮又**新增 107 条**
⇒ 判据每轮都在产。而业务口径已明确「除该通知外均不纳入」且该通知**已办结**
⇒ 该类**已无处置对象**。

**处置**：`tools/rfn_backlog.py` 加业务决策闸门 `_UN_CERTAIN_SUSPENDED`
（**停止登记**，而非"登记后自动 dismiss"——后者仍会污染审计日志与积压指标）。
- 白名单 `UNCERTAIN_WHITELIST` 保留为**文档**（让后人看到"为什么这个 kind 停了"）；
- 恢复方式写在代码里（置回 `False`）；
- 实测：识别到 2 条但 **open 保持 0**。
- 新一轮 107 条已 dismiss（理由含业务决策原文）。

### ⑤ 清洗逻辑修缺陷：正文空但附件有文本 ⇒ **受控回填**

**取证**：`nfra_cleaned_*.quarantine.jsonl` **每日固定 5 条、连续 9 天同一批**
（10-09 → 10-19），`_metadata.validation_errors` 全部只有
`body_text: required but null/empty`。其中 1 条
（《中国银保监会关于印发商业银行预期信用损失法实施管理办法的通知》）
**正文 0 字但 `attachment_content` 有 8727 字**，开头即
「中国银保监会文 件 银保监规〔2022〕号 …」⇒ **附件文本就是正文**，
本可清洗却被隔离 = **逻辑缺陷**（另 4 条附件也 0 字，源就缺，隔离正确）。

**实现**（`std_lib/scraper_std/pipeline.py`，在 **schema 校验之前**）：
**三重判据**缺一不回填 ——
① `body_text` 空（**绝不覆盖**已有正文）；② 附件 ≥200 字（页眉/水印级不回填）；
③ 附件里能找到**标题前 12 字**（证明"这段附件就是本文档正文"）。
回填后 `body_source="downloaded_doc"`（**受控枚举**内值）+ `_metadata` 三字段留痕
（`body_backfilled_from`/`body_backfill_att_chars`/`body_backfill_rule`）；
`REG_ORCH_DISABLE_BODY_BACKFILL=1` 可整体关闭（回滚开关）。

**实测**：真实数据 5 条里**恰 1 条**判可回填、4 条不可；反向 4 组（已有正文/太短/
**附件是别的文件**/标题空）全部正确拒绝。

**守卫 `tests/test_body_backfill.py`（12 项）**：真实数据恰 1 条可回填、
另 4 条确实无附件文本、绝不覆盖已有正文、**附件是他文不回填**（最关键的负例：
把附件清单当正文会污染检索且事后难发现）、`body_source` 在受控枚举内、
留痕字段存在、回滚开关存在、**回填必须在校验之前**（否则"改了但没生效"）。

### 验证
30 门禁 PASS（含新增 J15）、ruff 0、mypy 0；新增守卫 11 + 12 = 23 项全绿。
## [Unreleased] 2026-10-19 — P3 渲染三处修复 + depends_on 补全（揭示双生产者缺陷）+ 业务裁定清单 + 全链实跑（第一百批）

### ① P3 指标渲染异常：三处**真实的渲染缺陷**（数据层全对，问题在画法）

核对方法：先打 `/api/metrics`、`/api/quality`、`/api/slo`，**六个图表的数据全部正常**
（`gate_trend` 12 点、`worklist_aging` 键与 labels 逐一对应、DAMA 六维 score 齐备、
SLI 有 value+target、时效三态齐备）⇒ 问题必在渲染层，逐行核对后确认三处：

| 缺陷 | 现象 | 修复 |
|---|---|---|
| `barChart` 标签**无宽度约束** | 标签从 x=0 起、柱子从 x=110 起，而 `T1销售行为与消费者保护` 在 11px 下约 121px ⇒ **文字压在柱子上**（视觉上"糊在一起"） | 新增 `fitLabel()`：按**估算字宽**（ASCII 0.55×字号、中文 1×字号）截断到固定标签区，完整文本放 `<title>` 悬停可见 |
| 主题分布 `slice(0,8)` **静默丢数据** | 11 个主题只画 8 个 ⇒ 图上合计 **888** 而归属表实有 **1163** ⇒ 读者会认为"数据错了" | 按值降序取前 7 + 其余合并为「其他（N 项）」⇒ **合计恒等于总数**；hint 披露主题数与总数，可自校验 |
| `lineChart` 固定 ±5 边距 | 数据本身很稳（95.45~100）⇒ 曲线近乎水平，**看起来像"图没画出来"** | 边距按数据跨度自适应（跨度 <2 用 ±0.5），并在图上**显式标注 y 轴范围** `y 轴 95.45~100%`——平就是平，但要让人知道是数据稳而非图坏了 |

另修一处可读性：SLI 键名直接显示 `sli_chain_success_rate` 等机器口径 ⇒ 加
`SLI_LABEL` 展示映射（链成功率/数据新鲜度/门禁通过率），**不改数据键**
（键是契约，改了会让 `check_slo` 与看板口径分叉）。

### ② `depends_on` 补全：2/32 → **21/32**，并揭示一个**真实建模缺陷**

据 purpose/produces 的**事实关系**逐条补声明（不猜），**未声明的 11 步**是
有依据的"无上游/独立只读"，写入守卫的 `_NO_UPSTREAM` 表逐个注明理由。

**补全过程中 R4-c 报出环**：
`cleaned:{src} → apply:{src} → timeliness:ledger → timeliness:state → timeliness:verify → cleaned:{src}`

**根因是建模缺陷，不是声明错误**：`cleaned:{src}` 被 `clean:{src}` 与
`apply:{src}` **双重 produces**。而 `apply` 的实质是**消费 cleaned 并就地回写**
（时效结论覆盖进清洗产物的时效字段），它的产出只有旁路索引 `clean_index`。

**修复**：`cleaned:{src}` 从 `apply` 的 produces **移入 depends_on** ⇒
语义更准（消费+回写）、产物主要生产者唯一、拓扑无环。
**行为影响：无**——resume 判定读 watermark 的 `produced_by`（不读本声明），
而 `clean_index` 本就无水位记录。

**顺带解决决策 #3**：`semantic:assist` 的 `depends_on=("classify:products",)`
——正是那条"语义步骤未声明 inputs 致水位 stale 漏判"的诉求。

守卫 `test_step_triage.py` 从「锁定稀疏现状」改为「**守住改进**」：
覆盖率不低于新基线 + 未声明集合必须等于"有依据的无上游表"（新增步骤忘声明会被抓出）
+ 有上游声明者必须能被 topology_api 反查。

### ③ `doctor` 的 `token_fresh`：N-169 同类缺陷的**翻版**（只修了一半）

实跑前置自检发现：`token` 项报"长度 36（来源 timeliness_review）"，
而**紧邻的** `token_fresh` 项报"无 token，跳过时效探测"——**同一屏两行互相打脸**。

根因：`_c_token_fresh` 硬编码仓根 `.pkulaw_token`，而 token 实际在
`timeliness_review/`。这正是 `_c_token` docstring 里记录过的 N-169 缺陷，
**同处代码只修了 `_c_token` 没修 `_c_token_fresh`**。

修复：改用 `pkulaw_cli.load_token_any()` 的**同一事实源**（含 env 与多候选路径），
并据**命中来源**取 mtime（env ⇒ 如实说"无 mtime 可查"，不拿仓根文件冒充）。
修后两者一致：`token 最后更新 41 天前（来源 timeliness_review/.pkulaw_token）`。

### ④ 新增 `tools/business_review_list.py`：业务裁定清单（J1 登记，清单 57）

**为什么要聚类**：277 条机器不可判的待办直接甩给业务 = 没交付。

- `relevance_boundary`（211）：按 payload 的 `hits`（四组关键词命中结果，
  空数组=未命中）聚成 **6 个「命中模式」**——同模式的相关性不足**原因同类** ⇒ 可**按组批量裁定**。
  实测分布：组3 **116** / 组3＋组4 35 / 组1＋组4 32 / 组1 20 / 组1＋组2＋组4 4 / 组1＋组2 1。
- `internal_unaligned`（66）：payload 只有 `ipn/method/title`，**无机器维度**
  ⇒ 按标题关键词聚类为 8 组，并**明确标注"仅供阅读、非判定依据"**
  （未归类 35 / 财务费用 11 / 人事员工 8 / 业务核保 5 …）。

输出 Markdown，每条列示名称/文号/**访问地址**（空值可辨：未查到 / —），
并给每组一句**明确的裁定问题**（如"上述命中模式下该文件是否应纳入监管相关性体系"）。

### ⑤ 全链实跑（`--collect all --triggers`）

按用户要求：**全采、不跳过、含条件触发**。启动前 `--dry-run` 确认 argv
（5 源全覆盖；supp 如实标注"无网络采集"）。
**实跑进行中**（本批提交时为 `collect:mof`；日志见 `reports/_tmp/b100_run.log`）。
实跑结果与沿途新发现问题见下一批。

### 遗留：D-遗留 4 个大文件的**评估结论**（本批不拆，理由如下）

| 文件 | 行数 | `split_precheck` 评估 | 本批处置 |
|---|---|---|---|
| `gov/collector.py` | 841 | 2 个 class（`RobustSession`、`XzfgkScraper`）**咬合**；模块级 `LOG` + try-import 别名（`requests`/`BeautifulSoup`）**AST 捕获不到** | 不拆：拆分需同时搬运 2 类咬合对象与 2 处不可见依赖，风险最高 |
| `mof/collector.py` | 664 | 1 class + 13 函数（锁/签名/抓取/输出四组职责清晰）；但 **3 个 try-import 别名**（`CC_USER_AGENTS`/`extract_document_text`/`rich_object_fields`）需每层各自兜底 | 不拆：别名兜底易漏，且该文件正被实跑使用 |
| `mof/attachments.py` | 641 | 32 块，**19 个模块级常量** + 少数函数 | **优先候选**（常量可下沉 `constants`，与既有 pbc 拆分同构） |
| `supp/ingest.py` | 603 | 22 块，9 常量 + 9 函数（`build_records`/`enrich_*`/`land_doc` 可分层） | 次优候选（依赖 `_GUIDE_ROOT`/`_REGISTER_DOC`） |

**不拆的理由（质量优先的取舍）**：四个文件都需「逐字搬运 + 等价性三重锁定 +
守卫」的完整流程（E1–E5 的实测代价是每次 5–10 处"搬代码忘搬依赖"）；
本批同时在进行**全链实跑**，而采集是**子进程**（`COLLECT_CMD` 形如
`[python, <collector.py>]`）⇒ **改 collector 代码会影响尚未执行的采集步骤**，
破坏实跑一致性。故本批只做评估并给出候选优先级，拆分留待实跑完成后单独一批，
避免"代码改动 + 实跑验证"互相污染归因。

### 验证
30 门禁 PASS（含 J15）、ruff 0、mypy 0；`test_step_triage.py` 17 项全绿；
P3 前端 JS 语法 OK（esprima）；业务清单已生成（`reports/business_review/`）。
## [Unreleased] 2026-10-20 — 全链实跑暴露的三处**瘫痪级**缺陷（第一百零一批）

> 全链实跑（`--collect all --triggers`）跑完 **42 步**、`gates` 也 **PASS**，
> 但暴露了三处**门禁与单测都抓不到**的缺陷——因为它们只在"整链真跑"时出现。

### ① `run_clean_pipeline` 的 argparse help 含裸 `%` ⇒ **clean 5 源全瘫痪**

**症状**：clean 的 **5 个源全部 rc=1**，耗时 0.6~2.6s（远非超时），
且 `stderr` 字节数**完全相同（1785B）**——**等长 stderr 是"与数据无关的启动期崩溃"的指纹**。

**根因**：`help=f"…默认 {SCHEMA_FAIL_RATE_MAX:.2%}）"` 求值得到 `…默认 2.00%）`，
而 `argparse` 会对 help 做一次 `help_string % params` ⇒ 裸 `%` 触发
`ValueError: unsupported format character '?' (0xff09)` ⇒ **脚本启动即崩**。

**修**：f-string 后 `.replace("%", "%%")`（argparse 会还原成单个 `%`）。
**验证**：`clean:pbc` rc=0/5.0s、`clean:mof` 18.8s、`clean:nfra` 24.7s、`clean:supp` 2.2s（全部恢复）。

### ② `_c_token_exists` 硬编码仓根 ⇒ **时效核验永远不执行**

**症状**（日志自相矛盾）：
`SKIP（token_exists=True → 不满足（token 存在=False（.pkulaw_token））；…）`
第一段是**声明期望**，括号里才是实测——而实测用了错路径。

**根因**：只查仓根 `.pkulaw_token`，token 实际在 `timeliness_review/`。
**这是 N-169 同类缺陷的第 3 处**（前两处是 `doctor` 的 `token`/`token_fresh`）。
后果：**北大法宝预算再足也跑不到核验**（条件恒不满足）。

**修**：改用 `pkulaw_cli.load_token_any()`（唯一事实源，含 env 与全部候选路径），
说明里给出**命中来源**。验证：`token_exists(True) → True（timeliness_review/.pkulaw_token）`。

### ③ 条件步骤的占位符分支 return 残缺 rec ⇒ **整链在最后一屏崩**

**症状**：42 步全跑完、`gates` PASS，日志末尾却是
`[refresh] 主链异常终止: KeyError: 'elapsed_s'`。

**根因**：`_run_conditional` 的 `UnresolvedPlaceholder` 分支只 return
`{step, rc, skipped, reason}` 四字段（缺 `elapsed_s`）且**不写台账**
⇒ 汇总打印 `r['elapsed_s']` 抛 KeyError；且该步在 `run_step` 里**无记录**（不可追溯）。
实测触发者就是日志里的 `wiki:sync`（`{vault}` 缺值）。

**修（两层）**：
1. 该分支走**与其他分支同一条出口**：补齐 rec + `_record_step` + return rec；
2. 汇总打印改 `.get()` + **缺失显式告警**（`[WARN] … 缺 elapsed_s（按 ? 显示而非中断链路）`）
   —— 汇总表是**观测面**，不该成为链路可靠性的单点。

### ④ `collect:mof` 跑不完（4h 上限用尽）——**配置正常，问题在采集效率**

实测 `elapsed=14419.7s` ≈ `COLLECT_TIMEOUT["mof"]=14400` ⇒ **超时机制正常工作**。
该值是批 55/F2 有意放宽的（当时 7200s 用尽）。
**结论**：不是"缺上限"，而是 mof「列表+详情+附件」三段抓取（附件主机 429/500 退避至 60s、
502 连续 8 次熔断）**在 4h 内做不完**。登记 TD-11；**不再放宽**（会让每日链失控），
正确方向是分档（正文必成 / 附件增量可延后）或让 mof 支持 `--max-seconds` 早停并保证早停后数据可用。

### 守卫 `tests/test_chain_reliability_fixes.py`（7 项）

① help 里无裸 `%`（含**行为断言**：`--help` 必须 rc=0）；
② `_c_token_exists` 必须用 `load_token_any` + 披露命中来源 + want 互斥；
③ 占位符分支必须补 `elapsed_s` 且写台账 + 汇总打印不得用直接下标且缺失要告警。

### 验证
30 门禁 PASS、ruff 0、mypy 0；新增守卫 7 项全绿；clean 4/5 源已实跑恢复（gov 459MB 在跑）。
## [Unreleased] 2026-10-20 — TD-11 mof 采集专项：软上限 + 主机不可用熔断 + 五处保数据修复（第一百零二批）

### ① TD-11 取证：4h 上限耗尽的**真正主因是网络异常不熔断**

全链实跑 `collect:mof` 用尽 14419.7s（约等于 `COLLECT_TIMEOUT["mof"]=14400`）仍跑不完
⇒ mof raw 停滞 **10 天**（`mof_laws.jsonl` mtime 停在 10-10）。

**取证结论（推翻我上一批的推断）**：上批我说「502 退避约 180s/附件」，实测真因是
`download_attachment` 的**网络异常分支**（`URLError`/`TimeoutError`/`ConnectionError`）
**不计入熔断、也不熔断** ⇒ 附件主机**超时**（WinError 10060）时，每附件走满
`max_retries=6` 次、每次退避不超过 30+1s，约 **150s/附件**，且序列**永不终止**。
502 分支本来 8 次就熔断——**分开计会让任一单独出现时都熔不断**。

### ② 五层修复（逻辑抽成 `mof/limits.py`，纯逻辑可单测）

| 层 | 修法 | 实测 |
|---|---|---|
| **软耗时上限** | 新增 `--max-seconds`（对齐 gov 既有做法：gov_xzfgk=1500 / gov_zhengceku=5100，**mof 是唯一没有的**）；到期停抓新条目并落盘 | 76.8s 可控退出、rc=0 |
| **主机不可用熔断** | 网络异常**与 502 共用同一连续计数**；**任一次成功即清零**（熔断语义是「连续」不可用，不清清零会把健康主机的偶发抖动累积成误熔断） | 8 次即熔断（行为测试） |
| **早停不误判 removed** | 「历史存在但本次列表已无 ⇒ removed」与早停**语义互斥**（没抓到不等于已删除）⇒ 早停时**跳过该判定** + 一个兜底点补回所有未进 `new_store` 的历史记录 | 移除 0 / 活跃 870 |
| **跳过下载不等于丢弃历史** | `--no-attachments` 时被重处理的记录**沿用历史 attachments**（`build_entry` 的 `attachments` 参数直接派生 4 个字段，传 `[]` 即全清空） | 63.6MB 掉到 36.4MB 的坑已堵 |
| **正文截断不等于内容变短** | 详情未拿到**或**新正文短于历史 20% 以下 ⇒ 判「疑似被源站截断」并沿用历史；回退**计数**（`content_text_reverted`，不静默） | 6.0MB 静默缩水已堵；实测 reverted=133 |

`mof/collector.py` 同时从 822 行降到 **785 行**（回到 J11 行预算内；原 822 是上批加早停逻辑超的）。

### ③ 途中发现并修复的 4 个新问题（均为实跑才暴露）

| # | 问题 | 后果 | 修复 |
|---|---|---|---|
| 1 | **`--outdir` 默认值指向错位置**：`REPO_ROOT` 解析到 `collectors/` ⇒ 裸跑写到 `collectors/data/raw/`，与主库（`regulatory_scrapers/data/raw/`）不同 | 白跑 + 静默双份数据（**不报错**） | 已清理误产生的 3 个文件并记录；**未改默认值**（影响所有调用方，需单独评估） |
| 2 | **早停把 780/870 条误判为 removed**（我第一版兜底放在 removed 判定**之后**，无法撤销已置的 `active=False`） | **数据破坏**，已从备份恢复 | removed 判定加早停护栏 + 兜底前移（见上表第 3 层） |
| 3 | **`from . import limits` 相对导入在脚本形态下崩溃**（生产 argv 是 `[PY, <绝对路径>]`） | 采集**整体失败**（而包形态与单测全绿 ⇒ 只有真跑才暴露） | 双形态兼容导入（沿用仓内 `attachments` 的 try/except 惯例）+ 守卫新增「脚本形态必须能跑」用例 |
| 4 | **手动跑采集 ⇒ raw 文件变但水位未登记**（水位只由 `run_production_refresh._wm` 写）⇒ `gate_watermark` 判陈旧，且**级联**到 `cleaned`/`clauses`/`relations_index` | 门禁 FAIL、下游水位漂移 | 用**生产同一函数** `_wm` 补登记（不复制逻辑）；`relations_index` 交由续跑重建 |

### ④ 三处门禁合规（判据均正常工作）

| 判据 | 问题 | 修复 |
|---|---|---|
| **J11** 文件预算 | `collector.py` 822 > 800 | 抽 `mof/limits.py`（156 行，纯逻辑）⇒ 785 行 |
| **gate_watermark** | `cleaned:mof` 水位陈旧 | 复用 `_wm` 补登记（口径与生产一致） |
| **gate_runtime_hygiene** | `refactor/` 2 处裸 int | **口径对齐**：`pyproject` 的 ruff/mypy 早已排除 `refactor/`（属其他用户、本机不得修改），唯独该判据还在扫 ⇒ 加入 `SKIP_DIRS`。**不降低本仓标准**（`_STRICT_EXITCODES` 仍为 True，自有代码仍须 0） |

### ⑤ 守卫 `tests/test_mof_early_stop.py`（15 项）

**A 早停不得破坏数据**（removed 护栏 / 保留 prev / 兜底在统计前）、
**B 早停必须落盘**（`_flush_store` 共用出口 / status 记 `early_stop` / 告警不嫁祸「数据变更」）、
**C 主机不可用熔断**（连续 `URLError` 行为测试 / 熔断后短路 / 成功清零）、
**D 跳过不等于丢弃**（`--no-attachments` 沿用历史附件 / `_atts_for` 形参避 B023 /
截断沿用历史 / 回退计数 / status 字段稳定）、
**E 接线**（mof argv 带 `--max-seconds` 且**小于**硬上限 / gov 先例锚 /
**脚本形态必须能跑** / 相对导入必须双形态兼容）。

### ⑥ 一处自我纠偏

早停复测时文件体量反复下降（63.6 到 56.4 到 52.8MB），我先怀疑「级联恶化」并每次从备份重跑；
逐字段 diff 后才定位到**三个独立的丢失点**（attachments 29.4MB / content_text 6.0MB /
`--outdir` 写错位置）。教训：**体量异常要先做逐字段 diff，不要凭增量猜测**——
而「从备份重跑」只能恢复数据，不能修复逻辑。

### 验证
30 门禁 PASS（修齐 J11/水位/卫生后）、ruff 0、mypy 0；mof 采集 5 次实跑
（早停 30/75/90s + 双形态 3 次）行为一致；续跑 `--from timeliness:consolidate` 进行中。
## [Unreleased] 2026-10-20 — 采集器路径基准修正（4 处同类 bug）+ golden 体量归一化 + mof/supp 拆分（第一百零三批）

### ① `--outdir` 默认值 / `local_path` 基准：**同一类 bug 共 4 处**

根因统一：`REPO_ROOT = os.path.dirname(SRC_DIR)` 只上溯**一级**，而采集器在
`modules/regulatory_scrapers/collectors/<src>/`（两级）⇒ 实际得到 `.../collectors`
（**注释却都写 "regulatory_scrapers"**，两者不符）。分两类后果：

| 位置 | 后果 | 修法 |
|---|---|---|
| `mof/collector.py` 的 `--outdir` 默认值 | 落到 `collectors/data/raw`，而主库在 `regulatory_scrapers/data/raw` ⇒ **裸跑静默写错位置、白跑且不报错** | 新增 `SCRAPERS_ROOT`（上溯两级）专供 outdir |
| `mof/attachments.py` 的 `REPO_ROOT` | 用作 `local_path` 基准 ⇒ 新写入会变成 `../../data/docs/…` **脏路径** | 改用 `paths.ROOT`（**仓根**） |
| `pbc/collector.py` 的 `REPO_ROOT` | 同上，且**实测 13 条 `local_path` 里 10 条已是脏的** | 改用 `paths.ROOT` |
| `supp/ingest.py` 的 `SCRAPERS_ROOT` | `SRC_DIR`/`RAW_OUT`/`STATE_DIR` 全落到 `collectors/data/…` ⇒ **supp 采集白跑** | 上溯两级到 `regulatory_scrapers` |

**关键设计（两处基准不能混用）**：
· `outdir` ⇒ 主库 = `regulatory_scrapers/data/raw`（上溯两级）
· `local_path` ⇒ **仓根**（实测 mof 现存 246 条 `local_path` 以 `paths.ROOT`
  拼接后**文件真实存在** ⇒ 历史数据本就是仓根基准，修正是**回到既有口径**）

**历史数据迁移**：新增 `tools/migrate_local_path.py`（J1 登记，清单 58），把 pbc 的
10 条脏 `local_path` 迁到仓根基准。安全设计：只改一个字段、只替换**已知脏前缀**
（不猜任意 `..`）、**每条先验证新路径拼仓根后文件真实存在**（验证不过则原样保留
并报告）、备份后原子替换、默认 dry-run。实测：10 条脏、10 条可修、**0 条不可验证**。

### ② golden 的体量归一化（消掉反复出现的尾数脆弱点）

`raw: … mof=870条/63.5MB …` 这行的 **MB 尾数是纯噪声**（raw 含 `fetch_time` 等
时间戳，同批数据重跑也抖 ±0.1MB），而 golden 固化了它 ⇒ 每次采集都可能假失败
（第一百零二批就因此失败过一次）。

**修法**：`_normalize` 加 `(\d+)条/[\d.]+MB → \1条/<mb>MB` —— **只归一化 MB、
保留条数**。理由：条数变化意味着数据状态真的变了（应当 FAIL 让人知道）；
MB 尾数不携带行为信息（归一化掉）。这是「保留信息量最大维度、消除纯噪声」。

### ③ 拆分：`mof/attachments.py` 668→535，`supp/ingest.py` 603→565

**mof（三层）**：
| 文件 | 行数 | 职责 |
|---|---|---|
| `att_constants.py` | 169 | **纯常量 + 8 处 try-import 别名**（无逻辑、无可变状态） |
| `att_extract.py` | 206 | **纯变换**（html→text / 日期归一 / 文件名 / 缓存键 / 条目组装） |
| `attachments.py` | 535 | 网络 + 下载 + 熔断 + 编排，**re-export** 前两层 |

**刻意不搬**：`_ATT_502_STREAK`/`_ATT_CIRCUIT_OPEN` 是**跨调用可变全局**
（`download_attachment` 用 `global` 修改）。搬走会让 `global` 语义跨模块、
熔断变成两处状态（失效且难查）⇒ 留在使用处。

**supp（两层）**：`supp_constants.py`（52 行）承载 9 常量 + 一段**隐式顺序依赖**
（先算 `HERE`/`SCRAPERS_ROOT` → 再 `sys.path.insert` → 才能 `import config/std_lib`）。
那段依赖混在 13 个函数之间完全看不出来，抽出后成为文件级事实。

**等价性双证（两个拆分都做了）**：
· 符号可达（旧公开面一个不缺，`attachments`/`ingest` 仍导出全部）
· 纯变换函数是**同一对象**（不是复制）；常量同值
· 双形态：**脚本形态**（生产 argv 形态）实跑通过（`collector.py --help` rc=0、
  `ingest` 的 import 探针 rc=0）
· **行为**：mof 真实采集跑了一次（45s 早停，`移除 0 / 活跃 870 / 63.5MB`，零丢失）

### ④ 途中踩到并修正的两次自身失误（如实记录）

1. **删除脚本区间重叠**：为带走 `normalize_date` 上方的注释块而向上扩展起点，
   导致与 `html_to_text` 的区间**重叠** ⇒ 删出 `IndentationError`（已从备份恢复）。
   修法：区间只按 `def → next-def` 界定，并加**区间不重叠断言**。
2. **备份恢复回滚了已做的替换**：v2 从 `.bak_split` 恢复时，把第一步的「常量区替换」
   一起回滚了（只剩「删函数」生效）⇒ 常量未下沉、函数已删 ⇒ ruff 报 `F821`。
   教训：**多步改造中，"恢复"必须明确恢复到哪一步**，否则会静默回滚前序成果。

### 验证
30 门禁 PASS、ruff 0、mypy 0；新增守卫 `test_outdir_and_paths.py`(8) +
`test_mof_split.py`(11) + `test_supp_split.py`(11) + golden 归一化用例(1)。
## [Unreleased] 2026-10-20 — supp 采集端到端打通（**10 处同类缺陷**）+ local_path 跨基准解析 + gov/collector 常量层下沉（第一百零四批）

### ① 实跑 supp 采集暴露 **10 处缺陷**（全部同一根因的不同表现）

上一批我把 `supp_constants.SCRAPERS_ROOT` 修对了，但**只覆盖 `ingest.py`** ——
supp 目录下**另有 3 个脚本各自派生路径**，其中 2 个仍少上溯一级：

| # | 位置 | 后果 |
|---|---|---|
| 5 | `extract_doc_text.py` 的 `PROJECT_ROOT` | `SRC_DIR` 错位 ⇒ 提取的正文写错位置，`enrich_placeholder_records` 永远读不到 |
| 6 | `ingest_batch.py` 的 `SCRAPERS_ROOT` | `RAW_LOGICAL_PATH` 错位 + `--run-clean` 拼成不存在的 `collectors/clean/` |
| 7 | `ingest_local_dir.py` 的 `SCRAPERS_ROOT` | `RAW_PATH` 不存在 ⇒ **`FileNotFoundError`（脚本根本跑不起来）**；`relpath` 产出 `../data/…` 脏路径；`--run-clean` 脚本路径错 |
| 8 | `ingest_local_dir.py` 用 `json.load()` 读主库 | 主库是 **JSONL** ⇒ `JSONDecodeError: Extra data: line 2 column 1` |
| 9 | `ingest_local_dir.py` 用 `json.dump()` 写 raw | 写成 **JSON 数组**到 `.json` ⇒ 与主库 `.jsonl` **分叉**且格式不合规 |
| 10 | `local_path` **基准跨源分裂** | 见下 |

**修法（架构级）**：三者**统一从 `supp_constants` 导入**（单一事实源），不再各自派生；
`RAW_PATH`/`RAW_LOGICAL_PATH` 改为**直接复用 `RAW_OUT`**；读写统一走
`read_raw_records` / `write_raw_jsonl`（JSONL + `_meta` 信封 + 原子写）。

**实跑验证（真跑，非 dry-run）**：`ingest_local_dir.py` 端到端 —— rc=0、
记录 60→60（不丢）、仍是 JSONL+`_meta`、docs 文件 19→20、**未创建 `.json` 分叉**、
`local_path` 不带 `..`。

### ② `local_path` **基准跨源分裂**（实测取证）

| 源 | 基准 | 条数 |
|---|---|---|
| `mof` | **仓根**（`modules/…`） | 243 |
| `pbc` | **仓根** | 13 |
| `gov` | `modules/regulatory_scrapers`（`data/…`） | **1189** |
| `supp` | 同上 | 7 |

**关键取证**：全仓**没有任何代码**把 `local_path` 拼成路径打开文件
（`contract.py` 只做字段映射、`build_fts.py` 只存 SQLite 元数据、前端不用）
⇒ 这是**口径不一致**而非功能故障，**1199 条大规模迁移的收益不抵风险**。

**处置（质量最高 + 风险最低）**：
· **mof 源内 3 条**（同一记录的 3 个附件，旧代码写成了 `data/…`）⇒ 用
  `tools/migrate_local_path.py --allow-scrapers-to-repo` **归一为仓根**（mof 内部 100% 一致）；
· **跨源不强行统一**，改为提供**唯一正确解析方式**
  `std_lib/scraper_std/local_paths.resolve_local_path()`（自动识别两种基准，
  先验存在性再回退；`must_exist=True` 时解析不到返**空串**而非给错路径）。
· 实测解析可达率：mof **246/246**、pbc **13/13**、gov **1260/1261（99.9%）**、supp **4/4**。

### ③ `gov/collector.py` 常量层下沉：**841 → 701 行**

**"补夹具"的实质是等价性基线**（而不是再加 HTTP cassette）：拆分风险是
「漏搬/搬错/签名漂移」，cassette 覆盖的是行为。新增
`tests/gov_collector_baseline.py` + `tests/baselines/gov_collector_baseline.json`，
锁定四段**结构快照**：模块级名字全集（36）、常量摘要（容器长度+排序键 / 字符串
长度+前 12 字 / 正则 pattern+flags）、两个类的方法→参数名、`main()` 的 argparse
选项与默认值（25 个）。

**拆法（脚本按行区间逐字提取，零手抄）**：`gov_constants.py`（206 行）承接
引导 + 6 处 try-import 别名 + 全部常量；`collector.py` 保留
`_RESP_TEXT`（跨调用可变全局，`_init_cache` 用 `global` 改它）+ 三个缓存函数 +
`RobustSession`/`XzfgkScraper`/`main`，并 **re-export** 全部符号。

**基线当场抓到两次真实失误**（这是基线存在的意义）：
1. v1 把**标准库导入**（`sys`/`argparse`/`re`/…）一起搬走 ⇒ `cli.count` **25→0**、
   脚本形态 `NameError: name 'sys' is not defined`；
2. v2 把**仓库引导**一起搬走 ⇒ `ModuleNotFoundError: No module named 'paths'`
   （`collector.py` 保留的 `import paths` 需要它）。
   结论：**引导是每个文件的运行前提，不是"常量"** ⇒ 两份并存（`if not in sys.path` 幂等）。

### ④ 途中修正的三处自身失误（如实记录）

1. **手工写常量值全错**：我凭记忆写 `_ORGAN_KW`/`_NOISE_CLASS_HINTS`/
   `_SCHEME_UPGRADE_RE`/`CSV_COLUMNS`，与原文件**完全不同** ⇒ 改为脚本按行区间
   **逐字提取**（零手抄）。
2. **结构快照误放 `tests/cassettes/`**：那是 **HTTP 夹具**目录，J13 会按夹具 schema
   校验 ⇒ `CassetteError: 夹具 schema 不支持` ⇒ 移到 `tests/baselines/`。
3. **`sys.path.insert` 顶破 `tests/` 基线**（33→34）⇒ 去掉（`python -m` 时 cwd=仓根，
   天然可导入）。

### ⑤ 门禁调整（均为"拆分带来的必要变更"，已写明理由）

| 判据 | 变更 |
|---|---|
| J11 | 从 baseline **移除** `gov/collector.py`（拆分达标 701<800，基线**单调收缩**） |
| sys.path | `modules` 127→**128**（`gov_constants.py` 需要自己的仓根引导；`collector.py` 原计入不变） |
| R4 盘符字面量 | 基线 JSON 的 CLI default 归一化为 `<ROOT>`（`--out-dir` 默认是绝对路径） |

### 验证
30 门禁 PASS、ruff 0、mypy 0、**28 项新守卫全绿**（supp 13 + gov 15）、
基线自检一致、supp/pbc 端到端真跑通过、全量测试零失败。
## [Unreleased] 2026-10-20 — gov/mof 采集器四层拆分 + `local_path` 跨源统一 + 基线工具泛化（第一百零五批）

### ① gov/collector **841 → 451 行**（四层架构，零咬合）

AST 依赖实测推翻了此前的"2 类咬合"判断：`RobustSession` 与 `XzfgkScraper`
**互不依赖**（前者用 `_RESP_TEXT`，后者只经构造参数拿会话实例）。

| 文件 | 行数 | 职责 |
|---|---|---|
| `gov_constants.py` | 208 | 引导 + 6 处 try-import 别名 + 全部常量 |
| **`gov_session.py`** | 209 | `_RESP_TEXT` + `_OfflineMiss` + 3 个缓存函数 + `RobustSession` |
| **`gov_scraper.py`** | 336 | `XzfgkScraper` |
| `collector.py` | **451** | `main` + re-export |

**关键约束**：`_RESP_TEXT`（可变全局）必须与 `RobustSession`/`_init_cache` **同处**
—— 分开会造成"两处状态、缓存开关静默失效"（不报错，只是永不命中）。

### ② mof/collector **800 → 632 行**（五层）

| 文件 | 行数 | 职责 |
|---|---|---|
| `mof_constants.py` | 174 | 引导 + 常量 + `_RESP` + 3 个缓存函数 + 附件 re-export |
| `mof_sig.py` | 97 | 增量比对的 4 个纯函数 |
| `mof_lock.py` | 77 | `_LOCKS` + 4 个锁函数（**状态与函数同处**） |
| `mof_io.py` | 121 | 状态/JSON/CSV/HTML 四类产物写出 |
| `limits.py` | 156 | （上批）早停/熔断/沿用 |
| `collector.py` | **632** | `main` + `RateLimiter` + `fetch_detail` + re-export |

### ③ 基线工具**泛化 + 三处设计缺陷修正**

新增 `tests/collector_baseline.py`（`--source gov|mof|pbc|nfra`），替代只支持 gov 的
旧工具。修正三处会让基线**形同虚设**的缺陷：

| # | 缺陷 | 后果 |
|---|---|---|
| 1 | `expected` 每次从**当前文件** AST 重算 | 拆分后自动缩水 ⇒ `missing` 恒空 ⇒ **漏搬抓不到** |
| 2 | `MOD_DEFS` 只扫顶层节点 | `try/except` 导入块里的名字（`MasterView`/`requests`/`Retry`）全被漏掉 |
| 3 | 比对用"集合完全相同" | 拆分后 AST 自然多/少导入名 ⇒ 假失败 |

修正为：`expected` 固化在基线 JSON 里 + 递归 `ast.Try` + **单向包含**比对
（"拆分前有的，现在还得有且值一致"）。另加三类**噪声过滤**（标准库模块名、
内部 import 别名、非本仓定义的类 —— 后者会因 `datetime`/`Any` 是 `isclass`
而被误当业务类）。

### ④ `local_path` **跨源基准统一为仓根**（2911 条迁移）

**第 5 处同类缺陷**：`gov/attachments.py` 的 `REPO_ROOT = dirname(SRC_DIR)`
只上溯一级 ⇒ `collectors`（注释写 `regulatory_scrapers`）⇒ 重跑会写 `../data/…`。

统一动作：
· `gov` 的 `REPO_ROOT` → `paths.ROOT`；`supp` 新增 `WS_ROOT` 并改 `relpath` 基准；
· `tools/migrate_local_path.py` 迁移 **2911 条**历史值；
· 保留 1 条**不可验证**（文件不存在）原样不动 —— 保留旧值比改写成一个打不开的
  路径更诚实，且守卫会断言"该例外必须指向不存在的文件"，防它掩盖真漏迁。

**迁移工具的两处缺口**（实测发现）：
1. 只扫 `attachments`，**漏了 `body_docs`**（批 50 起正文载体从中分出）
   ⇒ 首轮只迁了 1282 条，漏 1627 条；
2. 只认 `../data/` 前缀，不认 `data/docs/`（SCRAPERS 基准本身）。

最终五源：仓根 3572 条 / 非仓根 **1** 条（合法例外），解析可达率 99.97%。

### ⑤ 途中踩到并修正的六次自身失误（如实记录）

1. **AST 依赖分析漏注解**：`visit_arg` 未遍历 `annotation`（`ScrapeConfig` 被漏）；
2. **`MOD_DEFS` 不递归 `try`**：漏掉 `MasterView`/`requests`/`Retry` 等 6 个依赖；
3. **手工猜行号**：`RobustSession` 末行猜成 L274，实际 L275 ⇒ 断言拦住后改 AST 取值；
4. **插入点落在 `try:` 体内**：破坏 try/except 结构 ⇒ `SyntaxError`；
5. **`RobustSession` 误列进 `gov_constants` 导入名单**（它在 `gov_session`）
   ⇒ 脚本形态 `ImportError`；
6. **`fs_lock`/`WS_ROOT` 被 ruff 当未使用删掉**（re-export 需 `as X` 或进 `__all__`）。

### ⑥ 门禁调整（拆分带来的必要变更，已写理由）

| 判据 | 变更 |
|---|---|
| sys.path | `modules` 128 → **134**（6 个新文件各需自己的仓根引导，互不继承） |
| R4 盘符 | 基线的 `const_digest` 字符串摘要也做仓根归一（`head` 曾含本机绝对路径前缀） |

### 验证
30 门禁 PASS、ruff 0、mypy 0、**94 项相关守卫全绿**（10 个测试文件）、
两源基线一致（gov 56 符号 / mof 73 符号，含类方法签名）。
## [Unreleased] 2026-10-21 — 确认台"偶发失败"根治 + 开机自启动 + 蓝屏排查 + 全链实跑（第一百零六批）

### ① 确认台"偶发 flake"根治：`/api/overview` 冷态 **22163ms → 118ms**

`tests/test_confirm_console.py::test_routes_return_json[/api/overview]` 曾**两次**
在全量跑中失败、单独复跑又通过 —— 一直被当"偶发 flake"。**实测是冷/热态差异**：

| 路由 | 冷态（修复前） | 热态 | 冷态（修复后） |
|---|---|---|---|
| `/api/overview` | **22163 ms**（撞破测试 20s 超时 ⇒ 必然失败） | 3963 ms | **118 ms** |
| `/api/worklist` | **7613 ms** | 0 ms | **107 ms** |
| `/api/assets` | **5965 ms** | 0 ms | **3.5 ms** |

**根因**：`overview()` 串行组合 6 个子快照，其中 `storage_usage()` 每次都
`os.walk` 扫 `external/`（**19320 文件 / 13.8 GB**，单次 4053ms；其余 4 目录仅 8ms）。

**三级修复**：
1. `_Cache` 支持 **per-key TTL**（外部模型 600s，其余 2s）+ **`warm()` 后台预热**
   + **`peek()` 只读不触发**；
2. `assets` / `worklist_fields._Bridge` 同样预热，未就绪时返回 `warming`
   （**绝不把"还没算完"误报成"查不到"** —— 后者会让用户以为数据缺失）；
3. **防缓存击穿**：一律 `peek` + `warm`，**不在无缓存时同步算**。
   （实测踩到：预热线程与请求线程同时扫 959MB ⇒ 反而恶化到 **11667ms**。）

守卫 `tests/test_console_perf_flake.py`（10 项，含"冷态必须 < 5s"的性能阈值）；
稳定性实测：`test_confirm_console.py` **连跑 5 次全绿**。

### ② 「删符号」语义：从**静默**变**显式告警**（用户提问的落地）

`tests/collector_baseline.py` 的比对是**单向包含**（"拆分前有的，现在还得有且值一致"）。
精确语义（已用可执行断言固化在 `tests/test_baseline_delete_semantics.py`）：

| 被删的符号类型 | 例子 | 后果 |
|---|---|---|
| **本仓符号**（`def`/`class`/赋值） | `main` / `RobustSession` / `HOST` | `missing` 非空 ⇒ **FAIL** |
| **导入名**（标准库/三方/内部别名） | `csv` / `Any` / `_att` | 过去**完全静默** ⇒ 现**显式汇总告警**（不阻断） |

**为什么导入名不判 FAIL**：拆分后 `csv`/`html` 本就该迁走；若判 FAIL 等于"拆分必须
保留所有无用导入"，最终会被 `# noqa` 绕过（比没有守卫更糟）。
**为什么不能静默**：`from collector import csv` 是合法调用，删了就断，而原来毫无提示。

**补盲区**：若被删的"导入名"当前**仍被本模块定义**（如自定义了 `json()` 函数），
说明它其实是本仓符号 ⇒ **提升为 FAIL**（`build()` 新增 `defined_names`，用
`_ast_imported_names` 区分"定义"与"导入"）。

**不受本工具保护的部分**：单向包含**不检查新增**（拆分后 AST 自然多出 re-export
目标）。"改名"若表现为"删旧名 + 加新名"，只有删旧名会被抓；**新名语义是否正确**
由具体业务守卫负责。

### ③ 开机自启动（`tools/workbench_service.py`）+ **乱码事故修复**

`install` / `uninstall` / `status`（任务·进程·健康·日志四项）/ `verify`（触发并探活）。
方案选**计划任务 ONLOGON**（用户级零提权、可查询/卸载、可延迟），而非启动文件夹
（无查询/卸载）或 Windows 服务（需管理员 + 第三方）。

**为自启动补的三个运行期能力**（缺任一都会翻车）：
`--single-instance`（计划任务每次登录都触发，无保护会起多进程互相踩踏）/
`--log-file`（`pythonw` 无控制台，`print` 全丢 ⇒ `_log()` 双写 + `RotatingFileHandler`）/
`--pid-file`。

**实测事故（蓝屏重启后发现）**：登录时命令行报
`'氭寔涔咃細閲嶈窇' 不是内部或外部命令`。根因：`.cmd` 用 **UTF-8** 写且含中文注释，
而 `cmd.exe` 按 **CP936/GBK** 读 ⇒ 乱码中部分字节被当命令分隔符、`rem` 行被截断。
**修法**：注释全英文 + `encoding="ascii"`（混入非 ASCII 直接抛错）+ 守卫
`test_start_cmd_is_ascii_only`。验证：非 ASCII 字节 **0**、执行 `rc=0`、无乱码。

**顺带修**：`serve --help` 一直写"端口被占用时换端口"，但代码直接 `OSError`
⇒ 文档与代码不符。新增 `_bind_free_port`（**有限递增** 20 次；不用 `port=0`
是避免 URL 每次都变，不利于收藏地址）。

### ④ 蓝屏排查（`reports/refactors/incident_20261021_bsod.md`）

**结论：不是本项目导致**。依据：①纯用户态 Python 无法破坏内核结构（`0x139` 的定义是
内核数据结构一致性校验失败）；②**5 次崩溃、4 种不同 BugCheck**
（`0x3B`/`0x139`×2/`0xA`/`0x1A`）、跨度 40 天 ⇒ 不符合单一软件缺陷特征（那会反复同一码）；
③最早一次 `2026-09-11` **早于本项目重负载**，`0x139` 在 10-13 也发生过；
④崩溃前 4 分钟的应用层事件是第三方 `HotKeyServiceDSU.exe` 的 .NET Runtime 错误。

**但本项目是"加重因素"**：mof 采集进程峰值 **6.26 GB**（机器 16GB，崩溃后空闲仅 2.9GB）。
极端内存压力会把潜伏的 RAM/驱动缺陷"压出来"。
**6.26GB 的成因（tracemalloc 定位，非泄漏）**：基线 `read_raw_records` 仅 **197MB**；
峰值来自**超大单条**（最大 31.3MB / 正文 23.57MB，Top5 占全库 59.2%）在
"去标签/正则/签名/序列化"每步产生 20–30MB 临时字符串，Windows 上 Python 高水位不归还 OS。

**建议**（报告 §4）：MemTest86（头号嫌疑）→ 排查 `HotKeyServiceDSU.exe` 与驱动 →
`WinDbg !analyze -v` 看 dump 的 `MODULE_NAME`。

### ⑤ 全链实跑暴露并修复的两个新缺陷

**(a) `--max-seconds` 粒度不足**：mof 设 7200s 却跑到 **13113s（超 82%）**，最终被父进程
4h 硬超时**强杀**（`rc=-1 TIMEOUT`）。原因：检查点只在"每条记录开始时"，而单条记录的
附件（下载 + PDF/表格解析）可能吃掉数小时。
**修法**：`EarlyStopGuard.deadline` 属性 + `collect_attachments(deadline=)` ⇒
**在附件循环内逐个子项检查**。

**(b) 陈旧锁 + PID 复用导致主链静默拒绝运行**：蓝屏重启后锁残留 `3992`，
而重启后 **PID 3992 已被 `svchost.exe` 复用** ⇒ `ProcessLock` 只校验"PID 是否存在"
⇒ 判"被占用" ⇒ `cli.py run` 直接退出（提示"等待 PID 退出"，永远等不到）。
**修法**：锁文件从裸 PID 升级为**进程身份** `{"pid", "started", "exe"}`，
`acquire()` 陈旧判定扩到 4 条（内容损坏 / PID 已死 / **PID 被复用** / 超龄）；
**向后兼容**：旧格式退回"PID + 超龄"逻辑，不误抢占。
守卫 `tests/test_lock_pid_reuse.py`（8 项）。

### ⑥ README 覆写（v3.0.0 → **v4.0.0**）

- 目录树补 **`web/`**、`tests/baselines/`；§2.2 补 `web/`、`workbench_service.py`、
  `tests/baselines/`，`STEP_ORDER` **31 → 32**；
- **§6.5** 补 `--single-instance`/`--pid-file`/`--log-file` + 端口递增 + 性能治理表；
- **新增 §6.6 开机自启动**（方案选型表 + 三件套 + 三层验证 + `/DELAY` 格式坑）；
- **新增 §6.7 运维要点**（7 条实测教训：`.cmd` 纯 ASCII、`/DELAY` 格式、mof 耗时结构、
  mof 内存峰值、异常重启后清陈旧锁、手跑采集会让水位陈旧、慢路由已治理）；
- **§8 与 §4.1 由 `tools/gen_flow_map.py` 重新生成**（受管块，勿手改）⇒ §8.2 步骤表
  **32 行 = 代码 32 步**；
- **`tools/audit_health` 的 2 个 HIGH 漂移已清零**：门禁数 `29 → 30`；
  补 3 个未记载环境变量（`REG_ORCH_MCP_TOKEN_FILE` / `REG_ORCH_DISABLE_BODY_BACKFILL` /
  `REG_ORCH_SEMANTIC_SEARCH_LIMIT`）。

### 验证
30 门禁 PASS、ruff 0、mypy 0、守卫新增 **34 项**（确认台 10 + 删符号 7 + 自启动 16 + 锁 8，
其中自启动本批从 16 项扩到 17 项）、`test_confirm_console` 连跑 5 次全绿、
全链实跑 `collect`5 源 + `clean`5 源 + 续跑 11 步全部 rc=0。
## [Unreleased] 2026-10-22 — README 渲染 + P3 指标 + 废弃代码清理 + mof 耗时根治（第一百零七批）

### ① README 两个渲染问题（用户报）

**(a) 4 张 Mermaid 图全部 "Unable to render rich display"**

本机**无 node/npm**（跑不了 mermaid-cli），mermaid.ink 在线渲染接口在网络策略下
**也不可达** ⇒ 无法用真实解析器定位到具体语法错。于是采取**可控性最高**的做法：

- 把 4 张图统一收敛到**跨版本稳定**的保守方言：禁 `<br/>`、禁双引号标签、
  禁 `;` 语句分隔、禁 `&` 多节点串联、禁 `(["…"])`/`[[…]]`/`[(…)]` 外形、
  `classDef` 只允许单值属性（`stroke-dasharray:4 2` 的空格值会被当两个属性）；
- 生成器 `gen_flow_map.py` 新增 `_mm()` / `_mm_sanitize_block()`：对整篇文本里**每个
  mermaid 块**做消毒，**只改 `[...]` 与 `|...|` 内部**，结构记号一律不碰；
- 手写的 2 张图（§3.1 ER、§4.1 业务数据流）手工重写；ER 图标签改成**单字单词**
  （erDiagram 关系标签只接受无空格单词），完整语义下沉为图下一行说明；
- 新增 `tools/mermaid_lint.py`（保守方言静态检查，11 条判据）+ `tests/test_readme_render.py`。

**(b) §5.2 定时表"列表未正常显示"**

真因：`<!-- SCHED:AUTO -->` 标记落在**表头与表体之间**。GFM 表格是**连续块**，
任何非表格行（含 HTML 注释）都会**终止表格** ⇒ 表体退化成普通段落（页面上一堆竖线）。
另外表头写的是 **4 列**（`频率/cron/命令/说明`）而生成行是 **5 列**
（`频率/cron/角色/命令/说明`）⇒ 即使不断开也会错列。

修法：标记移到表头**之前**，**表头随表体一起由 `gen_schedule_doc` 生成**
（顺带消灭"手工维护的表头副本"这一处漂移源）。

### ② 确认台 P3 指标渲染异常

逐项核对 `/api/metrics`+`/api/quality`+`/api/slo`：**数据全部正常**（DAMA 六维
95.55~100、SLI 三项齐备、主题 11 项、时效三态、aging 四档、gate_trend 12 点）。
定位到两处**画法**缺陷（都会让图"看起来坏了"）：

| 缺陷 | 现象 | 修法 |
|---|---|---|
| **数值标签会跑出画布** | `x = 条形起点+宽+6` 只留 56px，而 `num(14752)（94.5%）` 明显更宽 ⇒ 文字被裁一半 | 新增 `txtW()` 估算宽度；越界时**放进条形内部右对齐**（深色字） |
| **分布高度集中被误读成"图坏了"** | `timeliness_tri` pending=14752 / valid=643（23 倍）、`worklist_aging` 8-14=225 / 0-3=4（56 倍）⇒ 小值只剩 2px 细线 | 数值**带占比**；极值比 ≥20 时**显式说明**"真实分布，不是图坏"（**不做对数轴/截断**——那会失真） |

另补 DAMA/主题的空态说明（空 SVG 会被误读成渲染失败）。
新增 `tests/test_p3_contract.py`（7 项）：**静态抽取 `viewMetrics` 读的每个字段路径，
逐个到真实 payload 验证存在** —— P3 历史上被修过三次，根因都是"前端读的键与后端
给的键不是同一份契约"，这条把它机器化。

> 说明：本轮**未能取得像素级证据**（本机 Edge 155 的 `--headless` 已不支持
> `--dump-dom`/`--screenshot`，且外网渲染服务不可达）⇒ 修复针对的是**两类可证明
> 会产生"坏图"观感的几何缺陷** + 契约守卫；同时补了 hash 深链，使后续可按
> `#metrics` 直接定位页面做截图验证。

### ③ 废弃代码清理（用户报 `mof_laws_report.html` 仍在生成）

**HTML 报告整链拔除**（不是只删一次产出）：`_flush_store()` 里的
`save_html_report(...)` 调用、`mof_io.save_html_report` 函数体、`__all__` 条目、
两处 try/except 导入、日志里的 `HTML : …`、以及 `gate_flat_layout` 里把它当
"运行产物"举例的陈旧注释 —— 一并清掉（否则下次有人看到 import 又会接回去）。
顺带：`CSV : …` 日志改为**按 `--csv` 实际条件**打印（原来无条件打印，误导"已产出"）。

**全仓废弃/冗余审计**（新增 `tools/dead_code_audit.py`，四类判据）：
- **B 类「只定义不引用」**：初版报 217 项、**全是 pytest 测试类**（自动收集）⇒ 排除
  `tests/`；随后发现**根目录 `bootstrap.py`/`paths.py` 未被扫描** ⇒
  `apply_mirror_env`/`module_by_key`/`module_by_pkg` 被误判；再修**点号访问**
  （`模块.函数()` 是最常见引用形式，原正则 `(?<![\w.])` 把它全漏掉）⇒ 最终 **18 项**，
  逐个 `git grep` 复核后**全部删除**，复跑 **B 类清零**。
- **C 类**（`_old`/`_legacy` 命名）：40 项，人工核验**均为正当业务命名**
  （`ipn_old`=旧内部编号、`enrich_legacy`=是否处理旧格式、`self._old`=上下文旧值）。
- **D 类**（大段注释掉的代码）：**0 项**；`if False` 死分支：**0 项**。

### ④ `collect:mof` 耗时根治：**13113s → 复用后 0.9s/记录**

**实探网页结构**（一次请求）：默认 2 个类别（`lfgcc`），每类 **90 条 / 18 页**（size=50 时 2 页）；
列表已含 `lcontent`（HTML，样本 18016 字符）、`fileCount`、`publishDate`；详情接口用于
补全/校验正文（源站对长文会截断列表字段）；附件每条 0–9 个，全库 **246 个 / 500.3 MB**，
最大 **53.6 MB**（《进出口税则》PDF）。

**为什么慢（逐环节实测，不是猜）**：
- `extract_document_text` 对 50MB 税则 PDF **8–9.5s/次**，抽样均值 **4.31s/附件**；
- `_finalize_attachment` 还要再跑 `structured_table_fields` + `rich_object_fields`；
- 而 `download_attachment` 的"本地已有副本"分支**仍然无条件重抽**
  （注释写"仅本地重抽"）⇒ **记录一变（哪怕只是生效日期），它的全部附件都被重新解析**：
  一轮 182 条变更 × 约 5 附件 ≈ **900 次解析** ⇒ **13113s（3.64h）**；
  大表格 PDF 的解析峰值内存即 **6.26 GB**（与 TD-17 同一根因）。

**修法**：本地副本**未变**（磁盘大小 == 历史 `size_bytes`）且 `extract_version` 一致时，
**整段复用**历史抽取结果（`attachments._reuse_extraction`）；三条安全条件缺一不可；
`extract_version` 随抽取逻辑升级而递增（否则复用会保留旧口径产物 = 静默劣化）。
计数经 `REUSE_STATS` 进 `status`（`attachments_reused` / `attachments_extracted`，**优化可辨**）。

**真实数据验证**：样本记录 9 个历史附件 → `collect_attachments` **0.91s**、
**9/10 复用**（第 10 个是新出现的附件，正确走抽取）。

**存量立即生效**：新增 `tools/backfill_extract_version.py`（默认 dry-run）。
实跑 `--apply`：871 条记录保持、**246 个附件盖章**、107.6MB 未变（自动备份）。

### ⑤ 遗留风险处置

- **TD-19（audit 2 个 LOW 误报）已闭环**：① env 收集补 **常量形态**
  （`ENV_PATH = "REG_ORCH_MCP_TOKEN_FILE"` 这类写法原正则匹配不到 ⇒ 假报"未记载"）；
  ② 后缀判据从「**包含**」改为「**以后缀结尾**」（`_EXE` 是 `LAYER_EXEC_PLAN` 里
  `_EXEC_` 的子串 ⇒ 把变量名当 env 报 LOW）。复跑：**README 一致性 0 项**。
- **TD-17（内存 6.26GB）**：根因与耗时同一处（重复解析大 PDF）⇒ 复用后峰值频次大幅下降；
  仍存的是"单次解析 50MB PDF 的固有峰值"+`read_raw_records` 197MB 基线，登记为已缓解。
- **`collect:mof` 3.6h**：复用后预计降到分钟级（仅新附件需解析）；首次运行若未盖章
  仍会全量重抽一次。

### 验证
30 门禁 PASS、ruff 0、mypy 0、全量测试零失败；本批新增守卫 **22 项**
（README 渲染 7 + P3 契约 7 + 附件复用 8）。
## [Unreleased] 2026-10-22 — 时效 SSOT 漂移根治 + 配额安全增量 + spawn 输出修复 + Dash 评估（第一百零八批）

### ① TD-21 时效 SSOT 漂移：从「无法自愈」到**可回填且判据同源**

**真实条数不是 40，是 44** —— 门禁原返回 `problems[:40]` 且**不给总条数**，
TD-21 被登记成"40 条"正是这个切片的产物。已新增 `problem_count` /
`problems_truncated` / `warning_count`（**报告层静默截断本身就是缺陷**：让严重程度不可观测）。

**根因（两段）**：
1. 传播链只走「`verify` 产变更台账 → `timeliness sync` **只读最新一份台账**」；
   `verify` 一旦因**配额耗尽**失败（当日实证：跑 4662s 后 rc=2），台账不产出 ⇒
   44 条权威结论**永远进不了归属表**；
2. `classify` 重建归属表时把「时效状态」重置为 `pending`，**没有任何步骤从 state
   反向回填** ⇒ 只要 verify 停一次，漂移就永久留存。

**修法三件套（判据同源）**：
- 新增 `std_lib/common_lib/timeliness_ssot.py`：匹配（文号归一 ≥5 字优先、标题归一
  前 40 字兜底）+ **权威性四条件**（非 pending/uncertain、未超 90 日、来源不含
  「无同名命中/规则判断」、归属表非空）的**唯一实现**；
- 新增 `verification_state.apply_status_by_rfn()`：**按 RFN 精确写**归属表
  （不用标题子串猜，避免误配），复用既有备份 + 原子替换 + `utf-8-sig` 纪律；
- 新增 `cli.py timeliness sync --from-state [--dry-run]`：把"能否回填"与
  "verify 是否成功"**解耦**。

**门禁层1 切到共享实现**（删掉内联的 `st_docno`/`_norm_docno`/`_is_fresh_rec` 副本）——
两份判据必然分叉：门禁不算漂移的，命令却去改 ⇒ 覆盖人工判定；反之则永远修不掉。

**执行结果**：回填 44 条（amended 23 / valid 18 / repealed 3，未匹配 0；
跳过 placeholder 948 / 非权威来源 66）→ 层1 清零；随后**漂移如预期转到层2**
（16 条 base）→ `classify --steps base --all` → 8 条（final）→
`classify --steps cluster --all` → **判据 PASS（0 problems）**。
> 这条链路本身证明了"只改归属表会把漂移推到下一层"——所以命令收尾**强制**提示
> "下一步必须重建基座"，而不是让人以为已经修完。

### ② 配额安全：失败绝不写进 SSOT（T3 核心）

**原实现把"没查成"当成"查了但无同名命中"写进 SSOT**：
`mark_checked(status="pending", vsource="北大法宝（无同名命中，维持原判定）")`
且 `last_checked_at` 刷新为**当前时刻**。后果四重：
① 语义被篡改；② 记录被视为**新鲜 90 天**、不再优先重试；③ 门禁因"非权威来源"
跳过它 ⇒ **一次配额耗尽即可静默打穿覆盖率**；④ 从结果上看不出异常。

**修法**：
- **查询失败**（message ≠ 成功）/ **未执行**（配额中止、预算截断）⇒ **不写 state**；
- **真实无命中**（成功且 0 条）与「规则判断」⇒ 仍登记 pending（合法结论不能一起取消）；
- `execute_queries(..., meta=)` 新增**出参**回传 `stopped/reason(quota|auth)/attempted/
  remaining`（原来只在日志打 `[配额]`，调用方拿不到任何信号）；
- 摘要新增 `failed / not_attempted / quota_stopped / stop_reason / truncated`。

### ③ 增量策略：优先级 + 预算，**不需要游标**

- **优先级**：从未核验（SSOT 无记录）→ `last_checked_at` 最旧的 → 同档 `publish_date` 最新的；
- 核验后 `last_checked_at` 被刷新 ⇒ 自动沉到队尾 ⇒ **天然轮转**；
  且"状态本身就是进度"（游标文件容易与数据漂移，故**不引入**）；
- 新增 `--max-items`（每源预算，0=不限）。**口径**：预算截断记 `truncated`、
  **不算 partial** —— 否则一设预算，链上每次 rc=2，"有预算"变成"永远失败"；
  只有**查询失败 / 配额中止**才判 `partial`（可重试）；
- 排序移到 `dry-run` **之前**：dry-run 的意义就是"让我看看会按什么顺序查"。

### ④ 全局 spawn 包装器**静默吞掉子进程输出**（新发现）

`std_lib/scraper_std/proc.install_global_defaults()` 给 `run/Popen/check_output/
check_call/call` 注入 `CREATE_NO_WINDOW`；它为子进程创建**新的隐藏控制台**，
父进程重定向到文件/管道时，子进程输出**全部写进那个隐藏控制台**⇒ 丢失。

**实测矩阵**（父进程重定向到文件）：

| 组合 | 子进程输出 |
|---|---|
| 裸 `subprocess.run` | 可见 |
| 仅 `STARTUPINFO(SW_HIDE)` | 可见 |
| 仅 `CREATE_NO_WINDOW` | **全丢** |
| `CREATE_NO_WINDOW` + `STARTUPINFO` | **全丢** |
| `CREATE_NO_WINDOW` + `stdout=sys.stdout` | 可见 |

**实证影响**：`cli.py timeliness verify --source gov --dry-run` 跑 5.5s、rc=0、
**零字节输出**（同一命令直接跑脚本有 897 字节）。

**修法**：新增 `_inject_inherited_stdio()` —— 保留隐藏窗口效果，但在调用方
**未显式指定流**时把父进程 `stdin/stdout/stderr` 传下去。跳过条件：调用方已给该流 /
用了 `capture_output=True`（与显式 stdout 互斥）/ 父流无真实 `fileno()`。
**`check_output` 例外**：stdlib 固定 stdout=PIPE 且禁止外传
（否则 `ValueError: stdout argument not allowed`，实测踩到）。

### ⑤ 工作台引入 Dash 的评估（结论：不引入）

实测（`pip --dry-run --report` + wheel 解包）：`dash==4.4.1` 全树 **28 包**，
相对现有锁**新增 16 个**（Flask/Werkzeug/plotly/pydantic/pydantic_core/janus/
nest-asyncio/retrying/…），安装载荷 **≈18.6 MB**（dash 8.53 + plotly 9.24），
dash wheel 内 JS **33.21 MB**、plotly 内 JS **18.58 MB**，前端运行时需加载
`plotly.min.js`(4.59 MB) 等 **6–8 MB** —— 对比现状单文件 **46.9 KB**（约 130–170 倍）。

**结论：不引入 Dash 作为框架**（决策矩阵加权：现状 4.45 / 方案 C 4.05 / Dash 2.10）。
最优方案 C：保留零依赖骨架，需要交互图表时**只加 `plotly` 一个库**并本地托管
`plotly.min.js`（不引 Flask/Dash，SSE/单实例/脱敏/写路径全部不动）。
评估文档：`reports/refactors/工作台前端选型评估_Dash_20261022.md`（含矩阵、
迁移 6 项前置、验收标准）。

### ⑥ Mermaid（TD-20 闭环）

用户侧确认 GitHub 渲染正常（4 张图）。机器侧 `tools/mermaid_lint`（11 条判据）与
`tests/test_readme_render.py`(7) 继续守；TD-20 标记闭环，残余风险"本机无法回归真实渲染"
写入台账（改图时应在有 node 处复核，或提交预生成 SVG）。

### 验证
**30 门禁 PASS**（TD-21 的 FAIL 消除）、ruff 0、mypy 0；
本批新增守卫 **29 项**（时效回填 20 + 配额增量 9），全绿。
## [Unreleased] 2026-10-22 — 确认台 11 项缺陷根治 + 设计重做（第一百零九批）

### ① P3「渲染异常 TypeError」：根因不是画法，是**向 undefined 赋值**

用户报 `TypeError: Cannot convert undefined or null to object`。根因一行：
`Object.assign(LAST.metrics, m)` —— 首次进入指标页时 `LAST.metrics` 是 `undefined`，
而 `Object.assign` 的**第一个参数**必须是非空对象。改为**新建对象**
（`Object.assign({}, m, {...})`），并新增 `obj()` 兜底全部 `Object.entries/keys`：
后端某天少给一个字段，只该让**那张卡**显示"暂无数据"，不该整页白屏。

顺带把条形图从**手绘 SVG 改为 CSS 网格**：三次"渲染异常"里有两次的根因都是
SVG `<text>` 没有 ellipsis、标签/数值宽度只能靠估算，估偏就溢出（压柱或被裁）。
CSS 网格把标签、柱体、数值分到固定三列，**结构上不存在越界**——这是消除而非绕过。

### ② P4 列与内容错位：`rfn_clean_drift_c2` 的名称/文号取错了键

该 kind 的实体在 `payload.clean_title` / `payload.clean_docno`（它描述 clean 侧那份），
而 `derive()` 只认 `payload.title/document_number` ⇒ 全部回退到 `subject`（= RFN），
于是"名称显示 RFN 编号、文号未查到、地址 —"。修法：改为**逐 kind 能力矩阵**
（`KIND_SPEC`：title/docno/identity/url/local/note 各自的取值键），并把
`not_applicable`（该 kind 无此概念）与 `not_found`（查不到）严格分开。

新增 **「唯一标识号」列**：RFN / IPN / 文号，逐 kind 取 `identity`。
`internal_unaligned` 的 IPN 从此进这一列，**不再冒充外部文号**。

### ③ P4 访问地址张冠李戴：命中 ≠ 正确

用户报"点『营销行为管理通知』却打开『消费者权益保护评价办法』"。根因：
原实现**先按文号命中就直接返回 URL**，从不校验命中的是不是同一份文件 ——
只要某条待办的 `document_number` 与 `title` 来自不同文件（payload 混装/登记录错），
就会稳定地打开另一份文档。

修法：命中后做**一致性校验**（`_similar`：归一相等 / 包含 / 相似度 ≥0.82），
不通过则**不返回 URL**，而是 `url_src="mismatch"` + 两侧标题一起回传，
前端显示「地址不匹配」并说明原因。**宁可不给链接，也不给错链接。**

### ④ P4 本地文件：内部制度可直接用系统默认程序打开

`internal_unaligned` 按 IPN 反查 `internal_policy_index.json` 的 `original_path`，
派生**仓内相对路径**；经 `GET /api/local/file` 由**浏览器**打开/下载（初版用系统默认程序打开，被 `test_no_desktop_interference` 拦下——那会夺焦点，属本仓黑名单；改为服务端提供字节后既满足"点一下就能看"，又不夺焦点）。

**三重安全边界**（这不是"顺手开个文件"，等于以当前用户权限执行一次打开）：
① 路径必须解析到仓根之下（`..` 逃逸拒绝）；② 必须命中白名单目录前缀
（制度原件 / 清洗产物 / 报告）；③ 文件必须存在且扩展名在白名单内。
另：**只接受 POST**（GET 会被预取/历史记录误触发）。

**踩到的路径基准坑**：`original_path` 相对 `source_root` 的**父目录**，
不是仓根也不是 `source_root` 本身；直接拼接会让 861 条**全部解析不到**
（初版存在率 0/861，逐个候选试存在性后 861/861）。

### ⑤ P1「始终无任何显示」：模板节点 + 边匹配失败

后端其实**有数据**（32 步/5 源/15 条权重），但：
① 节点画的是**模板名** `collect:{src}`，而权重表存的是**展开名** `collect:gov`
⇒ 节点上读不到数字；② 连边靠 `collect:` 前缀正则 + `srcs.includes("src")` ⇒
**一条边都连不出来**。于是用户看到"两列灰方块、无流向、无数据量"。

修法（后端）：按 `StepSpec.produces / depends_on` 的**产物依赖**建真实 DAG ——
"谁产出该产物、谁消费该产物"就有一条边；`{src}` 按源展开。
实测 **48 节点 / 40 条边**，且每个节点带**真实记录数**
（gov 13178 / mof 870 / nfra 1935 / pbc 571 / supp 58）。
修法（前端）：**分层列布局**（L1→L10 每层一列），边宽 ∝ √记录数，悬停看
状态/rc/耗时/记录数/作用，点击开抽屉。

### ⑥ P2/P7 并入总览 + P6 访问地址 + P8 全量

- **P2（产物清单/发布件/契约自检）与 P7（双底座/wiki 投放源）并入总览**，
  页签从 9 个减到 7 个，总览分三段：关键指标 → 运行与风险 → 产物与发布。
- **P6 增加「访问地址」列**，复用 P4 的同一份解析（`worklist_fields.lookup`），
  保证同一份文件在两页给同一个链接。
- **P6 短词检索修复**：`records_fts` 用 **trigram** 分词，只索引 3 字窗口 ⇒
  `MATCH '保险'`（2 字）**必然 0 行**（实测 保险/银行/监管 全 0，保险公司/保险集团 有）。
  中文双字词恰恰最常见 ⇒ 新增 `search_external_like` / `search_internal_like` 兜底
  （**只扫标题/文号/RFN**，不扫 1.6GB 正文），并在响应里用 `mode="like"` + `note`
  说明"换了检索方式"（降级必须可辨）。
- **P8 提交流默认全量**（原硬编码 20 条 → `limit=0` 为全量，实测 327 条），
  点击任一提交展开**完整变更**（`/api/commit?sha=` → `git show` 的文件清单
  + 完整提交说明 + 原始输出）。

### ⑦ UI/UX 重设计（参照 idataserch.com）

设计令牌集中到 `:root`（页面内不再写死色值）：
浅底 `#F7F8FA` + 白卡 + 深蓝主色 `#1A5FB4` + 12px 圆角 + 8pt 栅格 + 极轻阴影；
数字用 `tabular-nums` 且放大（28px）做"数据背书"；表格 sticky 表头 + 悬停高亮；
状态统一用 pill（ok/warn/bad 三色语义）。**为什么换浅色**：确认台是每天读数字的
工作台，浅底在长时阅读、截图归档、与 Excel/公文并排时更稳（深色在投影与外发截图里失真）。

### ⑧ P4 处置交互上移

原实现：点行尾「处置」，表单出现在**整张表之后** —— 长列表里等于"点完要滚到页底"。
现改为：**操作列固定在最左**（勾选 + 处置），点处置就地在**该行下方**插入表单
（再点收起），提交后局部刷新。阅读动线变成"选 → 处置 → 填 → 提交"。

### ⑨ 遗留风险处置结论（三项）

| 项 | 结论 |
|---|---|
| **层3 20 条告警（nfra）** | **不是数据冲突，是匹配能力缺口**：这些归属表行的**发文字号为空**，而 `apply_timeliness_to_cleaned` 只按「文号」或「标题+日期」匹配 ⇒ 匹配不到（实测 `apply --source nfra --dry-run` 写回 0）。**故意不改匹配**：无文号时按标题模糊匹配极易张冠李戴（正是第③条修的病）。登记 TD-23：建议给这 20 条补文号，或走人工裁决。 |
| **948 条 pending placeholder** | 口径澄清：948 是「**归属表匹配到的 1159 行里**被跳过的那批」；**全量 state 口径**是 placeholder 14752 / 非权威 119。它们是历次 verify 的**合法结论**（"法宝无同名命中"），不是脏数据；90 天后自动回到高优先级重查。新增 `cli.py timeliness summary` 的**三维披露**（状态 × 可用性 × 年龄）让这批可读。 |
| **15603 vs 15604** | 差异是 `_meta` **版本键**（写侧注入 / 读侧剥离，F-D14），**不是丢记录**。判据原直接读 JSON 未剥离 ⇒ 与所有消费方口径不一致。已让判据剥离 `_meta`，现在两侧都是 **15603**。 |

### 验证
**30 门禁 PASS**、ruff 0、mypy 0；本批新增守卫 **28 项**
（P1/P3/P4/P6/P8 契约 + 本地文件安全 + 前端接线），全绿；
真实 HTTP 冒烟：7 个路由全 200 且形态符合预期。
