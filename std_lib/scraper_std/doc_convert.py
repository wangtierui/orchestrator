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


# --------------------------------------------------------------------------- #
# 批 58：**剪贴板抢占的根治**（活体取证结论）
#
# 监控实测（mof 采集运行中，每 250ms 采样）：系统剪贴板**序列号每 2.5~3.5 秒 +1**（持续不断），
# `GetClipboardOwner()` 多为 0（`EmptyClipboard()` 后无主）偶为 `soffice.bin` ⇒ **LibreOffice 启动/
# 退出时清空系统剪贴板** ⇒ 用户刚复制的文字被抹掉 ⇒ **Ctrl+V 粘贴不出来**（= 复制粘贴失效/被抢占）✓✓。
#
# `--headless` 在 Windows 仍会初始化原生 GUI 层（故碰剪贴板）；`SAL_USE_VCLPLUGIN=svp`（Server
# Virtual Platform）为**服务端渲染插件**，不注册剪贴板监听 ⇒ 根治。**本处为模块级环境默认**，
# 转换子进程继承之（若调用处显式传 `env=`，须确保其包含本变量）。
# --------------------------------------------------------------------------- #
os.environ.setdefault("SAL_USE_VCLPLUGIN", "svp")
os.environ.setdefault("SAL_DISABLE_OPENCL", "1")     # 无 GPU 的服务端渲染，避免额外初始化


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


#: 转换结果缓存目录（临时区；键 = **输入字节 sha256**）。
#: 为何需要（批 64 / W-AV+W-AT）：LibreOffice **单次转换即清空系统剪贴板 6 次**（实测 ✗），
#: 而同一 `.doc` 会被**表格轨**（`table_recovery`）与**富对象轨**（`rich_object`）各转一次 ✗，
#: 跨轮次还会重复 ⇒ 缓存把"每次都要付的剪贴板窗口"降为"每份内容一次" ✓（护栏之外的**降频**手段）。
_CACHE_DIR = os.path.join(tempfile.gettempdir(), "rco_lo_cache")


def _cache_dir() -> str:
    """缓存目录（首次调用即建）。"""
    os.makedirs(_CACHE_DIR, exist_ok=True)
    return _CACHE_DIR


def cache_key(data: bytes) -> str:
    """内容键（sha256 hex）——**按字节**而非路径（路径会变、内容不变 ✓）。"""
    import hashlib

    return hashlib.sha256(data).hexdigest()


def _cache_get(data: bytes) -> bytes | None:
    """命中返回 docx 字节；未命中/不可读返回 None（缓存**永远不是故障源** ✓）。"""
    if os.environ.get("RCO_DISABLE_LO_CACHE", "").strip() in ("1", "true", "True"):
        return None
    try:
        p = os.path.join(_cache_dir(), cache_key(data) + ".docx")
        if os.path.exists(p):
            with open(p, "rb") as fh:
                b = fh.read()
            if b:
                LOG.info("[doc_convert] 转换缓存命中（%d 字节，免一次 LibreOffice 转换 ⇒ 免剪贴板窗口）",
                         len(b))
                return b
    except Exception:  # noqa: BLE001  缓存异常不阻断
        pass
    return None


def _cache_put(data: bytes, docx_path: str) -> None:
    """把转换产物写入缓存（失败静默 ✓）。"""
    try:
        if not docx_path or not os.path.exists(docx_path):
            return
        with open(docx_path, "rb") as fh:
            b = fh.read()
        if not b:
            return
        dst = os.path.join(_cache_dir(), cache_key(data) + ".docx")
        tmp = dst + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(b)
        os.replace(tmp, dst)      # 原子落盘（并发安全 ✓）
    except Exception:  # noqa: BLE001
        pass

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
    """headless 将 .doc 等旧格式单文件转为 docx（输出同目录），返回新路径或 None。

    ⚠️ **批 58：默认禁用（返回 None）**。

    取证（2026-10-10 活体监控 + A/B 对照）：**每一次 LibreOffice 转换都会清空系统剪贴板**
    （剪贴板序列号确定性 +1；持有者 `Idle(0)`/`soffice.bin`）⇒ 用户在办公软件中刚复制的
    内容被抹掉、Ctrl+V 粘贴不出来（= 用户报告的"复制粘贴失效/被抢占"）✗✗。已排除：剪贴板 API
    （0 处）、输入自动化（0 处）、COM（全门禁）、子进程闪窗（全隐藏）；`SAL_USE_VCLPLUGIN=svp`
    经 A/B 实测**无效**（Δ 仍为 1）✗。故默认**不做该转换**（与既有决策 L1「legacy 表格轨不提取」
    一致 ⇒ 无额外功能损失）。

    需要时置 `RCO_ALLOW_LO_CONVERT=1` 放行（会在日志中警示剪贴板影响）。
    """
    if os.environ.get("RCO_DISABLE_LO_CONVERT", "").strip() in ("1", "true", "True"):
        LOG.info("[doc_convert] RCO_DISABLE_LO_CONVERT=1 ⇒ 跳过 LibreOffice 转换: %s",
                 os.path.basename(doc_path))
        return None
    # 批 64：**先查缓存**（命中直接返回 ⇒ 不启动 LibreOffice ⇒ 无剪贴板窗口 ✓）
    try:
        with open(doc_path, "rb") as _fh:
            _raw = _fh.read()
    except OSError:
        _raw = b""
    _hit = _cache_get(_raw) if _raw else None
    if _hit:
        try:
            _dst = os.path.join(os.path.dirname(os.path.abspath(doc_path)),
                                os.path.splitext(os.path.basename(doc_path))[0] + ".docx")
            with open(_dst + ".tmp", "wb") as _fh2:
                _fh2.write(_hit)
            os.replace(_dst + ".tmp", _dst)
            return _dst
        except OSError:
            pass

    lo = find_libreoffice(bin)
    if not lo or not os.path.exists(doc_path):
        return None
    # 批 61（W-AH 根治）：**在独立窗口站内执行转换** —— Windows 的剪贴板按窗口站隔离，
    # 私有站内的 EmptyClipboard/SetClipboardData 只作用于该站 ⇒ 用户 WinSta0 剪贴板不受影响 ✓。
    # 无法隔离时**放弃转换**（fail-safe：宁可少解析 legacy 表格，也绝不清空用户剪贴板）。
    from std_lib.scraper_std import win_station

    if not win_station.station_available():
        LOG.info("[doc_convert] 无窗口站隔离能力 ⇒ 放弃转换（不裸跑）: %s", os.path.basename(doc_path))
        return None
    out_dir = os.path.dirname(os.path.abspath(doc_path))
    # 批 56：**隔离 profile + 全静默旗标**（不挂用户会话、不弹恢复/默认文档对话框、不抢焦点与剪贴板）
    _profile_url = _isolated_profile_url()
    _before = _pids_by_profile(_profile_url)   # 只认我方 profile（不触碰用户会话）
    # 批 61 第二道机制：**剪贴板保全护栏**。实证：LO 自身会重新挂回 WinSta0（窗口站不足以约束它 ✗），
    # 故在转换外层快照用户剪贴板（文本/文件列表）并在转换后**原样恢复** ⇒ 用户内容永不丢失 ✓；
    # 未被保全的格式会在日志明示（不静默丢）。置 `RCO_DISABLE_CLIPBOARD_GUARD=1` 可关闭护栏。
    from std_lib.scraper_std import clipboard_guard

    _snap = clipboard_guard.snapshot()
    LOG.info("[doc_convert] 转换前剪贴板快照：%s", clipboard_guard.describe(_snap))
    # 批 65（W-AT 残余加固）：**用户活动感知让路** —— 用户正在用键鼠时**不开始**转换，
    # 直接对准"用户复制的那一刻被打断"的实际场景（LO 转换一旦开始无法安全中止 ✗，
    # 故唯一安全的让路是"不开始"）；退避有上限（`RCO_QUIET_MAX_WAIT`，默认 60s），
    # 且 API 不可用时 **fail-open**（绝不阻塞管线 ✓）。开关与批 58 让路共用 `RCO_YIELD_TO_USER`。
    from std_lib.scraper_std import user_activity

    _waited = user_activity.wait_for_quiet_window()
    if _waited > 0.05:
        LOG.info("[doc_convert] 用户前台活跃 ⇒ 已让路等待 %.1fs（quiet=%s；RCO_YIELD_TO_USER=0 可关）",
                 _waited, user_activity.QUIET_SECONDS_DEFAULT)
    _seq0 = win_station.global_clipboard_seq()  # 生产侧自审计：转换前后全局剪贴板序列号
    try:
        rc, _out, _err, _info = win_station.isolated_run(
            [lo, "--headless", "--norestore", "--nodefault", "--nologo", "--nolockcheck",
             "-env:UserInstallation=" + _profile_url,
             "--convert-to", "docx", "--outdir", out_dir, doc_path],
            timeout=timeout)
    except Exception as e:  # noqa: BLE001  隔离执行异常：fail-safe 放弃
        LOG.warning("[doc_convert] 隔离执行异常，放弃转换: %s", e)
        _gr0 = clipboard_guard.restore(_snap)   # 异常路径同样恢复（护栏必须闭环 ✓）
        LOG.info("[doc_convert] 护栏恢复（异常路径）：%s", _gr0)
        return None
    finally:
        # 批 57：**只清理命令行含我方隔离 profile 的进程**（精确归属）——
        # 绝不使用 PID 差集：那会误杀用户自己的 LibreOffice，并使其剪贴板内容失效。
        _leaked = _pids_by_profile(_profile_url) - _before
        if _leaked:
            _n = _kill_pids(_leaked)
            LOG.info("清理我方遗留 soffice %d 个（隔离 profile 精确归属）", _n)
        _gr = clipboard_guard.restore(_snap)   # 批 61：**原样恢复**用户剪贴板内容（保住用户数据 ✓）
        LOG.info("[doc_convert] 护栏恢复：%s", _gr)
        _delta = win_station.global_clipboard_seq() - _seq0
        if _delta:
            LOG.info("[doc_convert] 转换期间全局剪贴板被改写 Δ=%d（已知：LO 会挂回 WinSta0）⇒ **已由护栏原样恢复**", _delta)
    if rc == win_station.RC_NO_ISOLATION:
        LOG.info("[doc_convert] 窗口站隔离未建立 ⇒ 放弃转换: %s", _info.get("note", ""))
        return None
    if rc != 0:
        LOG.info("[doc_convert] 转换返回码 %d（winsta=%s）", rc, _info.get("winsta", ""))
        return None
    base = os.path.splitext(os.path.basename(doc_path))[0]
    conv = os.path.join(out_dir, base + ".docx")
    _cache_put(_raw, conv)   # 批 64：转换成功 ⇒ 写入缓存（下次免转换 ⇒ 免剪贴板窗口 ✓）
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
