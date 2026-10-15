"""PEP 8 / 风格规则**报告器**（方案 §13.11：先跑报告后启用）。

为什么先报告：本仓有大量历史层（`std_lib/scraper_std`、`modules/**`），直接启用
D（docstring）/ C901（复杂度）会产出数百条**当前无害**的告警——按"告警疲劳等于
没有告警"的纪律，先量化再决定启用范围与 per-file-ignores。

用法：
  python -m tools.style_report [--json]
退出码：恒 0（**报告器不是门禁**；启用后由 ruff + ci_check 阻断）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone

# 引导：**唯一引导点 bootstrap**（不自写 sys.path.insert —— gate_import_bootstrap 判据）
from bootstrap import bootstrap

bootstrap("all")

import paths

ROOT = paths.ROOT
TZ8 = timezone(timedelta(hours=8))
OUT = os.path.join(paths.REPORTS_DIR, "refactors", "style_report.json")
LAYERS = ("std_lib", "modules", "tools", "config", "interfaces", "gates", "commands")
# 本仓已有的显式采纳（pyproject 已生效者）不在本报告重复统计
ALREADY_ADOPTED = {
    "E501": "行长度 100（ruff line-length=100 + lint ignore，格式器口径）",
    "I001": "导入顺序（ruff select 含 I，isort 等价）",
    "mypy": "类型基线 0 error + check_untyped_defs（pyproject [tool.mypy]，CI 阻断）",
    "N801/N802/N803": "命名规则（§7.6 计划：先报告后启用，本次报告给出基线）",
}


def _run_ruff(rules: str, layers: tuple[str, ...] = LAYERS) -> dict:
    """跑 ruff 选定规则集，返回 {rule: count} + 总数（不写盘）。"""
    cmd = [sys.executable, "-m", "ruff", "check", "--quiet", "--select", rules,
           "--exclude", "reports/_tmp", "--statistics", *layers]
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out: dict[str, int] = {}
    for ln in (r.stdout or "").splitlines():
        m = re.match(r"^\s*(\d+)\s+(\S+)", ln)
        if m:
            out[m.group(2)] = int(m.group(1))
    return out


def complexity_report() -> dict:
    """复杂度（C901）与超长函数的自查（ruff C901 阈值 15）。"""
    stats = _run_ruff("C901")
    return {
        "rule": "C901",
        "threshold": 15,
        "counts": stats,
        "total": sum(stats.values()),
    }


def docstring_report() -> dict:
    """docstring 子集（D100 模块/100/103 函数）现状。"""
    stats = _run_ruff("D100,D101,D103")
    return {
        "rules": ["D100", "D101", "D103"],
        "counts": stats,
        "total": sum(stats.values()),
    }


def file_size_report() -> dict:
    """行数分布（规模债，与「单文件 <800 行」互补：行数管规模、C901 管结构）。"""
    buckets = {"<300": 0, "300-500": 0, "500-800": 0, "800-1200": 0, ">=1200": 0}
    over: list[dict] = []
    for base in LAYERS:
        for dirpath, dirnames, filenames in os.walk(os.path.join(ROOT, base)):
            dirnames[:] = [d for d in dirnames if d not in {"__pycache__", "retired"}]
            for fn in filenames:
                if not fn.endswith(".py"):
                    continue
                p = os.path.join(dirpath, fn)
                try:
                    n = len(open(p, encoding="utf-8", errors="replace").read().splitlines())
                except OSError:
                    continue
                rel = os.path.relpath(p, ROOT).replace("\\", "/")
                if n < 300:
                    buckets["<300"] += 1
                elif n < 500:
                    buckets["300-500"] += 1
                elif n < 800:
                    buckets["500-800"] += 1
                elif n < 1200:
                    buckets["800-1200"] += 1
                else:
                    buckets[">=1200"] += 1
                if n >= 800:
                    over.append({"file": rel, "lines": n})
    return {"buckets": buckets, "ge_800": sorted(over, key=lambda d: -d["lines"])}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="风格规则现状报告（§13.11 先报告后启用）")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    cx = complexity_report()
    ds = docstring_report()
    fs = file_size_report()
    doc = {
        "schema_version": "1.0",
        "generated_at": datetime.now(TZ8).replace(microsecond=0).isoformat(),
        "already_adopted": ALREADY_ADOPTED,
        "candidate_rules": {"complexity_c901": cx, "docstring_d": ds},
        "file_size": fs,
        "verdict": {
            "C901": ("**建议启用**（阈值 15）：现状命中数见 candidate_rules；"
                     "超限函数集中在历史层采集脚本，随接触拆分即下降。"),
            "D100/D101/D103": ("**分层启用**：新代码与 owned 层（gates/commands/interfaces/"
                              "std_lib/common_lib）启用，历史层走 per-file-ignores（同 E741 模式）"
                              "——直接全局启用会产生数百条无害告警。"),
            "结论": "本报告为**量化依据**，非门禁；启用动作走 pyproject + ci_check 阻断。",
        },
    }
    text = json.dumps(doc, ensure_ascii=False, indent=2)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    open(OUT, "w", encoding="utf-8", newline="\n").write(text + "\n")
    if a.json:
        print(text)
        return 0
    print(f"[style] C901(>15 复杂度函数)={cx['total']} {cx['counts']}")
    print(f"[style] docstring D100/D101/D103={ds['total']} {ds['counts']}")
    print(f"[style] 行数分布={fs['buckets']}")
    for r in fs["ge_800"][:6]:
        print(f"    ≥800 行：{r['file']} ({r['lines']})")
    print(f"[style] 报告：{os.path.relpath(OUT, ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
