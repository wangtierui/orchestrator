# -*- coding: utf-8 -*-
"""tools.split_precheck — **拆分前置检查**（2026-10-16，E1–E4 四次实证教训的固化）

为什么需要这个工具
------------------
E1–E4 拆了四个采集文件（pbc/collector 729→541、gov/parse 843→210、
gov/zhengceku 802→454、nfra/collector 716→543），每次都在**运行期**才发现
「搬代码忘搬依赖」，累计 20+ 处：

  类别 A · **import 未随迁**（最常见）
    `re` / `urllib.parse` / `csv` / `datetime` / `json` / `ssl` 等 stdlib；
    `lxml.html`（**最隐蔽**：缺失时函数内 try 抛 NameError ⇒ **静默落到正则兜底**，
    `<script>` 不被剥离、正文多出换行——函数不报错，只安静地给错结果）。
  类别 B · **模块级引导块 / logger / 路径常量不属于任何函数闭包**
    AST 求依赖闭包时**捕获不到**（它们不在任何 `def` 里）⇒ 拆分后运行期 NameError。
    实例：`_HERE` 被引导块 `del` 掉但常量用它算路径；`LOG` 迁层后漏迁。
  类别 C · **常量归属错配**
    `CONTENT_SELECTORS` 只被详情层用，我错配到列表层。
  类别 D · **层间 import 位置错误**
    常量在**导入期**求值（`API = BASE + "..."`），层间 import 放文件末尾 ⇒ NameError。
  类别 E · **脚本形态引导缺本目录**（单文件时代不需要，拆兄弟模块后必需）。

本工具把这些**全部变成运行前的静态检查**。

用法
----
    python -m tools.split_precheck <file.py> --target-layer <层名> --move <函数名,...>
    python -m tools.split_precheck <file.py> --survey        # 只出依赖矩阵（不改文件）

**只读**：不修改任何文件，只出「人工检查清单」，不代劳判断。
"""
from __future__ import annotations

import argparse
import ast
import os
import sys
from dataclasses import dataclass, field

from bootstrap import bootstrap

bootstrap("all")

#: 标准库（用于区分「搬漏了 stdlib」与「搬漏了仓内依赖」）
STDLIB = {
    "argparse", "ast", "base64", "collections", "contextlib", "csv", "datetime",
    "functools", "glob", "gzip", "hashlib", "html", "io", "itertools", "json",
    "logging", "math", "os", "pathlib", "random", "re", "shutil", "socket", "ssl",
    "statistics", "struct", "sys", "tempfile", "textwrap", "time", "traceback",
    "typing", "urllib", "uuid", "warnings", "zlib",
}

#: 已知三方库（缺失同样在运行期暴露，但归因不同——装依赖还是漏搬？）
THIRD_PARTY = {"lxml", "requests", "docx", "fitz", "pymupdf", "yaml", "numpy",
               "pandas", "sqlite3", "pyquery"}

#: 仓内顶层包/模块名（脚本形态下需能被裸名导入）
REPO_MODULES = {"paths", "bootstrap", "interfaces", "commands", "gates", "web",
                "tools", "modules", "std_lib", "config", "cli", "mcp_server",
                "reports"}


@dataclass
class BlockDep:
    """单个顶层定义块的外部依赖。"""

    name: str
    kind: str                              # function / class / const
    imports: set[str] = field(default_factory=set)   # 用到的「模块名.属性」的主名
    symbols: set[str] = field(default_factory=set)   # 用到的全部名字
    locals_: set[str] = field(default_factory=set)   # 本块内定义/参数名
    top_names: set[str] = field(default_factory=set)  # 本文件的全部顶层名
    module_imports: set[str] = field(default_factory=set)  # 本文件模块级已 import
        #: 模块级常量/函数被 `X.attr` 引用时（如 `_PAT.sub`），
        #: `X` 会被误当作「模块名」⇒ 必须按**顶层名**排除，否则零噪声失效。

    @property
    def _unimported(self) -> set[str]:
        """用到但**本块内未 import**的模块名。

        ⚠️ 不能用 `symbols` 排除：Attribute 主名（如 `lxml.html` 里的 `lxml`）
        本身就会出现在 `symbols` 里 ⇒ 会把真正缺失的 import 误排除掉
        （E4 的 `html_to_text` 缺 `lxml` 恰恰因此检测不到——而它是最隐蔽的一类：
        函数内 try 抛 NameError ⇒ **静默落到兜底分支**）。
        但**必须**排除：本文件顶层名（`_PAT.sub` 的 `X` 不是模块）
        与**模块级已 import**的名字（`import re` 后块内用 `re.escape` 可见）。
        """
        return self.imports - self.locals_ - self.top_names - self.module_imports

    @property
    def missing_imports(self) -> set[str]:
        """用到但未 import 的 **stdlib** 名。"""
        return self._unimported & STDLIB

    @property
    def missing_repo(self) -> set[str]:
        """用到但未 import 的 **仓内**模块名。"""
        return self._unimported & REPO_MODULES

    @property
    def missing_third(self) -> set[str]:
        """用到但未 import 的**三方**名（须随迁，否则运行期 NameError）。"""
        return self._unimported - STDLIB - REPO_MODULES


@dataclass
class Precheck:
    file: str
    deps: dict[str, BlockDep]
    module_level_risks: list[str] = field(default_factory=list)
    try_import_aliases: list[str] = field(default_factory=list)
    const_users: dict[str, list[str]] = field(default_factory=dict)


def _looks_like_path(name: str) -> bool:
    return name.endswith("_ROOT") or name.endswith("_DIR") or name == "_HERE"


def _collect(tree: ast.Module) -> tuple[dict[str, BlockDep], dict[str, list[str]],
                                        list[str], list[str]]:
    const_defs: dict[str, int] = {}
    #: 本文件全部顶层名（函数/类/常量）——用于把 `X.attr` 里的模块级 `X`
    #: 排除出「疑似缺失的模块名」（否则 `_PAT.sub` 会被当成缺 `re`）。
    all_top: set[str] = set()
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            all_top.add(n.name)
        elif isinstance(n, (ast.Assign, ast.AnnAssign)):
            tgts = n.targets if isinstance(n, ast.Assign) else [n.target]
            for t in tgts:
                if isinstance(t, ast.Name):
                    all_top.add(t.id)
                    if t.id.isupper():
                        const_defs[t.id] = n.lineno

    deps: dict[str, BlockDep] = {}
    const_users: dict[str, list[str]] = {}
    risks: list[str] = []
    aliases: list[str] = []

    # ── 模块级：引导块 / logger / 路径常量（**不属于任何闭包**）──────────
    #: 本文件**模块级**已 import 的名字（块内可见 ⇒ 不是缺失依赖）
    module_imports: set[str] = set()
    for n in tree.body:
        if isinstance(n, ast.Import):
            for a in n.names:
                module_imports.add(a.asname or a.name.split(".")[0])
        elif isinstance(n, ast.ImportFrom):
            for a in n.names:
                module_imports.add(a.asname or a.name)
        elif isinstance(n, ast.Assign):
            for t in n.targets:
                if not isinstance(t, ast.Name):
                    continue
                if t.id in ("_HERE", "_ROOT", "_REPO_ROOT", "_SRC_ROOT",
                            "_SCRAPERS_ROOT", "_PROJECT_ROOT", "LOG", "LOGGER", "_LOG"):
                    risks.append(f"L{n.lineno} `{t.id}`：模块级引导/logger —— "
                                 f"**不属于任何函数闭包**，AST 依赖捕获不到；"
                                 f"迁移时必须显式搬运或在每层各自定义")
                elif _looks_like_path(t.id):
                    risks.append(f"L{n.lineno} `{t.id}`：路径派生常量 —— 由其它常量/"
                                 f"引导块计算，迁移时**依赖会连锁缺失**")
        elif isinstance(n, ast.Try):
            for sub in ast.walk(n):
                if isinstance(sub, ast.ImportFrom):
                    for a in sub.names:
                        aliases.append(
                            f"L{sub.lineno} `{a.asname or a.name}` ← try-import "
                            f"{sub.module}：**AST 依赖闭包捕获不到**"
                            f"（赋值在 try 内，E2 的 `_unified_doc_number` 即此类）")
                elif isinstance(sub, ast.Import):
                    for a in sub.names:
                        aliases.append(f"L{sub.lineno} try-import `{a.name}`"
                                       + (f" as {a.asname}" if a.asname else ""))

    # ── 逐个顶层定义 ───────────────────────────────────────────────────
    for n in tree.body:
        if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        used_mod: set[str] = set()
        used_sym: set[str] = set()
        loc: set[str] = {n.name}
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            a = n.args
            loc |= {x.arg for x in
                    list(a.posonlyargs) + list(a.args) + list(a.kwonlyargs)}
            if a.vararg:
                loc.add(a.vararg.arg)
            if a.kwarg:
                loc.add(a.kwarg.arg)
        for x in ast.walk(n):
            if isinstance(x, ast.Name):
                (used_sym if isinstance(x.ctx, ast.Load) else loc).add(x.id)
            elif isinstance(x, ast.Attribute) and isinstance(x.value, ast.Name):
                used_mod.add(x.value.id)
            elif isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                loc.add(x.name)
            elif isinstance(x, ast.ExceptHandler) and x.name:
                loc.add(x.name)
            elif isinstance(x, ast.arg):
                loc.add(x.arg)
        deps[n.name] = BlockDep(name=n.name,
                                kind=("function" if isinstance(
                                    n, (ast.FunctionDef, ast.AsyncFunctionDef))
                                    else "class"),
                                imports=used_mod, symbols=used_sym, locals_=loc,
                                top_names=all_top, module_imports=module_imports)
        for c in const_defs:
            if c in used_sym:
                const_users.setdefault(c, []).append(n.name)

    # ── 常量自身的外部依赖 ──────────────────────────────────────────────
    for n in tree.body:
        if not isinstance(n, (ast.Assign, ast.AnnAssign)):
            continue
        tgts = n.targets if isinstance(n, ast.Assign) else [n.target]
        names = [t.id for t in tgts if isinstance(t, ast.Name) and t.id.isupper()]
        if not names:
            continue
        used_mod: set[str] = set()
        used_sym: set[str] = set()
        for x in ast.walk(n.value) if n.value is not None else []:
            if isinstance(x, ast.Name):
                used_sym.add(x.id)
            elif isinstance(x, ast.Attribute) and isinstance(x.value, ast.Name):
                used_mod.add(x.value.id)
        for nm in names:
            deps[nm] = BlockDep(name=nm, kind="const", imports=used_mod,
                                symbols=used_sym, locals_={nm}, top_names=all_top, module_imports=module_imports)

    return deps, const_users, risks, aliases


def survey(path: str) -> Precheck:
    tree = ast.parse(open(path, encoding="utf-8").read())
    deps, users, risks, aliases = _collect(tree)
    return Precheck(file=path, deps=deps, module_level_risks=risks,
                    try_import_aliases=aliases, const_users=users)


def render(pc: Precheck, move: list[str]) -> str:
    L: list[str] = []
    L.append(f"[split-precheck] {os.path.basename(pc.file)}")
    L.append(f"  顶层块 {len(pc.deps)} 个；待迁 {len(move)} 个")

    if pc.module_level_risks:
        L.append("\n  ⚠ 模块级风险（**AST 闭包捕获不到** ⇒ 运行期才炸）：")
        L.extend(f"    · {r}" for r in pc.module_level_risks)
    if pc.try_import_aliases:
        L.append("\n  ⚠ try-import 别名（**AST 闭包捕获不到**）：")
        L.extend(f"    · {r}" for r in pc.try_import_aliases)

    L.append("\n  待迁块依赖清单：")
    n_bad = 0
    for m in move:
        d = pc.deps.get(m)
        if d is None:
            L.append(f"    !! {m}：**不在顶层定义里**（是否嵌套/是变量）")
            n_bad += 1
            continue
        mi, mr, mt = sorted(d.missing_imports), sorted(d.missing_repo), \
            sorted(d.missing_third)
        syms = sorted((d.symbols & set(k for k in pc.deps if k != m)) - d.locals_)
        parts = []
        if mi:
            parts.append(f"缺 stdlib import {mi}")
        if mr:
            parts.append(f"缺仓内 import {mr}")
        if mt:
            parts.append(f"缺三方 import {mt}")
        L.append(f"    {'OK ' if not parts else '!! '}{m}（{d.kind}）"
                 + (f"  {'; '.join(parts)}" if parts else ""))
        if syms:
            L.append(f"         依赖模块级符号: {syms[:10]}")
        if parts:
            n_bad += 1

    multi = {c: u for c, u in pc.const_users.items() if len(set(u)) > 1}
    if multi:
        L.append("\n  共享常量（跨层共用 ⇒ 需公共上层或显式复制；"
                 "**各复制一份必漏改其一** ⇒ 不报错只抓错）：")
        for c, u in sorted(multi.items())[:14]:
            L.append(f"    · {c} ← {sorted(set(u))[:6]}")

    L.append(f"\n  结论：{'OK（无缺失导入）' if not n_bad else f'需处理 {n_bad} 处'}"
             + (f"；模块级风险 {len(pc.module_level_risks)} 项"
                f" + try-import {len(pc.try_import_aliases)} 项须人工确认"
                if pc.module_level_risks or pc.try_import_aliases else ""))
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="拆分前置检查（只读，不改任何文件）")
    ap.add_argument("file", help="待拆分的 .py")
    ap.add_argument("--move", default="", help="待迁移的顶层定义名（逗号分隔）")
    ap.add_argument("--survey", action="store_true", help="只出全量依赖矩阵")
    a = ap.parse_args(argv)

    if not os.path.exists(a.file):
        print(f"[split-precheck] 文件不存在：{a.file}", file=sys.stderr)
        return 1
    pc = survey(a.file)
    if a.survey:
        print(f"[split-precheck] {a.file}：{len(pc.deps)} 个顶层块")
        for nm, d in sorted(pc.deps.items()):
            mi = sorted(d.missing_imports)
            print(f"  {nm[:34]:34s} {d.kind:9s}" + (f" 缺 {mi}" if mi else " OK"))
        for title, items in (("模块级风险", pc.module_level_risks),
                             ("try-import 别名", pc.try_import_aliases)):
            if items:
                print(f"\n  ⚠ {title}：")
                for r in items:
                    print(f"    · {r}")
        return 0
    move = [x.strip() for x in a.move.split(",") if x.strip()]
    if not move:
        print("[split-precheck] 需 --move 或 --survey", file=sys.stderr)
        return 1
    print(render(pc, move))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
