# -*- coding: utf-8 -*-
"""
gates — 交付门禁（R23：门禁类别为唯一事实，实现/数量以 ALL_GATES 为准，禁止文本写死数量）

运行方式：python -m gates 或 orchestrator gates（见 cli.py）。每门禁实现返回 (passed, detail)，
GatesRunner 汇总 exit code；detail 建议 dict，含逐项原因。

P0 状态：门禁按"可空跑通过（TRIVIAL）/待接入（NOT_IMPLEMENTED）"标注；P1 起逐个以真实实现替换。
NOT_IMPLEMENTED 门禁在 P0 阶段以 exit 0 + note=待接入 通过（不阻塞骨架验收），
P1 切换 require_impl=True 后若未实现即 FAIL。
"""

from __future__ import annotations

import contextlib
import importlib
import sys

# 门禁注册表（键 = 模块名于 gates/ 下；order 决定执行顺序）
ALL_GATES: list[dict] = [
    {
        # N-152（2026-09-30）：**运行台账完整性** —— 事故：`clean:gov` 超时被杀、产物仍落盘
        # 被下游采用，而 `gates` 报"全部通过"（失败被无感吞掉）。本门禁读
        # `data/run_state/last_run_steps.json`，对失败步骤判定"其产物是否正由该步产生"
        # → 危险组合则 FAIL（可经 `acknowledged.json` 人工确认后降级为告警）。
        "module": "gates.gate_run_steps",
        "desc": "运行台账完整性（失败步骤的产物是否已被下游采用）",
        "require_impl": True,
    },
    {"module": "gates.gate_hardcoded_paths", "desc": "盘符字面量扫描（R4）", "require_impl": True},
    {"module": "gates.gate_enum_values", "desc": "受控枚举一致性（v3）", "require_impl": True},
    {
        "module": "gates.gate_flat_layout",
        "desc": "目录拍平（data/docs 无子目录）",
        "require_impl": True,
    },
    {
        "module": "gates.gate_no_duplicate_libs",
        "desc": "重复工具/重名再定义扫描",
        "require_impl": True,
    },
    {"module": "gates.gate_contract", "desc": "数据契约（列头/键集）", "require_impl": True},
    {
        "module": "gates.gate_hardcoded_snapshots",
        "desc": "硬编码 cleaned 快照日期（N-3）",
        "require_impl": True,
    },
    {"module": "gates.gate_rfn_sync", "desc": "RFN 跨文件一致性", "require_impl": True},
    {"module": "gates.gate_rfn_drift", "desc": "RFN↔clean 漂移未处置阻断", "require_impl": True},
    {"module": "gates.gate_timeliness_ssot", "desc": "时效单源传播一致性", "require_impl": True},
    {
        "module": "gates.gate_citations",
        "desc": "制度引用门禁（drafter --strict）",
        "require_impl": True,
    },
    {
        "module": "gates.gate_sources_config",
        "desc": "源目录配置一致性（R15）",
        "require_impl": True,
    },
    {
        "module": "gates.gate_provenance",
        "desc": "数据血缘 provenance 覆盖（R10）",
        "require_impl": True,
    },
    {
        "module": "gates.gate_field_aliases",
        "desc": "中文列名受控注册（字段治理）",
        "require_impl": True,
    },
    {
        "module": "gates.gate_secret_scan",
        "desc": "密钥/敏感值硬编码扫描（审查 P1-4）",
        "require_impl": True,
    },
    {
        "module": "gates.gate_original_resolvable",
        "desc": "内部制度索引↔原件库可解析性",
        "require_impl": True,
    },
    {
        "module": "gates.gate_relations",
        "desc": "依据/废止关系产物（R-F01：键集/枚举/强引用/溯源/统计）",
        "require_impl": True,
    },
    {
        "module": "gates.gate_watermark",
        "desc": "产物水位一致性（阶段 1：水位比对替代 mtime）",
        "require_impl": True,
    },
    {
        "module": "gates.gate_no_cross_module_import",
        "desc": "跨模块直连扫描（阶段 3：modules/ 零越权引导与裸 import）",
        "require_impl": True,
    },
    # ---- v2 重构新增（2026-09-26）：18 → 22 道 ----
    # 口径修正（v2 §3.8 表 A/B 的门禁计数不一致）：原计划"新增 6 道"，
    # 其中 `gate_module_registry` 与 `gate_cross_module_data` 实际以**判据**形式
    # 落在 `gate_config_integrity`（B2）与 `gate_no_cross_module_import`（判据 D，P1-3）中，
    # 不单独计数；因此本次新增 **4 道**（当时总数 22）。
    #
    # ⚠️ **门禁总数以 `ALL_GATES` 长度为准（唯一事实源），此处不重复维护计数** ——
    #    实测曾因「新增门禁未更新本注释」留下 **22 vs 实际 23** 的文档漂移
    #    （`tools/audit_health.py`「门禁失效」判据会机器核验「注释里写的总数」，勿再写死）。
    {
        "module": "gates.gate_config_integrity",
        "desc": "配置与清单一致性（tools 清单 J1–J6/结构清单/退出码）",
        "require_impl": True,
    },
    {
        "module": "gates.gate_import_bootstrap",
        "desc": "sys.path 引导纪律（commands/ 清零；其余层基线冻结）",
        "require_impl": True,
    },
    {
        "module": "gates.gate_runtime_hygiene",
        "desc": "运行时卫生（退出码/静默异常/接口空壳；首期只披露）",
        "require_impl": True,
    },
    {
        "module": "gates.gate_clean_schema",
        "desc": "清洗校验质量（失败记录不得进入交付；v2 §3.4）",
        "require_impl": True,
    },
    {
        # 批 63（W-AM/W-AJ/W-AK/AJ′）：**声明面「执行者存在性」** —— 既有门禁只校验名字/文件/
        # 字面量"存在"，不校验"谁执行它" ⇒ 全绿仍失实 ✗（第五十九批审计统一根因）。
        "module": "gates.gate_declaration_executor",
        "desc": "声明面执行者存在性（event 承接/告警发送方/resume 可达/manifest 归属）",
        "require_impl": True,
    },
    # ---- W0-5（2026-10-13）新增：24 → 26 道；W2 +1 → 27；W3 +2 → 29 ----
    {
        # D-1 的直接防线：条件触发观察对象的存在性/闭合/同源（见该门禁 docstring）。
        "module": "gates.gate_condition_observers",
        "desc": "条件触发观察对象存在性（T4：闭合/可达/同源/归属）",
        "require_impl": True,
    },
    {
        # D-3 的直接防线：`{src}` 模板步骤的运行时五源覆盖（台账断言，静态清单看不出来）。
        "module": "gates.gate_dynamic_step_coverage",
        "desc": "动态模板步骤运行时覆盖（R3：台账五源覆盖 + 连续成段）",
        "require_impl": True,
    },
    # ---- W2（2026-10-14）新增：26 → 27 道 ----
    {
        # MCP 门面：schema 派生一致性 + 写能力审计绑定 + E-4 资源齐备 + 零硬依赖。
        "module": "gates.gate_mcp_schema",
        "desc": "MCP tool schema 与声明面一致（schema 有效/写能力审计/资源版本联动/零 SDK）",
        "require_impl": True,
    },
    # ---- W3（2026-10-14）新增：27 → 29 道 ----
    {
        # 多监管数据体系建模：契约登记/受控域闭包/置信纪律（语义法不得自动确认）/映射闭合。
        "module": "gates.gate_reg_frameworks",
        "desc": "监管数据体系契约（登记/受控域/置信纪律/映射闭合）",
        "require_impl": True,
    },
    {
        # 落地执行检视：规则表完整性（覆盖四类 gap_class、order 唯一）/契约登记/差距闭合/受控域。
        "module": "gates.gate_implementation_audit",
        "desc": "落地执行检视契约（规则表/登记/差距闭合/受控域）",
        "require_impl": True,
    },
    {
        # 供应链安全（方案 §13.2；ADR-0011）：锁存在且与环境一致 / 直接依赖无影子
        # （policy 双向断言）/ pip-audit 漏洞（high·critical 零容忍，工具或网络缺失
        # 按 SKIP 披露而非误报）/ SBOM 与 lock 的 sha256 同步 / 子进程解释器路径。
        "module": "gates.gate_dependency_audit",
        "desc": "依赖治理与供应链安全（锁/影子依赖/CVE/SBOM/解释器路径）",
        "require_impl": True,
    },
]


class GatesRunner:
    def __init__(self, gates: list[dict] | None = None):
        self.gates = gates or ALL_GATES

    def run(self) -> tuple[bool, list[dict]]:
        results = []
        for spec in self.gates:
            mod = importlib.import_module(spec["module"])
            fn = getattr(mod, "run", None)
            if fn is None:
                results.append(
                    {
                        "module": spec["module"],
                        "desc": spec["desc"],
                        "passed": False,
                        "detail": {"error": "缺 run()"},
                    }
                )
                continue
            try:
                passed, detail = fn()
            except Exception as e:  # noqa: BLE001
                results.append(
                    {
                        "module": spec["module"],
                        "desc": spec["desc"],
                        "passed": False,
                        "detail": {"error": repr(e)},
                    }
                )
                continue
            if not passed and not spec.get("require_impl", False):
                # 未接入门禁在 P0 不阻塞；P1 置 require_impl=True 后未实现即 FAIL
                results.append(
                    {
                        "module": spec["module"],
                        "desc": spec["desc"],
                        "passed": True,
                        "detail": {"note": "待接入（P0 骨架放行）", "inner": detail},
                    }
                )
            else:
                results.append(
                    {
                        "module": spec["module"],
                        "desc": spec["desc"],
                        "passed": passed,
                        "detail": detail,
                    }
                )
        ok = all(r["passed"] for r in results)
        return ok, results


def run_cli(name: str, run_fn, *, indent: int = 1) -> int:
    """**门禁单跑入口**（N-172，2026-09-30）：`python -m gates.gate_x` 的统一实现。

    为何收口：此前 **8 个门禁各自复制**同一段 `__main__`（逐字相同，仅门禁名字符串不同）——
    属"要改就改 8 处"的重复（且实测已出现 `indent=1` 与不缩进两种分叉）。
    统一后新增门禁只需三行：

        if __name__ == "__main__":
            from gates import run_cli

            raise SystemExit(run_cli("x", run))

    **行为不变**：仍打印 `[name] PASS/FAIL <detail json>`，仍以 0/1 退出。
    ⚠️ 用 `python -m gates.gate_x`（**不是** `python gates/gate_x.py`）—— 后者 sys.path[0] 是
    `gates/`，连既有的 `import paths` 都会失败（该形态此前即不可用，本次不构成回归）。
    """
    import contextlib
    import json

    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    passed, detail = run_fn()
    try:
        payload = json.dumps(detail, ensure_ascii=False, indent=indent)
    except Exception:  # noqa: BLE001  极端不可序列化 → 降级为 str（不因打印失败而掩盖判定）
        payload = str(detail)
    print(f"[{name}]", "PASS" if passed else "FAIL", payload)
    return 0 if passed else 1


def main(argv=None) -> int:
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    runner = GatesRunner()
    ok, results = runner.run()
    for r in results:
        print(f"  {'[OK] ' if r['passed'] else '[FAIL]'}  {r['desc']}  {r.get('detail')}")
    print("====================")
    print("PASS: 全部门禁通过" if ok else "FAIL: 存在未通过门禁")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
