# -*- coding: utf-8 -*-
"""
paths.py — regulatory_compliance_orchestrator 路径唯一事实源（R4 落地）

设计纪律：
  1) 本文件是**全仓唯一**路径解析入口；业务代码禁止硬编码盘符绝对路径。
  2) 环境变量可覆盖（REG_ORCH_ROOT），用于 CI / 非标准部署 / 测试隔离。
  3) 模块清单不再本地定义：`module_dir()` 取自 `config.constants.MODULE_SPECS`
     （v2 §3.1.1；此前本地 4 项白名单与门禁的 5 项互相矛盾，且本函数零调用方）。
  4) `sys.path` 引导统一走 `bootstrap.py`（v2 §3.1.2）；本文件不做引导。

常量消费状况（v2 §2.1 A5，`gate_runtime_hygiene` 记录基线）：
  - 已消费：ROOT / MODULES_DIR / REPORTS_DIR / DATA_DIR / GOVERNANCE_DB /
    SOURCES_YAML / OCR_YAML / TOOLS_DIR / TESTS_DIR / CORPUS_DIR
  - 预留（当前无消费方，保持 API 稳定，供后续接入）：CONFIG_DIR / INTERFACES_DIR /
    GATES_DIR / STD_LIB_DIR / ENUMS_FILE / SCHEMA_DIR / INBOX_DIR（P2-3b 接入）

W4（方案 §7.10 目录 SSOT 收口，2026-10-15）：补八个顶层目录常量 +
  EXTERNAL_DIR / TESSDATA_DIR / LOGS_DIR / BACKUPS_DIR / ARCHIVE_DIR / EXPORTS_DIR /
  GRAPHIFY_OUT_DIR / DELIVERABLES_DIR——此前这些目录在全仓**自行拼接**（tessdata 5 处、
  logs/archive/exports/graphify-out 各 2~6 处；交付库 docs/reports 15 处），
  "目录一旦调整会漏改"（写入方与读取方静默分叉）。语义分工（§7.10 问题 6/7 的成文裁决）：
  · `logs/` = 工具/采集器日志（git-autopush.log 等；retention `logs_stale` 14d 管）；
  · `reports/_tmp/logs/` = **链步骤日志**（_run 的重定向落点；retention `reports_tmp` 7d 管）
    ——两者**不合并**（保留策略不同；待人工确认项已按"明确分工"结案）；
  · `backups/`（仓根）= retention 管理的 repo_baks；模块内 backups = 随数据迁移的
    操作备份（两套体系分工见 §7.10 问题 6）；
  · `external/` = 全部外部资产统一外置（模型权重/第三方源码/tessdata），仓根不再
    平铺工具数据目录（tessdata 已迁入，同卷 rename）。
"""
import os

# 本仓库根（paths.py 所在目录）
ROOT = os.environ.get("REG_ORCH_ROOT") or os.path.dirname(os.path.abspath(__file__))

# ---- 顶层目录 ----
CONFIG_DIR = os.path.join(ROOT, "config")
INTERFACES_DIR = os.path.join(ROOT, "interfaces")
GATES_DIR = os.path.join(ROOT, "gates")
STD_LIB_DIR = os.path.join(ROOT, "std_lib")
MODULES_DIR = os.path.join(ROOT, "modules")
REPORTS_DIR = os.path.join(ROOT, "reports")
TOOLS_DIR = os.path.join(ROOT, "tools")
TESTS_DIR = os.path.join(ROOT, "tests")
DOCS_DIR = os.path.join(ROOT, "docs")            # 人类文档根（§7.10：docs/= 对外文档、reports/= 对内过程）
DATA_DIR = os.path.join(ROOT, "data")          # 运行期数据（git 忽略）

# ---- W4（§7.10）新增顶层目录常量（原为全仓自拼）----
EXTERNAL_DIR = os.path.join(ROOT, "external")  # 外部资产统一外置（git 忽略、不分发）
TESSDATA_DIR = os.path.join(EXTERNAL_DIR, "tessdata")  # Tesseract 语言数据（§7.10 案例二迁入）
LOGS_DIR = os.path.join(ROOT, "logs")          # 工具/采集器日志（链步骤日志在 reports/_tmp/logs）
BACKUPS_DIR = os.path.join(ROOT, "backups")    # retention repo_baks（模块内 backups 另计）
ARCHIVE_DIR = os.path.join(ROOT, "archive")    # retention 归档（只移动不删除类目落点）
EXPORTS_DIR = os.path.join(ROOT, "exports")    # 治理库文本快照（跨机审计链）
GRAPHIFY_OUT_DIR = os.path.join(ROOT, "graphify-out")  # 图谱构建输出（git 忽略，225MB）
# W4（§7.10 案例一）：分析交付库根。原 `docs/reports/` 与 `reports/` **同名同级子树**
# （职责撞车：docs/= 对外交付物、reports/= 对内过程文档）→ 更名 `docs/deliverables/`，
# 与 docs/adr/ 并列（docs/= 人类文档根）。
DELIVERABLES_DIR = os.path.join(DOCS_DIR, "deliverables")

# ---- 治理库（阶段 1，2026-09-18）----
# 三轨制之「治理轨」：只放元数据/状态/关系/审计/水位（目标 < 50 MB），不含语料正文。
# 位于仓根 data/（.gitignore 的 `data/` 规则已忽略）；机器本地态，跨机审计链由
# exports/ 文本快照与 reports/ 台账承载。唯一读写实现 =
# std_lib/common_lib/governance_store.py（环境变量 REG_ORCH_GOVERNANCE_DB 可覆盖）。
GOVERNANCE_DB = os.path.join(DATA_DIR, "governance.db")

# ---- 语料分层（v2 §3.12，决策 D-9；2026-09-26 落地）----
# 三层：① 归集本体 `data/corpus/<domain>/`（**只读审计层**，为 originals/ 的硬链接目标）
#       ② 投放区 `data/inbox/<consumer>/`（投放/暂存，P2-3b）
#       ③ 归集清单 `reports/corpus/<domain>.manifest.json`（入库，唯一可审计入口）
# 纪律：本体必须**同卷 rename** 迁移（硬链接保活）；任何写方（ingest_corpus）只写本体目录，
#       不得写投放区；清单只写 reports/corpus/（域内不再保留副本）。
CORPUS_DIR = os.path.join(DATA_DIR, "corpus")
INBOX_DIR = os.path.join(DATA_DIR, "inbox")

# ---- config 子路径 ----
ENUMS_FILE = os.path.join(CONFIG_DIR, "enums.py")
SOURCES_YAML = os.path.join(CONFIG_DIR, "sources.yaml")
OCR_YAML = os.path.join(CONFIG_DIR, "ocr.yaml")
SCHEMA_DIR = os.path.join(CONFIG_DIR, "schema")


def module_dir(name: str) -> str:
    """返回 `modules/<name>` 绝对路径。

    `name` 接受**模块包名**（如 `regulatory_scrapers`）或**短键**（如 `scrapers`）；
    白名单唯一来源 = `config.constants.MODULE_SPECS`（含 `base_publish`）。
    """
    import config.constants as _c

    try:
        spec = _c.module_by_pkg(name)
    except KeyError:
        spec = _c.module_by_key(name)      # 未知时抛出带允许值的 KeyError
    return os.path.join(MODULES_DIR, spec.pkg)



def relations_dir() -> str:
    """关系产物目录（**唯一出口**）。"""
    return os.path.join(module_dir("regulatory_classifier"), "data", "relations")


def relations_index() -> str:
    """关系事实源 `relations_index.jsonl` 路径（**唯一出口**，2026-09-29 收敛）。

    收敛理由（`tools/audit_health.py`「数据源不唯一」）：此前 **8 个文件各自**用
    `os.path.join(<同名目录>, "relations_index.jsonl")` 构造同一路径 —— 真正的风险不是
    冗余本身，而是**目录一旦调整会漏改**，导致写入方与读取方**静默分叉**（两侧都"正常"，
    只是读的是旧位置）。所有消费方一律经本函数取路径。
    """
    return os.path.join(relations_dir(), "relations_index.jsonl")

def ensure_dirs() -> None:
    """确保骨架目录存在（幂等）。P0 后保留供安装/测试调用。"""
    for d in (CONFIG_DIR, INTERFACES_DIR, GATES_DIR, STD_LIB_DIR,
              MODULES_DIR, REPORTS_DIR, TOOLS_DIR, SCHEMA_DIR, DATA_DIR):
        os.makedirs(d, exist_ok=True)


if __name__ == "__main__":
    print(f"ROOT={ROOT}")
    ensure_dirs()
