# -*- coding: utf-8 -*-
"""
doc_convert.py —— Office 旧格式 → docx 转换共享层（2026-09-08，五源统一）

背景：.doc（OLE2 旧版 Word）/ .wps / .rtf / .ceb 无内置结构化表格解析，
此前在 table_recovery 中只能 raw_only 降级。本共享层统一 headless LibreOffice
doc→docx 转换（pbc 原内联 detect/convert 提升至此），供五源附件/正文解析复用：
转换成功即按 docx 走文本与表格解析，消除"旧 Word 仅 raw_only"。

接口：
  find_libreoffice(bin=None) -> str | None   探测 soffice 可执行（env LO_BIN 优先）
  doc_to_docx(doc_path, *, bin=None, timeout=120) -> str | None
      单文件 headless 转换到同目录，返回新 .docx 路径（失败 None）。
  doc_bytes_to_docx(data, name="", *, bin=None, timeout=120) -> bytes | None
      bytes → 临时文件 → 转换 → 读回 docx bytes → 清理临时目录。
本模块零网络、纯本地工具；转换失败/环境缺失一律返回 None（由调用方降级）。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile


def _windows_lo_candidates() -> list[str]:
    """Windows 常见安装路径：由 ProgramFiles 环境变量派生（避免盘符字面量硬编码）。"""
    out = []
    for var in ("ProgramFiles", "PROGRAMFILES"):
        pf = os.environ.get(var, "").strip()
        if pf:
            out.append(os.path.join(pf, "LibreOffice", "program", "soffice.exe"))
    for var in ("ProgramFiles(x86)", "PROGRAMFILES(X86)"):
        pf = os.environ.get(var, "").strip()
        if pf:
            out.append(os.path.join(pf, "LibreOffice", "program", "soffice.exe"))
    return out


def find_libreoffice(bin: str | None = None) -> str | None:
    """探测 LibreOffice(headless) 可执行文件；找不到返回 None。

    优先级：env LO_BIN / 显式 bin / PATH(soffice|libreoffice) / 常见安装路径
    （Windows Program Files 派生 + Git-bash /c/… 形式）。
    """
    if bin and os.path.exists(bin):
        return bin
    env_bin = (bin or os.environ.get("LO_BIN") or "").strip()
    if env_bin and os.path.exists(env_bin):
        return env_bin
    for c in ("soffice", "libreoffice"):
        p = shutil.which(c)
        if p:
            return p
    for cand in _windows_lo_candidates():
        if os.path.exists(cand):
            return cand
    for cand in (
        "/c/Program Files/LibreOffice/program/soffice.exe",
        "/c/Program Files (x86)/LibreOffice/program/soffice.exe",
    ):
        if os.path.exists(cand):
            return cand
    return None



#: 批 57/W-AB：正式模块 logger。**不以 `import logging` 语句引入**（本文件历史上无该导入，中途插入会触发 E402 "module level import not at top"）⇒ 用 `__import__` 取得同一对象。
LOG = __import__("logging").getLogger(__name__)


def _pids_by_profile(profile_url: str) -> set[int]:
    """**仅**返回命令行含本仓隔离 profile 标记的 soffice PID ⇒ **精确归属，绝不误杀用户 LibreOffice**。

    批 57 教训：批 56 用"前后 PID 差集"清理遗留进程 ✗ —— 若用户此刻打开/正在使用 LibreOffice 会被
    **误杀**；而**剪贴板内容由源进程持有**，进程被杀即表现为"复制粘贴失效" ✗✗。故改为按
    `rco_lo_profile`（本仓专属标记）在命令行里精确匹配。
    """
    pids: set[int] = set()
    if os.name != "nt":
        return pids
    try:
        from std_lib.scraper_std.proc import run as _run
    except Exception:  # noqa: BLE001  引导期兜底
        def _run(argv, **kw):          # 包装函数（避免 mypy 对重载函数赋值报错）
            return subprocess.run(argv, **kw)
    cmd = ("Get-CimInstance Win32_Process | Where-Object { $_.Name -match \'^soffice\' -and "
           "$_.CommandLine -like \'*rco_lo_profile*\' } | ForEach-Object { $_.ProcessId }")
    try:
        out = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
                   capture_output=True, text=True, encoding="utf-8",
                   errors="replace", timeout=30).stdout or ""
        for ln in out.splitlines():
            if ln.strip().isdigit():
                pids.add(int(ln.strip()))
    except Exception:  # noqa: BLE001  查询失败不阻断
        pass
    return pids


def _soffice_pids() -> set[int]:
    """当前 soffice/soffice.bin 进程 PID 集合（批 56：用于兜底清理，避免遗留进程与用户会话纠缠）。"""
    pids: set[int] = set()
    if os.name != "nt":
        return pids
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq soffice.exe", "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=20).stdout or ""
        out += subprocess.run(["tasklist", "/FI", "IMAGENAME eq soffice.bin", "/FO", "CSV", "/NH"],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=20).stdout or ""
        for ln in out.splitlines():
            parts = [x.strip('"') for x in ln.split(",")]
            if len(parts) > 1 and parts[1].isdigit():
                pids.add(int(parts[1]))
    except Exception:  # noqa: BLE001  查询失败不阻断
        pass
    return pids


def _kill_pids(pids: set[int]) -> int:
    """强杀给定 PID（连同子进程 `/T`）；返回成功条数。"""
    n = 0
    for pid in sorted(pids):
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=20)
            n += 1
        except Exception:  # noqa: BLE001  杀进程失败不阻断
            pass
    return n


def _isolated_profile_url() -> str:
    """**隔离的 LibreOffice 用户配置目录**（批 56 核心修复）。

    为何必须：`--headless` 在**未指定 `-env:UserInstallation`** 时会挂到**用户既有 LibreOffice
    环境/会话**（实测本仓 doc_convert 的调用即如此）⇒ 抢占桌面焦点与剪贴板、并与用户正在使用的
    LibreOffice 互相干扰；隔离后转换在独立 profile 内完成，**不触碰用户会话**。
    """
    from pathlib import Path

    base = os.path.join(tempfile.gettempdir(), "rco_lo_profile")
    os.makedirs(base, exist_ok=True)
    return Path(base).as_uri()


def doc_to_docx(doc_path: str, *, bin: str | None = None, timeout: int = 120) -> str | None:
    """headless 将 .doc 等旧格式单文件转为 docx（输出同目录），返回新路径或 None。"""
    lo = find_libreoffice(bin)
    if not lo or not os.path.exists(doc_path):
        return None
    out_dir = os.path.dirname(os.path.abspath(doc_path))
    # 批 56：**隔离 profile + 全静默旗标**（不挂用户会话、不弹恢复/默认文档对话框、不抢焦点与剪贴板）
    _profile_url = _isolated_profile_url()
    _before = _pids_by_profile(_profile_url)   # 只认我方 profile（不触碰用户会话）
    try:
        subprocess.run(
            [lo, "--headless", "--norestore", "--nodefault", "--nologo", "--nolockcheck",
             "-env:UserInstallation=" + _profile_url,
             "--convert-to", "docx", "--outdir", out_dir, doc_path],
            check=False,
            capture_output=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    finally:
        # 批 57：**只清理命令行含我方隔离 profile 的进程**（精确归属）——
        # 绝不使用 PID 差集：那会误杀用户自己的 LibreOffice，并使其剪贴板内容失效。
        _leaked = _pids_by_profile(_profile_url) - _before
        if _leaked:
            _n = _kill_pids(_leaked)
            LOG.info("清理我方遗留 soffice %d 个（隔离 profile 精确归属）", _n)
    base = os.path.splitext(os.path.basename(doc_path))[0]
    conv = os.path.join(out_dir, base + ".docx")
    return conv if os.path.exists(conv) else None


def doc_bytes_to_docx(
    data: bytes, name: str = "", *, bin: str | None = None, timeout: int = 120
) -> bytes | None:
    """bytes → 临时 .doc → 转 docx → 读回 bytes → 清理。失败返回 None。"""
    if not data:
        return None
    tmp_dir = tempfile.mkdtemp(prefix="doc_convert_")
    try:
        ext = os.path.splitext(name or ".doc")[1].lower() or ".doc"
        src = os.path.join(tmp_dir, "input" + ext)
        with open(src, "wb") as fh:
            fh.write(data)
        conv = doc_to_docx(src, bin=bin, timeout=timeout)
        if not conv:
            return None
        with open(conv, "rb") as fh:
            return fh.read()
    except Exception:  # noqa: BLE001  转换失败一律 None（调用方降级）
        return None
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
