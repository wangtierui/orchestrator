# -*- coding: utf-8 -*-
"""ci_check.py —— 本地 CI 一键校验（2026-09-12 审查 P1-3）。

串联三道闸：ruff 静态检查 → pytest 用例 → gates 交付门禁；
输出汇总表与 exit code（0=全绿 / 1=任一失败），供提交前、定时任务或
.git/hooks 选用（本仓无远端 CI，此脚本为本地等价物）。

用法：
  python tools/ci_check.py            # 全量（ruff+pytest+gates）
  python tools/ci_check.py --fast     # 跳过 pytest（仅 ruff+gates）
  python tools/ci_check.py --cov      # 追加覆盖率报告（需 coverage 已装）
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable


def _run(name: str, argv: list, timeout: int) -> dict:
    t0 = time.time()
    try:
        r = subprocess.run(
            argv,
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        rc, out = r.returncode, (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        rc, out = 124, f"（超时 {timeout}s）"
    except OSError as e:
        rc, out = 125, repr(e)
    tail = [ln for ln in out.strip().splitlines() if ln.strip()][-2:]
    return {
        "name": name,
        "rc": rc,
        "sec": round(time.time() - t0, 1),
        "tail": " | ".join(tail)[:160],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="本地 CI 一键校验（ruff+pytest+gates）")
    ap.add_argument("--fast", action="store_true", help="跳过 pytest")
    ap.add_argument("--cov", action="store_true", help="追加覆盖率报告")
    a = ap.parse_args(argv)

    # ---- 阻断项（v2 §3.7 G4，P2-4 → P9 转阻断）----
    # mypy（P9，2026-09-26 转阻断）：收窄到 owned 层（`std_lib/common_lib` + config/interfaces/
    # gates/commands），`pyproject.toml [tool.mypy] follow_imports="silent"` 已排除历史层
    # scraper_std/modules；owned 层已收敛到 0 error → mypy 参与 PASS/FAIL。
    NONBLOCKING: set[str] = set()

    plan = [
        ("ruff", [PY, "-m", "ruff", "check", ".", "--exclude", "reports/_tmp"], 300),
        (
            "mypy",
            [
                PY,
                "-m",
                "mypy",
                "std_lib/common_lib",
                "config",
                "interfaces",
                "gates",
                "commands",
                "--no-error-summary",
            ],
            600,
        ),
    ]
    if not a.fast:
        if a.cov:
            # 覆盖率：pytest 走 coverage 插桩，随后 `coverage report` 以 pyproject 的
            # `fail_under=20` 判定（§3.7 G5）
            plan.append(
                (
                    "pytest",
                    [
                        PY,
                        "-m",
                        "coverage",
                        "run",
                        "-m",
                        "pytest",
                        "tests",
                        "-m",
                        "not data",
                        "--color=no",
                        "-q",
                    ],
                    900,
                )
            )
        else:
            plan.append(("pytest", [PY, "-m", "pytest", "tests", "--color=no", "-q"], 600))
    plan.append(("gates", [PY, os.path.join(ROOT, "cli.py"), "gates"], 600))
    if a.cov and not a.fast:
        plan.append(("coverage", [PY, "-m", "coverage", "report"], 120))

    results = []
    for name, cmd, to in plan:
        r = _run(name, cmd, to)
        r["blocking"] = name not in NONBLOCKING
        results.append(r)
        flag = "OK " if r["rc"] == 0 else ("WARN" if not r["blocking"] else "FAIL")
        print(f"[{flag}] {r['name']:9s} rc={r['rc']:3d} {r['sec']:6.1f}s  {r['tail']}")

    bad = [r for r in results if r["rc"] != 0 and r["blocking"]]
    warn = [r for r in results if r["rc"] != 0 and not r["blocking"]]
    print("=" * 40)
    print(
        f"CI: {'PASS 全绿' if not bad else 'FAIL: ' + ', '.join(b['name'] for b in bad)}"
        f"（{len(results) - len(bad)}/{len(results)} 阻断项通过"
        + (f"；非阻断告警 {', '.join(w['name'] for w in warn)}" if warn else "")
        + "）"
    )
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
