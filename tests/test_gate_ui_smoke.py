# -*- coding: utf-8 -*-
"""tests/test_gate_ui_smoke.py — 运行时冒烟门禁的**判定逻辑**（不启动浏览器）。

真正的采集需要 Edge + 运行中的服务（`python -m tools.ui_smoke`）。
这里只验证门禁的三种结论：环境缺失→跳过、实测不符→FAIL、实测通过→PASS。
"""
from __future__ import annotations

import pytest

from gates import gate_ui_smoke as G
from tools import ui_smoke as S

pytestmark = pytest.mark.contract


GOOD = {
    "available": True,
    "saveDisabled": True,
    "pagerHover": {"color": "rgb(9, 105, 218)", "border": "rgb(9, 105, 218)",
                   "bg": "rgba(0, 0, 0, 0)"},
    "panel": {"w": 320, "optH": 32, "hoverBg": "rgba(129, 139, 152, 0.1)"},
    "rowWide": 65,
    "rowCompact": 38,
    "densityScope": {"view": "main-content lv-compact", "body": ""},
    "token": {"sort": "document_number", "desc": True, "saveDisabled": False},
    "truncate": {"maxOverflow": 79, "ellipsis": "ellipsis"},
    "tabStops": [{"tag": "button", "cls": "btn", "label": "主题"},
                 {"tag": "button", "cls": "btn", "label": "名称"},
                 {"tag": "button", "cls": "btn", "label": "紧凑"},
                 {"tag": "a", "cls": "page", "label": "2"},
                 {"tag": "a", "cls": "page", "label": "3"},
                 {"tag": "input", "cls": "q", "label": "搜索"},
                 {"tag": "button", "cls": "x", "label": "保存"},
                 {"tag": "a", "cls": "y", "label": "返回"},
                 {"tag": "button", "cls": "z", "label": "关闭"},
                 {"tag": "a", "cls": "w", "label": "更多"}],
}


def test_skipped_when_environment_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(S, "collect", lambda *a, **k: {"available": False, "reason": "服务未就绪"})
    ok, detail = G.run()
    assert ok is True and detail["skipped"] is True
    assert "服务未就绪" in detail["reason"]


def test_skipped_when_collect_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a, **k):
        raise RuntimeError("浏览器崩了")

    monkeypatch.setattr(S, "collect", boom)
    ok, detail = G.run()
    assert ok is True and detail["skipped"] is True
    assert "未实检" in detail["reason"]


def test_pass_when_all_checks_good(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(S, "collect", lambda *a, **k: dict(GOOD))
    ok, detail = G.run()
    assert ok is True and not detail.get("skipped")
    assert detail["passed"] == detail["total"] >= 7


def test_fail_when_metric_off(monkeypatch: pytest.MonkeyPatch) -> None:
    bad = dict(GOOD)
    bad["rowCompact"] = 47                 # 紧凑没压到 38
    bad["panel"] = {"w": 300, "optH": 32, "hoverBg": "rgba(0, 0, 0, 0)"}
    monkeypatch.setattr(S, "collect", lambda *a, **k: bad)
    ok, detail = G.run()
    assert ok is False
    assert detail["passed"] < detail["total"]
    names = [c["check"] for c in detail["checks"] if not c["ok"]]
    assert any("面板" in n for n in names)
    assert any("密度" in n for n in names)


def test_env_switch_disables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORCH_UI_SMOKE", "0")
    ok, detail = G.run()
    assert ok is True and detail["skipped"] is True
