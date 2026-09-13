# Changelog

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
