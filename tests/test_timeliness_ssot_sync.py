# -*- coding: utf-8 -*-
"""tests/test_timeliness_ssot_sync.py — 时效 SSOT 回填的守卫（第一百零八批 / TD-21）。

背景（一句话）
--------------
`verification_state.json`（SSOT）有权威结论，归属表仍是 `pending`，而**传播链只走
「verify 产台账 → timeliness sync 读台账」**；verify 一旦失败（实测：跑 4662s 后
pkulaw 配额耗尽 rc=2），权威结论就永远进不了归属表 ⇒ 层1 漂移 44 条且**无法自愈**。

修法三件套（本文件逐个守）：
  1. `std_lib.common_lib.timeliness_ssot`：匹配 + 权威性判据的**唯一实现**；
  2. `verification_state.apply_status_by_rfn`：按 RFN **精确**写归属表（不靠标题子串猜）；
  3. `timeliness sync --from-state`：把"能否回填"与"verify 是否成功"解耦。

另修一个**报告缺陷**：判据原返回 `problems[:40]` 且不给总条数 ⇒ TD-21 被登记成
"40 条"，实测真实是 **44 条**（切掉了 4 条）。"严重程度不可观测"本身是缺陷。

守卫六组
--------
A 匹配：文号优先（<5 字不用）、标题兜底、都不中 ⇒ 不算漂移
B 权威性：pending/uncertain、超 90 日、"无同名命中"/"规则判断" **一律不能作为依据**
C 归属表为空 ⇒ 只统计不回填（不擅自替人定级）
D 写入：按 RFN 精确、幂等、带备份、只动目标行
E 门禁：层1 判据必须走共享实现（不得再有内联副本）
F 报告：`problem_count` 必须反映**全量**（防退回 `[:40]` 截断）
"""
from __future__ import annotations

import csv
import os
from datetime import datetime, timedelta

import pytest

pytestmark = pytest.mark.contract

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATE = os.path.join(ROOT, "gates", "gate_timeliness_ssot.py")


def _ts(days_ago: float = 0) -> str:
    return (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")


def _code_only(path: str) -> str:
    """去掉注释与 docstring，只留**代码**。

    为什么必须这么做（实测踩到）：本文件的守卫想断言"不再出现 `problems[:40]`"，
    但修复说明的**注释**里正好引用了这个字符串（"原实现返回 `problems[:40]`"）
    ⇒ 直接扫原文会把自己的说明当成违规（两条守卫假失败）。
    **守卫要管的是代码，不是解释**；反过来也不该为了过守卫而删掉有价值的说明。
    """
    out: list[str] = []
    in_doc = False
    for line in open(path, encoding="utf-8").read().splitlines():
        s = line.strip()
        if in_doc:
            if '"""' in s or "'''" in s:
                in_doc = False
            continue
        if s.startswith('"""') or s.startswith("'''"):
            # 单行 docstring 直接跳过；多行则进入 doc 状态
            q = '"""' if s.startswith('"""') else "'''"
            if s.count(q) < 2:
                in_doc = True
            continue
        out.append(line.split("#", 1)[0])
    return "\n".join(out)


def _row(rfn: str, docno: str, title: str, status: str = "pending") -> dict:
    return {"监管文件编号": rfn, "发文字号": docno, "文件名称": title,
            "时效状态": status}


def _rec(status: str, days_ago: float = 1,
         vsrc: str = "北大法宝") -> dict:
    return {"status": status, "last_checked_at": _ts(days_ago),
            "verification_source": vsrc}


class TestMatch:
    """A. 匹配规则。"""

    def test_docno_first(self) -> None:
        from std_lib.common_lib import timeliness_ssot as S

        state = {"doc:银保监办发2022108": _rec("valid"),
                 "title:某个标题": _rec("repealed")}
        rep = S.drift_pairs([_row("RFN-1", "银保监办发〔2022〕108号", "某个标题")], state)
        assert len(rep["pairs"]) == 1
        assert rep["pairs"][0]["state"] == "valid", "应按文号命中，不是标题"
        assert rep["checked"] == 1

    def test_title_fallback(self) -> None:
        from std_lib.common_lib import timeliness_ssot as S

        state = {"title:关于加强监管的通知": _rec("amended")}
        rep = S.drift_pairs([_row("RFN-2", "", "关于加强监管的通知")], state)
        assert len(rep["pairs"]) == 1
        assert rep["pairs"][0]["state"] == "amended"

    def test_short_docno_not_used(self) -> None:
        """文号归一后 < 5 字 ⇒ 不拿它匹配（避免 `令第8` 这类噪声误配）。"""
        from std_lib.common_lib import timeliness_ssot as S

        state = {"doc:令第8": _rec("valid")}
        rep = S.drift_pairs([_row("RFN-3", "令第8", "无关标题")], state)
        assert rep["pairs"] == [], "过短文号被当匹配依据 ⇒ 可能改错行"
        assert rep["no_state"] == 1

    def test_no_state_not_drift(self) -> None:
        from std_lib.common_lib import timeliness_ssot as S

        rep = S.drift_pairs([_row("RFN-4", "某某〔2026〕1号", "标题")], {})
        assert rep["pairs"] == []
        assert rep["no_state"] == 1


class TestAuthority:
    """B. 权威性四条件。"""

    @pytest.mark.parametrize("status", ["pending", "uncertain"])
    def test_placeholder_not_used(self, status: str) -> None:
        from std_lib.common_lib import timeliness_ssot as S

        state = {"doc:某某20261": _rec(status)}
        rep = S.drift_pairs([_row("RFN-5", "某某〔2026〕1号", "t", "valid")], state)
        assert rep["pairs"] == []
        assert rep["skipped"]["placeholder"] == 1

    def test_stale_not_used(self) -> None:
        from std_lib.common_lib import timeliness_ssot as S

        state = {"doc:某某20261": _rec("valid", days_ago=200)}
        rep = S.drift_pairs([_row("RFN-6", "某某〔2026〕1号", "t")], state)
        assert rep["pairs"] == []
        assert rep["skipped"]["stale"] == 1

    @pytest.mark.parametrize("vsrc", ["无同名命中（维持原标注）", "规则判断"])
    def test_non_authoritative_source(self, vsrc: str) -> None:
        from std_lib.common_lib import timeliness_ssot as S

        state = {"doc:某某20261": _rec("valid", vsrc=vsrc)}
        rep = S.drift_pairs([_row("RFN-7", "某某〔2026〕1号", "t")], state)
        assert rep["pairs"] == [], "非权威结论不得覆盖归属表（曾致 1439 条假漂移）"
        assert rep["skipped"]["non_authoritative_source"] == 1

    def test_authoritative_is_drift(self) -> None:
        from std_lib.common_lib import timeliness_ssot as S

        state = {"doc:某某20261": _rec("valid")}
        rep = S.drift_pairs([_row("RFN-8", "某某〔2026〕1号", "t")], state)
        assert len(rep["pairs"]) == 1


class TestEmptyAttr:
    """C. 归属表为空 ⇒ 只统计，不回填。"""

    def test_empty_attr_not_filled(self) -> None:
        from std_lib.common_lib import timeliness_ssot as S

        state = {"doc:某某20261": _rec("valid")}
        rep = S.drift_pairs([_row("RFN-9", "某某〔2026〕1号", "t", "")], state)
        assert rep["pairs"] == [], "空值通常是'待人工定级'，擅自填=替人决策"
        assert rep["empty_attr"] == 1


class TestApplyByRfn:
    """D. 写入：精确、幂等、备份。"""

    @pytest.fixture()
    def attr(self, tmp_path, monkeypatch):
        from modules.regulatory_scrapers.timeliness_review import verification_state as V

        p = tmp_path / "attr.csv"
        with open(p, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["监管文件编号", "发文字号",
                                               "文件名称", "时效状态"])
            w.writeheader()
            w.writerow(_row("RFN-A", "甲〔2026〕1号", "甲文件", "pending"))
            w.writerow(_row("RFN-B", "乙〔2026〕1号", "乙文件", "pending"))
        monkeypatch.setattr(V, "CLASSIFIER_CSV", str(p))
        return V, p

    def test_writes_only_target(self, attr) -> None:
        V, p = attr
        changed, unmatched, _ = V.apply_status_by_rfn(
            [{"rfn": "RFN-A", "state": "valid"}], dry_run=False)
        assert (changed, unmatched) == (1, 0)
        rows = list(csv.DictReader(open(p, encoding="utf-8-sig")))
        assert rows[0]["时效状态"] == "valid"
        assert rows[1]["时效状态"] == "pending", "非目标行被改动"

    def test_idempotent(self, attr) -> None:
        V, _p = attr
        V.apply_status_by_rfn([{"rfn": "RFN-A", "state": "valid"}], dry_run=False)
        changed, _, _ = V.apply_status_by_rfn(
            [{"rfn": "RFN-A", "state": "valid"}], dry_run=False)
        assert changed == 0, "重复回填仍计为改动（不幂等）"

    def test_dry_run_no_write(self, attr) -> None:
        V, p = attr
        before = open(p, encoding="utf-8-sig").read()
        V.apply_status_by_rfn([{"rfn": "RFN-A", "state": "valid"}], dry_run=True)
        assert open(p, encoding="utf-8-sig").read() == before

    def test_backup_created(self, attr) -> None:
        V, p = attr
        V.apply_status_by_rfn([{"rfn": "RFN-A", "state": "valid"}], dry_run=False)
        assert list(p.parent.glob("attr.csv.bak_*")), "未留备份（不可回溯）"

    def test_unknown_rfn_counted(self, attr) -> None:
        V, _p = attr
        _c, unmatched, _ = V.apply_status_by_rfn(
            [{"rfn": "RFN-NOT-EXIST", "state": "valid"}], dry_run=False)
        assert unmatched == 1

    def test_invalid_status_rejected(self, attr) -> None:
        V, p = attr
        _c, unmatched, _ = V.apply_status_by_rfn(
            [{"rfn": "RFN-A", "state": "已废止"}], dry_run=False)
        assert unmatched == 1, "非受控枚举值不得写入"
        rows = list(csv.DictReader(open(p, encoding="utf-8-sig")))
        assert rows[0]["时效状态"] == "pending"


class TestGateUsesShared:
    """E. 门禁层1 必须走共享实现（不得再有内联副本）。"""

    def test_no_inline_matcher(self) -> None:
        src = _code_only(GATE)
        assert "drift_pairs" in src, "门禁未使用共享判据"
        for gone in ("st_docno", "_norm_docno", "_norm_title",
                     "def _is_fresh_rec"):
            assert gone not in src, (
                f"门禁里仍有内联判据 `{gone}` ⇒ 与回填命令必然分叉")

    def test_command_has_from_state(self) -> None:
        src = _code_only(os.path.join(ROOT, "commands", "timeliness.py"))
        assert "--from-state" in src, "缺少 SSOT 回填入口"
        assert "drift_pairs" in src, "命令未用共享判据"


class TestReportNotTruncated:
    """F. `problem_count` 必须是全量（防退回 `problems[:40]` 截断）。"""

    def test_gate_reports_full_count(self) -> None:
        src = _code_only(GATE)
        assert '"problem_count"' in src, "缺全量计数 ⇒ 严重程度不可观测"
        assert "problems_truncated" in src, "缺'已截断'标记 ⇒ 可能误以为看到全部"
        assert "problems[:40]" not in src, (
            "又退回 40 条硬截断（TD-21 的'40 条'正是这个切片的产物，实测真实 44）")
