
---

# 📘 完整版 README 撰写规范与内容大纲

> **项目名称**：[请在此处填写项目名称，例如：订单履约中台系统]
> **文档版本**：`v2.0.0`
> **维护团队**：[请填写团队名称]
> **最后更新**：[YYYY-MM-DD]

---

## 1. 项目概述 (Project Overview)

- **背景与痛点**：[用 2-3 句话说明为什么要做这个项目，解决了什么业务或技术痛点。例如：随着订单量激增，原有单体架构无法支撑高并发写入，且多源订单数据格式不一致导致履约效率低下。]
- **核心目标**：[用 1 句话概括项目核心价值。例如：构建一个高可用、可扩展的订单履约中台，统一订单数据模型，实现日处理百万级订单的能力。]
- **技术栈全景**：
  - **后端核心**：Python 3.11+ / FastAPI / SQLAlchemy 2.0 (异步)
  - **基础设施**：PostgreSQL 14 / Redis 7.0 (Cache & Lock) / RabbitMQ (Message Queue) / Kubernetes 1.28
  - **可观测性**：Prometheus + Grafana (Metrics) / ELK (Logs) / Jaeger (Tracing)
  - **依赖管理**：Poetry / Pyproject.toml (遵循 PEP 621)

---

## 2. 目录结构规范与文件用途详解 (Directory Structure & File Manifest)

> **核心原则**：本节定义了项目物理文件的组织规范。所有新增文件必须符合此结构。文件中标注了 **【常规流程脚本】** 与 **【特殊/工具脚本】** ，用于区分核心运行时逻辑与运维/一次性辅助逻辑。

### 2.1 根目录与一级模块总览

```
project-root/
├── .github/                    # 【CI/CD 常规流程】GitHub Actions 流水线定义
│   └── workflows/
│       ├── ci.yml              # 持续集成：Lint → Test → Build
│       └── cd.yml              # 持续部署：Helm 升级 K8s 集群
├── src/                        # 【核心常规流程】应用核心源代码
│   ├── main.py                 # FastAPI 应用入口（工厂模式）
│   ├── api/                    # 接口层 (REST Controllers)
│   ├── core/                   # 核心配置与工具 (Config, Logger, Exception)
│   ├── models/                 # SQLAlchemy ORM 实体模型
│   ├── services/               # 业务逻辑层 (Domain Services)
│   └── repositories/           # 数据访问层 (DAO)
├── tests/                      # 【常规流程】自动化测试用例
│   ├── unit/                   # 单元测试 (Fast, Isolated)
│   ├── integration/            # 集成测试 (需依赖 DB/Redis)
│   └── e2e/                    # 端到端测试 (Full Stack)
├── deploy/                     # 【部署常规流程】K8s 编排文件
│   └── helm/
│       ├── values.yaml         # 默认环境变量与资源配置
│       └── templates/          # K8s Deployment, Service, Ingress 模板
├── scripts/                    # 【混合目录】辅助脚本
│   ├── init_db.py              # 【特殊/工具】首次初始化数据库表与基础数据
│   ├── data_migration_v2.py    # 【特殊/工具】版本间存量数据清洗脚本
│   ├── daily_stats.py          # 【常规流程】每日 CronJob 统计脚本
│   └── benchmark.py            # 【特殊/工具】本地 Locust 压测脚本
├── alembic/                    # 【常规流程】数据库迁移 (DDL 版本管理)
│   └── versions/               # 包含所有 upgrade/downgrade 迁移文件
├── docs/                       # 【特殊辅助】外部设计文档与架构图
├── .env.example                # 【特殊辅助】环境变量模板
├── pyproject.toml              # 【元数据】项目依赖与 PEP 621 规范定义
├── Makefile                    # 【常规流程】项目快捷命令入口 (如 make run/test)
└── README.md                   # 本文档
```

### 2.2 核心目录文件用途清单（脚本分类明细）

*编写规范：请按以下表格模板，详述每一个核心目录及其内部文件的职责。*

| 路径 (Path) | 文件/目录类型 | **脚本分类 (Script Type)** | 用途描述 (Description) | 依赖/被调用方 (Caller) |
| :--- | :--- | :--- | :--- | :--- |
| **`src/`** | 目录 | **常规流程** | 全部业务逻辑代码所在。采用 **DDD（领域驱动设计）** 分层架构。 | 由 Uvicorn/Gunicorn 启动加载。 |
| `src/main.py` | 文件 | **常规流程（入口点）** | FastAPI 应用实例工厂。负责注册路由、中间件、数据库连接池及生命周期钩子（启动/关闭）。 | 被 `uvicorn` 命令调用；被 `tests` 引用。 |
| `src/api/routes/` | 目录 | **常规流程** | 定义 HTTP 接口层（Controller）。每个文件对应一类资源（如 `order.py`）。 | 由 `src/main.py` 中的路由器注册。 |
| `src/core/config.py` | 文件 | **常规流程** | 配置管理（Pydantic Settings）。从 `.env` 文件加载环境变量，并校验必填项。 | 被 `main.py` 以及所有 Service 层引用。 |
| `src/models/` | 目录 | **常规流程** | SQLAlchemy ORM 实体定义。与 `alembic` 配合生成 DDL。 | 被 Service 层和 Repository 层引用。 |
| `src/services/` | 目录 | **常规流程** | 业务逻辑层（核心）。编排多个 Repository 和外部 API 调用。 | 被 `api/routes` 层调用。 |
| **`scripts/`** | 目录 | **混合目录** | 存放非业务常驻运行的脚本。 | 通常手动触发或由 CI/定时任务触发。 |
| `scripts/init_db.py` | 文件 | **特殊工具脚本（一次性/初始化）** | 用于新环境初始化：创建数据库、扩展（如 pg_trgm）、初始化基础字典数据。 | 仅在环境搭建时手动执行一次。 |
| `scripts/data_migration_v2.py` | 文件 | **特殊工具脚本（数据修复/迁移）** | 用于版本间数据结构变更后的存量数据清洗与回填（例如：将旧状态枚举映射为新枚举）。 | 发版时由运维或开发人员执行一次。|
| `scripts/daily_stats.py` | 文件 | **常规流程脚本（定时任务）** | 每日 T+1 计算报表统计数据。包含完整的日志记录与异常捕获。 | 由 K8s CronJob 在每日凌晨调用。 |
| `scripts/benchmark.py` | 文件 | **特殊工具脚本（压测/性能）** | 基于 Locust 的本地压力测试脚本，用于上线前的容量评估。 | 开发或运维手动执行。 |
| **`alembic/`** | 目录 | **常规流程（变更管理）** | 数据库 Schema 版本管理。 | 由 `alembic` 命令行工具驱动。 |
| `alembic/versions/` | 目录 | **常规流程** | 包含所有按时间排序的 DDL 升级（`upgrade`）与回滚（`downgrade`）脚本。 | 应用启动时由 `alembic upgrade head` 自动执行。 |
| **`.github/`** | 目录 | **常规流程（CI/CD）** | Git 仓库的协作与自动化规范。 | - |
| `.github/workflows/ci.yml` | 文件 | **常规流程（流水线）** | PR 合并前触发：代码 Lint → 单元测试 → 集成测试 → 构建镜像。 | 由 GitHub 事件（如 `pull_request`）触发。 |
| `.github/workflows/cd.yml` | 文件 | **常规流程（流水线）** | 发布规范：打 Tag 后自动构建，并依据环境（Staging/Prod）执行 Helm 升级。 | 由 Git Tag 推送事件触发。 |
| **`deploy/`** | 目录 | **常规流程（交付物）** | Kubernetes 编排文件。 | 由 `kubectl` 或 CI/CD 流水线应用至集群。 |
| `deploy/helm/values.yaml` | 文件 | **常规流程** | 环境变量与 K8s 资源配置（CPU/Memory/Replicas）的声明。 | 被 Helm Chart 模板渲染。 |

---

## 3. 核心数据字典与物理模型 (Data Dictionary & Schema)

> **规范说明**：所有数据项必须在此定义，作为客户端、服务端及下游消费方的“单一事实来源”(Single Source of Truth)。

### 3.1 实体关系总览 (ER Diagram)
*(建议在此处嵌入用 PlantUML 或 DBML 生成的类图或表关系图)*

### 3.2 核心数据对象定义

请按以下表格模板，详尽定义系统中每一个核心数据结构。

| 数据项名称 (Field) | 数据类型 (Type) | 必填 (Required) | 枚举值/格式约束 (Enum/Format) | 业务含义/示例 (Description/Example) | 所属表/模块 (Scope) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `order_id` | `string` | 是 | `^ORD-[A-Z0-9]{12}$` | 全局唯一订单编号。规则：ORD + 时间戳 + 随机码。例：`ORD-20260808-ABCD` | `orders` 表 |
| `order_status` | `enum` | 是 | `PENDING`, <br>`PROCESSING`, <br>`SHIPPED`, <br>`CANCELLED` | 订单当前生命周期状态。 <br>**注意**：`CANCELLED` 为终态，不可逆。 | `orders` 表 |
| `price_amount` | `decimal(10,2)` | 是 | 最大值 `99999999.99` | 商品单价，单位：元（RMB）。采用银行家舍入法保留两位小数。 | `order_items` 表 |
| `is_deleted` | `boolean` | 否 | `true` / `false` | 逻辑删除标记。默认为 `false`，查询时需过滤 `true` 的记录。 | 所有核心表 |
| `ext_info` | `jsonb` | 否 | 无 | 扩展元数据，用于存放非结构化且频繁变动的字段（如前端埋点参数）。 | `orders` 表 |

### 3.3 枚举值（Enum）全量清单
- **`OrderStatus` (订单状态)**：
  - `PENDING` (待支付)：用户已下单，等待支付回调。
  - `PROCESSING` (处理中)：支付成功，系统进行库存扣减与风控。
  - `SHIPPED` (已发货)：物流单号已生成。
  - `CANCELLED` (已取消)：触发条件为用户主动取消或支付超时。

---

## 4. 数据流转与拓扑 (Data Flow & Topology)

> **说明**：描述数据从产生、处理、存储到消费的完整路径。包含 ETL（抽取-转换-加载）逻辑及消息队列的 Topic 设计。

### 4.1 业务端到端数据流图（Mermaid）

以下图表展示了从数据产生（上游）到最终消费（下游）的全链路，包含**同步/异步边界**和**存储层级**。

```mermaid
flowchart TD
    %% 上游来源
    A[第三方平台 Webhook] -->|HTTP POST| B[API Gateway]
    C[前端用户操作] -->|HTTP REST| B
    
    %% 接入层
    B -->|JWT 鉴权 + 限流| D[Order Service]
    
    %% 业务处理层
    D -->|1. 格式校验| E{数据字典校验}
    E -->|失败| F[返回 400 错误]
    E -->|通过| G[2. 业务规则引擎]
    G -->|库存扣减| H[(PostgreSQL 主库)]
    G -->|发送事件| I[[Message Queue<br>topic.order.lifecycle]]
    
    %% 异步消费者
    I --> J[履约消费者]
    I --> K[通知消费者]
    I --> L[审计日志消费者]
    
    %% 下游存储
    J -->|写入物流单| M[(PostgreSQL 从库)]
    K -->|发送邮件/SMS| N[第三方消息推送]
    L -->|写入操作日志| O[(Elasticsearch)]
    
    %% 缓存层
    H -->|CDC (Debezium)| P[Kafka]
    P -->|增量同步| Q[(Redis Cache)]
    
    %% 报表层
    M -->|T+1 数据同步| R[(OLAP 数据仓库<br>ClickHouse)]
    R -->|查询| S[BI 看板 / 报表]
    
    %% 样式
    classDef async fill:#ffe6cc,stroke:#d79b00
    classDef storage fill:#d5e8d4,stroke:#82b366
    classDef external fill:#e1d5e7,stroke:#9673a6
    
    class I,J,K,L async
    class H,M,Q,O,R storage
    class A,C,N external
```

### 4.2 ETL/数据处理管道明细

| 阶段 (Stage) | 数据源 (Source) | 目标存储 (Target) | 转换逻辑 (Transform) | 所属脚本/Job | **脚本分类** |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Extract (抽取)** | PostgreSQL（从库） | Kafka Topic `cdc.order` | Debezium 捕获 CDC 变更日志 | 无（外部组件） | 常规流程 |
| **Transform (转换)** | Kafka | ClickHouse（DWD 层） | Flink SQL：清洗无效字符、标准化状态枚举 | `flink-job-order.sql` | 常规流程（实时） |
| **Load (加载)** | ClickHouse（DWD） | ClickHouse（DWS 聚合表） | 每日汇总：`COUNT`, `SUM` 聚合 | `scripts/daily_stats.py` | **常规流程（定时任务）** |
| **Export (导出)** | ClickHouse | 阿里云 OSS / S3 | 导出为 Parquet 格式供外部 BI 工具读取 | `scripts/export_bi.py` | **特殊工具（按需执行）** |

---

## 5. 自动化任务节点与门禁设置 (Automation & Guardrails)

> **重要**：本节定义了代码合并、数据变更、生产操作及自动化节点的准入规则。

### 5.1 CI/CD 流水线任务矩阵

| 任务节点 (Job) | 触发条件 (Trigger) | 执行动作 (Action) | 超时 (Timeout) | 失败处理 (Failure Handling) |
| :--- | :--- | :--- | :--- | :--- |
| **lint-and-type** | `pull_request` / `push` to `main` | `ruff check` + `mypy --strict` | 5 min | 阻断 PR 合并，标注具体错误行 |
| **unit-test** | `pull_request` | `pytest tests/unit/ --cov` | 10 min | 阻断合并，输出覆盖率报告 |
| **integration-test** | `pull_request` (含 `[integration]` 标签) | `docker-compose up` + `pytest tests/integration/` | 20 min | 阻断合并，自动清理残留容器 |
| **build-and-push** | `push` to `main` (tag 匹配 `v*`) | `docker build` + `docker push` 至 **Harbor** | 15 min | 发送告警至 #devops 频道 |
| **deploy-staging** | 手动触发 (workflow_dispatch) | Helm 升级至 K8s 测试集群 | 10 min | 自动回滚至上一个稳定版本 |
| **deploy-prod** | 手动触发 (需审批) | 金丝雀发布 (5% → 50% → 100%) | 30 min | 自动切流 + 人工介入 |

### 5.2 定时与事件驱动任务清单

| 任务名称 | 触发方式 | **脚本/镜像位置** | **脚本分类** | 超时/重试策略 |
| :--- | :--- | :--- | :--- | :--- |
| `order-timeout-cancel` | Cron (每 5 分钟) | `src/services/timeout_scanner.py` | 常规流程（后台常驻） | 超时 60s / 重试 3 次 |
| `cache-warmup` | Cron (每日 08:00) | `scripts/warmup_cache.py` | 常规流程（定时任务） | 超时 10min / 失败告警 |
| `db-backup` | Cron (每日 03:00) | `scripts/backup_postgres.sh` | **特殊工具（运维保障）** | 超时 2h / 失败转人工 |

### 5.3 代码与数据质量门禁 (Quality Gates)

- **代码门禁**：
  - 增量测试覆盖率 ≥ **80%**（使用 `pytest-cov` 检查）。
  - 必须通过 `mypy --strict` 类型检查（不允许使用 `Any` 类型逃避检查）。
  - 必须通过 `ruff check --fix` 且无 Error 级问题。
- **数据门禁（写入拦截）**：
  - 核心字段空值率 > 1% 时阻断 ETL 任务。
  - `order_status` 仅允许定义内的枚举值（详见第 3 节数据字典）。
  - **禁止**在非紧急情况下手动 `UPDATE` 核心业务表（`orders`/`payments`）。
- **发布门禁**：
  - `main` 分支禁止直接 Push，必须通过 **PR (Pull Request)** 合并。
  - 生产环境发布必须触发 **“人工审批 (Manual Approval)”** 且至少 2 名 Reviewer 通过。

---

## 6. 环境搭建与本地开发 (Quick Start)

1. **克隆代码**：
   ```bash
   git clone https://github.com/your-org/your-project.git
   cd your-project
   ```

2. **环境准备**：
   ```bash
   cp .env.example .env
   # 根据本地环境修改 .env 中的数据库连接等配置
   ```

3. **依赖安装**（含开发依赖）：
   ```bash
   poetry install
   ```

4. **启动依赖中间件**（Docker Compose）：
   ```bash
   docker-compose up -d postgres redis rabbitmq
   ```

5. **执行数据库 Schema 迁移（常规流程脚本）**：
   ```bash
   alembic upgrade head
   ```
   > 注：此命令会执行 `alembic/versions/` 下的所有 DDL 升级脚本。

6. **填充种子数据（特殊工具脚本）**：
   ```bash
   python scripts/init_db.py
   ```
   > 注：此脚本为非必须步骤，仅在首次搭建或个人调试时需要。

7. **启动应用**：
   ```bash
   uvicorn src.main:app --reload --host 0.0.0.0 --port 8000
   ```

8. **验证**：
   访问 [http://localhost:8000/docs](http://localhost:8000/docs) 查看自动生成的 Swagger API 文档。

---

## 7. 附录与延伸阅读

- **架构决策记录 (ADR)**：参见 `docs/adr/`。
- **错误码全量清单**：参见 `docs/error_codes.md`。
- **变更日志**：参见 `CHANGELOG.md`（遵循 Keep a Changelog 规范）。
- **PyPA 规范合规性**：本项目的 `pyproject.toml` 遵循 [PEP 621](https://peps.python.org/pep-0621/) 标准，数据字典设计符合 PyPA 核心元数据规范。

---

### 📌 撰写提示

1. **脚本分类的逻辑**：表格中严格区分 **“常规流程脚本”** 与 **“特殊工具脚本”**，目的是明确 **SRE 运维责任**。
   - **常规流程脚本**：随应用部署，异常时需要自动恢复（K8s 重启或 Supervisor 守护），必须包含完善的日志与监控埋点。
   - **特殊工具脚本**：通常只在特定时间由特定角色（DBA/运维）运行，运行前建议进行 **“双人复核 (Peer Review)”**。
2. **文件级颗粒度**：如果项目 `src/` 下有大量子包，建议将上述第 2.2 节的表格拆分为 **“核心入口文件”** 和 **“业务包通识”** 两部分，避免 README 过于臃肿（可把全部文件清单移至 `docs/file_manifest.md`，在 README 中仅保留关键路径）。
3. **依赖关系链**：在描述文件用途时，务必带上 **“被谁调用”**（如 `src/models/` 被 `alembic` 和 `services` 引用），这有助于新人理解代码的调用层级（依赖倒置）。
4. **Mermaid 渲染**：上述 Mermaid 图表在 GitHub/GitLab 上**原生支持**，无需额外工具。

---
