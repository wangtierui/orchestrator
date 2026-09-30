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
                # N-72（2026-09-28）：范围由"owned 层"扩至**全仓**（含历史层 std_lib/scraper_std、
                # modules/*、tools/*）——历史层 236 处 error 已全部收敛（含 5 处真 bug），
                # 故与 owned 层同口径**阻断**。
                # N-73：严格化参数（`check_untyped_defs` / `follow_imports=normal`）由
                # `pyproject [tool.mypy]` **单点声明**，此处不再重复传参（避免两处口径漂移）。
                "std_lib",
                "modules",
                "tools",
                "config",
                "interfaces",
                "gates",
                "commands",
                "--no-error-summary",
            ],
            900,
        ),
        # N-61（2026-09-27）：计划任务与 `config/schedule.yaml` 的**一致性**入 CI —— 此前
        # 仅 doctor/status 调用，改 yaml 后系统侧仍跑旧 argv（实测 `--only 6.9` 空跑）而无告警。
        # 非 Windows / 无 schtasks → 该判据自报 skipped（不判 FAIL）。
        ("schedule", [PY, os.path.join(ROOT, "cli.py"), "schedule", "verify"], 180),
        # R-5（2026-09-30）：README §8（**机器生成的链路总图**）与源码派生结果一致性入 CI ——
        # 此前"重跑后应无差异"仅是人工步骤 → 链路改了却忘重跑，读者会按**过期链路**操作
        # （文档漂移不会报错，只会误导）。判据：`gen_flow_map --check`（只读，不写盘）。
        ("flowmap", [PY, "-m", "tools.gen_flow_map", "--check"], 180),
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
            # N-181（2026-09-30）：超时 **600 → 900**（与上方 `--cov` 分支一致）。
            # 依据（实测）：全量 pytest **冷缓存 ≈ 684s**、热缓存 ≈ 460~470s（基线 457s）。
            # 600s 会让**冷启动**（新克隆/久未跑/FS 缓存被冲掉）出现 rc=124 —— 而 rc=124 与
            # "真实失败"在结果里**长得一样**，属**假红**：会诱导人去"修"并不存在的问题。
            # ⚠️ 放宽的只是**时间预算**，pytest 仍是**阻断项**（rc 必须为 0）；用例集合与
            # 覆盖率门槛（`fail_under`）均未放宽。
            plan.append(("pytest", [PY, "-m", "pytest", "tests", "--color=no", "-q"], 900))
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
