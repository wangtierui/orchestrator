# -*- coding: utf-8 -*-
"""proc.py —— 子进程**隐藏执行**共享助手（批 57）。

## 为什么需要（用户实测症状 → 根因）
用户报告：任务运行期间，在**其他办公软件**中**复制粘贴失效或被抢占**，且批 56 修复后**仍在出现**。
批 57 取证：
  1. 可见窗口归属采样 ⇒ 唯一可疑窗口是**用户自己**的 WPS 表格（`et.exe` 打开 用户桌面（路径已脱敏）Desktop\\…xlsx`，
     父进程已退出）⇒ **不是我们驱动用户 WPS**；
  2. 我们的调度链上 COM 调用**均已门禁** ✓（未门禁的两处位于 `refactor/`，**不在本链**）；
  3. **全仓 `subprocess` 调用 54 处，0 处带隐藏旗标** ✗ ⇒ 在 Windows 上：
     · `soffice.exe` 是 **GUI 子系统启动器** —— 即使带 `--headless`，**进程创建阶段仍会建窗口** ✗；
     · 父进程无控制台（计划任务 / GUI 宿主）时，控制台子程序会**新建控制台窗口** ✗；
     · 二者都会**抢前台焦点** ⇒ 用户正在复制/粘贴或输入的窗口被打断 ✗。
  4. 批 56 我引入的 `soffice` 兜底清理用「**前后 PID 差集**」✗ ⇒ 若用户此刻打开/正在用 LibreOffice，
     会被**误杀** ⇒ **剪贴板内容由源进程持有，进程被杀即"复制粘贴失效"** ✗✗。

## 纪律（本批确立）
  · **一切子进程默认隐藏**：`install_global_defaults()` 在 `bootstrap.py` 引导点安装（唯一入口）；
  · 需要显式调用时用本模块 `run()/popen()`；**调用方显式传参优先**（`setdefault` 语义）；
  · 调试需要可见窗口时置 `RCO_ALLOW_VISIBLE_SUBPROCESS=1`；
  · **清理他人进程必须按"我方专属标记"精确归属**（见 `doc_convert._pids_by_profile`），
    禁止再使用裸 PID 差集强杀 ✗。
"""
from __future__ import annotations

import subprocess
import sys
from typing import Any

IS_WIN = sys.platform.startswith("win")
#: `CREATE_NO_WINDOW`：不创建控制台窗口（Windows 10+ 常量 0x08000000）
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

_HIDDEN_FLAG = "_rco_hidden_installed"


def hidden_kwargs() -> dict[str, Any]:
    """返回隐藏窗口所需的 `subprocess` 关键字（非 Windows 返回空 dict）。"""
    if not IS_WIN:
        return {}
    kw: dict[str, Any] = {"creationflags": CREATE_NO_WINDOW}
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0          # SW_HIDE：GUI 子系统程序（如 soffice.exe）不得显示窗口
        kw["startupinfo"] = si
    except Exception:  # noqa: BLE001  非 Windows 或属性缺失：只保留 creationflags
        pass
    return kw


def run(argv, **kw: Any):
    """`subprocess.run` 的隐藏窗口版本（调用方显式参数优先）。"""
    for k, v in hidden_kwargs().items():
        kw.setdefault(k, v)
    return subprocess.run(argv, **kw)


def popen(argv, **kw: Any):
    """`subprocess.Popen` 的隐藏窗口版本（调用方显式参数优先）。"""
    for k, v in hidden_kwargs().items():
        kw.setdefault(k, v)
    return subprocess.Popen(argv, **kw)


def install_global_defaults() -> bool:
    """**全局默认**：为 `subprocess.run/Popen/check_output/check_call/call` 注入隐藏窗口参数（幂等）。

    返回是否本次完成安装。已安装或非 Windows 返回 False。
    """
    if not IS_WIN or getattr(subprocess, _HIDDEN_FLAG, False):
        return False
    for name in ("run", "Popen", "check_output", "check_call", "call"):
        orig = getattr(subprocess, name, None)
        if orig is None or getattr(orig, "_rco_hidden", False):
            continue

        def _wrap(func):
            def wrapper(*args, **kwargs):
                for k, v in hidden_kwargs().items():
                    kwargs.setdefault(k, v)
                return func(*args, **kwargs)

            wrapper._rco_hidden = True      # type: ignore[attr-defined]
            wrapper.__doc__ = func.__doc__
            return wrapper

        setattr(subprocess, name, _wrap(orig))
    setattr(subprocess, _HIDDEN_FLAG, True)
    return True


if __name__ == "__main__":  # 离线自检（不联网）
    assert isinstance(hidden_kwargs(), dict)
    assert callable(run) and callable(popen) and callable(install_global_defaults)
    print("proc.py 自检通过（IS_WIN=%s, CREATE_NO_WINDOW=0x%08X）" % (IS_WIN, CREATE_NO_WINDOW))
