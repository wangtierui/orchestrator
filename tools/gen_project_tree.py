# -*- coding: utf-8 -*-
"""tools/gen_project_tree.py — 生成「项目结构」页所需的目录树 JSON。

为什么在构建期生成而不是加接口：这是一份**静态结构快照**（不随运行变化），
放进 web/assets/ 由静态路由直接发出，既不给后端加路由，也不受认证策略影响。

产出口径（可复算）：
  · 只列**目录**与**关键文件**（配置/入口/文档），不列业务数据、不列缓存；
  · **顶层目录全部保留**，剩余预算在顶层之间均分（避免按字母序只展开前几个）；
  · 每个文件带 size / mtime / lines / purpose（用途从 docstring、md 标题、注释里取）；
  · 深度上限 3 层、节点上限 420，超出部分在 meta.truncated 标注。

用法：
    python -m tools.gen_project_tree            # 写 web/assets/project-tree.json
    python -m tools.gen_project_tree --check    # 只校验结构是否过期（忽略时间戳）
"""
from __future__ import annotations

import json
import os
import re
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
DOC_RE = re.compile('"""(.*?)"""', re.S)
MAX_NODES = 420
PURPOSE_MAX = 120

_DQ3 = '"' * 3
_SQ3 = "'" * 3


def _is_key_file(name: str) -> bool:
    return name in KEY_NAMES or name.startswith("requirements") or name.endswith(KEY_SUFFIX)


def _purpose(path: str, name: str) -> str:
    """从文件自身取一句「用途」：py docstring 首行 / md 标题 / 配置首注释 / json note。"""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            head = fh.read(4000)
    except OSError:
        return ""
    lines = head.splitlines()

    if name.endswith(".py"):
        stripped = head.lstrip()
        if stripped.startswith(_DQ3) or stripped.startswith(_SQ3):
            quote = stripped[:3]
            body = stripped[3:].split(quote, 1)[0].strip()
            if body:
                return body.splitlines()[0][:PURPOSE_MAX]
        return ""

    if name.endswith(".md"):
        for ln in lines:
            if ln.startswith("#"):
                return ln.lstrip("# ").strip()[:PURPOSE_MAX]
        return ""

    if name.endswith((".toml", ".cfg", ".ini", ".yaml", ".yml")):
        for ln in lines:
            s = ln.strip()
            if s.startswith("#") and len(s.strip("# ")) > 3:
                return s.strip("# ").strip()[:PURPOSE_MAX]
        return ""

    if name.endswith(".json"):
        m = re.search(r'"note"\s*:\s*"([^"]{4,})"', head)
        if m:
            return m.group(1)[:PURPOSE_MAX]
    return ""


def _file_node(path: str, name: str) -> dict:
    node: dict = {"name": name, "type": "file",
                  "path": os.path.relpath(path, ROOT).replace("\\", "/")}
    try:
        st = os.stat(path)
    except OSError:
        return node
    node["size"] = int(st.st_size)
    node["mtime"] = time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime))
    purpose = _purpose(path, name)
    if purpose:
        node["purpose"] = purpose
    try:
        with open(path, "rb") as fh:
            blob = fh.read(2 << 20)
        if b"\0" not in blob:
            node["lines"] = blob.count(b"\n") + (0 if blob.endswith(b"\n") else 1)
    except OSError:
        pass
    return node


def _dir_purpose(path: str) -> str:
    """目录用途：优先 README 第一行标题，其次 __init__.py 的模块 docstring 首行。"""
    readme = os.path.join(path, "README.md")
    if os.path.exists(readme):
        try:
            for line in open(readme, encoding="utf-8", errors="replace").read().splitlines():
                s = line.strip().lstrip("#").strip()
                # 跳过 HTML 片段 / 纯链接行（README 里常见，不是"用途"）
                if not s or s.startswith("<") or s.startswith("[!") or s.startswith("!["):
                    continue
                s = re.sub(r"<[^>]+>", "", s).replace("**", "").strip()
                if s:
                    return s[:80]
        except OSError:
            pass
    init = os.path.join(path, "__init__.py")
    if os.path.exists(init):
        try:
            text = open(init, encoding="utf-8", errors="replace").read()
            m = DOC_RE.search(text)
            if m:
                for line in m.group(1).strip().splitlines():
                    s = line.strip()
                    if s:
                        return s[:80]
        except OSError:
            pass
    return ""


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
    node: dict = {"name": os.path.basename(path) or path, "type": "dir",
                  "path": os.path.relpath(path, ROOT).replace("\\", "/")}
    purpose = _dir_purpose(path)
    if purpose:
        node["purpose"] = purpose
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
        children.append(_file_node(os.path.join(path, f), f))

    if children:
        node["children"] = children
    if depth > 0:
        node["value"] = len(children) or 1
    return node


VOLATILE = ("size", "mtime", "lines", "purpose", "value")


def _shape(node: dict) -> dict:
    """去掉易变字段后的结构指纹（用于 --check）。"""
    out = {k: v for k, v in node.items() if k not in VOLATILE}
    kids = node.get("children")
    if kids:
        out["children"] = [_shape(c) for c in kids]
    return out


def _count(node: dict) -> int:
    return 1 + sum(_count(c) for c in node.get("children", []))


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


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    data = build()
    text = json.dumps(data, ensure_ascii=False, indent=1) + "\n"

    if "--check" in args:
        if not os.path.exists(OUT):
            print(f"[tree] 缺少 {OUT}，请运行 python -m tools.gen_project_tree")
            return 1
        cur = json.loads(open(OUT, encoding="utf-8").read())
        # 只比**结构**：generated_at / size / mtime / lines / purpose 随时会变，
        # 把它们算进去等于"永远过期"；这里关心的是"目录与文件有没有增减"。
        same = _shape(cur.get("tree")) == _shape(data["tree"])
        print(f"[tree] {'一致' if same else '已过期（请重新生成）'}")
        return 0 if same else 1

    with open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    print(f"[tree] 已写入 {OUT}（{_count(data['tree'])} 个节点，"
          f"截断={data['meta']['truncated']}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
