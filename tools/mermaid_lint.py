# -*- coding: utf-8 -*-
"""tools.mermaid_lint — README 里 Mermaid 图的**保守方言**静态检查。

背景（2026-10-22，第一百零七批）
--------------------------------
用户报告 README 的「实体关系 / 数据流 / 链路图」**全部**显示
"Unable to render rich display"（GitHub 的 Mermaid 渲染失败提示）。
本机**没有 node/npm**（跑不了 mermaid-cli），mermaid.ink 的在线渲染接口
在网络策略下也不可达 ⇒ **无法在此环境用真实解析器验证**。

因此采取**可控性最高**的做法：
  1. 把 4 张图统一收敛到一个**保守方言**（只用 Mermaid 8.x–11.x 全版本都稳定
     支持的构造），并**移出**所有历史上引发解析歧义的写法；
  2. 用本工具做**静态**机器校验（离线、可进 CI/门禁），防止再次漂移。

保守方言（本工具的判据）
------------------------
| # | 禁用 | 为什么 |
|---|---|---|
| R1 | `<br/>` 等 HTML 标签 | 标签内 HTML 需特定版本支持；换行改用 ` · ` |
| R2 | 双引号 `"` | 引号形态（`A["x"]`、`\\|"x"\\|`）在 8.x 上不支持，9.4+ 才稳；一律裸文本 |
| R3 | `**粗体**` 等 Markdown | Mermaid 不支持 Markdown 语法，会被当字面量甚至打断解析 |
| R4 | `;` 作语句分隔 | 一行多语句在部分版本解析异常；一行一句 |
| R5 | `&` 多节点串联 | `A & B --> C` 属 9.x 扩展；改为逐条边 |
| R6 | `classDef` 值含空格 | 如 `stroke-dasharray:4 2` 会被当两个属性 |
| R7 | 标签内 `()` `[]` `{}` `\\|` `<` `>` `#` | 与图元/样式记号冲突；改用全角括号或 ` · ` |
| R8 | 未闭合/多余的 `end`、`subgraph` | 结构必须配平 |
| R9 | 非 `[...]` 的节点外形（`([…])`/`[[…]]`/`[(…)]`） | 外形语法各版本支持面不同；统一矩形 |
| R10 | erDiagram 关系标签加引号 | er 的引号标签支持面最窄；用裸 `词` |
| R11 | 图块围栏未闭合 / 语言名不是裸 `mermaid` | 围栏坏了会退化成代码块 |

用法
----
    python -m tools.mermaid_lint            # 检查 README（默认）
    python -m tools.mermaid_lint --file x.md --verbose

退出码：0=通过；1=有违规。
"""
from __future__ import annotations

import argparse
import os
import re

from bootstrap import bootstrap

bootstrap("all")

import paths

DEFAULT_FILES = ("README.md",)

#: 允许出现在标签里的字符之外，这些**一定**要报（R7）
_BAD_LABEL_CHARS = set('()[]{}|<>#')
#: 标签内出现双引号即违规（R2）
_MD_BOLD = re.compile(r"\*\*|__")
_HTML_TAG = re.compile(r"<\s*/?\s*[A-Za-z][^>]*>")
_STYLE_SPACE = re.compile(r"classDef\s+\S+\s+.*:\s*\S+\s+\S+")
_NODE_SHAPES = (
    (re.compile(r"\(\["), "圆柱形 ([…])"),      # ([ ])
    (re.compile(r"\[\["), "子程序 [[…]]"),
    (re.compile(r"\{\{"), "六边形 {{…}}"),
)


def _strip_label(text: str) -> str:
    """从 `A[标签]` / `A -->|标签| B` 中抠出标签文本（宽松：取方括号/竖线内内容）。"""
    out = []
    for m in re.finditer(r"\[([^\]]*)\]", text):
        out.append(m.group(1))
    for m in re.finditer(r"\|([^|]*)\|", text):
        out.append(m.group(1))
    return " ".join(out)


def lint_block(lines: list[str], first_line_no: int) -> list[str]:
    """检查**一个** mermaid 块的图体（不含围栏）。返回问题列表。"""
    probs: list[str] = []
    heads = [ln for ln in lines if ln.strip()]
    if not heads:
        return [f"L{first_line_no}: 空图块"]
    header = heads[0].strip().split()[0]
    if header not in ("flowchart", "graph", "erDiagram", "sequenceDiagram",
                      "classDiagram", "stateDiagram", "stateDiagram-v2"):
        probs.append(f"L{first_line_no}: 未知图类型 `{header}`")

    depth = 0
    for off, raw in enumerate(lines):
        ln = raw.rstrip("\r")
        no = first_line_no + off
        s = ln.strip()
        if not s:
            continue
        # R1 HTML
        if _HTML_TAG.search(s):
            probs.append(f"L{no}: R1 图内含 HTML 标签（改用 ` · `）")
        # R2 双引号
        if '"' in s:
            probs.append(f"L{no}: R2 图内含双引号（改用裸文本）")
        # R3 Markdown 粗体
        if _MD_BOLD.search(s):
            probs.append(f"L{no}: R3 图内含 Markdown 粗体")
        # R4 分号多语句
        if s.count(";") >= 1:
            probs.append(f"L{no}: R4 图内含 `;` 语句分隔")
        # R5 & 串联
        if re.search(r"\w\s*&\s*\w", s):
            probs.append(f"L{no}: R5 图内含 `&` 多节点串联")
        # R6 classDef 值含空格
        if s.startswith("classDef") and _STYLE_SPACE.search(s):
            probs.append(f"L{no}: R6 classDef 属性值含空格（如 dasharray:4 2）")
        # R9 非矩形外形
        for rx, why in _NODE_SHAPES:
            if rx.search(s):
                probs.append(f"L{no}: R9 使用了{why}（统一用 `[…]`）")
        # R7 标签内禁用字符
        lab = _strip_label(s)
        if lab:
            bad = sorted({c for c in lab if c in _BAD_LABEL_CHARS})
            if bad:
                probs.append(f"L{no}: R7 标签含禁用字符 {bad}")
        # R8 subgraph 配平
        if s.startswith("subgraph"):
            depth += 1
        elif s == "end":
            depth -= 1
            if depth < 0:
                probs.append(f"L{no}: R8 多余的 `end`")
                depth = 0
    if depth != 0:
        probs.append(f"L{first_line_no}: R8 subgraph 未配平（缺 {depth} 个 `end`）")
    return probs


def lint_text(text: str, name: str = "<text>") -> list[str]:
    """检查一段 Markdown 里所有 mermaid 块。"""
    lines = text.replace("\r\n", "\n").split("\n")
    probs: list[str] = []
    i = 0
    n_blocks = 0
    while i < len(lines):
        s = lines[i].strip()
        if s.startswith("```mermaid"):
            info = s[len("```"):]
            if info != "mermaid":
                probs.append(f"L{i+1}: R11 围栏语言名应为裸 `mermaid`，实际 `{info}`")
            j = i + 1
            body: list[str] = []
            while j < len(lines) and lines[j].strip() != "```":
                body.append(lines[j])
                j += 1
            if j >= len(lines):
                probs.append(f"L{i+1}: R11 图块围栏未闭合")
                break
            n_blocks += 1
            probs.extend(lint_block(body, i + 2))
            i = j + 1
            continue
        i += 1
    if n_blocks == 0:
        probs.append(f"{name}: 未找到任何 ```mermaid 块（检查围栏写法）")
    return probs


def lint_file(path: str) -> list[str]:
    text = open(path, encoding="utf-8-sig", errors="replace").read()
    return lint_text(text, os.path.basename(path))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="mermaid_lint",
        description="README 的 Mermaid 图保守方言检查（离线，可进 CI）")
    ap.add_argument("--file", action="append", default=None,
                    help="待检文件（默认 README.md；可多次给）")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args(argv)
    files = a.file or [os.path.join(paths.ROOT, f) for f in DEFAULT_FILES]
    total = 0
    for f in files:
        if not os.path.exists(f):
            print(f"[mermaid_lint] 跳过（不存在）：{f}")
            continue
        probs = lint_file(f)
        rel = os.path.relpath(f, paths.ROOT)
        if probs:
            total += len(probs)
            print(f"[mermaid_lint] **{rel} 有 {len(probs)} 处违规**：")
            for p in probs:
                print("   ", p)
        else:
            print(f"[mermaid_lint] {rel} 通过 ✓")
    return 1 if total else 0


if __name__ == "__main__":
    raise SystemExit(main())
