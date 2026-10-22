# -*- coding: utf-8 -*-
"""tests/test_readme_render.py — README **可渲染性**守卫（第一百零七批）。

用户报的两个渲染问题
--------------------
① 实体关系 / 数据流 / 链路图 **全部**显示 "Unable to render rich display"；
② §5.2 内的列表未正常显示。

本机无 node（跑不了 mermaid-cli）、mermaid.ink 也不可达 ⇒ 无法用真实解析器定位，
因此把 4 张图收敛到**跨版本稳定的保守方言**（见 `tools/mermaid_lint.py`），
并用本文件把两类问题都变成**机器可查**的断言。

② 的确切根因（实测）
-------------------
`<!-- SCHED:AUTO -->` 标记落在**表头与表体之间**。GFM 表格必须是**连续块**——
任何非表格行（含 HTML 注释）都会终止表格 ⇒ 表体那些 `| … |` 退化成普通段落，
页面上显示成一堆竖线。
另外表头写的是 **4 列**（`频率/cron/命令/说明`）而生成行是 **5 列**
（`频率/cron/角色/命令/说明`）⇒ 即使不断开也会错列。

修法：标记移到表头**之前**；表头随表体一起由 `gen_schedule_doc` 生成
（顺带消灭"手工维护的表头副本"这一处漂移源）。

守卫四组
--------
A Mermaid：`tools.mermaid_lint` 对 README 必须 0 违规（保守方言）
B 表格连续：任何表格的表头之后，直到表格结束，**不得**出现非表格行
C 列数一致：同一张表每行的单元格数必须相同（含表头）
D 标记位置：`<!-- SCHED:AUTO -->` 必须在定时表**表头之前**
"""
from __future__ import annotations

import os
import re

import pytest

pytestmark = pytest.mark.contract

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
README = os.path.join(ROOT, "README.md")


def _lines() -> list[str]:
    return open(README, encoding="utf-8-sig").read().replace("\r\n", "\n").split("\n")


def _cells(line: str) -> int:
    """统计表格行的单元格数（**只按未转义的 `|` 切分**）。

    ⚠️ 必须尊重 `\\|` 转义：单元格内容里出现竖线（如本文件描述的 `` `\\| … \\|` ``）
    时，Markdown 要求转义；若切分时不排除转义竖线，正确写法会被误判成"多一列"
    （实测踩到：§6.7 说明"表格内竖线要转义"的**那一行**自己被判列数不一致）。
    """
    s = line.strip()
    if not s.startswith("|"):
        return 0
    inner = s[1:-1] if s.endswith("|") and len(s) > 1 else s[1:]
    return len(re.split(r"(?<!\\)\|", inner))


def _tables(lines: list[str]):
    """产出 (header_idx, [row_idx...])；以「表头 + 分隔行」识别表格起点。"""
    i = 0
    while i < len(lines) - 1:
        a, b = lines[i].strip(), lines[i + 1].strip()
        if a.startswith("|") and b.startswith("|") and set(b) <= set("|:- "):
            rows = []
            j = i + 2
            while j < len(lines) and lines[j].strip().startswith("|"):
                rows.append(j)
                j += 1
            yield i, rows
            i = j
        else:
            i += 1


class TestMermaidDialect:
    """A. 图必须是保守方言（0 违规）。"""

    def test_mermaid_lint_clean(self) -> None:
        from tools import mermaid_lint as ML

        probs = ML.lint_file(README)
        assert not probs, "Mermaid 保守方言违规：\n" + "\n".join(probs[:20])

    def test_all_four_blocks_present(self) -> None:
        """4 张图（ER / 业务数据流 / 架构共识 / 全链总图）都要在。"""
        lines = _lines()
        n = sum(1 for x in lines if x.strip().startswith("```mermaid"))
        assert n >= 4, f"仅 {n} 个 mermaid 块（应为 4）"


class TestTableContiguity:
    """B. 表格必须是连续块（HTML 注释等非表格行会终止表格）。"""

    def test_no_break_inside_table(self) -> None:
        lines = _lines()
        bad = []
        for header, rows in _tables(lines):
            if not rows:
                continue
            last = rows[-1]
            # 表格结束后的下一行不得再出现表格行（说明中间被"打断"过）
            nxt = last + 1
            while nxt < len(lines) and not lines[nxt].strip():
                nxt += 1
            if nxt < len(lines) and lines[nxt].strip().startswith("|"):
                bad.append((header + 1, nxt + 1))
        assert not bad, (
            "以下表格被非表格行打断（表头行, 断点后仍有表格行）："
            f"{bad[:5]} —— GFM 要求表格连续")

    def test_no_comment_between_header_and_rows(self) -> None:
        lines = _lines()
        for header, rows in _tables(lines):
            if not rows:
                continue
            for k in range(header + 2, rows[-1]):
                s = lines[k].strip()
                assert s.startswith("|"), (
                    f"L{header+1} 表头与 L{rows[-1]+1} 表体之间的 L{k+1} 不是表格行："
                    f"{s[:60]!r} ⇒ 会终止表格")


class TestTableColumns:
    """C. 同一张表每行列数一致（防"表头 4 列 / 表体 5 列"这类错列）。"""

    def test_column_count_consistent(self) -> None:
        lines = _lines()
        bad = []
        for header, rows in _tables(lines):
            n_head = _cells(lines[header])
            for r in rows:
                if _cells(lines[r]) != n_head:
                    bad.append((header + 1, r + 1, n_head, _cells(lines[r])))
        assert not bad, (
            "以下表格列数不一致（表头行, 数据行, 表头列数, 该行列数）："
            f"{bad[:5]}")


class TestSchedMarkerPlacement:
    """D. `<!-- SCHED:AUTO -->` 必须在定时表**表头之前**。"""

    def test_marker_before_header(self) -> None:
        lines = _lines()
        auto = next((i for i, x in enumerate(lines) if "SCHED:AUTO" in x), None)
        assert auto is not None, "缺 `<!-- SCHED:AUTO -->` 标记"
        end = next((i for i, x in enumerate(lines) if "SCHED:END" in x), None)
        assert end is not None and end > auto
        first_row = next((i for i in range(auto, end)
                          if lines[i].strip().startswith("|")), None)
        assert first_row is not None, "标记区间内没有表格行"
        # 标记与第一行之间不得有"表头"被漏在外面：第一行必须是表头本身
        assert lines[first_row].strip().startswith("| 频率"), (
            f"L{first_row+1} 应为表头（`| 频率 | ...`），实际 {lines[first_row].strip()[:50]!r}"
            " ⇒ 说明表头在标记之外，注释会打断表格")

    def test_marker_region_has_header_and_separator(self) -> None:
        lines = _lines()
        auto = next(i for i, x in enumerate(lines) if "SCHED:AUTO" in x)
        nxt = [lines[auto + 1 + k].strip() for k in range(2)]
        assert nxt[0].startswith("|"), nxt
        assert set(nxt[1]) <= set("|:- "), f"第二行应是分隔行，实际 {nxt[1][:50]!r}"
