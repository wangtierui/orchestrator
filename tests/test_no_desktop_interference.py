# -*- coding: utf-8 -*-
"""桌面干扰契约守卫（批 56 初版 → 批 61 重写为单一事实源）。

覆盖四类契约：
  1. **输入自动化 / 焦点干预全仓禁止**（用户在其他办公软件中被抢焦点不可接受）；
  2. **剪贴板 API 仅豁免两处**：`clipboard_guard.py`（内容保全）与 `win_station.py`（窗口站隔离器，
     其 `EmptyClipboard` 只出现在模块文档与**私有站自检**中）；其余文件出现即失败；
  3. **LibreOffice 转换路径**：必须经**独立窗口站**（`win_station.isolated_run`）＋ 带**隔离 profile**
     与**全静默旗标**；隔离不成立必须 fail-safe 放弃；保留紧急开关 `RCO_DISABLE_LO_CONVERT=1`；
     外层必须有**剪贴板保全护栏**（快照/恢复）；
  4. **子进程隐藏默认**（`proc.py`）：Windows 下 `hidden_kwargs()` 必须给出隐藏窗口参数。

背景（用户实测症状与结论）：LibreOffice 在 Windows 上即使 `--headless` 也会在启动/退出清空系统剪贴板
（实测每次转换 Δ≥1，最坏 6 次）⇒ 用户复制粘贴失效；窗口站隔离对普通操作有效但对 LO 不足 ⇒
故采取"窗口站（隔离面）+ 保全护栏（内容不丢）"双重机制。
"""
from __future__ import annotations

import os

#: 输入自动化 / 焦点干预（**全仓禁止**）
BANNED_FOCUS = ("pyautogui", "pynput", "SendInput", "keybd_event", "mouse_event",
                "SetForegroundWindow", "BringWindowToTop", "SetActiveWindow",
                "AttachThreadInput", "SendKeys", "os.startfile")
#: 剪贴板 API（**仅豁免清单内文件**）
CLIPBOARD_APIS = ("OpenClipboard", "SetClipboardData", "EmptyClipboard",
                  "win32clipboard", "pyperclip", "to_clipboard")
#: 剪贴板 API 豁免文件（相对仓根；附理由见模块 docstring）
CLIPBOARD_EXEMPT = ("std_lib/scraper_std/clipboard_guard.py",
                    "std_lib/scraper_std/win_station.py",
                    "tests/test_no_desktop_interference.py")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 说明：`/external` 为**第三方 vendored 代码**（如 PaddleOCR），不属本仓纪律范围；
_SKIP_DIRS = ("/.git", "__pycache__", "/refactor", "/reports", "/graphify-out",
              "/.venv", "/external")


def _iter_py():
    for dp, _dn, fn in os.walk(_ROOT):
        d = dp.replace(os.sep, "/")
        if any(s in d for s in _SKIP_DIRS):
            continue
        for f in fn:
            if f.endswith(".py"):
                p = os.path.join(dp, f)
                yield os.path.relpath(p, _ROOT).replace(os.sep, "/"), p


def _read(p: str) -> str:
    """读源码并**剔除注释行**：纪律扫描针对**调用**，不针对文档/注释中的提及。"""
    txt = open(p, encoding="utf-8", errors="replace").read()
    return "\n".join(ln for ln in txt.splitlines() if not ln.lstrip().startswith("#"))


def test_no_input_automation_or_focus_apis_repo_wide() -> None:
    """输入自动化 / 焦点干预 API **全仓禁止**（仅本测试文件内的禁列清单可提及）。"""
    hits = []
    for rel, p in _iter_py():
        if rel == "tests/test_no_desktop_interference.py":
            continue
        txt = _read(p)
        for kw in BANNED_FOCUS:
            if kw in txt:
                hits.append("%s:%s" % (rel, kw))
    assert not hits, "禁止的焦点/输入自动化 API 出现在：%s" % hits


def test_clipboard_apis_only_in_exempt_files() -> None:
    """剪贴板 API 仅允许出现在**豁免清单**；其余文件出现即失败（批 61 策略）。"""
    hits = []
    for rel, p in _iter_py():
        if rel in CLIPBOARD_EXEMPT:
            continue
        txt = _read(p)
        for kw in CLIPBOARD_APIS:
            if kw in txt:
                hits.append("%s:%s" % (rel, kw))
    assert not hits, "剪贴板 API 越界（仅允许 %s）：%s" % (list(CLIPBOARD_EXEMPT), hits)


def test_clipboard_guard_safety_properties() -> None:
    """护栏安全性质（源码级）：格式白名单 / 体积上限 / 关闭开关 / 不把"空"当内容恢复。"""
    g = _read(os.path.join(_ROOT, "std_lib", "scraper_std", "clipboard_guard.py"))
    for must in ("CF_UNICODETEXT", "CF_TEXT", "CF_HDROP", "_MAX_BYTES", "_MAX_TOTAL",
                 "RCO_DISABLE_CLIPBOARD_GUARD", "def snapshot", "def restore", "def describe"):
        assert must in g, "护栏缺少 %s" % must
    head = g.split("_UNSAFE_NAMES")[0]
    assert "CF_BITMAP" not in head, "位图不得进入保全白名单（延迟渲染会反向唤起持有者 GUI）"
    assert "raise" not in g.split("def restore")[0].split("def snapshot")[1], \
        "快照不得抛异常（护栏不可成为故障源）"


def test_window_station_self_provable_and_fail_safe() -> None:
    """窗口站隔离器：具备自证入口（`--selftest`）、fail-safe 语义与唯一站名。"""
    w = _read(os.path.join(_ROOT, "std_lib", "scraper_std", "win_station.py"))
    for must in ("CreateWindowStationW", "CreateDesktopW", "SetProcessWindowStation",
                 "SetThreadDesktop", "RC_NO_ISOLATION", "def selftest",
                 "def isolated_run", "rco_lo_ws_"):
        assert must in w, "隔离器缺少 %s" % must
    assert "SetThreadDesktop" in w and "隔离不成立" in w, "SetThreadDesktop 失败必须视为隔离不成立（fail-safe）"


def test_lo_convert_path_uses_station_and_guard() -> None:
    """转换路径守卫：必须经窗口站 + 护栏；隔离失败 fail-safe；保留紧急开关。"""
    src = _read(os.path.join(_ROOT, "std_lib", "scraper_std", "doc_convert.py"))
    assert "win_station.isolated_run(" in src, "转换必须经独立窗口站执行"
    assert "clipboard_guard.snapshot()" in src and "clipboard_guard.restore(" in src, "必须有护栏快照与恢复"
    assert "RC_NO_ISOLATION" in src, "隔离不成立时必须 fail-safe 放弃"
    assert "RCO_DISABLE_LO_CONVERT" in src, "需保留紧急关闭开关"


def test_lo_convert_disabled_by_env_only(monkeypatch) -> None:
    """**紧急开关**语义：仅 `RCO_DISABLE_LO_CONVERT=1` 时禁止转换；默认**恢复转换**（经隔离+护栏）。"""
    from std_lib.scraper_std import doc_convert as _dc

    called = {"n": 0}

    def _fake_run(*a, **k):
        called["n"] += 1
        raise AssertionError("关闭开关生效时不得启动任何子进程")

    monkeypatch.setenv("RCO_DISABLE_LO_CONVERT", "1")
    monkeypatch.setattr(_dc, "find_libreoffice", lambda *a, **k: "soffice-stub")
    monkeypatch.setattr(_dc.subprocess, "run", _fake_run)
    assert _dc.doc_to_docx(__file__) is None, "关闭开关生效时必须返回 None（不转换）"
    assert called["n"] == 0, "关闭开关生效时不得调用子进程"


def test_libreoffice_invocation_is_isolated_and_silent(monkeypatch) -> None:
    """LibreOffice 调用必须带**隔离 profile + 全静默旗标**，且经**独立窗口站**执行。"""
    from std_lib.scraper_std import doc_convert as _dc
    from std_lib.scraper_std import win_station as _ws

    captured: dict = {}

    def _fake_isolated(argv, **kw):
        captured.setdefault("argv", list(argv))
        captured.setdefault("kw", dict(kw))
        return 0, "", "", {"isolated": True, "winsta": "rco_lo_ws_stub", "note": ""}

    monkeypatch.setattr(_dc, "find_libreoffice", lambda *a, **k: "soffice-stub")
    monkeypatch.setattr(_ws, "isolated_run", _fake_isolated)
    monkeypatch.setattr(_ws, "station_available", lambda: True)
    monkeypatch.setattr(_ws, "global_clipboard_seq", lambda: 1)
    monkeypatch.delenv("RCO_DISABLE_LO_CONVERT", raising=False)
    _dc.doc_to_docx(__file__, timeout=5)

    argv = captured.get("argv") or []
    assert argv, "必须经 win_station.isolated_run 启动转换（不得直接 subprocess.run）"
    assert "--headless" in argv, "缺少 --headless"
    assert any(a.startswith("-env:UserInstallation=") for a in argv), "缺少隔离 profile"
    assert "--norestore" in argv and "--nolockcheck" in argv, "缺少全静默旗标"
    assert "--convert-to" in argv and "docx" in argv, "缺少转换目标"


def test_hidden_subprocess_defaults(monkeypatch) -> None:
    """子进程隐藏默认（批 57）：Windows 下 `hidden_kwargs()` 必须给出隐藏窗口参数且调用方优先。"""
    from std_lib.scraper_std import proc

    kw = proc.hidden_kwargs()
    if proc.IS_WIN:
        assert kw.get("creationflags"), "Windows 下必须带 CREATE_NO_WINDOW"
        assert "startupinfo" in kw, "Windows 下必须带 SW_HIDE 的 startupinfo"
        assert kw["startupinfo"].wShowWindow == 0
    # 调用方显式参数优先
    import subprocess  # noqa: F401

    assert proc.child_kwargs(polite=False).get("creationflags", 0) & proc.BELOW_NORMAL_PRIORITY_CLASS == 0
    assert proc.child_kwargs(polite=True).get("creationflags", 0) & proc.BELOW_NORMAL_PRIORITY_CLASS


def test_clipboard_guard_format_allowlist_is_exact() -> None:
    """**受控取舍（W-AU）**：保全白名单**恰为** `CF_TEXT`/`CF_UNICODETEXT`/`CF_HDROP`，

    不得擅自扩到延迟渲染格式 ✗。技术依据：位图/HTML/RTF/OLE 常为 **delayed rendering**，
    `GetClipboardData` 会**反向请求持有者渲染**（可能唤起其 GUI、阻塞，甚至使 `CloseClipboard`
    失败 ⇒ **用户的剪贴板被锁住** ✗✗，危害远大于收益）。故取舍为：只保全可确定性读取的
    文本/文件列表，并在日志中**明示未保格式**（`describe()`）✓。
    """
    import os as _os
    import re as _re

    root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    g = open(_os.path.join(root, "std_lib", "scraper_std", "clipboard_guard.py"),
             encoding="utf-8").read()
    m = _re.search(r"if fmt not in \(([^)]*)\):", g)
    assert m, "未找到保全白名单判据"
    got = {x.strip() for x in m.group(1).split(",") if x.strip()}
    assert got == {"CF_TEXT", "CF_UNICODETEXT", "CF_HDROP"}, (
        "白名单必须**恰为**文本/文件列表三项（W-AU 受控取舍）；实测=%s" % sorted(got))
