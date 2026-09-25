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
DATA_DIR = os.path.join(ROOT, "data")          # 运行期数据（git 忽略）

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
    import config.constants as _c  # noqa: PLC0415  局部导入：避免 config 包初始化顺序耦合

    try:
        spec = _c.module_by_pkg(name)
    except KeyError:
        spec = _c.module_by_key(name)      # 未知时抛出带允许值的 KeyError
    return os.path.join(MODULES_DIR, spec.pkg)


def ensure_dirs() -> None:
    """确保骨架目录存在（幂等）。P0 后保留供安装/测试调用。"""
    for d in (CONFIG_DIR, INTERFACES_DIR, GATES_DIR, STD_LIB_DIR,
              MODULES_DIR, REPORTS_DIR, TOOLS_DIR, SCHEMA_DIR, DATA_DIR):
        os.makedirs(d, exist_ok=True)


if __name__ == "__main__":
    print(f"ROOT={ROOT}")
    ensure_dirs()
