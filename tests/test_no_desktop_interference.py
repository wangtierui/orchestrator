# -*- coding: utf-8 -*-
"""批 56 守卫：后台任务**不得干扰用户桌面**（剪贴板/焦点/浏览器抢占）——回归防线。
背景（用户实测症状）：任务运行中，其他办公软件里**复制粘贴失效或被抢占**。
取证结论：`doc_convert.doc_to_docx` 调用 LibreOffice 时**未指定 `-env:UserInstallation`**
⇒ `--headless` 挂到**用户既有 LibreOffice 会话/默认 profile**，抢占焦点与剪贴板，且历史实测会
**遗留 soffice 进程常驻用户会话**（实测命令行即本仓 doc_convert 的调用、进程长期存活）；
另有 Word COM（`gov_zhengceku`）与 WPS COM（`crawler_common`）两条自动化链会与桌面会话交互。
"""
from __future__ import annotations

import os
import re
import sys


def test_libreoffice_invocation_is_isolated_and_silent(monkeypatch, tmp_path) -> None:
    """**行为守卫**：doc_convert 的 soffice 调用必须带隔离 profile 与全静默旗标。"""
    from std_lib.scraper_std import doc_convert as dc
    captured: dict = {}
    class _R:
        returncode = 0
        stdout = ""
        stderr = ""
    def _fake_run(cmd, *a, **kw):
        if "--convert-to" in cmd:   # 只捕获转换调用（另有 tasklist/powershell）
            captured.setdefault("cmd", list(cmd))
        return _R()
    monkeypatch.setattr(dc, "find_libreoffice", lambda *a, **k: "soffice-stub")  # 隔离探测依赖
    # 批 58：转换**默认禁用**（每次转换会清空系统剪贴板）⇒ 本测试需显式放行
    monkeypatch.setenv("RCO_ALLOW_LO_CONVERT", "1")
    monkeypatch.setattr(dc.subprocess, "run", _fake_run)
    monkeypatch.setattr(dc, "_soffice_pids", lambda: set())   # 兜底清理不触真实 tasklist
    monkeypatch.setattr(dc, "_kill_pids", lambda pids, **k: 0)
    src = tmp_path / "input.doc"
    src.write_bytes(b"x")
    dc.doc_to_docx(str(src), timeout=5)
    cmd = captured.get("cmd") or []
    assert cmd, "未观察到 LibreOffice 调用（守卫失效）"
    assert "--headless" in cmd, "必须 headless"
    assert any(x.startswith("-env:UserInstallation=") for x in cmd), \
        "缺少隔离 profile（会挂到用户 LibreOffice 会话并抢占焦点/剪贴板）"
    assert "--norestore" in cmd and "--nodefault" in cmd, "缺少静默/无对话框旗标"
    assert any("file://" in x for x in cmd), "隔离 profile 须为 file URL"
def test_isolated_profile_is_repo_owned_temp_dir() -> None:
    """隔离 profile 落在**本仓自有临时目录**（不触碰用户 LibreOffice 配置）。"""
    from std_lib.scraper_std.doc_convert import _isolated_profile_url
    url = _isolated_profile_url()
    assert url.startswith("file://") and "rco_lo_profile" in url
    assert os.path.isdir(os.path.join(__import__("tempfile").gettempdir(), "rco_lo_profile"))
def test_no_clipboard_or_focus_apis_repo_wide() -> None:
    """全仓**禁止**剪贴板/焦点/自动输入类 API（否则必然干扰用户桌面）。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    banned = ("win32clipboard", "pyperclip", "SetClipboardData", "SetForegroundWindow",
              "BringWindowToTop", "SetActiveWindow", "pyautogui", "SendKeys", "os.startfile")
    hits = []
    self_file = os.path.abspath(__file__)
    for dp, _dn, fn in os.walk(root):
        d = dp.replace(os.sep, "/")
        if any(s in (d + "/") for s in (".git/", "__pycache__/", "reports/", "graphify-out/",
                                        "external/", "tests/", "refactor/")):
            continue
        for f in fn:
            if not f.endswith(".py"):
                continue
            p = os.path.join(dp, f)
            if os.path.abspath(p) == self_file:
                continue
            txt = open(p, encoding="utf-8", errors="replace").read()
            for i, ln in enumerate(txt.splitlines(), 1):
                if any(k in ln for k in banned) and not ln.strip().startswith("#"):
                    hits.append("%s:%d" % (p.replace(os.sep, "/"), i))
    assert not hits, "发现剪贴板/焦点类 API：%s" % hits[:6]
def test_office_com_is_gated_by_env() -> None:
    """Office/WPS COM 必须**默认禁用**（需 `RCO_ALLOW_OFFICE_COM=1` 显式开启）。
    判定：COM 调用行的**前 6 行内**必须出现 `RCO_ALLOW_OFFICE_COM` 门禁（即先门禁后调用）。
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for rel, marker in (("std_lib/scraper_std/crawler_common.py", 'Dispatch("KWPS.Application")'),
                        ("modules/regulatory_scrapers/collectors/gov_zhengceku.py",
                         'Dispatch("Word.Application")')):
        ls = open(os.path.join(root, rel), encoding="utf-8").read().splitlines()
        # 只认**赋值形式的真实调用**（`x = mod.Dispatch(...)`），跳过 docstring/注释里的"提及"
        idx = next(i for i, ln in enumerate(ls)
                   if marker in ln and re.search(r"^\s*[\w.]+\s*=\s*[\w.]+\.Dispatch\(", ln))
        window = chr(10).join(ls[max(0, idx - 6):idx])
        assert "RCO_ALLOW_OFFICE_COM" in window, \
            "%s:%d 的 COM 调用缺少 env 门禁（会与用户桌面会话交互）" % (rel, idx + 1)
def test_no_lingering_process_helper_contract() -> None:
    """兜底清理契约：`_soffice_pids` 返回集合、`_kill_pids(空)` 无副作用且不抛异常。"""
    from std_lib.scraper_std.doc_convert import _kill_pids, _soffice_pids
    assert isinstance(_soffice_pids(), set)
    assert _kill_pids(set()) == 0
    assert sys.platform


def test_lo_convert_disabled_by_default(monkeypatch) -> None:
    """**剪贴板根治守卫**（批 58）：`doc_to_docx` 默认**不得**调用 LibreOffice。

    取证：每次转换确定性清空系统剪贴板（A/B 实测 Δ=1）⇒ 默认禁用是唯一零风险根治。
    """
    import os as _os

    from std_lib.scraper_std import doc_convert as _dc

    called = {"n": 0}

    def _fake_run(*a, **k):
        called["n"] += 1
        raise AssertionError("默认禁用时不得启动任何子进程")

    monkeypatch.delenv("RCO_ALLOW_LO_CONVERT", raising=False)
    monkeypatch.setattr(_dc, "find_libreoffice", lambda *a, **k: "soffice-stub")
    monkeypatch.setattr(_dc.subprocess, "run", _fake_run)
    assert _dc.doc_to_docx(__file__) is None, "默认必须返回 None（不转换）"
    assert called["n"] == 0, "默认禁用时不得调用子进程"
    assert _os.environ.get("SAL_USE_VCLPLUGIN") == "svp", "仍应保留 svp 环境默认（放行时降低影响）"

