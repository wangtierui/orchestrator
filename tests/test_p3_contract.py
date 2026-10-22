# -*- coding: utf-8 -*-
"""tests/test_p3_contract.py — 确认台 P3 指标的**前后端字段契约**守卫（第一百零七批）。

为什么需要
----------
`CHANGELOG` 里 P3 已经被修过 **三次**（2026-10-19 批 100 三处；10-16 键序错位；
更早一次是"主题分布字段根本不存在"）。根因都是同一类：**前端读的键与后端给的键
不是同一份契约**，一旦漂移就表现为"渲染异常"（空卡片/空图），而接口本身 200。

本文件把这条契约**机器化**：静态抽取 `viewMetrics` 里读的所有字段路径，
逐个到真实 payload 里验证存在。字段一漂移就 FAIL，而不是等用户截图来报。

另含两条几何守卫（P3 真实踩过的画法缺陷）
----------------------------------------
* **数值标签越界**：`x = 条形起点 + 宽 + 6` 只留 56px，而 `num(14752)（94.5%）`
  明显更宽 ⇒ 文字被裁掉（看起来像渲染坏了）。现改为越界回退到条形内部。
* **分布高度集中**：`timeliness_tri` pending=14752 / valid=643（23 倍）、
  `worklist_aging` 8-14=225 / 0-3=4（56 倍）⇒ 小值只剩 2px 细线。
  现改为数值带占比 + 极值比 ≥20 时**显式说明**"真实分布，不是图坏"。
这两条用断言钉住"实现里确实做了"（源码级），避免下次重构又退回去。
"""
from __future__ import annotations

import os
import re

import pytest

pytestmark = pytest.mark.contract

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML = os.path.join(ROOT, "web", "assets", "index.html")

#: 由前端**加载器合并**进来的键（不在 `/api/metrics` 里，属正常）
MERGED = {"quality", "slo"}


def _view_metrics_src() -> str:
    src = open(HTML, encoding="utf-8").read()
    i = src.find("function viewMetrics(d){")
    assert i > 0, "未找到 viewMetrics"
    j = src.find("\n}\n", i)
    return src[i:j]


def _payloads() -> dict:
    """真实 payload（直接调快照层，不依赖服务进程）。"""
    from web import snapshots as S

    return {"metrics": S.metrics(), "quality": S.quality(), "slo": S.slo()}


class TestFieldContract:
    """A. `viewMetrics` 读到的每个字段路径都必须在 payload 里存在。"""

    def test_metrics_keys_exist(self) -> None:
        body = _view_metrics_src()
        m = _payloads()["metrics"]
        keys = sorted(set(re.findall(r"\bd\.([a-z_][a-z0-9_]*)", body)))
        assert keys, "未能从 viewMetrics 抽取字段（正则失效？）"
        missing = [k for k in keys if k not in m and k not in MERGED]
        assert not missing, (
            f"`/api/metrics` 缺字段 {missing}（前端在读）——这是 P3 空卡片的典型根因；"
            f"实际键：{sorted(m)}")

    def test_quality_keys_exist(self) -> None:
        body = _view_metrics_src()
        q = _payloads()["quality"]
        # `q.doc.X` / `qd` 来自 doc.dimensions
        need = sorted(set(re.findall(r"\bq\.doc\.([a-z_][a-z0-9_]*)", body)))
        doc = q.get("doc") or {}
        missing = [k for k in need if k not in doc]
        assert not missing, f"`/api/quality` 的 doc 缺字段 {missing}；实际：{sorted(doc)}"
        assert "dimensions" in doc, "quality.doc.dimensions 缺失（DAMA 图会空）"

    def test_slo_keys_exist(self) -> None:
        body = _view_metrics_src()
        s = _payloads()["slo"]
        need = sorted(set(re.findall(r"\bs\.last\.([a-z_][a-z0-9_]*)", body)))
        last = s.get("last") or {}
        missing = [k for k in need if k not in last]
        assert not missing, f"`/api/slo` 的 last 缺字段 {missing}；实际：{sorted(last)}"

    def test_sli_label_keys_covered(self) -> None:
        """`SLI_LABEL` 的每个键都必须在 `slo.last.sli` 里真的存在（否则标签是死的）。"""
        src = open(HTML, encoding="utf-8").read()
        m = re.search(r"const SLI_LABEL = \{(.*?)\};", src, re.S)
        assert m, "未找到 SLI_LABEL"
        keys = re.findall(r'"([a-z_]+)"\s*:', m.group(1))
        assert keys, "SLI_LABEL 未解析出键"
        sli = (_payloads()["slo"].get("last") or {}).get("sli") or {}
        extra = [k for k in keys if k not in sli]
        assert not extra, f"SLI_LABEL 里的 {extra} 在 slo.last.sli 中不存在（死标签）"


class TestGeometryGuards:
    """B. P3 画法缺陷的实现级断言（防止重构退回去）。

    2026-10-22（第一百零九批）：条形图从**手绘 SVG 改为 CSS 网格**。
    原因不是"换个好看"——而是**几何问题在 SVG 里无法根治**：SVG `<text>`
    没有 ellipsis，标签/数值宽度只能靠 `txtW()` 估算，估偏就溢出（用户三次报
    "渲染异常"，两次根因都是它）。改用 `grid-template-columns` 后：
      · 标签在自己的格子里，`text-overflow:ellipsis` 保证**永不压到柱子**；
      · 数值在固定右列，`tabular-nums` 保证对齐；
      · 结构上不存在"算出坐标再判断越界"这一步 ⇒ 该类缺陷被消除而非绕过。

    断言随之**从"检查坐标判断"改为"检查结构约束"**——保证的是同一个意图：
    **标签不越界、分布集中要说明、空态要显式**。
    """

    def test_label_cannot_overflow_into_bar(self) -> None:
        src = open(HTML, encoding="utf-8").read()
        # 条形列表用网格：标签列 / 柱体列 / 数值列（顺序固定，标签不可能压到柱子上）
        assert "function bars(" in src, "缺少条形列表实现 `bars()`"
        i = src.find("function bars(")
        seg = src[i:i + 3000]
        assert ".brow" in src and "grid-template-columns" in src, (
            "条形列表未用网格布局（回到绝对定位/坐标计算就会重新出现越界）")
        assert "ellipsis" in src, "标签未做省略号截断（长标签会挤压柱体）"
        assert "title=" in seg, "截断后必须用 title 给出完整文本（信息不丢）"

    def test_dominance_is_explained_not_hidden(self) -> None:
        """极值比 ≥20 时必须**说明**而非美化（禁止对数轴/截断掩盖真实分布）。"""
        src = open(HTML, encoding="utf-8").read()
        i = src.find("function bars(")
        seg = src[i:i + 3000]
        assert "max/minPos>=20" in seg, "未检测分布高度集中"
        assert "真实分布" in seg, "未向读者说明「这是真实分布，不是图坏」"
        # 明确禁止的失真手段（全文件层面禁止，不限于某个函数）
        for bad in ("Math.log(", "log10("):
            assert bad not in src, f"使用了 {bad}（对数轴会失真，本仓禁止）"

    def test_empty_state_is_explicit(self) -> None:
        src = open(HTML, encoding="utf-8").read()
        i = src.find("function bars(")
        seg = src[i:i + 3000]
        assert "暂无数据" in seg, "空数据时未给明确说明（空画布会被误读成渲染失败）"
        assert "暂无质量快照" in src, "DAMA 空态未给说明"
