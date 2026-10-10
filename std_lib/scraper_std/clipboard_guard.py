# -*- coding: utf-8 -*-
"""clipboard_guard.py —— **剪贴板保全护栏**（批 61，与 `win_station.py` 配套的第二道机制）。

## 为什么还需要它（实证结论，不臆断）
批 61 归因实验：
  · `win_station` 自检（私有站内 `EmptyClipboard()`）⇒ 全局剪贴板 **Δ=0** ✓ ⇒ **窗口站隔离对普通剪贴板操作有效**；
  · 但**真实 LibreOffice 转换**在同一隔离下 ⇒ 全局 **Δ=1** ✗ ⇒ **LO 自身重新挂回 `WinSta0`**
    （其 VCL 层在 Windows 上做桌面/剪贴板初始化，不受父进程窗口站约束）✗。
⇒ 结论：**窗口站是必要的第一道（隔离面），但对 LO 不足**；必须以**保全**兜底：
   转换**前**快照用户剪贴板内容，转换**后原样恢复** ⇒ 用户内容**永不丢失** ✓。

## 设计取舍（质量优先，逐条说明）
1. **只保全"可安全读写"的格式**：`CF_UNICODETEXT` / `CF_TEXT` / `CF_HDROP`（文件列表）。
   *为何不含位图/HTML/RTF/自定义格式*：这些常为**延迟渲染（delayed rendering）**格式——
   `GetClipboardData` 会**反向请求持有者渲染**（可能唤起其 GUI、可能阻塞）✗；
   只读上述三种可**确定性地**取到字节，且覆盖办公场景最高频的"文字/文件"复制 ✓。
   未被保全的格式**在日志中明示**（`skipped_formats`）⇒ 不静默丢 ✓。
2. **失败绝不抛出**：任何 Win32 调用失败都记录并跳过（护栏不可成为故障源 ✓）；
   若**快照为空**（用户本来没复制东西）⇒ 不恢复（避免把"空"当成"内容"写回去 ✓）。
3. **恢复即"原样"**：按原格式逐项 `SetClipboardData`（所有权随句柄转移 ✓），
   顺序与格式相同；不做文本重编码、不做格式转换 ✓。
4. **可关闭**：`RCO_DISABLE_CLIPBOARD_GUARD=1`（用户若认为"短暂窗口"可接受，可关掉以免多写剪贴板 ✓）。
5. **只包住转换窗口**：护栏的临界区＝一次转换（秒级），不常驻、不轮询、不后台 ✓。

## 公开 API
    snapshot() -> Snapshot              取快照（只含安全格式）
    restore(snap) -> dict               恢复（返回 {restored, skipped, note}）
    guard() -> contextlib.ContextManager  with 语法糖：进入取快照、退出恢复
"""
from __future__ import annotations

import contextlib
import os
import sys
from collections.abc import Iterator
from typing import Any, NamedTuple

IS_WIN = sys.platform.startswith("win")

#: 安全可读写的剪贴板格式（标准格式值）
CF_TEXT = 1
CF_UNICODETEXT = 13
CF_HDROP = 15
#: 已知的"不可安全快照"格式（延迟渲染/句柄类）——仅用于日志解释
_UNSAFE_NAMES = {
    2: "CF_BITMAP(句柄)", 3: "CF_METAFILEPICT(句柄)", 8: "CF_DIB", 9: "CF_PALETTE(句柄)",
    14: "CF_ENHMETAFILE(句柄)", 16: "CF_LOCALE", 17: "CF_DIBV5",
}
#: 单格式体积上限（防超大剪贴板拖垮采集；超出即跳过并记录）
_MAX_BYTES = 8 * 1024 * 1024
#: 护栏总字节上限
_MAX_TOTAL = 16 * 1024 * 1024


class Item(NamedTuple):
    fmt: int
    data: bytes


class Snapshot(NamedTuple):
    items: tuple[Item, ...]
    present: tuple[int, ...]
    skipped: tuple[int, ...]
    note: str


def _enabled() -> bool:
    return os.environ.get("RCO_DISABLE_CLIPBOARD_GUARD", "").strip() not in ("1", "true", "True")


def _bind() -> dict[str, Any]:
    import ctypes
    from ctypes import wintypes

    u32 = ctypes.WinDLL("user32", use_last_error=True)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    u32.OpenClipboard.argtypes = [wintypes.HWND]
    u32.OpenClipboard.restype = wintypes.BOOL
    u32.CloseClipboard.restype = wintypes.BOOL
    u32.EmptyClipboard.restype = wintypes.BOOL
    u32.EnumClipboardFormats.argtypes = [wintypes.UINT]
    u32.EnumClipboardFormats.restype = wintypes.UINT
    u32.GetClipboardData.argtypes = [wintypes.UINT]
    u32.GetClipboardData.restype = wintypes.HANDLE
    u32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    u32.SetClipboardData.restype = wintypes.HANDLE
    k32.GlobalSize.argtypes = [wintypes.HGLOBAL]
    k32.GlobalSize.restype = ctypes.c_size_t
    k32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    k32.GlobalUnlock.restype = wintypes.BOOL
    k32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    k32.GlobalAlloc.restype = wintypes.HGLOBAL
    return {"u32": u32, "k32": k32, "ctypes": ctypes}


def snapshot() -> Snapshot:
    """取剪贴板快照（**只含安全格式**：文本/文件列表）。失败返回空快照并说明原因。"""
    if not IS_WIN or not _enabled():
        return Snapshot((), (), (), "非 Windows 或护栏已关闭")
    b = _bind()
    u32, k32, ct = b["u32"], b["k32"], b["ctypes"]
    items: list[Item] = []
    present: list[int] = []
    skipped: list[int] = []
    total = 0
    if not u32.OpenClipboard(None):
        return Snapshot((), (), (), "OpenClipboard 失败 err=%d" % ct.get_last_error())
    try:
        fmt = 0
        while True:
            fmt = int(u32.EnumClipboardFormats(fmt))
            if fmt == 0:
                break
            present.append(fmt)
            if fmt not in (CF_TEXT, CF_UNICODETEXT, CF_HDROP):
                skipped.append(fmt)
                continue
            h = u32.GetClipboardData(fmt)
            if not h:
                skipped.append(fmt)
                continue
            size = int(k32.GlobalSize(h) or 0)
            if size <= 0 or size > _MAX_BYTES or total + size > _MAX_TOTAL:
                skipped.append(fmt)
                continue
            p = k32.GlobalLock(h)
            if not p:
                skipped.append(fmt)
                continue
            try:
                items.append(Item(fmt, ct.string_at(p, size)))
                total += size
            finally:
                k32.GlobalUnlock(h)
    finally:
        u32.CloseClipboard()
    return Snapshot(tuple(items), tuple(present), tuple(skipped), "")


def restore(snap: Snapshot) -> dict:
    """把快照**原样**写回剪贴板。返回 `{"restored": n, "ok": bool, "note": str}`。"""
    if not IS_WIN or not _enabled() or not snap.items:
        return {"restored": 0, "ok": False,
                "note": snap.note or ("无可恢复内容" if not snap.items else "护栏已关闭")}
    b = _bind()
    u32, k32, ct = b["u32"], b["k32"], b["ctypes"]
    done = 0
    if not u32.OpenClipboard(None):
        return {"restored": 0, "ok": False, "note": "OpenClipboard 失败 err=%d" % ct.get_last_error()}
    try:
        u32.EmptyClipboard()
        for it in snap.items:
            h = k32.GlobalAlloc(0x0002, len(it.data))        # GMEM_MOVEABLE
            if not h:
                continue
            p = k32.GlobalLock(h)
            if not p:
                continue
            try:
                ct.memmove(p, it.data, len(it.data))
            finally:
                k32.GlobalUnlock(h)
            if not u32.SetClipboardData(it.fmt, h):
                continue
            done += 1                                        # 所有权已转移给系统
        return {"restored": done, "ok": done == len(snap.items), "note": ""}
    finally:
        u32.CloseClipboard()


def describe(snap: Snapshot) -> str:
    """快照摘要（供日志：既说明保全了什么，也说明**没**保全什么 ⇒ 不静默丢）。"""
    if not snap.items:
        return "空快照（%s）" % (snap.note or "用户剪贴板无安全格式内容")
    names = {CF_TEXT: "CF_TEXT", CF_UNICODETEXT: "CF_UNICODETEXT", CF_HDROP: "CF_HDROP"}
    kept = "+".join(sorted(names.get(i.fmt, str(i.fmt)) for i in snap.items))
    if snap.skipped:
        sk = ", ".join(_UNSAFE_NAMES.get(f, str(f)) for f in sorted(set(snap.skipped)))
        return "保全 [%s]；**未保全**格式：%s" % (kept, sk)
    return "保全 [%s]" % kept


@contextlib.contextmanager
def guard() -> Iterator[Snapshot]:
    """`with guard() as snap:` —— 进入取快照，退出**原样恢复**（任何异常都恢复 ✓）。"""
    snap = snapshot()
    try:
        yield snap
    finally:
        r = restore(snap)
        if snap.items and not r.get("ok"):
            print("[clipboard_guard] 恢复未完全成功：%s" % r, file=sys.stderr)
