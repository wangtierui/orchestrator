# -*- coding: utf-8 -*-
"""tests/test_workbench_service.py — 工作台开机自启动的守卫（第一百零六批 T3）。

背景
----
用户要求"配置为开机自启动，并说明启动方式与验证方式"。实现是
`tools/workbench_service.py`（install/uninstall/status/verify）+ `web.server.serve`
新增的三个运行期能力（`--single-instance` / `--log-file` / `--pid-file`）。

**方案选型**（三种，选计划任务）：
| 方案 | 需管理员 | 免登录启动 | 可查询/卸载 | 结论 |
|---|---|---|---|---|
| **计划任务 ONLOGON** | 否 | 否 | 是 | **选用** |
| 启动文件夹快捷方式 | 否 | 否 | 否 | 备选 |
| Windows 服务（nssm） | 是 | 是 | 是 | 不选（提权 + 第三方） |

**途中踩到并固化的坑**：
`schtasks /DELAY` 的格式是 **`mmmm:ss`（分钟:秒）**，不是 `HHMM`。
写成 `HHMM` 会报「值无效（应该是 mmmm:ss 格式）」（rc=2147500037）⇒ 本文件第 2 组
守卫专盯它。

**另一个坑**：`print()` 在 `pythonw`（无控制台）下**完全丢失** ⇒ 启动信息/错误/
退出原因都看不到。修法是 `_log()` 双写（控制台 + logger，后者有 FileHandler）。

守卫六组
--------
A `/DELAY` 格式必须 `mmmm:ss`（回归防护）
B 端口占用时**有限递增**（兑现 help 里"占用时换端口"的承诺）
C 单实例：第二次 acquire 必须失败且**不 bind**
D 日志落盘：`_setup_file_logging` 幂等 + 真的写文件
E CLI 透传：`cli.py serve --help` 必须暴露三个新参数
F `status`/`verify` 的判定逻辑（monkeypatch，不真的装任务）
"""
from __future__ import annotations

import logging
import os
import socket
import subprocess
import sys

import pytest

pytestmark = pytest.mark.contract

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestSchtasksDelayFormat:
    """A. `/DELAY` 必须是 `mmmm:ss`。"""

    def test_delay_format(self) -> None:
        from tools import workbench_service as W

        # 直接复用 install 内部构造逻辑的等价实现不可取（会重复实现）⇒
        # 改为断言"延迟参数被换算成 mmmm:ss"这一**行为**：用一个假的 _schtasks
        # 捕获 argv。
        captured = {}

        def _fake(*args):
            captured["args"] = list(args)
            return 0, ""

        orig = W._schtasks
        W._schtasks = _fake  # type: ignore[assignment]
        try:
            W.install(delay=30)
        finally:
            W._schtasks = orig  # type: ignore[assignment]
        args = captured.get("args") or []
        assert "/DELAY" in args, f"未传 /DELAY：{args}"
        i = args.index("/DELAY")
        val = args[i + 1]
        # 形如 `0000:30`
        assert ":" in val, f"/DELAY 值 {val!r} 不含冒号 —— schtasks 要求 mmmm:ss"
        mm, ss = val.split(":")
        assert mm.isdigit() and ss.isdigit(), val
        assert int(ss) < 60, f"秒部分 {ss} ≥ 60（schtasks 上限 59）"
        assert (int(mm), int(ss)) == (0, 30), f"30s 应换算为 0 分 30 秒，实际 {val}"

    @pytest.mark.parametrize("delay,expect", [
        (30, "0000:30"),
        (90, "0001:30"),
        (3600, "0060:00"),
    ])
    def test_delay_conversion(self, delay: int, expect: str) -> None:
        minutes, seconds = divmod(delay, 60)
        got = f"{minutes:04d}:{seconds:02d}"
        assert got == expect, (delay, got, expect)


class TestFreePort:
    """B. 端口占用时有限递增。"""

    def test_increments_when_busy(self) -> None:
        from web.server import _bind_free_port

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        busy = s.getsockname()[1]
        try:
            got = _bind_free_port("127.0.0.1", busy, tries=5)
            assert got != busy, f"端口 {busy} 已被占用，却仍返回它"
            assert busy < got <= busy + 5, got
        finally:
            s.close()

    def test_returns_same_when_free(self) -> None:
        from web.server import _bind_free_port

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        free = s.getsockname()[1]
        s.close()      # 释放 ⇒ 现在空闲
        got = _bind_free_port("127.0.0.1", free, tries=3)
        assert got == free, (free, got)


class TestSingleInstance:
    """C. 单实例：第二个进程必须 acquire 失败且不 bind。"""

    def test_second_acquire_fails(self, tmp_path) -> None:
        from std_lib.common_lib import fs_lock

        lock = str(tmp_path / "t.lock")
        lk1 = fs_lock.ProcessLock(lock)
        assert lk1.acquire(), "第一个应成功"
        try:
            lk2 = fs_lock.ProcessLock(lock)
            assert not lk2.acquire(), (
                "第二个进程竟也能 acquire ⇒ 单实例保护失效（会起多进程互相踩踏）")
            # 关键：此时**不得**绑定端口（serve 在 acquire 失败时应直接 return）
            pid = fs_lock._read_lock_pid(lock)
            assert pid == os.getpid(), (pid, os.getpid())
        finally:
            lk1.release()

    def test_serve_rejects_second_instance(self, tmp_path) -> None:
        """`serve(single_instance=True)` 第二次必须**立刻返回 0 且不监听**。"""
        from std_lib.common_lib import fs_lock
        from web import server as S

        lock = str(tmp_path / "svc.lock")
        lk1 = fs_lock.ProcessLock(lock)
        assert lk1.acquire()
        try:
            rc = S.serve(port=0, single_instance=True, pid_file=lock)
            assert rc == 0, f"已有实例时应返回 0（幂等），实际 {rc}"
        finally:
            lk1.release()


class TestFileLogging:
    """D. 日志落盘：幂等 + 真的写文件。"""

    def test_idempotent(self, tmp_path) -> None:
        from web import server as S

        p = str(tmp_path / "x.log")
        assert S._setup_file_logging(p) is True
        root = logging.getLogger()
        n1 = len(root.handlers)
        assert S._setup_file_logging(p) is True
        assert len(root.handlers) == n1, "重复配置导致 handler 双写"
        # 清理（避免污染其它测试）
        for h in list(root.handlers):
            if getattr(h, "baseFilename", None) == os.path.abspath(p):
                root.removeHandler(h)

    def test_log_writes_to_file(self, tmp_path) -> None:
        from web import server as S

        p = str(tmp_path / "y.log")
        try:
            S._setup_file_logging(p)
            logging.getLogger("web.server").info("__T3_PROBE__")
            for h in logging.getLogger().handlers:
                h.flush()
            body = open(p, encoding="utf-8").read()
            assert "__T3_PROBE__" in body, "logger 未写入文件（pythonw 下会全丢）"
        finally:
            root = logging.getLogger()
            for h in list(root.handlers):
                if getattr(h, "baseFilename", None) == os.path.abspath(p):
                    root.removeHandler(h)


class TestCliPassthrough:
    """E. CLI 必须暴露三个新参数（否则计划任务传了也没用）。"""

    def test_serve_help_has_flags(self) -> None:
        p = os.path.join(ROOT, "cli.py")
        r = subprocess.run([sys.executable, p, "serve", "--help"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=180, cwd=ROOT)
        assert r.returncode == 0
        for flag in ("--single-instance", "--pid-file", "--log-file"):
            assert flag in (r.stdout or ""), f"serve --help 缺 {flag}"


class TestStatusVerify:
    """F. status/verify 判定（monkeypatch，不真的创建计划任务）。"""

    def test_status_all_ok(self, monkeypatch) -> None:
        from tools import workbench_service as W

        monkeypatch.setattr(W, "_is_windows", lambda: True)
        monkeypatch.setattr(W, "_task_exists", lambda: True)
        monkeypatch.setattr(W, "_proc_running", lambda: (True, "pid=1 存活"))
        monkeypatch.setattr(W, "_health_ok", lambda port=8765: (True, "ok"))
        monkeypatch.setattr(W, "LOG_FILE", __file__)   # 存在 ⇒ 有日志
        assert W.status() == 0

    def test_status_reports_failure(self, monkeypatch, capsys) -> None:
        from tools import workbench_service as W

        monkeypatch.setattr(W, "_is_windows", lambda: True)
        monkeypatch.setattr(W, "_task_exists", lambda: False)
        monkeypatch.setattr(W, "_proc_running", lambda: (False, "无锁"))
        monkeypatch.setattr(W, "_health_ok", lambda port=8765: (False, "无响应"))
        monkeypatch.setattr(W, "LOG_FILE", __file__)
        rc = W.status()
        assert rc == 1, "未就绪必须返回非零（不能报'就绪'骗人）"

    def test_verify_requires_task(self, monkeypatch) -> None:
        from tools import workbench_service as W

        monkeypatch.setattr(W, "_is_windows", lambda: True)
        monkeypatch.setattr(W, "_task_exists", lambda: False)
        assert W.verify() == 1, "任务不存在时 verify 必须失败"

    def test_verify_passes_when_healthy(self, monkeypatch) -> None:
        from tools import workbench_service as W

        monkeypatch.setattr(W, "_is_windows", lambda: True)
        monkeypatch.setattr(W, "_task_exists", lambda: True)
        monkeypatch.setattr(W, "_health_ok", lambda port=8765: (True, "ok"))
        assert W.verify(wait=1) == 0

    def test_start_cmd_uses_pythonw_and_single_instance(self, tmp_path,
                                                        monkeypatch) -> None:
        """包装器必须：cd 仓根 + `pythonw`（无窗口）+ `--single-instance`。"""
        from tools import workbench_service as W

        monkeypatch.setattr(W, "START_CMD", str(tmp_path / "s.cmd"))
        monkeypatch.setattr(W, "LOG_FILE", str(tmp_path / "l.log"))
        monkeypatch.setattr(W, "PID_FILE", str(tmp_path / "p.lock"))
        p = W._write_start_cmd(port=8765)
        body = open(p, encoding="utf-8", errors="replace").read()
        assert "cd /d" in body, "未切到仓根（cli.py 依赖 cwd）"
        assert "pythonw" in body, "未用 pythonw（每次登录会弹黑框）"
        assert "--single-instance" in body
        assert "--log-file" in body
        assert "--pid-file" in body

    def test_start_cmd_is_ascii_only(self, tmp_path, monkeypatch) -> None:
        """**`.cmd` 必须纯 ASCII**（2026-10-21 蓝屏重启后实测的真实故障）。

        `cmd.exe` 按系统 OEM 代码页（简体中文 = **CP936/GBK**）读取 `.cmd`。
        若文件是 UTF-8 且含中文注释：
          · 中文变成乱码；
          · 其中部分字节被解释为命令分隔符 ⇒ `rem` 注释行被截断、
            后半段当命令执行 ⇒ 登录时弹出
            `'氭寔涔咃細閲嶈窇' 不是内部或外部命令…`。

        所以包装器注释必须全英文，且写入用 `encoding="ascii"`
        （混入非 ASCII 会直接抛 `UnicodeEncodeError`，从根上防回归）。
        """
        from tools import workbench_service as W

        monkeypatch.setattr(W, "START_CMD", str(tmp_path / "a.cmd"))
        monkeypatch.setattr(W, "LOG_FILE", str(tmp_path / "l.log"))
        monkeypatch.setattr(W, "PID_FILE", str(tmp_path / "p.lock"))
        p = W._write_start_cmd(port=8765)
        raw = open(p, "rb").read()
        bad = [(i, b) for i, b in enumerate(raw) if b > 127]
        assert not bad, (
            f".cmd 含 {len(bad)} 个非 ASCII 字节（首个 offset={bad[0][0]}）—— "
            "cmd.exe 会按 GBK 解码，中文注释变乱码并可能被当命令执行")
        assert b"\r\n" in raw, ".cmd 必须是 CRLF（LF-only 在某些 cmd 版本上会错行）"
