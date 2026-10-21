# -*- coding: utf-8 -*-
"""tests/test_lock_pid_reuse.py — **陈旧锁 + PID 复用**的守卫（第一百零六批）。

事故（2026-10-21，蓝屏重启后实测）
----------------------------------
蓝屏重启后 `data/run_production_refresh.lock` 残留内容 `3992`。重启后 **PID 3992
已被 `svchost.exe`（AudioEndpointBuilder）复用** ⇒ `ProcessLock` 只校验
"PID 是否存在" ⇒ 判"锁被占用" ⇒ `cli.py run` **静默拒绝运行**，还提示
"陈旧锁可删除或等待 PID 退出"（那个 PID 永远不会退出）。

修法
----
锁文件从**裸 PID** 升级为**进程身份**：

```json
{"pid": 3992, "started": 1759..., "exe": "python.exe"}
```

`acquire()` 的陈旧判定扩到 4 条：① 内容损坏 ② PID 已死
③ **PID 被复用**（锁里的 `started` 与该 PID 的**真实创建时刻**相差 > 5s）
④ 超过 `max_age_sec`。

**向后兼容**：读到旧格式（裸 PID）时 `_read_lock_meta` 返 `None`
⇒ 退回"PID + 超龄"判定，**不会误抢占**正在运行的老进程的锁。

守卫五组
--------
A 正常互斥（同进程二次 acquire 失败）
B 旧格式（裸 PID）仍被尊重（不得抢占存活进程）
C **PID 复用被识别为陈旧**（本轮修复的核心）
D 正常 release 后他人可取
E 写入的是**身份元数据**（不是裸 PID）
"""
from __future__ import annotations

import json
import os

import pytest

pytestmark = pytest.mark.contract


def _lock(tmp_path, name: str = "t.lock"):
    from std_lib.common_lib import fs_lock

    return fs_lock.ProcessLock(str(tmp_path / name))


class TestNormalMutex:
    """A. 正常互斥。"""

    def test_second_acquire_fails(self, tmp_path) -> None:
        a = _lock(tmp_path, "a.lock")
        assert a.acquire() is True
        try:
            b = _lock(tmp_path, "a.lock")
            assert b.acquire() is False, "同进程二次 acquire 竟成功 ⇒ 互斥失效"
        finally:
            a.release()


class TestLegacyFormatHonored:
    """B. 旧格式（裸 PID）必须仍被尊重 —— 升级不能抢占正在跑的老进程。"""

    def test_plain_pid_lock_not_stolen(self, tmp_path) -> None:
        p = tmp_path / "legacy.lock"
        p.write_text(str(os.getpid()), encoding="utf-8")   # 旧格式 + 存活 PID
        lk = _lock(tmp_path, "legacy.lock")
        assert lk.acquire() is False, (
            "旧格式锁被误抢占 ⇒ 可能让两个进程同时改数据面")

    def test_plain_pid_dead_is_stale(self, tmp_path) -> None:
        p = tmp_path / "dead.lock"
        # 用一个几乎不可能存在的 PID + 旧格式
        p.write_text("999999", encoding="utf-8")
        lk = _lock(tmp_path, "dead.lock")
        assert lk.acquire() is True, "PID 已死却未判陈旧"


class TestPidReuse:
    """C. **PID 复用**必须被识别为陈旧（本轮事故的根因）。"""

    def test_reused_pid_is_stale(self, tmp_path) -> None:
        p = tmp_path / "reuse.lock"
        # 关键构造：PID 是**当前存活进程**，但 `started` 故意落后 1 小时
        # ⇒ 与真实创建时刻不符 ⇒ 说明"这个 PID 已经不是当初那个进程"
        p.write_text(json.dumps({
            "pid": os.getpid(),
            "started": __import__("time").time() - 3600,
            "exe": "someone_else.exe",
        }), encoding="utf-8")
        lk = _lock(tmp_path, "reuse.lock")
        assert lk.acquire() is True, (
            "PID 被复用却仍判'被占用' ⇒ 重启后主链会永远拒绝运行（实测事故）")

    def test_matching_identity_not_stolen(self, tmp_path) -> None:
        """身份**匹配**（同一进程）时不得抢占。"""
        from std_lib.common_lib import fs_lock

        p = tmp_path / "same.lock"
        started = fs_lock._proc_start_time(os.getpid())
        if started is None:
            pytest.skip("当前平台取不到进程创建时刻（仅 Windows 支持）")
        p.write_text(json.dumps({"pid": os.getpid(), "started": started,
                                 "exe": "python.exe"}), encoding="utf-8")
        lk = _lock(tmp_path, "same.lock")
        assert lk.acquire() is False, "身份匹配的锁被误抢占"


class TestRelease:
    """D. release 后可被他人获取。"""

    def test_release_then_others_can_take(self, tmp_path) -> None:
        a = _lock(tmp_path, "r.lock")
        assert a.acquire()
        a.release()
        b = _lock(tmp_path, "r.lock")
        assert b.acquire() is True
        b.release()


class TestIdentityPayload:
    """E. 写入的是身份元数据（不是裸 PID）。"""

    def test_payload_is_json_with_identity(self, tmp_path) -> None:
        lk = _lock(tmp_path, "p.lock")
        assert lk.acquire()
        try:
            raw = (tmp_path / "p.lock").read_text(encoding="utf-8").strip()
            assert raw.startswith("{"), f"仍写裸 PID：{raw!r}"
            d = json.loads(raw)
            assert d.get("pid") == os.getpid()
            assert isinstance(d.get("started"), (int, float))
            assert d.get("exe")
        finally:
            lk.release()

    def test_read_lock_pid_compatible(self, tmp_path) -> None:
        """`_read_lock_pid` 要能读两种格式（其它模块依赖它）。"""
        from std_lib.common_lib import fs_lock

        lk = _lock(tmp_path, "c.lock")
        assert lk.acquire()
        try:
            assert fs_lock._read_lock_pid(str(tmp_path / "c.lock")) == os.getpid()
        finally:
            lk.release()
        (tmp_path / "old.lock").write_text("12345", encoding="utf-8")
        assert fs_lock._read_lock_pid(str(tmp_path / "old.lock")) == 12345
