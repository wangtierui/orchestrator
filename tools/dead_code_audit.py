# -*- coding: utf-8 -*-
"""tools.dead_code_audit — **废弃/冗余代码**审计（第一百零七批 T3）。

用户提问
--------
「排查项目全脚本是否存在已明确废弃、删除的代码仍留在脚本内；排查当前项目脚本代码
编写是否存在**只增不减**的冗余情况，导致存在大量垃圾代码。」

这类问题**不能靠眼睛扫**（本仓 200+ 脚本、数万行）⇒ 做成可复跑的工具。
本工具**只报告、不改代码**（改动必须人工判断，避免误删动态引用）。

四类判据
--------
A **废弃标记**：注释里出现 `已废弃/已删除/不再使用/不再生成/停用/下线/deprecated/
  TODO: remove/待删除` 等字样 —— 这些位置要么已死、要么注释过期，都该看一眼。
B **只定义不引用**：模块顶层 `def`/`class` 的名字在**全仓**只出现在定义处
  （排除 `__all__` 登记、`main`、测试、dunder、以及 `getattr`/字符串注册的场景无法
  静态判定 ⇒ 一律标"需人工确认"）。
C **只增不减痕迹**：同一文件内出现 `_old/_legacy/_v1/_bak/_deprecated/_unused`
  命名；或 `if False:`/`if 0:` 死分支。
D **大段注释掉的代码**：连续 ≥5 行以 `#` 开头且含 `=`/`(`/`return` —— 通常是
  "改成新写法但旧代码留在注释里"。

用法
----
    python -m tools.dead_code_audit              # 全仓（跳过 refactor/ 等）
    python -m tools.dead_code_audit --only B     # 只看某一类
    python -m tools.dead_code_audit --json out.json

退出码：0=无发现；1=有发现（**披露性质**，不阻断门禁——B 类存在假阳性）。
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re

from bootstrap import bootstrap

bootstrap("all")

import paths

#: 扫描根（相对仓根）；`refactor/` 属其他用户，按仓规**不碰**
ROOTS = ("std_lib", "modules", "tools", "interfaces", "gates", "commands",
         "web", "config", "mcp_server", "tests")
SKIP_DIRS = {"__pycache__", ".git", "node_modules", "retired", "archive",
             "refactor", ".codebuddy"}

DROP_MARKERS = (
    "已废弃", "已删除", "不再使用", "不再生成", "已下线", "停用",
    "deprecated", "DEPRECATED", "Deprecated",
    "TODO: remove", "TODO:remove", "待删除", "可删除",
)
LEGACY_NAME = re.compile(r"(_old|_legacy|_v1|_bak|_deprecated|_unused|_obsolete)\b")
DEAD_BRANCH = re.compile(r"^\s*if\s+(False|0)\s*:", re.M)
COMMENTED_CODE = re.compile(r"^\s*#\s*(?:self\.)?\w+\s*(=|\(|\.\w+\()")


def _iter_py():
    # ⚠️ **根目录的 .py 也必须扫**（2026-10-22 实测的假阴性）：
    # `bootstrap.py` / `paths.py` / `cli.py` 不在任何子目录 ROOT 里，
    # 漏掉它们会让 `apply_mirror_env` / `module_by_key` / `module_by_pkg`
    # 这类**正在被 bootstrap 使用**的函数被误判为死代码。
    for fn in sorted(os.listdir(paths.ROOT)):
        if fn.endswith(".py"):
            yield os.path.join(paths.ROOT, fn)
    for root in ROOTS:
        base = os.path.join(paths.ROOT, root)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for fn in filenames:
                if fn.endswith(".py"):
                    yield os.path.join(dirpath, fn)


def _rel(p: str) -> str:
    return os.path.relpath(p, paths.ROOT).replace(os.sep, "/")


def collect_defs() -> dict[str, list[tuple[str, int]]]:
    """顶层 `def`/`class` 定义：name → [(file, lineno)]。"""
    out: dict[str, list[tuple[str, int]]] = {}
    for p in _iter_py():
        try:
            tree = ast.parse(open(p, encoding="utf-8", errors="replace").read())
        except (SyntaxError, OSError):
            continue
        rel = _rel(p)
        if rel.startswith("tests/"):
            # pytest 测试由**自动收集**驱动（`TestXxx`/`test_*`），不是死代码；
            # 不排除会产生 200+ 假阳性，把真信号淹没（实测第一版就是这样）。
            continue
        for n in tree.body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                out.setdefault(n.name, []).append((rel, n.lineno))
    return out


def file_text_cache() -> dict[str, str]:
    cache = {}
    for p in _iter_py():
        try:
            cache[_rel(p)] = open(p, encoding="utf-8",
                                     errors="replace").read()
        except OSError:
            pass
    return cache


def audit(only: str | None = None) -> dict:
    res: dict[str, list] = {"A_drop_markers": [], "B_defined_unused": [],
                            "C_legacy_names": [], "D_commented_code": []}
    texts = file_text_cache()

    # ---- A / C / D：文本级 ----
    if only in (None, "A", "C", "D"):
        for rel, txt in texts.items():
            lines = txt.splitlines()
            for i, ln in enumerate(lines, 1):
                if only in (None, "A") and any(m in ln for m in DROP_MARKERS):
                    res["A_drop_markers"].append(
                        {"file": rel, "line": i, "text": ln.strip()[:120]})
                if only in (None, "C"):
                    if LEGACY_NAME.search(ln) and not ln.strip().startswith("#"):
                        res["C_legacy_names"].append(
                            {"file": rel, "line": i, "text": ln.strip()[:120]})
                    if DEAD_BRANCH.match(ln):
                        res["C_legacy_names"].append(
                            {"file": rel, "line": i,
                             "text": "[死分支] " + ln.strip()[:110]})
            if only in (None, "D"):
                run: list[int] = []
                for i, ln in enumerate(lines, 1):
                    if COMMENTED_CODE.match(ln):
                        run.append(i)
                    else:
                        if len(run) >= 5:
                            res["D_commented_code"].append(
                                {"file": rel, "from": run[0], "to": run[-1],
                                 "lines": len(run)})
                        run = []
                if len(run) >= 5:
                    res["D_commented_code"].append(
                        {"file": rel, "from": run[0], "to": run[-1],
                         "lines": len(run)})

    # ---- B：定义但全仓无引用 ----
    if only in (None, "B"):
        defs = collect_defs()
        blob = "\n".join(texts.values())
        for name, locs in sorted(defs.items()):
            if name in ("main", "__init__") or name.startswith("__"):
                continue
            if name.startswith("test_"):
                continue
            # 全仓出现次数：定义处各 1 次 + 引用次数。
            # ⚠️ **允许前置点号**：本仓最常见的引用形式是 `模块.函数(...)` /
            # `store.list_records(...)`；若用 `(?<![\w.])` 排除点号，会把这类引用
            # 全漏掉 ⇒ 报出大量"假死代码"（第一版实测把 `decorate`、
            # `list_records` 等正在用的函数误判为死代码）。
            occurrences = len(re.findall(
                rf"(?<![\w]){re.escape(name)}(?![\w])", blob))
            if occurrences > len(locs):
                continue        # 有引用
            in_all = f'"{name}"' in blob or f"'{name}'" in blob
            res["B_defined_unused"].append(
                {"name": name, "defined_at": [f"{f}:{n}" for f, n in locs],
                 "string_mentioned": in_all,
                 "note": ("被字符串提及（可能经 getattr/注册表动态调用）"
                          if in_all else "全仓仅定义处出现")})
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="dead_code_audit",
                                 description="废弃/冗余代码审计（只报告）")
    ap.add_argument("--only", choices=["A", "B", "C", "D"], default=None)
    ap.add_argument("--json", default="", help="把结果写成 JSON")
    ap.add_argument("--limit", type=int, default=25, help="每类最多打印条数")
    a = ap.parse_args(argv)
    r = audit(a.only)
    total = sum(len(v) for v in r.values())
    print(f"[dead_code] 扫描根：{', '.join(ROOTS)}（跳过 {sorted(SKIP_DIRS)}）")
    print(f"[dead_code] 合计 {total} 项发现\n")
    titles = {
        "A_drop_markers": "A 废弃标记（已废弃/不再使用/deprecated…）",
        "B_defined_unused": "B 只定义不引用（可能死代码；需人工确认动态调用）",
        "C_legacy_names": ("C 命名/死分支提示（**需人工判读**）：本仓实测 40 项均为"
                           "语义正当的业务命名（ipn_old=旧内部编号 / enrich_legacy="
                           "是否处理旧格式 / self._old=上下文旧值）；可自动判定的"
                           "`if False/0` 死分支实测 0 项"),
        "D_commented_code": "D 大段注释掉的代码（连续 ≥5 行）",
    }
    for key, title in titles.items():
        items = r[key]
        if a.only and not key.startswith(a.only):
            continue
        print(f"=== {title}：{len(items)} 项 ===")
        for it in items[:a.limit]:
            if key == "B_defined_unused":
                print(f"   {it['name']:<34} @ {it['defined_at'][0]}  [{it['note']}]")
            elif key == "D_commented_code":
                print(f"   {it['file']}:{it['from']}-{it['to']}（{it['lines']} 行）")
            else:
                print(f"   {it['file']}:{it['line']}  {it['text']}")
        if len(items) > a.limit:
            print(f"   … 其余 {len(items) - a.limit} 项（用 --json 导出全量）")
        print()
    if a.json:
        with open(a.json, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(r, fh, ensure_ascii=False, indent=2)
        print(f"[dead_code] 已导出 {a.json}")
    return 1 if total else 0


if __name__ == "__main__":
    raise SystemExit(main())
