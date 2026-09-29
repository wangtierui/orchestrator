# -*- coding: utf-8 -*-
"""tools/audit_health — **全链路健康审计**（2026-09-29）

为什么需要（本轮用户诉求）
-------------------------
"统一入口未实现全流程自动化 / 流程断点 / 门禁失效 / 数据源不唯一 / 数据阻塞 /
代码冗余 / 代码效率低 / 实现重复 / 硬编码"这九类问题，**靠人工巡检必然漏**，
且漏了不会报错。本工具把九类问题**机器化**：每类给出**可复跑的判据**与**定位证据**，
使"健康"从主观印象变为可执行检查（同 `retention`/`preflight` 的披露口径）。

纪律
----
  · **只读**：不写任何产物、不改任何状态（审计本身不得成为风险源）；
  · **可复跑**：同一代码同一数据 → 同一结论（无随机、无网络）；
  · **不静默**：无发现时也输出"0 项"（避免"没输出"被误读成"没跑"）；
  · **分级**：`HIGH`（会导致错误结论/阻断）/ `MED`（可维护性/一致性）/ `LOW`（提示）。
"""

from __future__ import annotations

import ast
import io
import json
import os
import re
import sys
from collections import Counter, defaultdict

# 引导：**必须经 `bootstrap`（唯一引导点，v2 §3.1.2）** —— `gate_import_bootstrap` 断言
# tools/ 层的 `sys.path.insert` **只减不增**，新代码**不得**自写插入。
# 运行方式相应为 `python -m tools.audit_health`（仓根位于 sys.path[0] 才能 import bootstrap）。
from bootstrap import bootstrap

bootstrap("all")

import paths

ROOT = paths.ROOT


SCAN_DIRS = ("std_lib", "modules", "tools", "config", "interfaces", "gates", "commands")
SKIP_PART = ("__pycache__", "retired", "_tmp", "external", "\\data\\", "/data/", "backups")


def _pyfiles(with_tests: bool = False) -> list:
    dirs = SCAN_DIRS + (("tests",) if with_tests else ())
    out: list = []
    for d in dirs:
        base = os.path.join(ROOT, d)
        for dirpath, dirnames, filenames in os.walk(base):
            if any(s in dirpath for s in SKIP_PART):
                continue
            dirnames[:] = [x for x in dirnames if x != "__pycache__"]
            for fn in filenames:
                if fn.endswith(".py"):
                    out.append(os.path.join(dirpath, fn))
    return out


def _rel(p: str) -> str:
    return os.path.relpath(p, ROOT).replace("\\", "/")


def _read(p: str) -> str:
    try:
        return open(p, encoding="utf-8").read()
    except OSError:
        return ""


# --------------------------------------------------------------------------
# ① 统一入口 / 全流程自动化
# --------------------------------------------------------------------------
def check_entry() -> list:
    """入口覆盖：`tools/` 下脚本是否入链；`STEP_ORDER` 是否有声明未实装；triggers 一致性。"""
    f: list = []
    import importlib

    rpr = importlib.import_module("tools.run_production_refresh")
    order = list(rpr.STEP_ORDER)
    src = _read(os.path.join(ROOT, "tools", "run_production_refresh.py"))

    # (a) STEP_ORDER 声明 vs `_run("name"` 实装
    #  ⚠️ 修正（2026-09-29）：步骤名可为 **f-string**（`_run(f"collect:{src}", …)` 在
    #  `for src in want:` 内**循环生成**）→ 正则须同时匹配 `f"…"`，否则 3 个按源展开的
    #  步骤会被误报为"声明未实装"（实测误报）。同时过滤文档示例里的占位名（含 `<`）。
    wired = set(re.findall(r'_run(?:_conditional)?\(\s*\n?\s*f?"([^"]+)"', src))
    missing = [s for s in order if s not in wired]
    if missing:
        f.append(
            {
                "cat": "统一入口",
                "sev": "HIGH",
                "where": "tools/run_production_refresh.py:STEP_ORDER",
                "detail": f"声明但**未见 `_run` 实装**的步骤 {missing}（声明与实装脱节）",
            }
        )
    extra = sorted(x for x in (wired - set(order)) if "<" not in x and ">" not in x)
    if extra:
        f.append(
            {
                "cat": "统一入口",
                "sev": "MED",
                "where": "tools/run_production_refresh.py",
                "detail": f"已实装但**未登记 STEP_ORDER** 的步骤 {extra}（顺序台账不全）",
            }
        )

    # (b) tools/ 下脚本是否被入口消费（未入链 = 需人工执行 → 自动化不全）
    #  ⚠️ 口径（2026-09-30，R-5）：**须读 `tools/_manifest.json` 的 `independent` 声明**。
    #  该检查的价值在发现**漂移**（如 N-53："触发项声明完备却从未执行"）；若把"未入链"一律
    #  当缺陷，运维/开发类独立工具的噪音会淹没真信号；若一律忽略，真漂移会漏。
    #  故以 manifest 的**显式意图**为准：声明 independent → 刻意独立，不报；未声明 → 报。
    order_txt = " ".join(order) + src
    indep: set = set()
    try:
        man = json.loads(
            _read(os.path.join(ROOT, "tools", "_manifest.json")) or "{}"
        ).get("entries") or []
        indep = {str(e.get("file")) for e in man if e.get("independent")}
    except Exception:  # noqa: BLE001  清单不可读 → 退回"全部报"（宁多报不漏报）
        indep = set()
    standalone: list = []
    for fn in sorted(os.listdir(os.path.join(ROOT, "tools"))):
        if not fn.endswith(".py"):
            continue
        if fn.startswith("_") or fn in ("__init__.py",):
            continue
        if fn in indep:
            continue
        if fn in order_txt or fn.replace(".py", "") in order_txt:
            continue
        standalone.append(fn)
    if standalone:
        f.append(
            {
                "cat": "统一入口",
                "sev": "MED",
                "where": "tools/",
                "detail": f"**未被入口引用**的脚本 {standalone}（须经人工执行；若非故意，即自动化缺口）",
            }
        )

    # (c) triggers.yaml 与 STEP_ORDER 的登记差
    try:
        import yaml

        tr = yaml.safe_load(_read(os.path.join(ROOT, "config", "triggers.yaml"))) or {}
        # 结构兼容：`triggers` 既可能是**映射**（name → 定义），也可能是**列表**（元素含 name/steps）。
        # 初版只按映射处理 → 实测报 `'list' object has no attribute 'items'`（误报提示）。
        names: set = set()
        _trig = tr.get("triggers") if isinstance(tr, dict) else None
        _items = _trig.items() if isinstance(_trig, dict) else enumerate(_trig or [])
        for k, v in _items:
            if isinstance(v, dict) and v.get("name"):
                names.add(str(v["name"]))
            elif isinstance(k, str):
                names.add(k)
            if isinstance(v, dict) and v.get("steps"):
                # ⚠️ steps 元素是 **dict**（{argv:[...], timeout:N}）→ 初版 str(x) 会把
                #   整个 dict 当名字（实测输出 "{'argv': [...], 'timeout': 1800}" 这类噪音）。
                #   只取有意义的标识：优先 `name` 字段，否则取 argv 里首个非解释器元素。
                for x in v["steps"] or []:
                    if isinstance(x, dict):
                        if x.get("name"):
                            names.add(str(x["name"]))
                        # 不再从 argv 反推步骤名（实测会捞出 --apply/python/{arg} 等噪音）；
                        # 步骤名以 STEP_ORDER 为准，本检查只看 triggers 里**显式声明**的 name。
                    else:
                        names.add(str(x))
        only_tr = sorted(n for n in names if n not in order and not n.startswith("_"))
        if only_tr:
            f.append(
                {
                    "cat": "统一入口",
                    "sev": "LOW",
                    "where": "config/triggers.yaml",
                    "detail": f"triggers 中登记但不在 STEP_ORDER 的名称 {only_tr[:8]}（可能为触发器名，非步骤名）",
                }
            )
    except Exception as e:  # noqa: BLE001
        f.append({"cat": "统一入口", "sev": "LOW", "where": "triggers.yaml", "detail": f"解析跳过：{e}"})
    return f


# --------------------------------------------------------------------------
# ② 门禁失效
# --------------------------------------------------------------------------
def check_gates() -> list:
    """门禁挂载完整性 + 静默吞错 + 门禁位置（须在链尾以终态产物为准）。"""
    f: list = []
    gdir = os.path.join(ROOT, "gates")
    files = sorted(x for x in os.listdir(gdir) if x.startswith("gate_") and x.endswith(".py"))
    # ⚠️ 修正（2026-09-29）：门禁的**唯一事实源**是 `gates.ALL_GATES`（显式注册表，
    # `GatesRunner` 只跑注册表内的项）。此前按"模块名是否在 cli.py/其他门禁里出现"判定 →
    # 把注册表本身（`gates/__init__.py`）漏在核对之外 → **13 个门禁被误报为"未挂载"**。
    # 审计器自身误报会让人去"修"不存在的问题，故此处直接读注册表。
    try:
        from gates import ALL_GATES

        registered = {str(x.get("module") or "").split(".")[-1] for x in ALL_GATES}
    except Exception as e:  # noqa: BLE001
        registered = set()
        f.append(
            {
                "cat": "门禁失效",
                "sev": "HIGH",
                "where": "gates/__init__.py:ALL_GATES",
                "detail": f"**注册表不可读**：{type(e).__name__}: {e}（所有门禁均不可核验）",
            }
        )
    unmounted = [fn[:-3] for fn in files if fn[:-3] not in registered]
    if unmounted:
        strict = [x for x in unmounted if not x.endswith("_helper")]
        if strict:
            f.append(
                {
                    "cat": "门禁失效",
                    "sev": "HIGH",
                    "where": "gates/__init__.py:ALL_GATES",
                    "detail": f"**存在于磁盘但未注册**的门禁 {strict}（写了不跑 = 失效）",
                }
            )
    ghost = sorted(registered - {fn[:-3] for fn in files})
    if ghost:
        f.append(
            {
                "cat": "门禁失效",
                "sev": "HIGH",
                "where": "gates/__init__.py:ALL_GATES",
                "detail": f"**注册表指向不存在模块** {ghost}（运行即 ImportError 或静默跳过）",
            }
        )

    # 静默吞错：`except ...: pass` 且函数体只有 pass → 该判据永不报错
    for p in _pyfiles():
        if "gates" not in p.replace("\\", "/"):
            continue
        src = _read(p)
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                # ⚠️ 修正（2026-09-29）：原判据把"函数体只含**表达式语句**"也算静默
                # （`not body` 其中 body 已过滤掉 Expr）→ 于是 `problems.append(...)` /
                # `warnings.append(...)` / `print(WARN...)` 这些**实体处理**全被误报为
                # `except: pass`（实测 11 处全为误报）。正确判据：**仅** `pass`（或空体）才静默。
                silent = (not node.body) or all(isinstance(b, ast.Pass) for b in node.body)
                if silent:
                    f.append(
                        {
                            "cat": "门禁失效",
                            "sev": "HIGH",
                            "where": f"{_rel(p)}:{node.lineno}",
                            "detail": "`except: pass` —— 该路径异常被吞，判据可能永不报错",
                        }
                    )
    # 跳过门禁的开关（读入口文件文本）
    entry_txt = _read(os.path.join(ROOT, "cli.py")) + _read(os.path.join(ROOT, "tools", "ci_check.py"))
    for pat, why in (("skip-gates", "存在跳过门禁的 CLI 开关"), ("NO_GATES", "存在跳过门禁的 env")):
        if pat.lower() in entry_txt.lower():
            f.append({"cat": "门禁失效", "sev": "MED", "where": "cli.py/ci_check.py", "detail": why})
    return f


# --------------------------------------------------------------------------
# ③ 数据源不唯一（重复字面量）
# --------------------------------------------------------------------------
DUP_KEYS = [
    "relations_index.jsonl",
    "clauses_index.json",
    "publish_manifest",
    "semantic_tools.json",
    "contract_manifest.json",
    "governance.db",
]


def check_single_source() -> list:
    """同一关键标识符在多个文件重复字面量出现（应经 `paths`/`enums` 单一出口）。"""
    f: list = []
    for key in DUP_KEYS:
        hits: list = []
        for p in _pyfiles():
            n = _read(p).count(f'"{key}"') + _read(p).count(f"'{key}'")
            if n:
                hits.append(_rel(p))
        if len(hits) > 2:
            f.append(
                {
                    "cat": "数据源不唯一",
                    "sev": "MED",
                    "where": ", ".join(hits[:5]),
                    "detail": f"`{key}` 在 {len(hits)} 个文件出现为**字面量**（宜收敛到 paths/enums 单一出口）",
                }
            )
    return f


# --------------------------------------------------------------------------
# ④ 硬编码
# --------------------------------------------------------------------------
ABS_PATH = re.compile(r'["\']([A-Za-z]:\\\\|[A-Za-z]:/|/home/|/Users/)')
DATE_LIT = re.compile(r'["\'](20\d{2}-\d{2}-\d{2})["\']')


def check_hardcode() -> list:
    """绝对路径（破坏可克隆性）与硬编码日期（应经 clock/paths）。"""
    f: list = []
    # ⚠️ 修正（2026-09-29）：**检测该问题的门禁自身**（`gate_hardcoded_paths.py`）必然含
    # 绝对路径**正则模式**，把它当违规属**自指误报**（会把"守门人"当成"违规者"）。排除之。
    self_exempt = {"gates/gate_hardcoded_paths.py"}
    for p in _pyfiles():
        r = _rel(p)
        if r.startswith("tests/") or r in self_exempt:
            continue
        src = _read(p)
        for m in ABS_PATH.finditer(src):
            line = src[: m.start()].count("\n") + 1
            f.append(
                {
                    "cat": "硬编码",
                    "sev": "HIGH",
                    "where": f"{r}:{line}",
                    "detail": f"绝对路径字面量 `{m.group(1)}`（破坏克隆可移植性）",
                }
            )
        # ⚠️ 修正（2026-09-29）：日期字面量须区分两类 ——
        #   · **领域字面量**（如文件名日期规则 `2024-01-02`、政策生效日）→ **正常**，不该报；
        #   · **时间语义写死**（把"今天/当前"写死成常量）→ 危险。
        # 判据：**仅当同文件同时使用 `now()`/`today()`** 时，日期字面量才可能是"写死当前时间"。
        # 否则只登记为 LOW 提示（不制造噪音）。
        uses_now = bool(re.search(r"\b(now|today|utcnow)\s*\(", src))
        for m in DATE_LIT.finditer(src):
            line = src[: m.start()].count("\n") + 1
            if uses_now:
                sev, why = "MED", "同文件存在 `now()/today()`，该日期字面量**可能是写死的当前时间**"
            else:
                sev, why = "LOW", "领域字面量（同文件无 `now()/today()`，判为规则/示例日期）"
            f.append(
                {
                    "cat": "硬编码",
                    "sev": sev,
                    "where": f"{r}:{line}",
                    "detail": f"日期字面量 `{m.group(1)}`：{why}",
                }
            )
    return f


# --------------------------------------------------------------------------
# ⑤ 实现重复 / 代码冗余
# --------------------------------------------------------------------------
def _body_fp(src: str) -> str:
    s = re.sub(r"#.*", "", src)
    s = re.sub(r'"""(?:.|\n)*?"""', "", s)
    s = re.sub(r"\s+", "", s)
    return s


def check_duplication() -> list:
    """函数体指纹重复（跨文件/跨函数）→ 同一逻辑多处实现。"""
    f: list = []
    seen: dict = defaultdict(list)
    for p in _pyfiles():
        try:
            tree = ast.parse(_read(p))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                try:
                    body = ast.get_source_segment(_read(p), node) or ""
                except Exception:  # noqa: BLE001
                    continue
                # 只统计"有实质逻辑"的函数（≥6 行）
                if body.count("\n") < 6:
                    continue
                seen[_body_fp(body)].append(f"{_rel(p)}:{node.name}")
    for _fp, where in seen.items():
        if len(where) > 1:
            f.append(
                {
                    "cat": "实现重复",
                    "sev": "MED" if len(where) == 2 else "HIGH",
                    "where": ", ".join(where[:4]),
                    "detail": f"{len(where)} 处**函数体逐字相同**（应抽公共实现，避免改一处漏一处）",
                }
            )
    return f


def check_dead_public() -> list:
    """公共函数（非 `_` 前缀）在仓内**零引用** → 冗余或漏接线。

    ⚠️ 修正（2026-09-29）：引用统计须**含 `tests/`** —— 否则"仅被测试引用"的共享纯函数
    （如 `artifact_shape.py` 的断言类函数）会被整片误报为死代码（实测 87 项多为此类）。
    定义侧仍只查源码目录（测试不定义公共 API）。
    """
    f: list = []
    all_src = {p: _read(p) for p in _pyfiles()}
    ref_blob = "\n".join([_read(p) for p in _pyfiles(with_tests=True)])
    blob = ref_blob
    for p, src in all_src.items():
        if _rel(p).startswith(("tests/", "tools/audit_health.py")):
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
                # ⚠️ 判据修正（2026-09-29）：原按 **调用形态** `name(` 计数 → **多行导入
                # `from mod import (\n  a,\n  b,\n)` 里的裸名字不是调用**，于是被整片误报为
                # 死代码（实测 `artifact_shape` 的 10 个函数**全部**由 `gate_contract` /
                # `recall_audit` 经多行导入消费，却报 9 项"零引用"）。
                # 正确判据：按**词边界出现次数**统计（导入、属性访问、调用一并计入）。
                n = len(re.findall(rf"\b{re.escape(node.name)}\b", blob))
                if n <= 1 and node.name not in ("main",):
                    f.append(
                        {
                            "cat": "代码冗余",
                            "sev": "MED",
                            "where": f"{_rel(p)}:{node.name}",
                            "detail": "公共函数**全仓零引用**（死代码，或漏接线）",
                        }
                    )
    return f


# --------------------------------------------------------------------------
# ⑥ 数据阻塞 / 效率
# --------------------------------------------------------------------------
def check_blocking() -> list:
    """固定 sleep（不可预测阻塞）与最近一轮全链的**耗时排行**。"""
    f: list = []
    # ⚠️ 修正（2026-09-29）：原判据把**一切** `time.sleep(...)` 都当"固定等待不可控"→ 误报为主
    # （实测样本 100% 为合法：随机限速 `random.uniform`、指数退避重试、带 deadline 的轮询间隔）。
    # 收敛为**只看字面量且 ≥1 秒**者（秒级硬等待才是可优化/可阻塞点）；变量/退避一律不报。
    for p in _pyfiles():
        src = _read(p)
        for m in re.finditer(r"\btime\.sleep\(\s*(\d+(?:\.\d+)?)\s*\)", src):
            sec = float(m.group(1))
            if sec < 1.0:
                continue
            line = src[: m.start()].count("\n") + 1
            f.append(
                {
                    "cat": "数据阻塞",
                    "sev": "MED",
                    "where": f"{_rel(p)}:{line}",
                    "detail": f"**秒级字面量等待** `time.sleep({sec:g})`（硬等待；宜为可配置/退避）",
                }
            )
    # 全链耗时（读最近汇总）
    try:
        tmp = os.path.join(ROOT, "reports", "_tmp")
        sums = sorted(
            [x for x in os.listdir(tmp) if x.startswith("生产刷新汇总_")], reverse=True
        ) if os.path.isdir(tmp) else []
        if sums:
            d = json.loads(_read(os.path.join(tmp, sums[0])))
            steps = d.get("steps") or d.get("report") or []
            rows = []
            for s in steps if isinstance(steps, list) else []:
                if isinstance(s, dict) and s.get("elapsed") is not None:
                    rows.append((float(s["elapsed"]), s.get("name") or "?"))
            rows.sort(reverse=True)
            if rows:
                tot = sum(r[0] for r in rows)
                top = ", ".join(f"{n}({e:.0f}s)" for e, n in rows[:5])
                f.append(
                    {
                        "cat": "代码效率",
                        "sev": "LOW",
                        "where": f"reports/_tmp/{sums[0]}",
                        "detail": f"全链耗时 {tot:.0f}s；**Top5** {top}（占 {sum(r[0] for r in rows[:5]) / max(tot, 1):.0%}）",
                    }
                )
    except Exception as e:  # noqa: BLE001
        f.append({"cat": "代码效率", "sev": "LOW", "where": "reports/_tmp", "detail": f"耗时台账不可读：{e}"})
    return f


# --------------------------------------------------------------------------
# ⑦ 流程断点（产物有产无消）
# --------------------------------------------------------------------------
def check_breaks() -> list:
    """关键产物常量：**有生产者但无读取方** → 流程断点（白跑/无人用）。"""
    f: list = []
    # ⚠️ 修正（2026-09-29）：引用面须**含 tests/** 与**契约清单**（`contract_manifest.json`
    # 以 `symbol` 字段登记常量名）—— 否则"被测试或清单消费"的契约会被误报为死契约。
    blob_parts = [_read(p) for p in _pyfiles(with_tests=True)]
    blob_parts.append(_read(os.path.join(ROOT, "config", "schema", "contract_manifest.json")))
    blob = "\n".join(blob_parts)
    try:
        import interfaces.contract as ct
    except Exception as e:  # noqa: BLE001
        return [{"cat": "流程断点", "sev": "LOW", "where": "interfaces/contract.py", "detail": f"跳过：{e}"}]
    names = [n for n in dir(ct) if n.isupper() and isinstance(getattr(ct, n), (list, tuple))]
    for n in names:
        if blob.count(n) <= 1:
            f.append(
                {
                    "cat": "流程断点",
                    "sev": "MED",
                    "where": f"interfaces/contract.py:{n}",
                    "detail": "契约常量**全仓仅定义处出现**（无消费者：要么漏接线，要么是死契约）",
                }
            )
    return f


def check_readme_consistency() -> list:
    """**README 与代码一致性**（本轮用户诉求："确保代码与 readme 文档一致"）。

    为什么机器核验：文档漂移**不会报错**，只会让人按错的过程操作。故把"README 里写死的数字
    与路径"逐项对照代码：① 门禁数；② README 引用的关键文件/工具是否真实存在；
    ③ README 引用的 env 是否在工具清单/配置中有出处。
    """
    f: list = []
    p = os.path.join(ROOT, "README.md")
    if not os.path.exists(p):
        return [{"cat": "README 一致性", "sev": "HIGH", "where": "README.md", "detail": "README 缺失"}]
    txt = _read(p)
    # ① 门禁数声明
    try:
        from gates import ALL_GATES

        n_gate = len(ALL_GATES)
        for m in re.finditer(r"\*\*(\d+)\s*道\*\*", txt):
            if int(m.group(1)) != n_gate:
                line = txt[: m.start()].count("\n") + 1
                f.append(
                    {
                        "cat": "README 一致性",
                        "sev": "HIGH",
                        "where": f"README.md:{line}",
                        "detail": f"声明门禁数 {m.group(1)} ≠ 实际 ALL_GATES={n_gate}（文档漂移）",
                    }
                )
    except Exception as e:  # noqa: BLE001
        f.append(
            {"cat": "README 一致性", "sev": "LOW", "where": "gates/__init__.py", "detail": f"门禁数核验跳过：{e}"}
        )
    # ② README 引用的关键产物/工具是否存在
    for rel in (
        "docs/全链数据流总图.md",
        "tools/gen_flow_map.py",
        "tools/audit_health.py",
        "config/schema/semantic_tools.json",
        "interfaces/contract.py",
    ):
        if rel in txt and not os.path.exists(os.path.join(ROOT, rel)):
            f.append(
                {
                    "cat": "README 一致性",
                    "sev": "MED",
                    "where": "README.md",
                    "detail": f"引用了不存在的路径 `{rel}`（文档指向空气）",
                }
            )
    # ③ README 提到的 env 是否在工具清单中有出处
    try:
        from std_lib.common_lib import semantic_tools as st

        declared = {e for s in (st.load_manifest().get("tools") or {}).values() for e in (s.get("offline_env") or [])}
        # ⚠️ 修正（2026-09-29）：原"已知 env 集合"只含工具清单项 → 把 OCR 等**真实存在但与
        #    P1 无关**的 env（`OCR_PADDLE_ROOT`/`OCR_TESSERACT_BIN`/`OCR_TESSDATA_DIR`）误报为脱节。
        #    改为**动态收集**：全仓代码里的 `os.environ.get("X")`/`os.getenv("X")` + ocr.yaml 键。
        # 收集口径**放宽**到"任何含 environ/getenv/env 的行里的全大写标识符"——
        # 因为取值形态多样（`os.environ[...]`、`env.get(...)`、`_ENV["X"]`、`config.get("X")`），
        # 窄正则仍会漏（实测 `OCR_PADDLE_ROOT` 即以非标准形态取用）。
        for p in _pyfiles():
            for src_line in _read(p).splitlines():  # 注意：勿复用 `line`（上文已绑定为 int 行号）
                if re.search(r"environ|getenv|\benv\b", src_line, re.I):
                    declared |= set(re.findall(r'["\']([A-Z][A-Z0-9_]{3,})["\']', src_line))
        # 配置里的 env 常以**占位符**出现（`${OCR_PADDLE_ROOT:-默认}`）→ 须同时收集
        # **值**中的占位符名（只看键会漏，实测 `OCR_PADDLE_ROOT` 即由此漏掉）。
        for yml in ("ocr.yaml", "sources.yaml", "schedule.yaml", "triggers.yaml"):
            txt_y = _read(os.path.join(ROOT, "config", yml))
            declared |= set(re.findall(r"\$\{([A-Z][A-Z0-9_]{2,})", txt_y))
        declared |= {"PGVECTOR_DSN", "PGVECTOR_APP_PWD", "PKULAW_NODE_EXE", "PKULAW_PKG_DIR"}
        for m in re.finditer(r"`([A-Z][A-Z0-9_]{3,})`", txt):
            name = m.group(1)
            if not any(k in name for k in ("_HOME", "_DIR", "_DSN", "_BIN", "_TOKEN", "_EXE", "_ROOT", "_OFFLINE", "_PWD")):
                continue
            if name in declared:
                continue
            line = txt[: m.start()].count("\n") + 1
            f.append(
                {
                    "cat": "README 一致性",
                    "sev": "LOW",
                    "where": f"README.md:{line}",
                    "detail": f"env `{name}` 未在工具清单/已知 env 集合中出现（文档与实现脱节之嫌）",
                }
            )
    except Exception as e:  # noqa: BLE001
        f.append({"cat": "README 一致性", "sev": "LOW", "where": "README.md", "detail": f"env 核验跳过：{e}"})
    return f


CHECKS = [
    ("README 一致性", check_readme_consistency),
    ("统一入口", check_entry),
    ("门禁失效", check_gates),
    ("数据源不唯一", check_single_source),
    ("硬编码", check_hardcode),
    ("实现重复", check_duplication),
    ("代码冗余", check_dead_public),
    ("数据阻塞/效率", check_blocking),
    ("流程断点", check_breaks),
]


def run() -> list:
    """→ 全部发现（`[{cat, sev, where, detail}, ...]`）。

    注：返回类型为 `list` 而非 `tuple`（mypy 拒绝了原标注 `-> tuple`）。
    """
    findings: list = []
    for _name, fn in CHECKS:
        try:
            findings += fn()
        except Exception as e:  # noqa: BLE001  审计器自身不得中断（但须可见）
            findings.append(
                {"cat": _name, "sev": "LOW", "where": "audit_health", "detail": f"检查器异常：{type(e).__name__}: {e}"}
            )
    return findings


def main() -> int:
    findings = run()
    by_cat: dict = defaultdict(list)
    for x in findings:
        by_cat[x["cat"]].append(x)
    order = [c for c, _ in CHECKS]
    print(f"全链路健康审计：{len(findings)} 项发现（目录 {_rel(ROOT)}）")
    sev = Counter(x["sev"] for x in findings)
    print(f"  分级：HIGH {sev.get('HIGH', 0)} / MED {sev.get('MED', 0)} / LOW {sev.get('LOW', 0)}")
    for c in order:
        items = by_cat.get(c) or []
        print(f"\n【{c}】{len(items)} 项")
        for x in items[:12]:
            print(f"  [{x['sev']:4}] {x['where']}")
            print(f"        {x['detail']}")
        if len(items) > 12:
            print(f"  … 另 {len(items) - 12} 项同类")
    if os.environ.get("AUDIT_JSON"):
        with open(os.environ["AUDIT_JSON"], "w", encoding="utf-8") as fh:
            json.dump(findings, fh, ensure_ascii=False, indent=1)
        print(f"\nJSON 已写入 {os.environ['AUDIT_JSON']}")
    return 0


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    raise SystemExit(main())
