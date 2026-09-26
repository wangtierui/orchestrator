# classifier 交付物滞后归因 · 条款数据接入评估（2026-09-27）

> 本文回答两个问题：
> **Q1** 为何 `regulatory_classifier/docs/reports` 的主题报告与 `regulatory_classifier/data` 的部分数据未随全链同步更新（具体环节 / 不一致表现 / 遗漏原因）。
> **Q2** 内部制度↔监管制度关联、监管主题分类是否应调用 `regulatory_scrapers/data/clauses` 与 `internal_policy_base/data/processed` 的条款数据（数据范围 / 匹配逻辑 / 预期效果 / 优劣）。
> 全部结论基于 2026-09-27 实测（文件 mtime、`classify_state.json` 的 `input_sha` 逐项比对、声明表与调用点核对）。

---

# 一、Q1：交付物滞后归因

## 1.1 实测现状（时间戳矩阵）

`modules/regulatory_classifier/` 下产物按 mtime 分成四档：

| 档 | 时间 | 产物 |
|---|---|---|
| **链内本轮** | **09-27 00:23–00:30** | `_t{n}_matched.json`、`_t{n}_citerefs.json`、`T1–T10_*明细表.csv`、`_upper_laws.json`、`classify_state.json`、`rfn_clean_bridge.csv`、`rfn_drift_*.csv/json`、`relations/*`（4195 条）、`人身保险公司-全景分析报告.md`、`人身保险公司-主题分类报告.md` |
| **09-26 17:34** | 上一轮（P9 数据面刷新） | `_t1/_t2/_t4_clause_graph.json` |
| **09-21 20:47–20:58** | 更早一轮 | `_t{n}_base.json`、`_t{n}_final.json`、`T0_89*明细表.csv`、`人身保险公司-文件归属表.csv` |
| **09-20 / 09-17 / 09-08** | 陈旧 | `_t3/_t5–T10_clause_graph.json`（09-20）、`人身保险公司-主题归属表.csv`（09-17）、**`T0–T10主题报告.md` ×11（09-08，滞后 19 天）** |

## 1.2 归因：三类，只有第三类是缺陷

### A 类 · 断点幂等跳过（**设计正确**，非缺陷）

`classify.py` 是「hash 断点幂等」编排器：每个 (主题, 子步) 记录 `inputs` 的 sha256；**输入未变且产物存在 → 跳过**（`classify.py:143`）。逐项实测比对（当前 sha vs `classify_state.json` 记录值）**全部一致**，即"跳过"判定正确：

| 产物 | 停在 | 为什么正确 |
|---|---|---|
| `_t{n}_base.json` / `_t{n}_final.json` (T1–T10) | 09-21 | `base` 的 inputs =（**文件归属表**（09-21）、**主题归属表**（09-17））——**两张表本轮未变** → 断点命中跳过 |
| `T0_89逐份条款引用…明细表.csv` | 09-21 | T0 的 `detail` inputs = `(_ATTR,)` **不含 clean_index**（`classify.py:77-79`）——T0 是唯一不依赖 clean 快照的主题；其余 T1–T10 的 `detail` inputs 含 `_CLEAN_INDEX`（09-27 因 clean 重跑而变）→ 已重跑 |
| `_t{n}_clause_graph.json` | 09-20 / 09-26 | inputs =（`matched`, `citerefs`）。二者虽在 09-27 被**重写**（mtime 变），但**内容 sha 未变**（match 重跑输出与上次一致）→ 断点命中跳过（实测 sha 一致） |

> 关键点：**"文件 mtime 旧" ≠ "内容过时"**。A 类是幂等语义的正常表现——内容与最新输入一致，只是没被重写。

### B 类 · 上游事实源（"未更新"是正常语义）

| 产物 | 停在 | 说明 |
|---|---|---|
| `人身保险公司-文件归属表.csv` | 09-21 | **它是输入（事实源）而非链的产出**——RFN 注册表，只在 `rfn register`（新文件入归属）时写；链只读不写 |
| `人身保险公司-主题归属表.csv` | 09-17 | 同上——人工主题改判（`rfn set-theme`）时才写 |

### C 类 · **链外遗漏（真实缺陷，N-46）**

| 产物 | 停在 | 根因 |
|---|---|---|
| **`T0–T10主题报告.md` ×11** | **09-08** | **`build_theme_report.py` 从未接入编排** |

**证据链**：
1. 编排的阶段 4.5 `reports:build`（`run_production_refresh.py:839-849`）**只调用** `report_builders/build_overview_report.py`；
2. 全仓搜索 `build_theme_report` 的调用点：**仅其自身文件**（`scripts/report_builders/build_theme_report.py`）——**无任何编排/命令调用**；
3. 对比：兄弟生成器 `build_overview_report.py` 产出「全景分析报告 + 主题分类报告」→ **09-27 已刷新** ✓。

即：F-O06「报告生成入编排」当初**只接了一半**（overview），漏接了同目录的 `build_theme_report.py`。

## 1.3 涉及的更新环节（责任链）

| 产物类别 | 应由哪个环节更新 | 实际状态 |
|---|---|---|
| `_t{n}_base/final` | `classify:all` → 子步 `base`/`cluster` | 断点正确跳过（输入未变）✓ |
| `_t{n}_matched/citerefs` | `classify:all` → 子步 `match` | 09-27 已重跑 ✓ |
| `T1–T10` 明细表 | `classify:all` → 子步 `detail` | 09-27 已重跑 ✓ |
| `T0` 明细表 | 同上（T0 分支 inputs 仅归属表） | 断点正确跳过 ✓ |
| `_t{n}_clause_graph` | `classify:all` → 子步 `clause_graph` | 断点正确跳过（内容未变）✓ |
| `_upper_laws.json` | `classify:all` → 全库 `upper` | 09-27 已重跑 ✓ |
| 归属表/主题归属表 | `cli.py rfn register` / `set-theme`（人工/按需） | 本轮无 RFN/主题变动 → 不更新属正确 ✓ |
| 全景/主题分类报告 | 编排阶段 4.5 `reports:build` | 09-27 已刷新 ✓ |
| **T0–T10 主题报告** | **无任何环节**（生成器未接线） | **09-08 起未更新 ✗** |

## 1.4 遗漏原因（3 条）

1. **编排接线遗漏（直接原因）**：`reports:build` 只接了 `build_overview_report.py`；`build_theme_report.py` 虽已按 R9 落地（读 `_t{n}_final.json` + 归属表 + 主题归属表 + `clause_index`），但**无调用方**——属"实现了但没接线"。
2. **水位声明表未覆盖报告类产物（放大原因）**：`GOV_ARTIFACTS`（`run_production_refresh.py:138-206`）覆盖 10 个**数据**产物族（`cleaned:{src}`/`clauses:{src}`/`classify:products`/`relations_index`/`reconcile:drift`/`merged_view`/`published:*`/`analysis:manifest`），**没有任何报告类条目**（overview/theme 皆无）→ `gate_watermark` 无从校验其新鲜度 → "19 天未更新"无人告警。
3. **`classify:products` 代表集过窄（盲区）**：其产物路径代表集只取「11 份 `T*_*明细表.csv`」（`run_production_refresh.py:282`）→ `base`/`final`/`clause_graph`/主题报告的新鲜度都不在水位与门禁视野内，只能靠人工看 mtime。

## 1.5 修复建议（按性价比排序）

| 优先级 | 动作 | 说明 |
|---|---|---|
| **P0** | **`reports:build` 增接 `build_theme_report.py`** | 一行接线（与 overview 同目录、同风格）；建议与 overview **同步骤串行执行**（先 overview 后 theme，复用其条款统计） |
| **P1** | **`GOV_ARTIFACTS` 增 `reports:classifier` 条目** | `inputs: [classify:products, rfn_attr, rfn_theme]`，`produced_by: report_builders.{overview,theme}` → 让"报告是否随底座刷新"成为**水位可校验项**（当前唯一能自动发现滞后的手段） |
| P2 | `classify:products` 代表集扩容 | 由「11 份明细表」扩为「明细表 + `_t{n}_final.json` + `_t{n}_clause_graph.json`」（成本：水位 sha 计算量增加，final 合计 ~1.2 MB，可接受） |
| P3 | `status` 增"产物新鲜度"视图 | `cli.py status` 展示关键产物的 `expected_inputs_sha` vs 实测，把 A/B 两类"正确滞后"与 C 类"真滞后"区分开（避免误读 mtime） |

---

# 二、Q2：条款数据接入评估

## 2.1 两侧条款数据的实测现状

### 监管侧 `regulatory_scrapers/data/clauses/`（链内产物，09-27 更新 ✓）

| 源 | 文件 | rfn 非空 | `parse_score` 覆盖 | `is_fallback` | 条款总数 |
|---|---|---|---|---|---|
| gov | 13 178 | **82（1%）** | 100%（中位 **0.884**） | 3 860（**29%**） | 69 350 |
| nfra | 1 934 | **787（41%）** | 100%（中位 **0.942**） | 216（11%） | 28 821 |
| supp | 58 | **38（66%）** | 100%（中位 0.922） | 8（14%） | 1 037 |
| mof / pbc | — | — | — | — | —（未逐项统计） |

**字段结构**（每条 = 一份文件）：
```
rfn, document_number, title, dedup_key, source_url, timeliness_status,
publish_date, effective_date, chapter_count, article_count,
chapters[]         = [{no, title, article_index}]
articles[]         = [{no, number:"第一条", body:"…"}]
article_structure[]= [{level:"条", number, title, content, items:[{level:"款"…}]}]   ← 编/章/条/款/项 树
parse_mode, parse_score{total,coverage,legality,continuity}, is_fallback, validation{...}
```
**接口面**：`interfaces/clause_index_api`（`clauses_dir` / `latest_clause_path` / `iter_file_clauses` / `find_clauses(docno,title,src)` / `build_clause_index`）。

### 内部侧 `internal_policy_base/data/processed/`

| 项 | 实测 |
|---|---|
| `{ipn}_clauses.json` | **877 份**（§ 878 制度），结构 `{ipn, articles[], chapters[], structure, parse_mode, structure_count}`——**与监管侧同构** |
| `{ipn}_fulltext.json` | 877 份（全文，`{text}`）——**当前关联链读的就是它** |
| **目录污染（N-47）** | 另有 **`.png` 1 638 + `.jpeg` 20 + `.emf` 2 = 1 660 个 OCR 中间图片**（大量同名 `d1_img1_image1.*`）混在同层 |
| 最新 mtime | 09-20（**`internal index` 不在链内**——见 2.5 附注） |

## 2.2 现有两条匹配链（**都是文件级**）

| 任务 | 实现 | 读取 | 匹配信号 | 粒度 |
|---|---|---|---|---|
| **内部制度 ↔ 监管制度关联** | `internal_policy_base/merged.py: extract_rfns` | `processed/{ipn}_fulltext.json`（**全文**）+ 归属表 | ② 文号签名（`iter_docno_signatures`，匹配"〔年〕序号"的数字尾）；② 书名号标题（`iter_quote_titles`，4–40 字）与归属表「文件名称」**归一化全等** | **文件级**——只知"制度 X 引用了文件 Y"，**不知第几条** |
| **监管主题分类** | `regulatory_classifier/scripts/match_theme_docs.py` | `final.json` + `clean_index`（正文） | 标题/文号**索引查找**（`match_one`，取 `body_len` 最大者）+ 正文正则抽「根据/依据…《X》」（`extract_basis`） | **文件级**（matched）；条款线索靠**文本正则推断**（citerefs） |

### 现有关系产物 `relations_index.jsonl`（4 195 条）——**半条款级**

字段：`src_kind/src_ref/src_name/src_docno/article/source_offset/source_snippet` + `dst_kind/dst_ref/dst_name/dst_docno/dst_normalized_name/dst_class` + `relation/basis_type/matched_by/confidence`。

**实测关键缺口**：
- **源侧 `article` 非空 = 545 / 4 195（13%）**——条款号靠 `source_offset` + 邻域文本**推断**，87% 定位不到；
- **目标侧无任何条款字段**（`dst_*` 只有 `dst_class/dst_docno/dst_key/dst_kind/dst_name/dst_normalized_name/dst_ref`）——**`dst_article` 不存在**。

**典型样例（证明信息在丢失）**：
```
source_snippet: "…根据《中华人民共和国民法典》第一百八十六条…"
产出: dst_name="中华人民共和国民法典"  (dst_docno="")
丢失: "第一百八十六条"  ← 目标侧条款定位
```

## 2.3 评估结论：**分任务裁决**——关联**应当接入**，主题分类**不应接入**（但可共享派生层）

| 任务 | 是否接入条款数据 | 理由 |
|---|---|---|
| **内部制度 ↔ 监管制度关联** | **应当接入（高价值）** | 该任务本质是"条款→条款"的依据关系（合规对照），现有实现已做到**半条款级**（源侧 13%），条款表可把源侧提到 100% 并**新增目标侧定位**——这是当前**唯一的信息缺口**，且下游（起草对照素材 `build_draft_clause_view`）明确需要 |
| **监管主题分类** | **不应接入（粒度不匹配）** | 主题分类是「文件 → 主题」的归属判定（`classify` 的 `base/cluster/detail` 链），**判定单元是文件而非条款**；引入条款不改变归属结果，只增加 O(N×M) 成本 |
| **（例外）主题报告 / clause_graph** | **可接入（统计与图增强）** | `build_theme_report.py` **已有先例**（经 `clause_index_api` 统计"条款化文件数/条款总数"）；`clause_graph` 可把"文件-条款"边升级为"条款-条款"边 |

## 2.4 数据接入方式（若落地关联增强）

### 数据范围
- **源侧（内部）**：`processed/{ipn}_clauses.json` 的 `articles[]`（877 份，条款 `{no, number, body}`）；**不读** `_fulltext.json` 做条款定位（现方案即读全文）。
- **目标侧（监管）**：`clauses/{src}_clauses_{date}.jsonl` 的 `articles[]` + `article_structure[]`（五源）。
- **不引入**：`processed` 的 `_fulltext.json` 之外内容；OCR 图片（N-47 应清理）。

### 匹配逻辑（三步）
1. **定位源侧条款（精确化）**：用关系条目现成的 `source_offset`（字符偏移）在 `processed/{ipn}_clauses.json` 的 `articles[].body` 上做**区间反查** → 判定该引用落在**第 N 条**（替换现有"邻域文本推断"，可把 13% → ≈100%）。
2. **定位目标侧条款（新增）**：在 `source_snippet` 中匹配 `第[一二三四五六七八九十百零〇\d]+条(?:之[一二三四五六七八九十]+)?` 模式；若命中，则在该 RFN 对应文件的 `clauses` 条目里按 `articles[].number` 归一化**查表定条** → 新增字段 `dst_article` / `dst_article_body_head`。
3. **质量门控**：仅采用 `parse_score.total ≥ 0.75` 且 `is_fallback == False` 的条款参与（剔除 gov 29% fallback 噪音）；命中不确定时置 `dst_article=""` + `article_placement="unresolved"`（**不猜**，保持可审计）。

### 预期效果
- 关系粒度：`文件 → 文件` ⇒ **`文件第 N 条 → 文件第 M 条`**（带双侧证据 `source_snippet` + `dst_article_body`）；
- 计量口径：从"引用 **N** 部法规"升级为"引用 **N** 部法规的 **M** 个条款"（可直接支撑合规清单、差距分析）；
- 下游增值：起草对照素材（drafter）从"整文对照"升级为"逐条对照"；`clause_graph` 可建成条款级引用网络。

### 联接键风险（必须先解决）
⚠️ **`clauses` 的 `rfn` 填充率极低（gov 1% / nfra 41% / supp 66%）** → "按 RFN 查目标文件条款"**当前不可行**。落地前需**先回填**：
- 回填路径：`clauses.document_number` / `title` → 经**归属表**（`norm_docno` + `norm_title_strict`）反查 RFN → 写回 `rfn` 字段；
- 该回填可复用既有 `reconcile_clean_drift` 的桥表机制（`rfn_clean_bridge` 已有 RFN↔clean 锚），**无需新造轮子**；
- 回填后应加**门禁**（`gate` 级）：`clauses` 的 `rfn` 填充率下限（如 ≥ 80%），否则静默降级会再次发生。

## 2.5 优劣对比（现行方案 vs 接入条款数据）

| 维度 | 现行（文件级 + 全文正则） | 接入条款数据（结构化条款对条款） |
|---|---|---|
| **精度** | 只能到文件级；源侧条款仅 13% 且靠推断 | 源侧 ≈100%（区间反查）；**新增目标侧条款定位** |
| **召回** | 标题需**归一化全等**（`norm_title_strict`）→ 改述/简称/无书名号引用全漏 | 可基于条款正文关键词/文号签名双通道（召回提升，需防误召） |
| **可审计** | `source_snippet` 有证据但无条款号 | 双侧条款号 + 双侧正文首句，**证据更强** |
| **成本** | 低（全文正则，无额外 IO） | 中：clauses 体积大（gov 148 MB），需**定向读**（`find_clauses` 查表，不整载）+ 条款级索引；`processed` 已有条款表，增量小 |
| **风险** | 低（无新依赖） | ① `rfn` 联接断裂（**前置必修**）；② fallback 条款质量（gov 29%）；③ 与 `relations_index` **口径重叠**（须明确分工，见下） |
| **一致性** | 与 `relations_index` 同源（它就是产出方） | **不应新建并列管道**——应在 `relations_index` 上**加字段**（`dst_article` 等），保持"关系"单一事实源 |
| **链内位置** | `relations:gen`（阶段 2.6） | 同步骤内增强；**须注意** `clauses` 属阶段 1、`processed` 非链内（见附注），存在**轮次差**（§3.3.1 已用 `ok_within_round` 处理 clauses，processed 需补齐） |

## 2.6 落地建议（最小改动、不破单一事实源）

1. **前置修复（P0）**：
   - 回填 `clauses.rfn`（经归属表反查 + 复用桥表机制），并加填充率门禁；
   - 清理 `processed/` 的 1 660 个 OCR 中间图片（N-47）——避免 `processed_signature()`（`merged.py:129-139`，对**目录全量**做名称+size+mtime 指纹）被图片噪音长期扰动、造成 `merged_view` 无谓重建。
2. **增强（P1）**：在 `tools/extract_relations.py` 内**增设条款定位 pass**（读 `processed/{ipn}_clauses.json` + `clauses/*.jsonl`，经 `clause_index_api`），输出**新增字段** `dst_article` / `article_placement` / `dst_article_head`，**不新建产物**。
3. **增强（P2）**：`build_clause_graph.py` 消费新增字段 → 边从"文件-条款"升级为"条款-条款"。
4. **不做**：主题分类（`classify` 的 base/cluster/detail）与 `match_theme_docs` 引入条款数据——粒度不匹配，收益不足以抵成本。

---

# 三、本次发现的问题项（新增登记）

| 编号 | 问题 | 影响 | 处置建议 |
|---|---|---|---|
| **N-46** | `build_theme_report.py` 未接入编排 → `T0–T10主题报告.md` 滞后 19 天；`GOV_ARTIFACTS` 无报告类条目 | 交付物静默过时、无水门禁覆盖 | **P0 接线**（`reports:build` 增调）+ **P1 声明表增条目**（详见 1.5） |
| **N-47** | `processed/` 混入 1 660 个 OCR 中间图片（png/jpeg/emf，大量重名） | `merged.py:_processed_signature()` 对目录全量取指纹 → 图片增删会误触 `merged_view` 重建；目录语义污染 | 移入独立 OCR 临时目录（或 `processed/_ocr_tmp/`）；签名改按 `*.json` 过滤 |
| **N-48** | `clauses.rfn` 填充率极低（gov 1% / nfra 41% / supp 66%） | 条款级接入（Q2）**前置阻断**；任何"按 RFN 找监管条款"的方案均失效 | 回填 + 加填充率门禁（详见 2.6） |
| **N-49** | `relations_index` 源侧 `article` 仅 13%、目标侧无 `dst_article` | 关联精度停在文件级；`source_snippet` 中的"第 M 条"信息被丢弃 | 条款定位 pass（详见 2.6） |
| **N-50** | `internal index`（含条文抽取，产出 `processed`）**不在 `STEP_ORDER`**（链内只有 `internal:merged`） | 内部制度新增/变更后，`processed` 不会自动更新（最新 09-20）；`merged_view` 会基于旧条款产物 | 评估是否纳入链（或至少在 `doctor`/`status` 做"processed 落后于 originals"告警） |

---

## 四、结论速览

- **Q1**：滞后分三类——**A 断点幂等跳过（正确，实测 sha 全一致）**：`base/final`（归属表未变）、T0 明细（T0 不依赖 clean 快照）、`clause_graph`（matched/citerefs 内容未变）；**B 上游事实源（正常）**：文件/主题归属表；**C 真实遗漏**：**T0–T10 主题报告（生成器从未接线）+ 报告类产物无水位声明**。修复 = 接一行线 + 加一条声明。
- **Q2**：**关联任务应接入条款数据**（核心增量 = 源侧条款定位 13%→≈100% + **新增目标侧 `dst_article`**，因 `source_snippet` 中的条款号正被丢弃）；**主题分类不应接入**（粒度不匹配）；接入须**先回填 `clauses.rfn` 并清理 `processed` 图片**，且在 `relations_index` 上**加字段而非新建管道**。

**文档结束。**
