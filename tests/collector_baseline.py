# -*- coding: utf-8 -*-
"""tests.collector_baseline — 采集器拆分的**等价性基线**（结构快照，非 HTTP 夹具）。

为什么需要（而不是只靠 cassette）
--------------------------------
拆分（把 800 行的采集器拆成 常量/会话/抓取器/编排 四层）的真实风险**不是 HTTP
行为变化**（那是 cassette 的职责），而是：

  · **漏搬**：某个模块级常量/函数没搬到新位置 ⇒ 运行时 `NameError`，
    且**只在脚本形态暴露**（包形态与单测可能全绿）；
  · **搬错**：常量值被改（selector 少一项、header 少一条）；
  · **签名漂移**：类的方法参数变了但调用点没改；
  · **CLI 漂移**：`main()` 的 argparse 选项少了/改名（调度会传 `--source` 等）。

快照四类（**全部用稳定结构表示，不用 `hash()`**——str hash 每进程随机）
------------------------------------------------------------------------
1. `module_names` —— 模块级名字全集（`expected` 存基线，`present` 运行时取值）
2. `const_digest` —— 容器常量取「长度 + 排序键/元素」；字符串取「长度 + 前 12 字」；
   正则取「pattern + flags」
3. `class_methods` —— 每个类的方法 → 参数名元组
4. `cli` —— `main()` 的 argparse 选项与默认值（默认值里的仓根归一为 `<ROOT>`）

**`expected` 由 AST 从**首次生成时的源文件**收集**（v1 是硬编码 36 个名字，
实测漏了 `requests`/`Retry`/`BeautifulSoup`/`HTTPAdapter` 等 ⇒ 漏搬抓不到）。
生成后 `expected` 固化在 JSON 里，比对时以它为准 ⇒ **改名/删符号可被抓出**。

用法
----
    python -m tests.collector_baseline --source gov --write
    python -m tests.collector_baseline --source gov          # 比对（差异则非零退出）
    python -m tests.collector_baseline --source mof --write
"""
from __future__ import annotations

import argparse
import ast
import inspect
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 支持的采集器（源名 → 采集器包路径）。新增源时在此登记即可复用同一套基线。
SOURCES: dict[str, str] = {
    "gov": "modules/regulatory_scrapers/collectors/gov/collector.py",
    "mof": "modules/regulatory_scrapers/collectors/mof/collector.py",
    "pbc": "modules/regulatory_scrapers/collectors/pbc/collector.py",
    "nfra": "modules/regulatory_scrapers/collectors/nfra/collector.py",
}


def baseline_path(source: str) -> str:
    return os.path.join(ROOT, "tests", "baselines", f"{source}_collector_baseline.json")


def _module_path(source: str) -> str:
    return os.path.join(ROOT, SOURCES[source])


# --------------------------------------------------------------------------- #
# expected 名字全集：**AST 自动收集**（不硬编码）
# --------------------------------------------------------------------------- #
#: 引导临时名（用后即 `del`，不是模块符号）
_GUIDE_TMP = {"_GUIDE_ROOT", "_os", "_sys"}


def _is_noise(name: str) -> bool:
    """判断是否为"AST 采集噪声"（不是需要守护的模块符号）。

    两类噪声（实测在比对时各制造一个假 `missing`）：
      · **标准库/三方模块名**：`import logging` / `import json` 收集到的是**模块**，
        不是本模块定义的符号（换实现时它会消失，但那不是"漏搬"）；
      · **内部 import 别名**：`from . import attachments as _att` ⇒ `_att` 只是
        跨模块访问的中转名（真正要守护的是 `build_entry` 等被赋值的符号）。
    判据：在 `sys.modules` 里能查到（是模块）⇒ 噪声；或以 `_` 开头且形如
    单下划线+单词（纯别名，如 `_att`/`_parse`）⇒ 噪声。
    """
    if name in _GUIDE_TMP:
        return True
    if name in sys.modules:
        return True
    # ⚠️ 还要查**标准库名单**：`_is_noise` 被调用时（过滤基线 expected）目标模块
    # 可能**尚未 import**（如拆分后 `csv`/`html` 只在 `mof_io` 里 import，
    # 而过滤发生在 import 之前）⇒ 只查 `sys.modules` 会漏判，把标准库模块名当成
    # "漏搬符号"（实测 `csv`/`html`/`logging` 三条假 missing）。
    if name in getattr(sys, "stdlib_module_names", frozenset()):
        return True
    return bool(name.startswith("_") and name.count("_") == 1
                and name[1:].isalpha() and name.lower() == name)


def _ast_module_names(path: str) -> list[str]:
    """收集模块级名字全集（递归进 `try/except` 导入块）。

    ⚠️ 必须递归 `ast.Try`：本仓采集器的依赖大多写在
    `try: from .x import y / except ImportError: from x import y` 里，
    只扫顶层会漏掉一半（实测漏了 `requests`/`Retry`/`MasterView` 等）。

    `import x` **不收集**（那是模块名，见 `_is_noise`）；
    `from m import a` / `from m import a as b` 收集 `a`/`b`（真实符号）。
    """
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    names: set[str] = set()

    def walk(stmts) -> None:
        for n in stmts:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(n.name)
            elif isinstance(n, ast.Assign):
                for t in n.targets:
                    if isinstance(t, ast.Name):
                        names.add(t.id)
            elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
                names.add(n.target.id)
            elif isinstance(n, ast.ImportFrom):
                for a in n.names:
                    if a.name == "*":
                        continue
                    names.add(a.asname or a.name)
            elif isinstance(n, ast.Try):
                walk(n.body)
                for h in n.handlers:
                    walk(h.body)

    walk(tree.body)
    return sorted(n for n in names if not _is_noise(n))


# --------------------------------------------------------------------------- #
# 其余三类快照
# --------------------------------------------------------------------------- #
def _normalize_default(v: str) -> str:
    """把默认值里的**仓根前缀**归一为 `<ROOT>`（防"盘符字面量"门禁）。"""
    if not v:
        return v
    s = str(v)
    for cand in (ROOT, ROOT.replace("\\", "/")):
        s = s.replace(cand, "<ROOT>")
    return s


def _const_digest(mod, names: list[str]) -> dict:
    """对容器/字符串/正则型常量做稳定摘要（按**实测类型**分类，不按名字猜）。"""
    out: dict[str, object] = {}
    for n in names:
        v = getattr(mod, n, None)
        if v is None:
            continue
        if isinstance(v, dict):
            out[n] = {"kind": "dict", "len": len(v),
                      "keys": sorted(map(str, v.keys()))}
        elif isinstance(v, (list, tuple)):
            out[n] = {"kind": "seq", "len": len(v),
                      "items": [str(x)[:80] for x in v]}
        elif isinstance(v, (set, frozenset)):
            out[n] = {"kind": "set", "len": len(v),
                      "items": sorted(map(str, v))}
        elif isinstance(v, str):
            # ⚠️ `head` 也要做仓根归一：字符串常量里可能是**绝对路径**
            # （如 `_WT_ROOT = os.path.join(paths.ROOT, …)` 的求值结果），
            # 直接存前 12 字会把盘符写进基线 ⇒ 触发"盘符字面量"门禁（实测踩到）。
            out[n] = {"kind": "str", "len": len(v),
                      "head": _normalize_default(v)[:12]}
        elif hasattr(v, "pattern"):          # re.Pattern
            out[n] = {"kind": "regex", "pattern": v.pattern,
                      "flags": int(v.flags)}
    return out


def _class_methods(mod, names: list[str]) -> dict:
    """从**模块属性**（含 re-export）取类，记录方法 → 参数名。

    ⚠️ 为什么按 `getattr(mod, name)` 而不是扫源文件的 AST：
    拆分后类已不在 `collector.py`（在 `gov_session` / `gov_scraper`），
    扫本文件 AST 会得到 **0 个类**（v1 实测），于是方法签名守护**静默失效**。
    按"collector 能取到的类"来收集 ⇒ 不论类搬到哪里，只要 re-export 完整就被守护。
    """
    #: 只收**本仓定义**的类。`datetime`/`Any`/`BeautifulSoup` 等虽是 `inspect.isclass`，
    #: 但它们是标准库/三方类（我方无法控制其方法签名，且版本升级就会漂移）
    #: ⇒ 收进来只会制造假 `missing`（实测 `class:datetime` 一条）。
    ours = ("modules.", "std_lib.", "interfaces.", "tools.", "gates.", "config.")
    out: dict[str, dict] = {}
    for cname in names:
        cls = getattr(mod, cname, None)
        if cls is None or not inspect.isclass(cls):
            continue
        if not str(getattr(cls, "__module__", "")).startswith(ours):
            continue
        methods: dict[str, list[str]] = {}
        for mname, fn in inspect.getmembers(cls, predicate=inspect.isfunction):
            if mname.startswith("__") and mname != "__init__":
                continue
            try:
                sig = inspect.signature(fn)
            except (TypeError, ValueError):
                methods[mname] = ["<unavailable>"]
                continue
            methods[mname] = list(sig.parameters)
        out[cname] = methods
    return out


def _cli_options(mod) -> dict:
    """截获 `main()` 的 `add_argument` 调用（不改行为）。"""
    captured: list[dict] = []
    orig = argparse.ArgumentParser.add_argument

    def spy(self, *a, **kw):
        captured.append({
            "flags": [str(x) for x in a if isinstance(x, str)],
            "default": (_normalize_default(str(kw["default"]))
                        if "default" in kw and kw["default"] is not None else None),
            "action": str(kw.get("action") or "store"),
            "required": bool(kw.get("required", False)),
        })
        return orig(self, *a, **kw)

    argv_backup = sys.argv[:]
    argparse.ArgumentParser.add_argument = spy  # type: ignore[method-assign]
    try:
        sys.argv = ["collector.py", "--help"]
        try:
            mod.main()
        except SystemExit:
            pass
        except Exception:  # noqa: BLE001  --help 之外的失败与"选项集合"无关
            pass
    finally:
        argparse.ArgumentParser.add_argument = orig  # type: ignore[method-assign]
        sys.argv = argv_backup
    return {"count": len(captured), "options": captured}


def build(source: str, expected: list[str] | None = None) -> dict:
    """构造快照。

    ⚠️ **`expected` 必须由调用方传入基线里的那份**（`--write` 时才用 AST 现算）。
    这是 v1 的设计缺陷：`expected` 每次从**当前文件** AST 重算 ⇒ 拆分后 expected
    自动缩水 ⇒ `missing` 恒为空 ⇒ **"漏搬"永远抓不到**（基线形同虚设）。
    正确语义：expected = **首次生成基线时**的名字全集（拆分前），
    present = 现在 `hasattr` 的结果 ⇒ 二者之差才是"漏搬"。
    """
    import importlib

    path = _module_path(source)
    modname = ("modules.regulatory_scrapers.collectors."
               f"{source}.collector")
    m = importlib.import_module(modname)
    if expected is None:
        expected = _ast_module_names(path)      # 仅 --write 时用
    present = [n for n in expected if hasattr(m, n)]
    return {
        "_note": f"{source}/collector.py 拆分等价性基线（结构快照，非 hash）",
        "_source_path": os.path.relpath(path, ROOT).replace("\\", "/"),
        "module_names": {"expected": expected, "present": sorted(present),
                         "missing": sorted(set(expected) - set(present))},
        "const_digest": _const_digest(m, expected),
        # 类方法按 `getattr(mod, 名字)` 收集 ⇒ 类搬到 gov_session/gov_scraper 后，
        # 只要 collector 仍 re-export 就被守护（v1 扫源文件 AST 得到 0 个类，守护静默失效）
        "class_methods": _class_methods(m, expected),
        "cli": _cli_options(m),
    }


def compare(source: str) -> list[str]:
    """返回不一致的段落名（空 = 全部一致）。"""
    p = baseline_path(source)
    if not os.path.exists(p):
        return ["<基线文件缺失>"]
    base = json.load(open(p, encoding="utf-8"))
    # ⚠️ 用**基线里的 expected** 构造当前快照（关键，见 build() 的 docstring）：
    # 否则 expected 每次从当前文件重算，拆分后自动缩水 ⇒ missing 恒空 ⇒ 漏搬抓不到。
    # 同时**过滤噪声**（老基线用旧规则生成，含 `import logging`/`_att` 这类
    # 模块名与内部别名，它们在拆分后必然"消失"但并非漏搬）。
    exp = [n for n in (base.get("module_names", {}).get("expected") or [])
           if not _is_noise(n)]
    cur = build(source, expected=exp)
    diffs: list[str] = []

    # ── module_names：**单向包含**（拆分前有的，现在还得有）──
    # 不比"集合完全相同"：拆分后 AST 会自然多/少一些导入名（如多出 re-export 目标），
    # 那既不表示漏搬也不表示漂移。真正的信号是 **missing 非空**。
    if cur["module_names"]["missing"]:
        diffs.append("module_names")

    # ── const_digest：基线里每个常量，现在必须一致（子集检查）──
    for k, v in (base.get("const_digest") or {}).items():
        if cur["const_digest"].get(k) != v:
            diffs.append(f"const:{k}")

    # ── class_methods：基线里的类，现在必须可取得且方法签名一致（子集检查）──
    cur_cm = cur.get("class_methods") or {}
    for cname, msig in (base.get("class_methods") or {}).items():
        if cur_cm.get(cname) != msig:
            diffs.append(f"class:{cname}")

    # ── cli：必须完全一致（调度依赖选项名与默认值）──
    if base.get("cli") != cur.get("cli"):
        diffs.append("cli")
    return diffs


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    # 无需 `sys.path.insert`：pytest 下 `tests/conftest.py` 已 bootstrap；
    # `python -m tests.collector_baseline` 时 `sys.path[0]` 即 cwd（仓根）。
    # （`gate_import_bootstrap` 的 `tests/` 基线「只减不增」，多一行 insert 就顶破。）
    from bootstrap import bootstrap

    bootstrap("all")

    ap = argparse.ArgumentParser(description="采集器拆分等价性基线")
    ap.add_argument("--source", required=True, choices=sorted(SOURCES),
                    help="采集器源名")
    ap.add_argument("--write", action="store_true", help="生成/更新基线")
    a = ap.parse_args(argv)

    cur = build(a.source)
    p = baseline_path(a.source)
    if a.write:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(cur, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")
        print(f"[baseline:{a.source}] 已写入 {os.path.relpath(p, ROOT)}")
        print(f"  模块名 {len(cur['module_names']['present'])}/"
              f"{len(cur['module_names']['expected'])}，"
              f"类 {len(cur['class_methods'])} 个，CLI 选项 {cur['cli']['count']} 个，"
              f"常量摘要 {len(cur['const_digest'])} 项")
        return 0

    diffs = compare(a.source)
    if diffs:
        print(f"[baseline:{a.source}] **不一致** 段落: {diffs}")
        base = json.load(open(p, encoding="utf-8"))
        for k in diffs:
            print(f"--- {k}")
            print("  基线:", json.dumps(base.get(k), ensure_ascii=False)[:600])
            print("  当前:", json.dumps(cur.get(k), ensure_ascii=False)[:600])
        return 1
    print(f"[baseline:{a.source}] 一致 ✓（模块名/常量/方法签名/CLI 选项）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
