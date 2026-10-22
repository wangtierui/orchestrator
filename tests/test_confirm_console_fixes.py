# -*- coding: utf-8 -*-
"""tests/test_confirm_console_fixes.py — 确认台三缺陷的**回归守卫**。

2026-10-16 用户报告三处缺陷，本文件把它们钉住（每条都配失态）：

  ① **P1 无边**：`flow_graph` 的边权读**不存在的** `run_step.raw_size`/`record_count`
     ⇒ `weights` 恒空 ⇒ 桑基图不画边。修法：改用**产物侧权威**
     （`clean_index_api.record_count`，ADR-0014 C3 决策指定"产物侧才是权威"）。
  ② **P3 图表错位**：`barChart` 按**位置**取值（`Object.values(data)[i]`）而 JSON
     对象键序**无语义保证**（实测 `worklist_aging` 键序是字典序
     `0-3,15+,4-7,8-14`，而 labels 是语义序 `0-3,4-7,8-14,15+`）⇒ 标签与数值整片错位。
  ③ **P4 列表截断 + 无批量**：`worklist_items(limit=200)` 硬编码 ⇒ 641 条只显示 200。
"""
from __future__ import annotations

import inspect
import os
import sys

import pytest

pytestmark = pytest.mark.contract

SNAP = ("web", "snapshots.py")
HTML = ("web", "assets", "index.html")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _src(*parts: str) -> str:
    return open(os.path.join(ROOT, *parts), encoding="utf-8").read()


def _code_only(text: str) -> str:
    """剔除注释与 docstring——判据只看**可执行代码**。

    （说明性文本里引用旧写法是正常的解释，不该被当成"仍在用旧写法"——
     `barChart` 的修正注释引用了 `Object.values(data)`，
     `flow_graph` 的修正 docstring 引用了旧列名。）
    """
    import io as _io
    import tokenize

    # 用 tokenize 精确剥离注释与字符串（比行级启发式可靠：docstring 是字符串 token）
    try:
        out: list[str] = []
        prev_end = (1, 0)
        with _io.StringIO(text) as fh:
            for tok in tokenize.generate_tokens(fh.readline):
                if tok.type in (tokenize.COMMENT, tokenize.STRING):
                    continue
                srow, scol = tok.start
                erow, ecol = tok.end
                if srow > prev_end[0]:
                    out.append("\n" * (srow - prev_end[0]))
                if (srow, scol) != prev_end:
                    out.append(" " * (scol - prev_end[1]))
                out.append(tok.string)
                prev_end = (erow, ecol)
        return "".join(out)
    except (tokenize.TokenError, IndentationError, SyntaxError):
        # 片段可能不是完整语句 ⇒ 回退到行级启发式
        out2 = []
        for ln in text.splitlines():
            s = ln.strip()
            if s.startswith("//") or s.startswith("#") or s.startswith("*"):
                continue
            out2.append(ln.split("//")[0] if "//" in ln else ln)
        return "\n".join(out2)


def _flow_seg() -> str:
    """取 `flow_graph` 的**可执行代码**（tokenize 需完整语句 ⇒ 从 `def` 起整段取）。"""
    src = _src(*SNAP)
    i = src.find("def flow_graph(")
    j = src.find("\ndef ", i + 10)
    body = src[i:j if j > 0 else len(src)]
    return _code_only(body)


def _ensure_path() -> None:
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)


# --------------------------------------------------------------------------- #
# ① P1 边权
# --------------------------------------------------------------------------- #
def test_flow_weights_come_from_artifact_side() -> None:
    """正例：边权取自**产物侧**（clean_index_api），不再读 run_step 的不存在列。"""
    seg = _flow_seg()
    assert "clean_index_api" in seg, "边权未走产物侧权威（ADR-0014 C3 决策）"
    assert "record_count(" in seg, "未调用 record_count"


def test_flow_weights_no_longer_read_run_step_size_columns() -> None:
    """负例：**不得**再从 `run_step` 读 `raw_size`/`output_count`
    （该表无这些列 ⇒ 恒空 ⇒ P1 无边；这正是 2026-10-16 修掉的缺陷）。"""
    seg = _flow_seg()
    for col in ("raw_size", "output_count"):
        assert col not in seg, f"边权仍在读不存在的列 {col}（P1 无边复发）"


def test_flow_weights_are_actually_populated() -> None:
    """正例：实测边权非空（回归测试：改坏会立刻红）。"""
    _ensure_path()
    from bootstrap import bootstrap

    bootstrap("all")
    from web import snapshots as S

    d = S.flow_graph()
    w = d.get("weights") or {}
    assert w, "weights 为空 ⇒ P1 桑基图无边（用户报'未呈现数据流转'）"
    assert all(isinstance(v, int) and v > 0 for v in w.values()), w


# --------------------------------------------------------------------------- #
# ② P3 barChart 按 label 取值
# --------------------------------------------------------------------------- #
def test_bar_chart_reads_by_label_not_by_position() -> None:
    """正例：条形图按 **label** 取值（不按 `Object.values` 的位置）。

    2026-10-22：条形图从手绘 SVG（`barChart`）改为 CSS 网格（`bars`）——
    换的是实现，**守的是同一个缺陷**：按位置取值会让标签与数值整片错位
    （JSON 对象键序无语义保证，实测 `worklist_aging` 键序是字典序）。
    """
    src = _src(*HTML)
    i = src.find("function bars(")
    assert i > 0, "找不到 bars() 实现"
    seg = _code_only(src[i:i + 1800])
    assert "Object.values(data" not in seg, (
        "bars 仍在按位置取值 ⇒ 标签与数值错位（P3 渲染异常复发）")
    assert "Object.entries(obj(data))" in seg, "bars 未按 label 取值"


# --------------------------------------------------------------------------- #
# ③ P4 列表完整 + 批量
# --------------------------------------------------------------------------- #
def test_worklist_items_defaults_to_all() -> None:
    """正例：默认 `limit=0`（全部）—— 此前硬编码 200 ⇒ 641 条只显示 200。"""
    _ensure_path()
    from bootstrap import bootstrap

    bootstrap("all")
    from web import snapshots as S

    sig = inspect.signature(S.worklist_items)
    assert sig.parameters["limit"].default == 0, (
        f"limit 默认={sig.parameters['limit'].default}（应 0=全部）")
    assert "offset" in sig.parameters, "缺 offset 参数（无分页能力）"


def test_worklist_items_reports_total() -> None:
    """正例：返回 `total`（前端据此显示"共 N 条"）。"""
    _ensure_path()
    from bootstrap import bootstrap

    bootstrap("all")
    from web import snapshots as S

    assert '"total"' in inspect.getsource(S.worklist_items), "未回填 total"


def test_console_exposes_bulk_selection() -> None:
    """正例：前端有多选（全选 checkbox + 批量处置按钮 + 理由必填）。"""
    src = _src(*HTML)
    assert 'id="wlall"' in src, "缺全选 checkbox"
    assert "wlck" in src, "缺行内 checkbox"
    assert 'id="wlbulk"' in src, "缺批量处置按钮"
    assert "批量处置理由" in src, "批量处置缺理由输入（审计要求必填）"


def test_console_flow_nodes_are_clickable() -> None:
    """正例：P1 节点带 `data-step` 且有事件绑定（此前"点击无反应"）。

    2026-10-22：节点从 `<rect class="fnode">` 改为 `<g class="fnode">`（组内 rect+text）。
    整组可点比"只有矩形能点"更好用（点在文字上也算），故断言随之改为 `g.fnode`。
    """
    src = _src(*HTML)
    assert "fnode" in src, "P1 节点缺 class=fnode（无法绑定点击）"
    assert "openStep" in src, "缺 openStep()（节点抽屉入口）"
    assert "g.fnode" in src, "P1 节点未绑定 click"


def test_console_shows_real_total() -> None:
    """正例：P4 标题显示真实总数（此前 d.count=200 而真实 641）。"""
    src = _src(*HTML)
    assert "d.total" in src, "P4 未使用 total 字段"
    assert "共 <b>${total}</b> 条" in src or "共 ${total} 条" in src, "标题未显示总数"
