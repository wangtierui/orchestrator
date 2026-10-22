# -*- coding: utf-8 -*-
"""tests/test_verify_quota_incremental.py — 时效核验的**配额安全 + 增量**守卫（T3）。

两个真实缺陷（2026-10-22 实测定位）
===================================
**(1) 配额耗尽会污染 SSOT 90 天**（正确性）
`verify_missing.run_source` 原先对"查询失败 / 未执行"的候选一律
`mark_checked(status="pending", vsource="北大法宝（无同名命中，维持原判定）")`，
且 `last_checked_at` 刷新为**当前时刻**。后果：
  · 语义被篡改 —— "没查成"被记成"查了、无同名命中"；
  · 这些记录此后 90 天内被判定为**新鲜**，**不再优先重试**；
  · 门禁又因"非权威来源"跳过它们 ⇒ **一次配额耗尽即可静默打穿覆盖率**，
    而且从门禁上看不出任何异常（这正是 TD-21 里 948 条 placeholder 的来源之一）。

**(2) 每次都从同一段开始**（效率/公平性）
原实现按 cleaned CSV 的自然顺序（≈入库顺序）查；配额一停，**尾部记录永远轮不到**。

**(3) 全局 spawn 包装器静默吞掉子进程输出**（可观测性）
`proc.install_global_defaults()` 给所有 spawn 注入 `CREATE_NO_WINDOW`；它会让子进程
挂到**新的隐藏控制台**，父进程重定向到文件/管道时子输出**全部丢失**。
实证：`cli.py timeliness verify --dry-run` 跑 5.5s、rc=0、**零字节输出**。
修法：包装器在调用方未指定流时**显式继承**父 std（保留隐藏窗口的效果）。

守卫六组
--------
A 失败/未执行 ⇒ **不写 state**（核心）
B 真实无命中（成功且 0 条）⇒ 仍写 pending（合法结论不能被一起取消）
C 优先级：从未核验 → 最久未核 → 新公布优先
D 预算截断 ⇒ `truncated` 且**不算 partial**（否则设了预算就永远 rc=2）
E 配额中止 ⇒ `partial` + `quota_stopped`，未执行项不写
F spawn 包装器：继承输出必须可见（回归）
"""
from __future__ import annotations

import os
import sys
import types

import pytest

pytestmark = pytest.mark.contract

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _args(**kw) -> types.SimpleNamespace:
    base = {"dry_run": False, "probe": 0, "workers": 1, "retry_failed": False,
            "token_file": "", "max_items": 0}
    base.update(kw)
    return types.SimpleNamespace(**base)


def _cand(title: str, docno: str = "", date: str = "") -> dict:
    return {"title": title, "document_number": docno, "publish_date": date,
            "timeliness_status": "", "source": "gov"}


@pytest.fixture()
def vm(tmp_path, monkeypatch):
    """把 run_source 依赖全部替换为可控桩（不触网、不写生产）。"""
    from modules.regulatory_scrapers.timeliness_review import verify_missing as VM

    recorded = {"mark": [], "saved": 0, "queries": None}

    monkeypatch.setattr(VM, "OUT_DIR", str(tmp_path))
    monkeypatch.setattr(VM, "collect_missing",
                        lambda src: ([], list(VM._TEST_MISSING)))
    monkeypatch.setattr(VM.vstate, "load_state", lambda: dict(VM._TEST_STATE))
    monkeypatch.setattr(VM.vstate, "save_state",
                        lambda s: recorded.__setitem__("saved", recorded["saved"] + 1))

    def _mark(docno="", title="", status="", replacement="", vsource="", state=None):
        recorded["mark"].append({"title": title, "docno": docno,
                                 "status": status, "vsource": vsource})
        return (state or {}), False, f"k:{title}"

    monkeypatch.setattr(VM.vstate, "mark_checked", _mark)
    monkeypatch.setattr(VM.pk, "find_cli", lambda *a, **k: ["node"])
    monkeypatch.setattr(VM.pk, "load_token", lambda *a, **k: "TOKEN")
    monkeypatch.setattr(VM.pk, "load_token_any", lambda *a, **k: ("TOKEN", "p"))
    monkeypatch.setattr(VM.pk, "judge_candidate",
                        lambda data, c: ("valid", "", "北大法宝", "ok"))

    def _plan(missing):
        plan = [{"key": c["title"], "title": c["title"],
                 "docno": c["document_number"]} for c in missing]
        return plan, {i: plan[i] for i in range(len(plan))}

    monkeypatch.setattr(VM.pk, "build_query_plan", _plan)

    def _exec(plan, cli, token, ckpt, workers=2, retry_failed=False, pause=0.2,
              log=print, meta=None):
        recorded["queries"] = [p["key"] for p in plan]
        done, meta_upd = VM._TEST_DONE_FN(plan)
        if meta is not None:
            meta.update(meta_upd or {})
        return done

    monkeypatch.setattr(VM.pk, "execute_queries", _exec)
    VM._TEST_STATE = {}
    VM._TEST_MISSING = []
    VM._TEST_DONE_FN = lambda plan: ({}, {})
    return VM, recorded


class TestFailureDoesNotPollute:
    """A. 查询失败 / 未执行 ⇒ **不写 state**。"""

    def test_failed_query_writes_nothing(self, vm) -> None:
        VM, rec = vm
        VM._TEST_MISSING = [_cand("甲文件", "甲〔2026〕1号")]
        # 失败：message 非"成功"
        VM._TEST_DONE_FN = lambda plan: (
            {p["key"]: {"message": "失败: 90001 积分用尽", "data": []} for p in plan},
            {"stopped": True, "reason": "quota", "attempted": 1, "remaining": 0})

        out = VM.run_source("gov", _args())
        assert rec["mark"] == [], (
            "配额失败被写进 SSOT（会伪装成'已核验无命中'并冻结 90 天）")
        assert out["fail"] == 1
        assert out["status"] == VM.S_PARTIAL, "有失败必须是 partial（可重试）"
        assert out["quota_stopped"] is True

    def test_not_attempted_writes_nothing(self, vm) -> None:
        VM, rec = vm
        VM._TEST_MISSING = [_cand("乙文件", "乙〔2026〕1号")]
        # 完全未执行（done 为空，meta 未停）
        VM._TEST_DONE_FN = lambda plan: ({}, {})

        out = VM.run_source("gov", _args())
        assert rec["mark"] == [], "未执行项被当成结论写入"
        assert out["not_attempted"] == 1
        assert out["status"] == VM.S_SUCCESS, "仅'没跑到'不算失败（下轮续）"


class TestRealNoMatchStillRecorded:
    """B. 真实无命中（成功 + 0 条）仍须登记 pending。"""

    def test_zero_hit_marks_pending(self, vm) -> None:
        VM, rec = vm
        VM._TEST_MISSING = [_cand("丙文件", "丙〔2026〕1号")]
        VM._TEST_DONE_FN = lambda plan: (
            {p["key"]: {"message": "成功", "data": []} for p in plan}, {})

        out = VM.run_source("gov", _args())
        assert len(rec["mark"]) == 1, "真实无命中未登记（会导致无限重查）"
        assert rec["mark"][0]["status"] == "pending"
        assert "无同名命中" in rec["mark"][0]["vsource"]
        assert out["nomatch"] == 1
        assert out["status"] == VM.S_SUCCESS

    def test_rule_judgement_keeps_original(self, vm) -> None:
        VM, rec = vm
        VM._TEST_MISSING = [_cand("丁文件", "丁〔2026〕1号")]
        VM._TEST_DONE_FN = lambda plan: (
            {p["key"]: {"message": "成功",
                        "data": [{"title": "x"}]} for p in plan}, {})
        VM.pk.judge_candidate = lambda data, c: ("valid", "", "规则判断", "n")

        out = VM.run_source("gov", _args())
        assert rec["mark"][0]["status"] == "pending"
        assert out["nomatch"] == 1


class TestPriority:
    """C. 优先级排序。"""

    def test_never_verified_first_then_oldest(self, vm) -> None:
        VM, rec = vm
        from std_lib.common_lib import timeliness_ssot as S

        # 三条：A 有记录且较新；B 从未核验；C 有记录但很旧
        VM._TEST_STATE = {
            "doc:甲20261": {"status": "valid", "last_checked_at": "2026-10-20 00:00:00",
                            "verification_source": "北大法宝"},
            "doc:丙20261": {"status": "valid", "last_checked_at": "2026-01-01 00:00:00",
                            "verification_source": "北大法宝"},
        }
        VM._TEST_MISSING = [_cand("甲文件", "甲〔2026〕1号", "2026-10-01"),
                            _cand("乙文件", "乙〔2026〕1号", "2026-01-01"),
                            _cand("丙文件", "丙〔2026〕1号", "2026-05-01")]
        VM._TEST_DONE_FN = lambda plan: ({}, {})
        VM.run_source("gov", _args())
        order = rec["queries"]
        assert order[0] == "乙文件", f"从未核验的应最先，实际顺序 {order}"
        assert order[1] == "丙文件", "其次应是最久未核验"
        assert order[2] == "甲文件"
        del S


class TestBudget:
    """D. 预算截断：不算失败。"""

    def test_budget_truncates_and_stays_success(self, vm) -> None:
        VM, rec = vm
        VM._TEST_MISSING = [_cand(f"文件{i}", f"某〔2026〕{i}号", "2026-01-01")
                            for i in range(10)]
        VM._TEST_DONE_FN = lambda plan: (
            {p["key"]: {"message": "成功", "data": []} for p in plan}, {})

        out = VM.run_source("gov", _args(max_items=3))
        assert len(rec["queries"]) == 3, "预算未生效"
        assert out["truncated"] == 7
        assert out["status"] == VM.S_SUCCESS, (
            "设了预算就永远 partial/rc=2 ⇒ 预算变成'永远失败'")
        assert out["fail"] == 0


class TestQuotaStopReporting:
    """E. 配额中止的摘要必须可辨。"""

    def test_quota_stop_fields(self, vm) -> None:
        VM, _rec = vm
        VM._TEST_MISSING = [_cand("戊文件", "戊〔2026〕1号")]
        VM._TEST_DONE_FN = lambda plan: (
            {}, {"stopped": True, "reason": "quota", "attempted": 0, "remaining": 1})
        out = VM.run_source("gov", _args())
        assert out["quota_stopped"] is True
        assert out["stop_reason"] == "quota"
        assert out["status"] == VM.S_PARTIAL


class TestSpawnStdio:
    """F. 全局 spawn 包装器不得吞掉子进程输出（回归）。"""

    def test_inherited_output_visible(self, capfd) -> None:
        from bootstrap import bootstrap

        bootstrap("all")
        import subprocess

        subprocess.run([sys.executable, "-c", 'print("X-INHERIT-OK")'], timeout=120,
                       check=False)
        out = capfd.readouterr().out
        assert "X-INHERIT-OK" in out, (
            "子进程输出被 CREATE_NO_WINDOW 吞掉（父进程重定向时完全看不到）")

    def test_capture_still_works(self) -> None:
        from bootstrap import bootstrap

        bootstrap("all")
        import subprocess

        r = subprocess.run([sys.executable, "-c", "print(42)"],
                           capture_output=True, text=True, timeout=120)
        assert r.stdout.strip() == "42"
        assert subprocess.check_output(
            [sys.executable, "-c", "print(43)"], text=True, timeout=120).strip() == "43"
