# -*- coding: utf-8 -*-
"""gate_runtime_hygiene — 运行时卫生（v2 §3.8 表 A 第 5 项，判据 ①退出码 ②静默异常 ③接口空壳）

三组判据与当前状态（2026-09-26 引入，**首期只披露、不阻断**，v2 D-8 过渡策略）：

    ① 退出码：裸整数 `return N` 应改为 `config.exitcodes.ExitCode` 成员。
       现状：`cli.py` 与 `commands/` 已可直接采用；`tools/`、`modules/` 仍有历史值。
    ② 静默异常：`except ...: pass`（`pass` 独占行且紧邻 except）数量只减不增。
       现状：≥143 处（v2 §2.4.1）；本门禁按目录披露并冻结基线。
    ③ 接口空壳：`interfaces/**` 的 `raise NotImplementedError` 数量。
       现状：`theme_api.py` 3 处（v2 §2.2.3）；P1-3 实装后归零。

设计说明：本门禁**当前恒 PASS**，只把三项指标写入 detail（并冻结基线），
目的是让"只减不增"可机器观察；待 P1-3/P1-5 完成后把 `_STRICT` 置 True
即可转为阻断（每项均在下面留有 `_STRICT_*` 开关）。
"""

from __future__ import annotations

import os
import re

import paths

ROOT = paths.ROOT
SKIP_DIRS = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    "data",
    "reports",
    "graphify-out",
    "external",
    "tessdata",
    "backups",
    ".ruff_cache",
    ".codebuddy",
    "retired",
    "archive",
}  # v2 §3.10（P2-3）：归档层不参与运行时卫生计数

PAT_RETURN_INT = re.compile(r"^\s*return\s+[1-9]\d*\s*(?:#.*)?$", re.M)
PAT_BARE_PASS = re.compile(r"except[^\n]*:\s*\n\s*pass\b")

# 三项开关：True 即阻断（首期为 False，只披露）
_STRICT_EXITCODES = False
_STRICT_BARE_PASS = False
# ③ 接口空壳：**已转严格**（v2 §3.1.3 I-7，2026-09-26）——P1-3 实装 `theme_api` 后
# `interfaces/**` 的 `NotImplementedError` 为 0，故此处从"只披露"切为"阻断"。
# 白名单仅允许「抽象基类的正当声明」，须写明期次；当前为空（`ocr_engine.py` 未用该模式）。
_IFACE_STUB_ALLOW: dict[str, str] = {}
_STRICT_IFACE_STUBS = True

# 基线（只减不增；None = 尚未冻结）
BASELINE_BARE_PASS: int | None = None
BASELINE_RETURN_INT: int | None = None

# ⑤ owned 层（v2 §3.6 X1/X2 的"每行自写"层，P3-2/R3，2026-09-26）
#    config/interfaces/gates/commands 是本仓**自有的治理/标准/命令层**，必须 0 裸整数 return
#    + 0 except-pass（新代码一律 `ExitCode` + 显式异常处理或 `contextlib.suppress`）；
#    历史爬虫/业务层（modules/、std_lib/scraper_std/、tools/、std_lib/common_lib/）仍以
#    "只减不增"基线冻结（判据①②）—— 不强行收敛存量（高 churn、低治理收益，D-8 口径）。
_OWNED_LAYERS = ("config", "interfaces", "gates", "commands")

# ④ 生产脚本日志化目标（v2 §3.6 X3）：LOG 下限 / print 上限
# 取值依据（2026-09-26 P1-5 实测）：三脚本各自 8 处状态行已转 LOG；
# 残余 print 为**机器可读载荷**（extract_relations 的 json.dumps 汇总、
# governance_sync 的双列表头/行、apply_timeliness 的多列表格行与分隔线）。
_PROD_LOG_TARGETS: dict[str, dict[str, int]] = {
    "tools/extract_relations.py": {"log_min": 8, "print_max": 2},
    "tools/governance_sync.py": {"log_min": 8, "print_max": 2},
    "modules/regulatory_scrapers/timeliness_review/apply_timeliness_to_cleaned.py": {
        "log_min": 8,
        "print_max": 8,
    },
}


def _iter_py(*, only_prefix: str = ""):
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            fp = os.path.join(dirpath, fn)
            rel = os.path.relpath(fp, ROOT).replace("\\", "/")
            if only_prefix and not rel.startswith(only_prefix):
                continue
            yield rel, fp


def run() -> tuple[bool, dict]:
    problems: list[str] = []
    detail: dict = {}

    # ① 退出码
    ints: dict[str, int] = {}
    for rel, fp in _iter_py():
        try:
            text = open(fp, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        n = len(PAT_RETURN_INT.findall(text))
        if n:
            ints[rel] = n
    total_ints = sum(ints.values())
    detail["exitcode_bare_int"] = {
        "total": total_ints,
        "files": dict(sorted(ints.items(), key=lambda kv: -kv[1])[:8]),
        "baseline": BASELINE_RETURN_INT,
    }
    if BASELINE_RETURN_INT is not None and total_ints > BASELINE_RETURN_INT and _STRICT_EXITCODES:
        problems.append(f"裸整数退出码 {total_ints} > 基线 {BASELINE_RETURN_INT}")

    # ② 静默异常
    bare: dict[str, int] = {}
    for rel, fp in _iter_py():
        try:
            text = open(fp, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        n = len(PAT_BARE_PASS.findall(text))
        if n:
            bare[rel] = n
    total_bare = sum(bare.values())
    detail["bare_except_pass"] = {
        "total": total_bare,
        "files": dict(sorted(bare.items(), key=lambda kv: -kv[1])[:8]),
        "baseline": BASELINE_BARE_PASS,
    }
    if BASELINE_BARE_PASS is not None and total_bare > BASELINE_BARE_PASS and _STRICT_BARE_PASS:
        problems.append(f"except-pass {total_bare} > 基线 {BASELINE_BARE_PASS}")

    # ③ 接口空壳
    stubs: dict[str, list[int]] = {}
    for rel, fp in _iter_py(only_prefix="interfaces/"):
        try:
            text = open(fp, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        locs = [
            text[: m.start()].count("\n") + 1
            for m in re.finditer(r"raise\s+NotImplementedError", text)
        ]
        if locs:
            stubs[rel] = locs
    allowed = {k: stubs.pop(k) for k in list(stubs) if k in _IFACE_STUB_ALLOW}
    detail["interface_stubs"] = {
        "total": sum(len(v) for v in stubs.values()),
        "files": stubs,
        "allowlisted": allowed,
        "strict": _STRICT_IFACE_STUBS,
    }
    if stubs and _STRICT_IFACE_STUBS:
        problems.append(
            f"interfaces/ 存在 NotImplementedError 空壳：{stubs}"
            "（实现后须删除空壳；抽象基类声明请登记 _IFACE_STUB_ALLOW 并注明期次）"
        )

    # ④ 生产脚本日志化（v2 §3.6 X3，P1-5 新增）：LOG 使用**下限** + print **上限**（只减不增）。
    #    针对 §2.4.3 点名的三个"纯 print"脚本——它们的 print 使编排器只能靠 stdout tail
    #    判失败，结构化日志在主链上完全未启用。保留的 print 是**机器可读载荷/多列表格行**
    #    （stdout 契约），故上限非 0。
    logs: dict[str, dict] = {}
    for rel, spec in sorted(_PROD_LOG_TARGETS.items()):
        fp = os.path.join(ROOT, rel.replace("/", os.sep))
        if not os.path.exists(fp):
            problems.append(f"④ 生产脚本缺失：{rel}")
            continue
        lines = open(fp, encoding="utf-8", errors="replace").read().splitlines()
        n_log = sum(
            1
            for s in (ln.strip() for ln in lines)
            if s.startswith(
                ("LOG.info(", "LOG.warning(", "LOG.error(", "LOG.exception(", "LOG.debug(")
            )
        )
        n_print = sum(1 for s in (ln.strip() for ln in lines) if s.startswith("print("))
        logs[rel] = {
            "log": n_log,
            "log_min": spec["log_min"],
            "print": n_print,
            "print_max": spec["print_max"],
        }
        if n_log < spec["log_min"]:
            problems.append(
                f"④ {rel} LOG 使用 {n_log} < 下限 {spec['log_min']}"
                "（状态行须经统一日志设施，不得用 print）"
            )
        if n_print > spec["print_max"]:
            problems.append(
                f"④ {rel} print {n_print} > 上限 {spec['print_max']}"
                "（仅机器可读载荷/表格行可保留 print）"
            )
    detail["prod_logging"] = logs

    # ⑤ owned 层严格卫生（阻断）
    owned_bad: list[tuple] = []
    for rel, fp in _iter_py():
        top = rel.split("/")[0]
        if top not in _OWNED_LAYERS:
            continue
        try:
            text = open(fp, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        nr = len(PAT_RETURN_INT.findall(text))
        nb = len(PAT_BARE_PASS.findall(text))
        if nr or nb:
            owned_bad.append((rel, nr, nb))
    detail["owned_hygiene"] = {"layers": list(_OWNED_LAYERS), "violations": owned_bad}
    if owned_bad:
        problems.append(
            f"⑤ owned 层（{list(_OWNED_LAYERS)}）存在裸整数 return / except-pass：{owned_bad}"
            "（须 `ExitCode` 与显式异常处理；历史层仍走判据①②的基线冻结）"
        )

    if problems:
        detail["problems"] = problems
    detail["note"] = (
        "①退出码 ②静默异常 为**只披露**（历史层基线冻结，v2 D-8）；"
        "③接口空壳 ④生产脚本日志化 ⑤owned 层卫生 为**阻断**"
    )
    return (not problems), detail


if __name__ == "__main__":
    import json

    passed, det = run()
    print(
        "[runtime_hygiene]",
        "PASS" if passed else "FAIL",
        json.dumps(det, ensure_ascii=False, indent=1),
    )
    raise SystemExit(0 if passed else 1)
