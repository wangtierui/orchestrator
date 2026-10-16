"""J14. 同名模块的**裸名导入**禁令 —— 防"导入到错的模块"这类静默失效。

问题（2026-10-16 E3 拆分时暴露）：仓内有 **32 组同名 .py**
（`gov/extract.py` vs `internal_policy_base/extract.py`、`pbc/constants.py` vs
`config/constants.py` …）。裸名 `from extract import X` 解析到哪一份，取决于
**谁先被导入** —— 而采集器层为支持"可独立脚本直调"会在脚本形态下走裸名 import，
把顶层模块名注册进 `sys.modules`。于是全套件里的导入结果**依赖测试执行顺序**：
同一天可能全绿，换个顺序就 ImportError（本次即如此）。

判据：
  J14.1 测试文件中**不得**出现 `from <同名模块> import` / `import <同名模块>`
         （同名 = 该模块名在仓内 >1 处出现）；必须显式按路径加载
         （`importlib.util.spec_from_file_location`，与 test_resource_hygiene 同款）。
  J14.2 例外白名单：仓根的 `bootstrap.py`、`paths.py` 等**唯一**模块不在此列
         （同名判定已排除唯一项）；`refactor/` 属其他用户目录，不在扫描范围。
"""
from __future__ import annotations

import ast
import os
from collections import defaultdict

TESTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "tests")
SKIP_DIRS = {".git", "__pycache__", "data", "external", "graphify-out", "node_modules",
             "reports", "backups", "archive", "cache", "logs", "retired", ".ruff_cache",
             "refactor"}


def _homonym_names(root: str) -> set[str]:
    """仓内出现 >1 次的模块名（不含 `__init__`）。"""
    by_name: dict[str, int] = defaultdict(int)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if fn.endswith(".py") and fn != "__init__.py":
                by_name[fn[:-3]] += 1
    return {n for n, c in by_name.items() if c > 1}


def _check_homonym_imports(root: str) -> tuple[list[str], dict]:
    problems: list[str] = []
    detail: dict = {}
    tests_dir = os.path.join(root, "tests")
    if not os.path.isdir(tests_dir):
        return problems, detail
    dupes = _homonym_names(root)
    detail["homonym_module_count"] = len(dupes)
    hits: list[str] = []
    for dirpath, dirnames, filenames in os.walk(tests_dir):
        dirnames[:] = [d for d in dirnames if d not in {"__pycache__"}]
        for fn in sorted(filenames):
            if not fn.endswith(".py"):
                continue
            fp = os.path.join(dirpath, fn)
            rel = os.path.relpath(fp, root).replace("\\", "/")
            try:
                tree = ast.parse(open(fp, encoding="utf-8", errors="replace").read())
            except SyntaxError as e:
                problems.append(f"J14: {rel} 语法错误，无法扫描（{e}）")
                continue
            for node in ast.walk(tree):
                mods: list[str] = []
                if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                    mods = [node.module.split(".")[0]]
                elif isinstance(node, ast.Import):
                    mods = [a.name.split(".")[0] for a in node.names if "." not in a.name]
                for m in mods:
                    if m in dupes:
                        # `ast.Import` 无 lineno（仅 ImportFrom 有）⇒ getattr兜底
                        ln = getattr(node, "lineno", 0)
                        hits.append(f"{rel}:{ln}: {m}")
    detail["bare_import_hits"] = len(hits)
    if hits:
        problems.append(
            "J14.1: 测试中裸名导入同名模块（解析结果依赖执行顺序）："
            f"{hits[:12]}"
            "　⇒ 改用 importlib 按路径显式加载（见 tests/test_resource_hygiene.py::_load）")
    return problems, detail
