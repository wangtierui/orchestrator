# orchestrator 全链路优化方案——引入工具与数据链条

> 基于对 `wangtierui/orchestrator` 仓库实际代码的审查，针对分句分词、主题分类、关系抽取、文档清洗、制度×监管关联、文档解析、检索层七个薄弱环节，出具完整的优化工具引入与数据链条方案。所有工具均为**本地模型/服务，零 Token 消耗**。


## 一、分句分词优化

### 1.1 现状问题

`std_lib/scraper_std/sentence_split.py` 是**纯正则四级流程**：① 标签语义转换断句 → ② 中文标点自动分隔 → ③ 中英文粘连拆分 → ④ 无标点兜底切分。核心局限是**无语义理解**，无法处理“第 X 条”与前后句的边界、嵌套引用、但书结构。

### 1.2 引入工具

| 工具 | 用途 | 安装 |
|---|---|---|
| **HanLP 2.x** | 中文分句 + 分词 + 依存句法分析，支持104种语言上的10种联合任务，语料库覆盖法律等多个领域 | `pip install hanlp`（首次自动下载约 1.2GB 模型） |
| **LTP 4** | 中文分句 + 分词 + 依存句法分析，提供 `sent_split` 专用分句接口 | `pip install ltp`（基于 PyTorch） |

### 1.3 数据链条

```
cleaned 正文（body_text）
    ↓
sentence_split.py（保留，处理显式标点模式）
    ↓ 正则无法处理的复杂边界
HanLP/LTP 分句 + 依存句法分析
    ↓
sentence_split_ml.py（新增，增强层）
    ↓ 输出增强后的分句结果
pipeline.py 的 _clean_record_body（下游消费）
```

**降级链**：HanLP/LTP 不可用 → 回退 `sentence_split.py` 纯正则。


## 二、条文结构解析优化

### 2.1 现状问题

`std_lib/scraper_std/document_structure.py` 的 `extract_structure` 是**纯正则**，无法识别“但书”结构、无法区分“第 X 条”作条文号 vs 作引用。

### 2.2 引入工具

| 工具 | 用途 | 增强点 |
|---|---|---|
| **HanLP/LTP 依存句法** | 条文边界识别 | 识别“第 X 条”作条文号 vs 作引用 |
| **HanLP NER** | 专有名词识别 | 识别文号前缀（“金办发”“银保监办发”） |

### 2.3 数据链条

```
cleaned 正文
    ↓
document_structure.extract_structure（保留，正则基础解析）
    ↓ 复杂边界
HanLP/LTP 依存句法分析
    ↓
条文边界 + 但书结构识别
    ↓
增强后的 chapters/articles/structure
    ↓
clause_index → base_publish.external_clauses
```


## 三、主题分类优化

### 3.1 现状问题

`cluster_by_keywords.py` 是**纯关键词匹配**：多轮 `passes` 关键词表，命中即归类。核心问题是**关键词表硬编码、无语义理解、覆盖不全时漏分类**。

### 3.2 引入工具

| 工具 | 用途 | 安装 |
|---|---|---|
| **text2vec** (`shibing624/text2vec-base-chinese`) | 中文语义向量化，CoSENT 方法训练，在中文 STS-B 测试集上有较好效果 | `pip install text2vec` |
| **BERTopic** | 无监督主题聚类，利用预训练 Transformer 模型捕捉文本语义关系 | `pip install bertopic` |
| **UMAP + HDBSCAN** | 降维 + 密度聚类，BERTopic 依赖 | `pip install umap-learn hdbscan` |
| **ChatLaw-Text2Vec**（可选增强） | 法律领域微调的语义相似度模型，使用 93w 条判决案例数据集基于 BERT 训练 | 从 HuggingFace 下载 |

### 3.3 数据链条

```
归属表（人身保险公司-文件归属表.csv）
    ↓
标题 + 正文摘要提取
    ↓
text2vec 向量化（本地模型，零 Token）
    ↓
BERTopic 聚类（TF-IDF + UMAP + HDBSCAN + c-TF-IDF）
    ↓
每个簇匹配 THEME_MAP 中语义最接近的主题
    ↓
输出：主题归属表（替代/增强 cluster_by_keywords）
    ↓
match_theme_docs（现有，保留）→ build_detail_tables（现有，保留）
```

**降级链**：BERTopic/text2vec 不可用 → 回退 `cluster_by_keywords.py` 关键词匹配。


## 四、关系抽取优化

### 4.1 现状问题

`build_clause_graph.py` 的 P1–P5 抽取模式是**纯正则**，`extract_relations.py` 的 `RelationPipeline` 同样是**正则驱动**。

### 4.2 引入工具

| 工具 | 用途 | 安装 |
|---|---|---|
| **HanLP/LTP 依存句法** | 隐式关系抽取 | 同分句工具 |
| **APRCOIE** | 中文开放信息抽取，自动生成抽取模式，定义新的中文 OIE 模式形式 | 从 GitHub 获取（`github.com/jialin666/APRCOIE_v1`） |
| **SignalGraph** | 零 Token 确定性图构建 | 纯 Python，零外部依赖 |

### 4.3 数据链条

```
cleaned 正文
    ↓
第一层：正则抽取（现有，保留）→ 覆盖显式模式
    ↓ 正则未命中的句子
第二层：HanLP/LTP 依存句法分析 → 覆盖隐式模式
    ↓
第三层：APRCOIE 中文 OIE（输入=依存分析结果）→ 覆盖未预定义关系
    ↓
第四层：关系推理（传递性/对称性/逆关系）
    ↓
合并去重 → relations_index.jsonl（SSOT）
    ↓
gate_relations 校验
```

**关键约束**：APRCOIE 依赖依存分析结果，**第二层必须先执行**。


## 五、制度×监管关联优化

### 5.1 现状问题

`merged.py` 的关联方法是**文号精确命中 + 标题归一化命中**，制度正文中的“参照监管要求”“落实相关监管规定”这类**无显式文号的引用无法匹配**。

### 5.2 引入工具

| 工具 | 用途 | 增强点 |
|---|---|---|
| **text2vec** | 条款级语义向量化 | 无显式文号的语义兜底 |
| **scikit-learn** | 余弦相似度矩阵 | 条款级关联计算 |

### 5.3 数据链条

```
制度正文条款（internal_policy_base/processed/）
监管文件条款（clause_index/）
    ↓
第一层：文号精确命中（现有，保留）
    ↓ 未命中
第二层：标题归一化命中（现有，保留）
    ↓ 未命中
第三层：text2vec 条款级向量化 → 余弦相似度矩阵
    ↓
阈值筛选（相似度 > 0.75）
    ↓
关键词交叉验证（用关键词库二次筛选）
    ↓
输出：条款级关联（增强 merged_view.json 的 associated_rfns）
    ↓
gate_citations 校验
```


## 六、文档清洗优化

### 6.1 现状问题

清洗规则分散在 7 个模块中（`cleaner.py` / `sentence_split.py` / `ocr_correction.py` / `table_recovery.py` / `text_reflow.py` / `rich_object.py` / `schema_validation.py`），**无统一注册表、无量化度量、只能事后拦截**。

### 6.2 引入工具

| 工具 | 用途 | 增强点 |
|---|---|---|
| **HanLP/LTP** | 分句增强 | 同分句工具 |
| **text2vec** | 语义去重 + 语义校验 | 检测清洗后文本是否偏离原文 |
| **新增 `clean_rules_registry.py`** | 清洗规则集中注册 | 统一规则注册、版本管理、变更审计 |
| **新增 `quarantine_fixer.py`** | 隔离记录自动修复 | 可自动修复的隔离记录回写 |

### 6.3 数据链条

```
body_text
    ↓
cleaner.py（保留，字符级清洗）
    ↓
sentence_split_ml.py（新增，HanLP/LTP 句法断句）
    ↓
text_reflow.py / table_recovery.py / ocr_correction.py（保留）
    ↓
text2vec 向量化（新增，语义去重 + 语义校验）
    ↓
schema_validation.py（保留，字段校验）
    ↓
clean_rules_registry.py（新增，规则注册校验）
    ↓
gate_clean_rules（新增门禁）校验规则注册完整性
    ↓
cleaned CSV+JSONL
```

**质量度量框架**：

```
每轮清洗后输出：
    ├── 隔离率（按源/按字段）
    ├── 空值率（核心字段）
    ├── 异常率（格式校验失败）
    ├── 语义偏离度（text2vec 相似度 vs 原文）
    └── 历史趋势对比（与上轮对比）
    ↓
reports/数据质量度量_*.md（新增）
```


## 七、文档解析优化（WeKnora docreader 解耦）

### 7.1 现状问题

`crawler_common.py` 的 `extract_document_text` **仅做文本层提取**，无版面分析、无多模态处理。扫描件 PDF 直接标记 `library_missing`。

### 7.2 引入工具

| 工具 | 用途 | 部署 |
|---|---|---|
| **WeKnora docreader** | 独立 Python gRPC 微服务，支持 PDF/DOCX/Excel/EPUB/网页等 25+ 格式解析与页面渲染 | Docker 独立部署，端口 50051 |

**关键约束**：docreader 只做解析，不做 OCR/VLM/分块/存储。扫描 PDF 被渲染为 JPEG 后交由调用方处理 OCR。

### 7.3 数据链条

```
内部制度原件（PDF/Word/Excel）
    ↓
crawler_common.extract_document_text（保留，快速文本层提取）
    ↓ 扫描件/复杂版面
DocReaderClient.read()（新增，gRPC 调用）
    ↓
docreader 独立服务（Docker 部署，端口 50051）
    ↓ 返回 Markdown + ImageRefs
scanned_pdf 页面标记 → ocr_engine.py 补充 OCR（本地 PaddleOCR/Tesseract）
    ↓
增强后的 body_text + rich_structured
    ↓
internal_policy_base/extract.py → indexer.py
```

**降级链**：docreader 不可达 → 回退 `crawler_common.extract_document_text`。


## 八、检索层优化（ParadeDB 解耦）

### 8.1 现状问题

`build_fts.py` 使用 **SQLite + FTS5 trigram**（子串匹配），**无语义向量检索**。

### 8.2 引入工具

| 工具 | 用途 | 部署 |
|---|---|---|
| **ParadeDB**（PostgreSQL 17 + pgvector + BM25） | 向量检索 + BM25 关键词检索 + 混合检索（一条 SQL） | Docker：`paradedb/paradedb:v0.22.2-pg17` |
| **Redis** | 异步解析队列 | `redis:7.0-alpine` |

### 8.3 数据链条

```
发布件 JSONL
    ↓
build_fts.py（保留，SQLite FTS5 作为降级）
    ↓ 可选启用
ParadeDB 索引构建（pgvector 向量 + BM25 关键词）
    ↓
base_api.search_external（检索层）
    ↓
支持语义向量检索 + 关键词混合检索
```


## 九、引入工具汇总表

| 环节 | 引入工具 | 替换/增强的模块 | 安装方式 | 零 Token |
|---|---|---|---|---|
| 分句分词 | HanLP 2.x / LTP 4 | `sentence_split.py` | `pip install hanlp` 或 `pip install ltp` | ✅ |
| 条文结构 | HanLP/LTP 依存句法 | `document_structure.py` | 同上 | ✅ |
| 主题分类 | text2vec + BERTopic + UMAP + HDBSCAN | `cluster_by_keywords.py` / `align.py` | `pip install text2vec bertopic umap-learn hdbscan` | ✅ |
| 关系抽取 | HanLP/LTP + APRCOIE + SignalGraph | `build_clause_graph.py` / `relations.py` | HanLP + APRCOIE GitHub | ✅ |
| 制度×监管关联 | text2vec + scikit-learn | `merged.py` | `pip install text2vec scikit-learn` | ✅ |
| 文档清洗 | HanLP/LTP + text2vec + `clean_rules_registry.py` + `quarantine_fixer.py` | 7 个清洗模块 | 同上 | ✅ |
| 文档解析 | WeKnora docreader（gRPC） | `crawler_common.extract_document_text` | Docker 独立部署 | ✅ |
| 检索层 | ParadeDB（PostgreSQL + pgvector + BM25） | `build_fts.py` | Docker：`paradedb/paradedb` | ✅ |


## 十、实施路线图

| 阶段 | 任务 | 依赖 | 优先级 |
|---|---|---|---|
| **P0** | 新增 `clean_rules_registry.py`，集中清洗规则 | 无 | 高 |
| **P0** | 新增质量度量框架，每轮清洗后输出度量报告 | 无 | 高 |
| **P0** | 引入 HanLP/LTP，新增 `sentence_split_ml.py` | `pip install hanlp` 或 `pip install ltp` | 高 |
| **P0** | 引入 text2vec，新增 `semantic_utils.py` | `pip install text2vec` | 高 |
| **P1** | BERTopic 增强主题分类 | `pip install bertopic umap-learn hdbscan` | 高 |
| **P1** | 条款级语义相似度增强制度×监管关联 | text2vec + scikit-learn | 高 |
| **P1** | 依存句法增强条文结构 + 关系抽取 | HanLP/LTP | 高 |
| **P2** | APRCOIE 中文 OIE | 从 GitHub 获取 | 中 |
| **P2** | 新增 `quarantine_fixer.py`，自动修复隔离记录 | P0 | 中 |
| **P2** | WeKnora docreader 独立部署 + `docreader_client.py` | Docker | 中 |
| **P3** | ParadeDB 替代 SQLite FTS5 | PostgreSQL + pgvector | 低 |
| **P3** | SignalGraph 确定性图构建 | 纯 Python | 低 |


## 十一、关键约束

| 约束 | 说明 |
|---|---|
| **零 Token** | 所有引入工具均为本地模型/服务，不调用 LLM API |
| **零硬依赖** | 新增模块作为**增强层**，未安装时回退现有正则 |
| **单一事实源** | `relations_index.jsonl` / `merged_view.json` / `cleaned CSV+JSONL` 仍为 SSOT，新增模块只增强抽取/清洗，不改 schema |
| **门禁兼容** | `gate_relations` / `gate_citations` / `gate_contract` 校验对象不变 |
| **降级链** | 所有新增模块必须保留正则回退路径 |
| **规则注册** | 新增 `gate_clean_rules` 校验清洗规则注册完整性 |