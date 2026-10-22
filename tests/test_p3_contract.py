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
    """B. P3 两类画法缺陷的实现级断言（防止重构退回去）。"""

    def test_value_label_has_overflow_fallback(self) -> None:
        src = open(HTML, encoding="utf-8").read()
        assert "function txtW(" in src, "缺少文本宽度估算函数 `txtW`"
        # barChart 里必须有越界判断 + 回退
        i = src.find("function barChart(")
        seg = src[i:i + 3000]
        assert "tx+tw>418" in seg, "barChart 数值标签未做越界判断（会被裁掉）"
        assert 'text-anchor="end"' in seg, "barChart 越界后未回退到条形内部（右对齐）"

    def test_dominance_is_explained_not_hidden(self) -> None:
        """极值比 ≥20 时必须**说明**而非美化（禁止对数轴/截断掩盖真实分布）。"""
        src = open(HTML, encoding="utf-8").read()
        i = src.find("function barChart(")
        seg = src[i:i + 3000]
        assert "max/minPos>=20" in seg, "未检测分布高度集中"
        assert "真实分布" in seg, "未向读者说明「这是真实分布，不是图坏」"
        # 明确禁止的失真手段
        for bad in ("Math.log(", "log10("):
            assert bad not in seg, f"barChart 使用了 {bad}（对数轴会失真，本仓禁止）"

    def test_empty_state_is_explicit(self) -> None:
        src = open(HTML, encoding="utf-8").read()
        i = src.find("function barChart(")
        seg = src[i:i + 3000]
        assert "暂无数据" in seg, "空数据时未给明确说明（空画布会被误读成渲染失败）"
        assert "暂无质量快照" in src, "DAMA 空态未给说明"
