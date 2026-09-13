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

import importlib
import sys

# 门禁注册表（键 = 模块名于 gates/ 下；order 决定执行顺序）
ALL_GATES: list[dict] = [
    {"module": "gates.gate_hardcoded_paths", "desc": "盘符字面量扫描（R4）", "require_impl": True},
    {"module": "gates.gate_enum_values", "desc": "受控枚举一致性（v3）", "require_impl": True},
    {"module": "gates.gate_flat_layout", "desc": "目录拍平（data/docs 无子目录）", "require_impl": True},
    {"module": "gates.gate_no_duplicate_libs", "desc": "重复工具/重名再定义扫描", "require_impl": True},
    {"module": "gates.gate_contract", "desc": "数据契约（列头/键集）", "require_impl": True},
    {"module": "gates.gate_hardcoded_snapshots", "desc": "硬编码 cleaned 快照日期（N-3）", "require_impl": True},
    {"module": "gates.gate_rfn_sync", "desc": "RFN 跨文件一致性", "require_impl": True},
    {"module": "gates.gate_rfn_drift", "desc": "RFN↔clean 漂移未处置阻断", "require_impl": True},
    {"module": "gates.gate_timeliness_ssot", "desc": "时效单源传播一致性", "require_impl": True},
    {"module": "gates.gate_citations", "desc": "制度引用门禁（drafter --strict）", "require_impl": True},
    {"module": "gates.gate_sources_config", "desc": "源目录配置一致性（R15）", "require_impl": True},
    {"module": "gates.gate_provenance", "desc": "数据血缘 provenance 覆盖（R10）", "require_impl": True},
    {"module": "gates.gate_field_aliases", "desc": "中文列名受控注册（字段治理）", "require_impl": True},
    {"module": "gates.gate_secret_scan", "desc": "密钥/敏感值硬编码扫描（审查 P1-4）", "require_impl": True},
    {"module": "gates.gate_original_resolvable", "desc": "内部制度索引↔原件库可解析性", "require_impl": True},
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
                results.append({"desc": spec["desc"], "passed": False, "detail": {"error": "缺 run()"}})
                continue
            try:
                passed, detail = fn()
            except Exception as e:  # noqa: BLE001
                results.append({"desc": spec["desc"], "passed": False,
                                "detail": {"error": repr(e)}})
                continue
            if not passed and not spec.get("require_impl", False):
                # 未接入门禁在 P0 不阻塞；P1 置 require_impl=True 后未实现即 FAIL
                results.append({"desc": spec["desc"], "passed": True,
                                "detail": {"note": "待接入（P0 骨架放行）", "inner": detail}})
            else:
                results.append({"desc": spec["desc"], "passed": passed, "detail": detail})
        ok = all(r["passed"] for r in results)
        return ok, results


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    runner = GatesRunner()
    ok, results = runner.run()
    for r in results:
        print(f"  {'[OK] ' if r['passed'] else '[FAIL]'}  {r['desc']}  {r.get('detail')}")
    print("====================")
    print("PASS: 全部门禁通过" if ok else "FAIL: 存在未通过门禁")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
