# -*- coding: utf-8 -*-
"""gates.gate_config_integrity — 配置与清单一致性门禁（W4-c 五拆，方案 7.5.1）。

原 1407 行单文件按判据族拆分（与 governance_store 拆分同构——包 + 门面）：
    gate_config_integrity/__init__.py（本文件）run() 聚合入口 + 公共基础设施转供
    _common.py     包内公共基础设施（ROOT/_MANIFEST/_load_module_from_path 等）
    _manifest.py   J 族：tools 清单 / worklist kind 闭合 / corpus 索引一致性
    _schedule.py   S/T 族：schedule.yaml 作业 + triggers 条件步骤（含 T4）
    _steps.py      R 族：STEP_ORDER 清单一致性 + STEP_LAYERS 语义声明（含 R4）
    _domains.py    V/U 族：agent 状态 / domains 登记 / theme 覆盖 / semantic manifest
    _structure.py  B 族：config/constants 结构断言

纪律：run() 是唯一入口（gates/__init__.py ALL_GATES 只认本模块的 run）；
各族判据函数经包内私有模块承载，跨族共用件住 `_common`（**子模块一律
`from ._common import …`，禁止回头 import 门面**——环状导入虽运行期可解，
mypy 却无法定类型，曾致 CI mypy 阻断 [has-type]）；族间禁止横向 import。
import 路径不变：`from gates.gate_config_integrity import run`。
"""

from __future__ import annotations

import os

from ._cassette import _check_cassette_manifest

# 公共基础设施经 _common 单点定义，本门面**原样转供**（noqa F401）：保持既有
# import 面（消费方/tests 经 `gates.gate_config_integrity.<name>` 访问不变）。
from ._common import (  # noqa: F401
    _MANIFEST,
    _REF_EXTS,
    _REF_SKIP_DIRS,
    _RETIRED_DIR,
    _RETIRED_README,
    _SCAN_BASE_SKIP,
    _TOOLS,
    ROOT,
    _load_manifest,
    _load_module_from_path,
)
from ._console import _check_console_discipline
from ._domains import (
    _check_agent_state,
    _check_domains,
    _check_semantic_manifest,
    _check_theme_coverage,
)
from ._filebudget import _check_file_budget
from ._manifest import (
    _check_corpus,
    _check_modules_manifest,
    _check_readme_gate_count,
    _check_tools,
    _check_worklist,
)
from ._schedule import _check_schedule, _check_triggers

# _chain_module 为**转供 re-export**（noqa F401）：消费方 gate_declaration_executor/
# mcp_server 的既有 import 路径 `from gates.gate_config_integrity import _chain_module`。
from ._steps import _chain_module, _check_step_layers, _check_step_order  # noqa: F401
from ._structure import _check_constants


def run() -> tuple[bool, dict]:
    if not os.path.exists(_MANIFEST):
        return False, {"error": f"缺少脚本清单 {_MANIFEST}（v2 §3.15.5 J1）"}

    problems: list[str] = []
    warnings: list[str] = []
    detail: dict = {}

    p1, d1, w1 = _check_tools(_load_manifest())
    problems += p1
    detail.update(d1)
    warnings += w1

    p2, d2 = _check_constants()
    problems += p2
    detail.update(d2)

    p3, d3 = _check_worklist()
    problems += p3
    detail.update(d3)

    p4, d4 = _check_schedule()
    problems += p4
    detail.update(d4)

    p5, d5 = _check_triggers()
    problems += p5
    detail.update(d5)

    p6, d6 = _check_step_order()
    problems += p6
    detail.update(d6)

    # 判据 R4（W1）：STEP_LAYERS 语义声明完整（受控 kind/条件 trigger_id/
    # depends_on 无环/produces ∈ GOV_ARTIFACTS）——W4-c 拆分时曾漏接，已补回。
    p6b, d6b = _check_step_layers()
    problems += p6b
    detail.update(d6b)

    p7, d7 = _check_agent_state()
    problems += p7
    detail.update(d7)

    p8, d8 = _check_domains()
    problems += p8
    detail.update(d8)

    # 判据 T（N-76）：主题关键词表覆盖完整性
    p9, d9 = _check_theme_coverage()
    problems += p9
    detail.update(d9)

    # 判据 U（N-102）：P1 语义工具清单自洽（零硬依赖/降级链/指纹三条纪律机器化）
    p10, d10 = _check_semantic_manifest()
    problems += p10
    detail.update(d10)

    p4, d4 = _check_corpus()
    problems += p4
    detail.update(d4)

    # 判据 J9（W4 §7.2 A 类）：modules 脚本三态归属闭合
    # （chain / independent 登记 / retired——零引用脚本 37 → 0 的门禁化）
    p11, d11, w11 = _check_modules_manifest()
    problems += p11
    detail.update(d11)
    warnings += w11

    # 判据 J10（W5）：README 门禁计数 ≡ ALL_GATES（禁文本写死数量的机器对冲）
    p12, d12 = _check_readme_gate_count()
    problems += p12
    detail.update(d12)

    # 判据 J11（§13.11）：单文件行数预算（超限集合 ⊆ 策略基线，只减不增）
    p13, d13 = _check_file_budget(ROOT)
    problems += p13
    detail.update(d13)

    # 判据 J12（§8）：确认台（web/）纪律——只读取数/唯一引导/零 CDN/serve 已接线/响应脱敏
    p14, d14 = _check_console_discipline(ROOT)
    problems += p14
    detail.update(d14)

    # 判据 J13（TD-1）：采集器录制-重放夹具完整性——manifest 双向闭合/摘要一致/
    # 结构自校验/无凭据残留/origin 受控（防「回放测试绿着说谎」）
    p15, d15 = _check_cassette_manifest(ROOT)
    problems += p15
    detail.update(d15)

    if warnings:
        detail["warnings"] = warnings
    if problems:
        detail["problems"] = problems
    return (not problems), detail


if __name__ == "__main__":
    # N-172：单跑入口**统一实现**（原 8 处各自复制同一段）
    from gates import run_cli

    raise SystemExit(run_cli("config_integrity", run))
