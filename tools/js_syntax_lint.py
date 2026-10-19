# -*- coding: utf-8 -*-
"""tools.js_syntax_lint — 内联 JS 的**语法**体检（真解析器优先，词法兜底）。

为什么需要它
------------
`web/assets/index.html` 的内联 `<script>` 一旦有语法错误，**整个脚本不执行**
⇒ 页面停在 HTML 初始态（`加载中…` + `SSE 连接中…`），而**服务端一切正常**
（HTTP 200、API 全绿、SSE 有 hello/ping）⇒ 极易被误判成"后端 / SSE 故障"。
本仓无 Node，故需要 Python 侧的检查手段。

两种后端（**优先真解析器**）
----------------------------
1. **esprima**（纯 Python 的 ECMAScript 解析器，`requirements` 已登记）——
   真语法树解析，能给出准确的行/列与错误消息。**这是首选**。
2. **词法兜底**（esprima 不可用时）：只查括号配对 / 字符串与模板闭合。

⚠️ 词法兜底**无法正确处理嵌套模板字符串**
（`` `...${cond?`...${x}...`:...}...` `` 这类三层嵌套在本仓真实存在，如
`viewPublish`）⇒ 单 `mode` 变量的状态机必然误报。这正是"必须用真解析器"的
实证理由：曾用它误报 3 个历史版本全 FAIL。

用法
----
    python -m tools.js_syntax_lint web/assets/index.html
    python -m tools.js_syntax_lint --json web/assets/index.html

退出码：0=未发现问题；1=发现问题（供 CI/门禁消费）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from bootstrap import bootstrap

bootstrap("all")

from config.exitcodes import ExitCode


def extract_script(html: str) -> list[tuple[int, str]]:
    """取出所有**非 src 的**内联 `<script>` 块，返回 `[(起始行号, 内容)]`。"""
    out: list[tuple[int, str]] = []
    lower = html.lower()
    pos = 0
    while True:
        i = lower.find("<script", pos)
        if i < 0:
            break
        j = lower.find(">", i)
        if j < 0:
            break
        head = lower[i:j]
        body_start = j + 1
        k = lower.find("</script>", body_start)
        if k < 0:
            break
        if "src=" not in head:
            line = html.count("\n", 0, body_start) + 1
            out.append((line, html[body_start:k]))
        pos = k + len("</script>")
    return out


# --------------------------------------------------------------------------- #
# 后端 1：esprima（真解析器）
# --------------------------------------------------------------------------- #
#: ES2020 → ES2017 的**等长**降级表（esprima 4.0.1 只到 ES2017）。
#:
#: 为什么必须**等长**：替换后要保持每个字符的偏移不变，否则报出的行列号会错位，
#: 定位就失去意义（这是"宁可多写几行替换规则，也不让定位失真"的取舍）。
#:
#: 为什么这些替换是**语义等价或至少语法等价**（仅用于解析检查，不改真实代码）：
#:   `??`  → `||`   两者都是二元运算符、优先级相同 ⇒ 语法树形状一致
#:   `?.[` → ` [ `  `a?.[0]` 与 `a [ 0]` 都是成员访问（`a [0]` 合法）
#:   `?.(` → ` ( `  `a?.(x)` 与 `a ( x)` 都是调用
#:   `?.`  → `. `    `a?.b` 与 `a. b` 都是成员访问
_ES2020_DOWNGRADE = (
    ("?.[", " [ "),
    ("?.(", " ( "),
    ("??", "||"),
    ("?.", ". "),
)


def _downgrade_es2020(code: str) -> tuple[str, int]:
    """把 ES2020 语法降级为 ES2017 等价形式（**等长**，行列号不变）。

    返回 `(降级后代码, 替换次数)`。次数用于在报告里披露"做过降级"，
    避免读者误以为源码本身就是 ES2017。
    """
    n = 0
    out = code
    for src, dst in _ES2020_DOWNGRADE:
        assert len(src) == len(dst), f"降级表必须等长：{src!r} -> {dst!r}"
        c = out.count(src)
        if c:
            out = out.replace(src, dst)
            n += c
    return out, n


def _parse_with_esprima(code: str, base_line: int) -> list[dict] | None:
    """用 esprima 解析；返回 issues，或 `None` 表示 esprima 不可用。"""
    try:
        import esprima
    except ImportError:
        return None
    downgraded, n_sub = _downgrade_es2020(code)
    try:
        esprima.parseScript(downgraded, {"loc": True, "tolerant": False})
        return []
    except Exception as e:  # noqa: BLE001  esprima 的异常类型随版本变化
        # esprima 的错误对象带 lineNumber/column/description
        ln = getattr(e, "lineNumber", None) or getattr(e, "line", None) or 0
        col = getattr(e, "column", None) or 0
        desc = (getattr(e, "description", None) or str(e))
        return [{
            "kind": "syntax_error",
            "line": base_line + int(ln) - 1 if ln else base_line,
            "col": int(col),
            "detail": f"JS 语法错误：{desc}",
            "es2020_downgrades": n_sub,
        }]


# --------------------------------------------------------------------------- #
# 后端 2：词法兜底（仅括号/字符串/模板闭合；**不处理嵌套模板**）
# --------------------------------------------------------------------------- #
_REGEX_PREV_OK = set("(,=:[!&|?{};+-*%~^<>") | {"\n"}


def _scan_lexical(text: str, base_line: int) -> list[dict]:
    """词法兜底：括号配对 + 字符串/模板闭合。

    ⚠️ **不支持嵌套模板字符串**（见模块 docstring）；仅在 esprima 不可用时使用。
    """
    issues: list[dict] = []
    stack: list[tuple[str, int, int]] = []
    pairs = {")": "(", "]": "[", "}": "{"}
    i, n = 0, len(text)
    line, col = base_line, 0
    prev_sig = "\n"
    mode = "normal"

    while i < n:
        c = text[i]

        if mode == "template_text":
            if c == "\\":
                i += 2
                continue
            if c == "`":
                mode = "normal"
                prev_sig = "`"
                i += 1
                col += 1
                continue
            if c == "$" and i + 1 < n and text[i + 1] == "{":
                stack.append(("${", line, col))
                i += 2
                col += 2
                continue
            if c == "\n":
                line += 1
                col = 0
            else:
                col += 1
            i += 1
            continue

        if c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                if text[i] == "\n":
                    line += 1
                    col = 0
                i += 1
            i += 2
            continue

        if c in "\"'":
            quote = c
            sl, sc = line, col
            i += 1
            closed = False
            while i < n:
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == quote:
                    i += 1
                    closed = True
                    break
                if text[i] == "\n":
                    break
                i += 1
            if not closed:
                issues.append({"kind": "unterminated_string",
                               "line": sl, "col": sc,
                               "detail": f"字符串未闭合（起始 {quote!r}）"})
            prev_sig = quote
            continue

        if c == "`":
            mode = "template_text"
            prev_sig = "`"
            i += 1
            col += 1
            continue

        if c == "/" and (prev_sig in _REGEX_PREV_OK or prev_sig == ""):
            j = i + 1
            in_class = False
            ok = False
            while j < n:
                ch = text[j]
                if ch == "\\":
                    j += 2
                    continue
                if ch == "[":
                    in_class = True
                elif ch == "]":
                    in_class = False
                elif ch == "/" and not in_class:
                    ok = True
                    j += 1
                    break
                elif ch == "\n":
                    break
                j += 1
            if ok:
                i = j
                prev_sig = "/"
                continue

        if c in "([{":
            stack.append((c, line, col))
            prev_sig = c
            i += 1
            col += 1
            continue
        if c in ")]}":
            want = pairs[c]
            if stack and stack[-1][0] == "${" and c == "}":
                stack.pop()
                mode = "template_text"
                prev_sig = "}"
                i += 1
                col += 1
                continue
            if not stack:
                issues.append({"kind": "unmatched_close", "line": line, "col": col,
                               "detail": f"多余的闭合括号 {c!r}"})
                i += 1
                col += 1
                continue
            top, tl, tc = stack.pop()
            if top != want:
                issues.append({"kind": "mismatched_bracket", "line": line, "col": col,
                               "detail": f"括号不配对：{c!r} vs 第 {tl} 行 {top!r}"})
            prev_sig = c
            i += 1
            col += 1
            continue

        if c == "\n":
            line += 1
            col = 0
        else:
            col += 1
            if not c.isspace():
                prev_sig = c
        i += 1

    if mode == "template_text":
        issues.append({"kind": "unterminated_template", "line": line, "col": col,
                       "detail": "模板字符串未闭合（`）"})
    for br, bl, bc in stack:
        issues.append({"kind": "unclosed_bracket", "line": bl, "col": bc,
                       "detail": f"括号 {br!r} 未闭合"})
    return issues


def _esprima_available() -> bool:
    try:
        import esprima  # noqa: F401
    except ImportError:
        return False
    return True


def lint_file(path: str) -> dict:
    """体检一个 HTML 文件的全部内联脚本。"""
    html = open(path, encoding="utf-8", errors="replace").read()
    blocks = extract_script(html)
    backend = "esprima" if _esprima_available() else "lexical"
    all_issues: list[dict] = []
    for start, body in blocks:
        got = _parse_with_esprima(body, start) if backend == "esprima" else None
        if got is None:                      # esprima 不可用 ⇒ 兜底
            backend = "lexical"
            got = _scan_lexical(body, start)
        for it in got:
            it["script_start_line"] = start
            all_issues.append(it)
    all_issues.sort(key=lambda x: (x.get("line", 0), x.get("col", 0)))
    return {"file": path, "scripts": len(blocks), "backend": backend,
            "lines": len(html.splitlines()),
            "issues": all_issues, "ok": not all_issues}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="内联 JS 语法体检（esprima 优先）")
    ap.add_argument("path", nargs="?", default="web/assets/index.html")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if not os.path.exists(a.path):
        print(f"[js-lint] 文件不存在：{a.path}", file=sys.stderr)
        return ExitCode.USAGE
    r = lint_file(a.path)
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        print(f"[js-lint] {a.path}：{r['scripts']} 个内联脚本 / {r['lines']} 行"
              f" / 后端={r['backend']}")
        if r["ok"]:
            print("  OK 未发现语法错误")
        else:
            for it in r["issues"]:
                print(f"  [{it['kind']}] L{it['line']}:C{it['col']}  {it['detail']}")
        print(f"  结论：{'OK' if r['ok'] else 'FAIL'}")
    return ExitCode.OK if r["ok"] else ExitCode.FAIL


if __name__ == "__main__":
    raise SystemExit(main())
