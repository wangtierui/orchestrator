# -*- coding: utf-8 -*-
"""win_station.py —— **独立窗口站隔离执行器**（批 61 / 遗留项 W-AH 的根治实现）。

## 问题与本批之前的现状
LibreOffice 在 Windows 上**即使传 `--headless`**，也会在启动/退出阶段调用 `EmptyClipboard()` **清空系统剪贴板**。
活体取证（2026-10-10，`reports/_tmp/ac1_clip_watch.py`）：
  · mof 采集运行中，系统剪贴板**序列号每 2.5~3.5 秒 +1**，持有者多为 `Idle(0)`（清空后无主），偶为 `soffice.bin`；
  · A/B 对照（`reports/_tmp/ae1_ab.py`）：**每次转换确定性 +1**（`SAL_USE_VCLPLUGIN=svp` 无效 ✗）。
用户症状：在其他办公软件中刚复制的内容被抹掉 ⇒ Ctrl+V 粘贴不出来（"复制粘贴失效/被抢占"）。
批 58 的临时处置是**默认禁用转换**（`doc_to_docx` 返回 None）——功能上有损（legacy `.doc` 表格不再解析）。
本模块即按用户指令"**必须恢复转换**"，以**窗口站隔离**根治。

## 为什么"独立窗口站"能根治
Windows 的**剪贴板是"每个窗口站（window station）一份"**，并非全局唯一：
  · 交互会话的用户进程都在 `WinSta0` 上，用户复制粘贴用的是 `WinSta0` 的剪贴板；
  · 在**另一个窗口站**里运行的进程，其 `EmptyClipboard()` / `SetClipboardData()` **只作用于该窗口站自己的剪贴板**，
    `WinSta0` 完全不受影响 ✓。

## 实现设计（质量优先的取舍，逐条说明）
1. **用"桌面继承"而非 `CreateProcessW`+`lpDesktop` 显式指定**：
   `STARTUPINFO.lpDesktop` 为 NULL 时，新进程**继承父进程的窗口站与桌面**（Win32 文档语义）。
   故只需让**助手进程**自建窗口站+桌面并 `SetProcessWindowStation`/`SetThreadDesktop`，
   再用普通 `subprocess` 启动 `soffice.exe` 即完成隔离。
   权衡：省去 `CreateProcessW` 手写 `STARTF_USESTDHANDLES`/句柄继承/`lpDesktop` 字面量（出错面更小 ✓），
   代价是**多一个短命助手进程**（毫秒级，可接受 ✓）；如未来需要，同一模块内加显式 `lpDesktop` 路径即可。
2. **隔离体是"短命助手进程"，绝不动调用方进程**：
   `SetProcessWindowStation`/`SetThreadDesktop` 都是**进程级/线程级**的，
   在长命采集进程里调用会让它**自己离开 `WinSta0`**（危险 ✗）。因此隔离只发生在助手内（本模块 `--exec`）。
3. **失败即不转换（fail-safe）**：
   无法建立隔离（非 Windows / 无权限 / `SetThreadDesktop` 失败）时，助手以退出码 `125` 明确失败；
   `doc_to_docx` 见此码即**放弃转换**并记日志。宁可少解析 legacy 表格，也**绝不能**破坏用户剪贴板。
4. **超时归助手所有**：助手对子进程实施 `timeout`（超时先 `kill` 子进程再退出，返回 124），
   父侧给助手留出余量（`timeout + 30s`）⇒ 不遗留孤儿进程、不误杀。
5. **与既有纪律一致**：助手与目标进程均**隐藏窗口**（`CREATE_NO_WINDOW`+`SW_HIDE`）并**低于常规优先级**
   （沿用 `proc.py`）；每次调用使用**唯一窗口站名**（零跨次状态，避免残留对象互相干扰）。
6. **可自证**：`selftest()` 用"助手内 `EmptyClipboard()` ⇒ **全局剪贴板序列号必须不变**"证明隔离成立；
   再配合 `reports/_tmp/ae1_ab.py` 的真实转换 A/B（期望 Δ=0）。

## 命令行（内部协议，供 `doc_to_docx` 调用）
    python win_station.py --exec-json "<json argv>" --out <file> --err <file> [--timeout 120] [--report <json>]
退出码：子进程原样 | 124 超时 | 125 隔离建立失败 | 126 参数错误
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from typing import Any

IS_WIN = sys.platform.startswith("win")

#: `WINSTA_ALL_ACCESS`（含 `WINSTA_CREATEDESKTOP`/`WINSTA_ACCESSCLIPBOARD` 等）
WINSTA_ALL_ACCESS = 0x000F037F
#: `DESKTOP_ALL_ACCESS`
DESKTOP_ALL_ACCESS = 0x000F01FF
#: 助手退出码语义
RC_TIMEOUT = 124
RC_NO_ISOLATION = 125
RC_BAD_ARGS = 126


# --------------------------------------------------------------------------- #
# Win32 绑定（仅 Windows 可用；其余平台整体降级为"无隔离"）
# --------------------------------------------------------------------------- #
def _bind() -> dict[str, Any]:
    import ctypes
    from ctypes import wintypes

    u32 = ctypes.WinDLL("user32", use_last_error=True)
    u32.CreateWindowStationW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                                         wintypes.DWORD, ctypes.c_void_p]
    u32.CreateWindowStationW.restype = wintypes.HANDLE
    u32.OpenWindowStationW.argtypes = [wintypes.LPCWSTR, wintypes.BOOL, wintypes.DWORD]
    u32.OpenWindowStationW.restype = wintypes.HANDLE
    u32.SetProcessWindowStation.argtypes = [wintypes.HANDLE]
    u32.SetProcessWindowStation.restype = wintypes.BOOL
    u32.CreateDesktopW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p,
                                   wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    u32.CreateDesktopW.restype = wintypes.HANDLE
    u32.SetThreadDesktop.argtypes = [wintypes.HANDLE]
    u32.SetThreadDesktop.restype = wintypes.BOOL
    u32.CloseDesktop.argtypes = [wintypes.HANDLE]
    u32.CloseDesktop.restype = wintypes.BOOL
    u32.CloseWindowStation.argtypes = [wintypes.HANDLE]
    u32.CloseWindowStation.restype = wintypes.BOOL
    u32.GetProcessWindowStation.restype = wintypes.HANDLE
    u32.GetThreadDesktop.argtypes = [wintypes.DWORD]
    u32.GetThreadDesktop.restype = wintypes.HANDLE
    u32.GetClipboardSequenceNumber.restype = wintypes.DWORD
    return {"u32": u32, "ctypes": ctypes, "wintypes": wintypes}


def station_available() -> bool:
    """当前环境**是否可能**建立隔离（仅 Windows 可能；真正可用性由助手实测）。"""
    return IS_WIN


def global_clipboard_seq() -> int:
    """**WinSta0（用户桌面）剪贴板序列号**：内容每变一次自增（用于验收/自检）。"""
    if not IS_WIN:
        return 0
    try:
        return int(_bind()["u32"].GetClipboardSequenceNumber())
    except Exception:  # noqa: BLE001
        return 0


# --------------------------------------------------------------------------- #
# 助手进程：建立窗口站+桌面 → 继承式启动目标进程 → 回收
# --------------------------------------------------------------------------- #
def _helper_main(argv: list[str]) -> int:
    """助手入口。隔离成功则返回子进程退出码；失败返回 `RC_NO_ISOLATION`。"""
    import argparse

    ap = argparse.ArgumentParser(description="独立窗口站隔离执行器（内部协议）")
    ap.add_argument("--exec-json", required=True, help="目标 argv 的 JSON 数组")
    ap.add_argument("--out", default="", help="stdout 落盘文件")
    ap.add_argument("--err", default="", help="stderr 落盘文件")
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--report", default="", help="隔离信息 JSON 落盘（站名/桌面名/子进程 pid）")
    a = ap.parse_args(argv)

    try:
        target = json.loads(a.exec_json)
        if not isinstance(target, list) or not target:
            print("win_station: --exec-json 必须是**非空 argv 数组**", file=sys.stderr)
            return RC_BAD_ARGS
    except Exception as e:  # noqa: BLE001
        print("win_station: --exec-json 解析失败: %s" % e, file=sys.stderr)
        return RC_BAD_ARGS

    if not IS_WIN:
        return RC_NO_ISOLATION

    try:
        b = _bind()
        u32 = b["u32"]
        # 唯一站名：避免跨次残留对象互相干扰（创建开销毫秒级）
        ws_name = "rco_lo_ws_%d_%d" % (os.getpid(), int(time.time() * 1000) % 100000)
        desk_name = "default"
        hwinsta = u32.CreateWindowStationW(ws_name, 0, WINSTA_ALL_ACCESS, None)
        if not hwinsta:
            print("win_station: CreateWindowStation 失败 err=%d" % b["ctypes"].get_last_error(),
                  file=sys.stderr)
            return RC_NO_ISOLATION
        # ⚠️ 顺序关键：先切**进程**窗口站，再建**桌面**（CreateDesktop 作用于当前窗口站）
        if not u32.SetProcessWindowStation(hwinsta):
            print("win_station: SetProcessWindowStation 失败 err=%d" % b["ctypes"].get_last_error(),
                  file=sys.stderr)
            return RC_NO_ISOLATION
        hdesk = u32.CreateDesktopW(desk_name, None, None, 0, DESKTOP_ALL_ACCESS, None)
        if not hdesk:
            print("win_station: CreateDesktop 失败 err=%d" % b["ctypes"].get_last_error(),
                  file=sys.stderr)
            return RC_NO_ISOLATION
        # `SetThreadDesktop` 失败 ⇒ 子进程会落到 WinSta0 的默认桌面 ⇒ **隔离不成立** ⇒ 必须失败退出
        if not u32.SetThreadDesktop(hdesk):
            print("win_station: SetThreadDesktop 失败 err=%d（隔离不成立，放弃执行）"
                  % b["ctypes"].get_last_error(), file=sys.stderr)
            return RC_NO_ISOLATION

        rc = _spawn_isolated(target, out=a.out, err=a.err, timeout=a.timeout)
        if a.report:
            try:
                with open(a.report, "w", encoding="utf-8") as f:
                    json.dump({"winsta": ws_name, "desktop": desk_name, "rc": rc,
                               "isolated": True}, f, ensure_ascii=False)
            except Exception:  # noqa: BLE001  报告写盘失败不影响执行
                pass
        try:
            u32.CloseDesktop(hdesk)
            u32.CloseWindowStation(hwinsta)
        except Exception:  # noqa: BLE001
            pass
        return rc
    except Exception as e:  # noqa: BLE001
        print("win_station: 隔离执行异常: %s" % e, file=sys.stderr)
        return RC_NO_ISOLATION


def _spawn_isolated(target: list[str], *, out: str, err: str, timeout: int) -> int:
    """在**当前（已是私有）窗口站**内启动目标进程（继承 ⇒ 目标也在该站 ⇒ 剪贴板隔离）。"""
    try:
        from proc import hidden_kwargs  # 同目录兄弟模块（脚本方式运行时 sys.path[0] 即本目录）
    except Exception:  # noqa: BLE001  包方式导入的回退
        try:
            from std_lib.scraper_std.proc import hidden_kwargs  # type: ignore[no-redef]
        except Exception:  # noqa: BLE001  极端回退：手工构造隐藏参数
            def hidden_kwargs() -> dict:  # type: ignore[misc]
                kw: dict = {}
                if IS_WIN:
                    kw["creationflags"] = 0x08000000      # CREATE_NO_WINDOW
                    try:
                        si = subprocess.STARTUPINFO()
                        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                        si.wShowWindow = 0
                        kw["startupinfo"] = si
                    except Exception:  # noqa: BLE001
                        pass
                return kw

    fo = open(out, "w", encoding="utf-8", errors="replace") if out else subprocess.DEVNULL
    fe = open(err, "w", encoding="utf-8", errors="replace") if err else subprocess.DEVNULL
    try:
        try:
            p = subprocess.run(target, stdout=fo, stderr=fe, timeout=timeout, **hidden_kwargs())
            return int(p.returncode)
        except subprocess.TimeoutExpired:
            # 助手持有超时：**先收拾子进程再退出**（不留孤儿）
            print("win_station: 子进程超时 %ds，已终止" % timeout, file=sys.stderr)
            return RC_TIMEOUT
    finally:
        for f in (fo, fe):
            try:
                if hasattr(f, "close"):
                    f.close()
            except Exception:  # noqa: BLE001
                pass


# --------------------------------------------------------------------------- #
# 父侧 API
# --------------------------------------------------------------------------- #
def isolated_run(argv: list[str], *, timeout: int = 120,
                 out_path: str = "", err_path: str = "",
                 report_path: str = "") -> tuple[int, str, str, dict]:
    """在**独立窗口站**内运行 `argv`。

    返回 `(rc, stdout, stderr, info)`：
      · `rc` = 目标退出码；`RC_NO_ISOLATION(125)` = **隔离未建立 ⇒ 调用方必须放弃该操作**（fail-safe）；
      · `RC_TIMEOUT(124)` = 目标超时（已由助手清理）；
      · `info` 含 `{"isolated": bool, "winsta": str, "note": str}`。
    """
    info: dict[str, Any] = {"isolated": False, "winsta": "", "note": ""}
    if not IS_WIN:
        info["note"] = "非 Windows：无窗口站隔离能力"
        return RC_NO_ISOLATION, "", info["note"], info
    import tempfile

    tmpdir = tempfile.mkdtemp(prefix="rco_ws_")
    out_p = out_path or os.path.join(tmpdir, "out.txt")
    err_p = err_path or os.path.join(tmpdir, "err.txt")
    rep_p = report_path or os.path.join(tmpdir, "report.json")
    helper = os.path.join(os.path.dirname(os.path.abspath(__file__)), "win_station.py")
    try:
        from proc import hidden_kwargs as _hk
    except Exception:  # noqa: BLE001
        try:
            from std_lib.scraper_std.proc import hidden_kwargs as _hk  # type: ignore[no-redef]
        except Exception:  # noqa: BLE001
            _hk = lambda: {}  # noqa: E731
    cmd = [sys.executable, helper, "--exec-json", json.dumps(list(argv), ensure_ascii=False),
           "--out", out_p, "--err", err_p, "--timeout", str(int(timeout)), "--report", rep_p]
    try:
        # 父侧超时留余量：助手自己会在 timeout 处收尾 ⇒ 父侧只兜底"助手自身卡死"
        p = subprocess.run(cmd, timeout=int(timeout) + 30, **(_hk() or {}))
        rc = int(p.returncode)
    except subprocess.TimeoutExpired:
        info["note"] = "助手进程超时（父侧兜底 %ds）" % (int(timeout) + 30)
        return RC_TIMEOUT, "", info["note"], info
    except Exception as e:  # noqa: BLE001
        info["note"] = "助手启动失败: %s" % e
        return RC_NO_ISOLATION, "", info["note"], info

    def _read(path: str) -> str:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                return f.read()
        except Exception:  # noqa: BLE001
            return ""

    try:
        with open(rep_p, encoding="utf-8") as f:
            rep = json.load(f)
        info.update({"isolated": bool(rep.get("isolated")), "winsta": str(rep.get("winsta") or "")})
    except Exception:  # noqa: BLE001
        pass
    if rc == RC_NO_ISOLATION:
        info["note"] = info["note"] or "窗口站隔离未建立（fail-safe：调用方应放弃操作）"
    return rc, _read(out_p), _read(err_p), info


# --------------------------------------------------------------------------- #
# 自检：证明"私有窗口站里的剪贴板操作**不影响**用户剪贴板"
# --------------------------------------------------------------------------- #
_SELFTEST_PY = (
    "import ctypes,sys;"
    "u=ctypes.WinDLL('user32',use_last_error=True);"
    "n=lambda: int(u.GetClipboardSequenceNumber());"
    "print('seq_before', n(), flush=True);"
    "u.EmptyClipboard();"          # 私有站内的清空：必须**不**影响 WinSta0
    "print('seq_after', n(), flush=True)"
)


def selftest() -> dict:
    """自检（**不触碰用户剪贴板内容**）：助手在私有站内 `EmptyClipboard()` ⇒ 全局序列号必须不变。"""
    res: dict[str, Any] = {"ok": False, "reason": ""}
    if not IS_WIN:
        res["reason"] = "非 Windows"
        return res
    seq0 = global_clipboard_seq()
    rc, out, err, info = isolated_run([sys.executable, "-c", _SELFTEST_PY], timeout=60)
    seq1 = global_clipboard_seq()
    res.update({"rc": rc, "out": out.strip(), "err": err.strip(), "info": info,
                "seq_before": seq0, "seq_after": seq1, "delta": seq1 - seq0})
    if rc == RC_NO_ISOLATION:
        res["reason"] = "隔离未建立：%s" % (info.get("note") or err.strip()[:120])
        return res
    if not info.get("isolated"):
        res["reason"] = "助手未报告 isolated=true"
        return res
    if seq1 != seq0:
        res["reason"] = "隔离站内的清空**仍改动了全局剪贴板**（Δ=%d）⇒ 隔离不成立" % (seq1 - seq0)
        return res
    res["ok"] = True
    res["reason"] = "私有站内 EmptyClipboard 未影响全局剪贴板（Δ=0）⇒ 隔离成立 ✓"
    return res


def _cli(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "--selftest":
        r = selftest()
        print(json.dumps(r, ensure_ascii=False, indent=1))
        return 0 if r["ok"] else 1
    return _helper_main(args)


if __name__ == "__main__":
    raise SystemExit(_cli())
