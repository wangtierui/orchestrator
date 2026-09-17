# -*- coding: utf-8 -*-
"""
paths.py — regulatory_compliance_orchestrator 路径唯一事实源（R4 落地）

设计纪律：
  1) 本文件是**全仓唯一**路径解析入口；禁止在业务代码中硬编码盘符绝对路径或 sys.path.insert。
  2) 环境变量可覆盖（REG_ORCH_ROOT），用于 CI / 非标准部署 / 测试隔离。
  3) common_lib 不再内置第二套路径逻辑（R4：删除 paths_api 概念），一律 from paths import ...。
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
DATA_DIR = os.path.join(ROOT, "data")          # 预留：D-05 决策数据随仓，本目录可选
TESTS_DIR = os.path.join(ROOT, "tests")

# ---- 治理库（阶段 1，2026-09-18）----
# 三轨制之「治理轨」：只放元数据/状态/关系/审计/水位（目标 < 50 MB），不含语料正文。
# 位于仓根 data/（.gitignore 的 `data/` 规则已忽略）；机器本地态，跨机审计链由
# exports/ 文本快照与 reports/ 台账承载。唯一读写实现 =
# std_lib/common_lib/governance_store.py（环境变量 REG_ORCH_GOVERNANCE_DB 可覆盖）。
GOVERNANCE_DB = os.path.join(DATA_DIR, "governance.db")

# ---- config 子路径 ----
ENUMS_FILE = os.path.join(CONFIG_DIR, "enums.py")
SOURCES_YAML = os.path.join(CONFIG_DIR, "sources.yaml")
OCR_YAML = os.path.join(CONFIG_DIR, "ocr.yaml")
SCHEMA_DIR = os.path.join(CONFIG_DIR, "schema")

# ---- modules 子仓（沿用现有名，D-02）----
def module_dir(name: str) -> str:
    """返回 modules/<name> 绝对路径（仅允许白名单名）。"""
    allowed = {"regulatory_scrapers", "regulatory_classifier",
               "internal_policy_base", "internal_policy_drafter"}
    if name not in allowed:
        raise ValueError(f"未知模块名: {name}（允许 {sorted(allowed)}）")
    return os.path.join(MODULES_DIR, name)


def ensure_dirs() -> None:
    """确保骨架目录存在（幂等）。P0 后保留供安装/测试调用。"""
    for d in (CONFIG_DIR, INTERFACES_DIR, GATES_DIR, STD_LIB_DIR,
              MODULES_DIR, REPORTS_DIR, TOOLS_DIR, SCHEMA_DIR, DATA_DIR):
        os.makedirs(d, exist_ok=True)


if __name__ == "__main__":
    print(f"ROOT={ROOT}")
    ensure_dirs()
