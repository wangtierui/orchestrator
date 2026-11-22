# 政府公文法规 RAG 系统 — 架构概要

## 技术选型

| 层 | 语言/技术 | 理由 |
|---|---|---|
| 采集 | Java (Spring Boot + Jsoup/WebMagic) | 长稳抓取、并发与异常处理成熟 |
| RAG | Python (LangChain/LlamaIndex) | Embedding、向量检索、LLM 生态一等公民 |
| 接口 | MCP (FastMCP, Python) | Agent 原生工具调用协议 |
| 存储 | PostgreSQL + pgvector | 单一存储，运维最简；规模足够 |

## 数据流

```
政府网站 → Java 采集 → 文档表 → Python 索引 → Chunk 表(含向量)
                                                ↓
Workbuddy (Agent) → MCP Tool → PG 混合检索 → 带引用的回答
```

## 存储设计

所有数据（元数据、正文、向量）统一存储在 PostgreSQL（pgvector 扩展），不引入独立向量库。

**逻辑分两张表，职责分离：**
- **文档表**：Java 采集器写入，Python 读取。存原始文档与采集状态，驱动增量处理。
- **Chunk 表**：Python 索引器写入，RAG 检索用。存切分后的文本片段与向量，通过外键关联文档表。
- 向量列加 HNSW 索引，文本列加 GIN 全文索引；关键词 + 语义混合检索在 PG 内完成。

**字段设计原则（尚未冻结，待真实数据验证后确定）：**
- 只定义置信度高的最小字段集，不确定的属性先存入 `JSONB` 兜底字段，高频查询后再提升为正式列。
- 状态字段用字符串枚举而非布尔，便于后续扩展取值。
- 大字段（原始 HTML/PDF）外置对象存储，表内仅存引用键。

## 关键约束

1. 跨语言契约 = PG 表结构，migration 用 Flyway/Liquibase 版本化；大字段（原始 HTML/PDF）外置对象存储，表内存 `object_key`。
2. Embedding 模型定死维度（如 `bge-large-zh-v1.5` 为 1024 维），换模型需全量重算。
3. 采集触发设计为异步：MCP 返回 `task_id`，不阻塞等待。
4. MCP 只封装 RAG 查询能力（`search_regulations`, `get_regulation_by_id`）；采集管理不暴露给 Agent。

## 扩展边界

数据量达千万级 chunk 或 HNSW 查询 P99 > 500ms 时，再考虑迁移专用向量库（Qdrant/Milvus）。法规采集场景增长慢、并发低，大概率无需拆分。
