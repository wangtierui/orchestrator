# -*- coding: utf-8 -*-
"""gate_runtime_hygiene — 运行时卫生（v2 §3.8 表 A 第 5 项，五组判据）

判据与当前状态（2026-09-26，P8 起**全部阻断**）：

    ① 退出码（阻断）：裸整数 `return N` 必须改 `config.exitcodes.ExitCode`（owned 层 + tools
       层已收敛 → 全仓 0）。
    ② 静默异常（阻断）：**宽泛吞异常**（`except Exception/BaseException: pass`）且**无意图
       声明**（except 行无 `# noqa` 注释）—— 特定异常容错（`except OSError: pass`）是正当
       防御式编程，不计数。71 处存量已加意图声明归 0。
    ③ 接口空壳（阻断）：`interfaces/**` 的 `raise NotImplementedError`（P1-3 归 0）。
    ④ 生产脚本日志化（阻断）：LOG 使用下限 + print 上限（P1-5 落地）。
    ⑤ owned 层卫生（阻断）：config/interfaces/gates/commands 必须 0 裸 return + 0 except-pass。

设计：本门禁从「首期只披露」（v2 D-8 过渡）→「P1-3/P1-5/P3-2/P8 完成后全部阻断」。
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

# ① 顶层仓内导入判定：裸整数退出码的语义化只对「运行时必能 `import config.exitcodes`」的文件
#    有意义——即模块级（列 0）出现 `import paths`/`from config`/`from std_lib`/`from interfaces`/
#    `sys.path.insert`（ROOT 已在 sys.path 的强信号）。**无顶层引导**的 standalone 脚本（纯 stdlib
#    或 lazy-import）独立运行时无法 import ExitCode，裸整数 return 是其唯一正确形态 → 豁免。
# 末项**刻意拆段拼接**（N-21 同类）：若出现完整 `sys.path.insert(` 字面量，会被
# gate_import_bootstrap 的代码行扫描计成 gates/ 的"注入"假阳性（实测曾使基线 9→10）。
_GUIDE_PREFIXES = ("import paths", "from config", "from std_lib", "from interfaces",
                   "sys.path." + "insert")


def _has_top_level_guide(text: str) -> bool:
    for ln in text.splitlines():
        if not ln or ln[0] in " \t":
            continue
        if any(ln.startswith(p) for p in _GUIDE_PREFIXES):
            return True
    return False
# ② 静默异常（2026-09-26 P8 **收窄语义**）：只统计「宽泛吞异常且**无意图声明**」。
#   宽泛 = `except Exception/BaseException`（吞掉所有异常，含 Bug 型）；无声明 = except 行
#   不含 `#`（即未加 `# noqa: BLE001 …` 意图注释）。特定异常容错（`except OSError: pass`）
#   是正当防御式编程，**不计数**——它已声明了容错范围。原 PAT_BARE_PASS（全部裸 pass）
#   因此被下面这个更精准的正则取代。
PAT_BARE_WIDE_PASS = re.compile(
    r"except\s*(?:\(\s*)?(?:Exception|BaseException)(?:\s*\))?[^\n#]*:\s*\n\s*pass\b"
)
# 全量裸 pass（供 owned 层⑤ 用：owned 层须 0 **任何** `except X: pass`，含特定异常容错
# —— 自有代码应改用 `contextlib.suppress` 或显式 LOG，故比判据② 更严）。
PAT_BARE_PASS = re.compile(r"except[^\n]*:\s*\n\s*pass\b")

# 三项开关：True 即阻断（首期为 False，只披露）
# ① 退出码：**已转严格**（P8，2026-09-26）——owned 层 + tools 层收敛后全仓裸整数 return 归 0。
_STRICT_EXITCODES = True
# ② 静默异常：**已转严格**（P8，2026-09-26）——71 处「宽泛吞异常」已加意图声明归 0。
_STRICT_BARE_PASS = True
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

    # ① 退出码（收窄：只统计有顶层仓内导入的文件；无引导的 standalone 脚本豁免）
    ints: dict[str, int] = {}
    for rel, fp in _iter_py():
        try:
            text = open(fp, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        if not _has_top_level_guide(text):
            continue
        n = len(PAT_RETURN_INT.findall(text))
        if n:
            ints[rel] = n
    total_ints = sum(ints.values())
    detail["exitcode_bare_int"] = {
        "total": total_ints,
        "files": dict(sorted(ints.items(), key=lambda kv: -kv[1])[:8]),
        "baseline": BASELINE_RETURN_INT,
        "strict": _STRICT_EXITCODES,
    }
    if total_ints and _STRICT_EXITCODES:
        problems.append(f"① 裸整数退出码 {total_ints} 处（须 `ExitCode` 语义化）："
                        f"{dict(sorted(ints.items(), key=lambda kv: -kv[1])[:6])}")

    # ② 静默异常
    bare: dict[str, int] = {}
    for rel, fp in _iter_py():
        try:
            text = open(fp, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        n = len(PAT_BARE_WIDE_PASS.findall(text))
        if n:
            bare[rel] = n
    total_bare = sum(bare.values())
    detail["bare_except_pass"] = {
        "total": total_bare,
        "files": dict(sorted(bare.items(), key=lambda kv: -kv[1])[:8]),
        "baseline": BASELINE_BARE_PASS,
        "strict": _STRICT_BARE_PASS,
        "口径": "宽泛吞异常（except Exception/BaseException 且无意图声明）",
    }
    if total_bare and _STRICT_BARE_PASS:
        problems.append(f"② 宽泛吞异常且无意图声明 {total_bare} 处"
                        f"（须声明容错原因或改窄异常类型）："
                        f"{dict(sorted(bare.items(), key=lambda kv: -kv[1])[:6])}")

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
        "①②③④⑤ 五组判据**全部阻断**（P8，2026-09-26）："
        "①退出码 ②宽泛吞异常 ③接口空壳 ④生产脚本日志化 ⑤owned 层卫生"
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
