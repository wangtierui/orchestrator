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

import ctypes
import inspect
import os
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


def _inject_inherited_stdio(kw: dict[str, Any]) -> None:
    """把父进程的 stdin/stdout/stderr **显式**传给子进程（就地修改 `kw`）。

    ⚠️ 为什么必须这么做（2026-10-22 实测定位，问题项 P-2）
    ----------------------------------------------------
    `CREATE_NO_WINDOW` 会给子进程创建**一个新的隐藏控制台**；此时若不显式指定
    标准流，子进程的 stdout/stderr 指向那个隐藏控制台，而**不是**父进程的
    文件/管道 ⇒ **子进程输出全部丢失**。

    实测矩阵（父进程重定向到文件）：

    | 组合 | 子进程输出 |
    |---|---|
    | 裸 `subprocess.run` | ✅ 可见 |
    | 仅 `STARTUPINFO(SW_HIDE)` | ✅ 可见 |
    | 仅 `CREATE_NO_WINDOW` | ❌ **全丢** |
    | `CREATE_NO_WINDOW` + `STARTUPINFO(SW_HIDE)` | ❌ **全丢** |
    | `CREATE_NO_WINDOW` + `stdout=sys.stdout` | ✅ 可见 |

    影响面：`install_global_defaults()` 把 `run/Popen/check_output/check_call/call`
    全部包了一层 ⇒ **任何"不捕获输出"的 spawn 都会静默丢日志**。
    实证：`cli.py timeliness verify --source gov --dry-run` 跑 5.5s、rc=0、
    **零字节输出**（同一命令直接跑脚本则有 897 字节）。

    为什么不干脆去掉 `CREATE_NO_WINDOW`：它解决的是"每次 spawn 闪一个黑框"，
    在无控制台的 `pythonw` 场景下仍然必要。所以保留它，改为**显式继承**标准流。

    跳过条件（任一即不注入，避免与调用方意图冲突）：
      · 调用方已显式给了该流（含 `subprocess.PIPE`/`DEVNULL`）；
      · 调用方用了 `capture_output=True`（与显式 stdout 互斥，会 ValueError）；
      · 父进程该流为 `None` 或没有真实 `fileno()`（如 `io.StringIO` 被测试替换）。
    """
    if kw.get("capture_output"):
        return
    for name in ("stdin", "stdout", "stderr"):
        if name in kw:
            continue
        stream = getattr(sys, name, None)
        if stream is None:
            continue
        try:
            stream.fileno()
        except (AttributeError, OSError, ValueError):
            continue
        kw[name] = stream


def run(argv, **kw: Any):
    """`subprocess.run` 的隐藏窗口版本（调用方显式参数优先）。"""
    for k, v in hidden_kwargs().items():
        kw.setdefault(k, v)
    _inject_inherited_stdio(kw)
    return subprocess.run(argv, **kw)


def popen(argv, **kw: Any):
    """`subprocess.Popen` 的隐藏窗口版本（调用方显式参数优先）。"""
    for k, v in hidden_kwargs().items():
        kw.setdefault(k, v)
    _inject_inherited_stdio(kw)
    return subprocess.Popen(argv, **kw)


def install_global_defaults() -> bool:
    """**全局默认**：为 `subprocess.run/Popen/check_output/check_call/call` 注入隐藏窗口参数（幂等）。

    返回是否本次完成安装。已安装或非 Windows 返回 False。

    ⚠️ **类形态包装（2026-10-13 修复，问题项 P-1）**：`Popen` 必须用**子类**包装而非函数——
    函数替换后 `class X(subprocess.Popen)` 会以 `TypeError: function() argument 'code' must be
    code, not str` 崩溃（type() 把函数当元类）。实证：stdlib `asyncio.windows_utils` 在导入时
    `class Popen(subprocess.Popen)` ⇒ `import pdb` → `import asyncio` 链全断（pytest 9 的
    debugging 插件在 configure 期无条件 `import pdb`，故全测试套件无法启动——此前旧版
    pytest 不 import pdb 才掩盖至今）。其余四个是函数，保持函数包装。
    """
    if not IS_WIN or getattr(subprocess, _HIDDEN_FLAG, False):
        return False
    for name in ("run", "Popen", "check_output", "check_call", "call"):
        orig = getattr(subprocess, name, None)
        if orig is None or getattr(orig, "_rco_hidden", False):
            continue
        if inspect.isclass(orig):
            setattr(subprocess, name, _wrap_class(orig))
        else:
            # `check_output` 的 stdout 由 stdlib 固定为 PIPE 且禁止外部再传
            # ⇒ 只对它关闭"继承标准流"注入（其余函数都要注入，见 _inject_inherited_stdio）。
            setattr(subprocess, name,
                    _wrap_func(orig, inherit_stdio=(name != "check_output")))
    setattr(subprocess, _HIDDEN_FLAG, True)
    return True


def _wrap_func(func: Any, inherit_stdio: bool = True) -> Any:
    """函数形态包装（run/call/check_output/check_call）。

    `inherit_stdio=False` 用于 **`check_output`**：stdlib 实现内部把 stdout 固定成
    PIPE 并显式校验"不允许再给 stdout"⇒ 注入会 `ValueError: stdout argument not
    allowed, it will be overridden`（实测踩到）。它本就不需要注入。
    """

    def wrapper(*args, **kwargs):
        for k, v in hidden_kwargs().items():
            kwargs.setdefault(k, v)
        # 全局包装器覆盖的正是"不捕获输出"的调用；若不显式继承标准流，
        # CREATE_NO_WINDOW 会让子进程的输出写进它的隐藏控制台 ⇒ 静默丢失。
        if inherit_stdio:
            _inject_inherited_stdio(kwargs)
        return func(*args, **kwargs)

    wrapper._rco_hidden = True  # type: ignore[attr-defined]
    wrapper.__doc__ = func.__doc__
    wrapper.__name__ = getattr(func, "__name__", "wrapper")
    return wrapper


def _wrap_class(cls: type) -> type:
    """类形态包装（Popen）：**子类化**以保持 `class X(subprocess.Popen)` 可继承（问题项 P-1）。"""

    class wrapper(cls):  # type: ignore[misc,valid-type]
        _rco_hidden = True  # type: ignore[attr-defined]

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            for k, v in hidden_kwargs().items():
                kwargs.setdefault(k, v)
            _inject_inherited_stdio(kwargs)
            super().__init__(*args, **kwargs)

    wrapper.__doc__ = cls.__doc__
    wrapper.__name__ = cls.__name__
    wrapper.__qualname__ = cls.__qualname__
    return wrapper


if __name__ == "__main__":  # 离线自检（不联网）
    assert isinstance(hidden_kwargs(), dict)
    assert callable(run) and callable(popen) and callable(install_global_defaults)
    print("proc.py 自检通过（IS_WIN=%s, CREATE_NO_WINDOW=0x%08X）" % (IS_WIN, CREATE_NO_WINDOW))

#: `BELOW_NORMAL_PRIORITY_CLASS`：低于常规优先级（子进程及其后代**继承**收敛）
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
#: `PROCESS_MODE_BACKGROUND_BEGIN`：Windows"后台模式"（CPU 与 **I/O** 优先级同时降到最低）
PROCESS_MODE_BACKGROUND_BEGIN = 0x00100000


def _polite_default() -> bool:
    """让路策略默认开启，可用 `RCO_YIELD_TO_USER=0` 关闭（批 58/W-AD 处置）。"""
    return os.environ.get("RCO_YIELD_TO_USER", "1").strip() not in ("0", "false", "False")


def child_kwargs(*, polite: bool | None = None) -> dict[str, Any]:
    """**子进程启动参数**：隐藏窗口 +（默认）**让路优先级**。

    取证：全仓 0 处剪贴板 API、0 处输入自动化 ⇒ 用户"复制粘贴被抢占"的可解释机制只剩
    **资源饱和导致办公软件卡顿**；故重活子进程默认**低于常规优先级**（整棵子进程树继承）⇒
    用户前台应用优先获得 CPU。可用 `RCO_YIELD_TO_USER=0` 关闭让路。
    """
    kw = hidden_kwargs()
    if IS_WIN and (polite if polite is not None else _polite_default()):
        kw["creationflags"] = int(kw.get("creationflags", 0)) | BELOW_NORMAL_PRIORITY_CLASS
    return kw


def set_self_background() -> bool:
    """把**本进程**切到 Windows 后台模式（CPU+I/O 双低优先级）。返回是否成功。

    供长时重活（采集/清洗）显式调用；只影响本进程与后代，**不动用户进程**。
    """
    if not IS_WIN:
        return False
    try:
        return bool(ctypes.windll.kernel32.SetPriorityClass(
            ctypes.windll.kernel32.GetCurrentProcess(), PROCESS_MODE_BACKGROUND_BEGIN))
    except Exception:  # noqa: BLE001
        return False
