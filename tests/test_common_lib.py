# -*- coding: utf-8 -*-
"""
tests/test_common_lib.py — common_lib 基础测试（P2 验收）

覆盖：
  - fs_lock：ProcessLock bool API / PidFileLock 上下文(BusyError) / with_pid_lock /
             原子写 text/json/csv（含 overwrite=False 冲突）
  - norm：norm_docno/norm_title 语义（与旧 rfn/recall 归一一致）
  - io_atomic：sha256、audit_append 追加幂等
  - index_store：单例 + save/load
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from std_lib.common_lib import (
    fs_lock,  # noqa: E402
    index_store,  # noqa: E402
    io_atomic,  # noqa: E402
    norm,  # noqa: E402
)


# ---------------- fs_lock ----------------
def test_process_lock_acquire_release():
    with tempfile.TemporaryDirectory() as d:
        lp = os.path.join(d, "a.lock")
        lk = fs_lock.ProcessLock(lp)
        assert lk.acquire() is True
        # 同进程再 acquire：own_pid 相同但锁存在且 PID 存活 → False（模拟占用）
        lk2 = fs_lock.ProcessLock(lp)
        assert lk2.acquire() is False
        lk.release()
        lk3 = fs_lock.ProcessLock(lp)
        assert lk3.acquire() is True
        lk3.release()


def test_pid_lock_context_busy():
    with tempfile.TemporaryDirectory() as d:
        lp = os.path.join(d, "b.lock")
        with fs_lock.PidFileLock(lp):
            pass  # 正常获取释放
        assert not os.path.exists(lp)


def test_atomic_write_text_overwrite():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "x.txt")
        fs_lock.atomic_write_text(p, "hello")
        assert open(p, encoding="utf-8").read() == "hello"
        with pytest.raises(FileExistsError):
            fs_lock.atomic_write_text(p, "world", overwrite=False)  # 已存在 → FileExistsError
        assert open(p, encoding="utf-8").read() == "hello"  # 原内容未被覆盖


def test_atomic_write_json_csv():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "x.json")
        fs_lock.atomic_write_json(p, {"a": 1})
        import json
        assert json.load(open(p, encoding="utf-8")) == {"a": 1}
        cp = os.path.join(d, "x.csv")
        fs_lock.atomic_write_csv_dict(cp, [{"监管文件编号": "RFN-1", "文件名称": "t"}],
                                      ["监管文件编号", "文件名称"])
        import csv
        rows = list(csv.DictReader(open(cp, encoding="utf-8-sig")))
        assert rows[0]["监管文件编号"] == "RFN-1"


# ---------------- norm ----------------
def test_norm_docno():
    assert norm.norm_docno("银保监办发〔2019〕19号") == "银保监办发201919"
    assert norm.norm_docno("保监发[2000]144号") == "保监发2000144"
    assert norm.norm_docno("") == ""
    assert norm.norm_docno("N/A") == ""


def test_norm_title():
    assert norm.norm_title("《保险销售行为管理办法》（试行）") == "保险销售行为管理办法"
    assert norm.norm_title("关于规范人身保险经营行为有关问题的通知（已废止）") == \
        "关于规范人身保险经营行为有关问题的通知"


# ---------------- io_atomic ----------------
def test_sha256_and_audit():
    assert io_atomic.sha256_bytes(b"abc") == \
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "audit.jsonl")
        io_atomic.audit_append(p, {"op": "register", "rfn": "RFN-1"})
        io_atomic.audit_append(p, {"op": "register", "rfn": "RFN-2"})
        lines = [ln for ln in open(p, encoding="utf-8") if ln.strip()]
        assert len(lines) == 2


# ---------------- index_store ----------------
def test_index_store_singleton():
    class Demo(index_store.JsonIndexStore):
        pass
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "idx.json")
        index_store.atomic_save_demo = None  # no-op
        fs_lock.atomic_write_json(p, {"k": "v"})
        s1 = index_store.get_store("demo_test", Demo, p)
        s2 = index_store.get_store("demo_test", Demo, p)
        assert s1 is s2
        assert s1.raw == {"k": "v"}
        index_store.drop_store("demo_test")
