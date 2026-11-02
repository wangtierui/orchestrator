# -*- coding: utf-8 -*-
"""tools/gen_project_tree.py — 生成「项目结构」页所需的目录树 JSON。

为什么在构建期生成而不是加接口：这是一份**静态结构快照**（不随运行变化），
放进 web/assets/ 由静态路由直接发出，既不给后端加路由，也不受认证策略影响。

产出口径（可复算）：
  · 只列**目录**与**关键文件**（配置/入口/文档），不列业务数据、不列缓存；
  · **顶层目录全部保留**，剩余预算在顶层之间均分（避免按字母序只展开前几个）；
  · 深度上限 3 层、节点上限 420，超出部分在 meta.truncated 标注。

用法：
    python -m tools.gen_project_tree            # 写 web/assets/project-tree.json
    python -m tools.gen_project_tree --check    # 只校验现有文件是否过期
"""
from __future__ import annotations

import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "web", "assets", "project-tree.json")

#: 不参与展示的目录（数据、缓存、虚拟环境、导出物）
SKIP_DIRS = {
    ".git", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".venv", "venv",
    "__pycache__", "node_modules", "dist", "build", ".idea", ".vscode",
    "data", "logs", "reports", "vendor",
}

#: 关键文件：配置 / 入口 / 文档 / 契约（其余文件不进图）
KEY_SUFFIX = (".toml", ".cfg", ".ini", ".yaml", ".yml", ".lock", ".json", ".md")
KEY_NAMES = {
    "cli.py", "bootstrap.py", "paths.py", "conftest.py", "requirements.txt",
    "pyproject.toml", "ruff.toml", "mypy.ini", "pytest.ini", "Makefile",
    "README.md", "CODEBUDDY.md",
}

MAX_DEPTH = 3
MAX_NODES = 420


def _is_key_file(name: str) -> bool:
    return name in KEY_NAMES or name.startswith("requirements") or name.endswith(KEY_SUFFIX)


def _list(path: str) -> tuple[list[str], list[str]]:
    try:
        entries = sorted(os.listdir(path))
    except OSError:
        return [], []
    dirs, files = [], []
    for e in entries:
        p = os.path.join(path, e)
        if os.path.isdir(p):
            if e in SKIP_DIRS:
                continue
            dirs.append(e)
        elif _is_key_file(e):
            files.append(e)
    return dirs, files


def walk(path: str, depth: int, budget: list[int]) -> dict:
    """递归展开；budget 是单元素列表（可变 int），保证同层共享余量。"""
    node = {"name": os.path.basename(path) or path}
    if depth >= MAX_DEPTH or budget[0] <= 0:
        return node

    dirs, files = _list(path)
    children: list[dict] = []

    if dirs:
        share = max(1, budget[0] // len(dirs))
        for d in dirs:
            if budget[0] <= 0:
                break
            grant = min(share, budget[0])
            sub_budget = [grant]
            child = walk(os.path.join(path, d), depth + 1, sub_budget)
            used = grant - sub_budget[0]
            budget[0] -= used + 1
            children.append(child)

    for f in files:
        if budget[0] <= 0:
            break
        budget[0] -= 1
        children.append({"name": f})

    if children:
        node["children"] = children
    if depth > 0:
        node["value"] = len(children) or 1
    return node


def build() -> dict:
    budget = [MAX_NODES]
    tree = walk(ROOT, 0, budget)
    return {
        "meta": {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "root": os.path.basename(ROOT),
            "max_depth": MAX_DEPTH,
            "max_nodes": MAX_NODES,
            "truncated": budget[0] <= 0,
            "node_count": _count(tree),
            "note": "自动生成：仅目录 + 关键文件（配置/入口/文档/契约），不含数据与缓存",
        },
        "tree": tree,
    }


def _count(node: dict) -> int:
    return 1 + sum(_count(c) for c in node.get("children", []))


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    data = build()
    text = json.dumps(data, ensure_ascii=False, indent=1) + "\n"
    if "--check" in args:
        if not os.path.exists(OUT):
            print(f"[tree] 缺少 {OUT}，请运行 python -m tools.gen_project_tree")
            return 1
        cur = json.loads(open(OUT, encoding="utf-8").read())
        # 只比**结构**：`generated_at` 每次都变，比它等于永远"已过期"
        meta_a = dict(cur.get("meta") or {})
        meta_b = dict(data["meta"])
        meta_a.pop("generated_at", None)
        meta_b.pop("generated_at", None)
        same = cur.get("tree") == data["tree"] and meta_a == meta_b
        print(f"[tree] {'一致' if same else '已过期（请重新生成）'}")
        return 0 if same else 1

    with open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    print(f"[tree] 已写入 {OUT}（{_count(data['tree'])} 个节点，"
          f"截断={data['meta']['truncated']}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
