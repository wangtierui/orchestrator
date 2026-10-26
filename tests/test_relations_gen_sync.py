# -*- coding: utf-8 -*-
"""tests/test_relations_gen_sync.py — `relations gen` = 抽取 + 投影（一条命令）。

背景（2026-10-26 用户诉求）
--------------------------
以前要跑两条命令：`relations gen`（写 jsonl）+ `governance sync --apply`（投影进库）。
确认台读**库**，所以只跑前者会看到旧关系（实测：jsonl 4509 条、界面仍 5281 条）。
本守卫钉住三件事：

  A. 投影白名单语义：`project_metadata(only=("relation",))` **不得**动其它三表
     （四表全量替换语义下传空 documents 会清空公文表 —— 那是数据事故）
  B. `sync_relations()` 幂等且摘要一致（库 == jsonl）
  C. CLI 接线：`gen` 默认投影、`--no-sync` 可关、`dry-run` 一律不投影
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.contract

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture()
def temp_gov(tmp_path, monkeypatch):
    """把治理库指到临时文件（**绝不动生产库**）。"""
    from std_lib.common_lib import governance_store as gs

    db = tmp_path / "governance.db"
    monkeypatch.setenv("REG_ORCH_GOVERNANCE_DB", str(db))
    gs.init_db()
    yield gs


class TestProjectionWhitelist:
    """A. `only` 白名单语义。"""

    def test_only_relation_keeps_other_tables(self, temp_gov) -> None:
        gs = temp_gov
        # 必填列以 `_DOC_COLS` 为准：`doc_ref`(键) + `kind`（有 CHECK 约束）不可省
        docs = [{"doc_ref": "RFN-test0001", "kind": "regulatory",
                 "title": "样例公文", "source": "nfra"}]
        gs.project_metadata(documents=docs, theme_assigns=[], relations=[],
                            timeliness_rows=[])
        before = gs.table_count("document")
        assert before == 1

        # 只投影 relation ⇒ 公文表**必须原样**
        gs.project_metadata(documents=[], theme_assigns=[], relations=[],
                            timeliness_rows=[], only=("relation",))
        assert gs.table_count("document") == before, (
            "only=('relation',) 仍改动了 document 表 ⇒ 白名单没生效（会清空公文表）")

    def test_default_still_replaces_all(self, temp_gov) -> None:
        """不传 `only` 时保持既有语义（四表全量替换）—— 向后兼容。"""
        gs = temp_gov
        gs.project_metadata(
            documents=[{"doc_ref": "RFN-test0001", "kind": "regulatory",
                        "title": "x", "source": "nfra"}],
            theme_assigns=[], relations=[], timeliness_rows=[])
        gs.project_metadata(documents=[], theme_assigns=[], relations=[],
                            timeliness_rows=[])
        assert gs.table_count("document") == 0, "默认语义被改动（兼容性破坏）"


class TestSyncRelations:
    """B. `sync_relations()` 幂等 + 摘要一致。"""

    def test_ok_and_idempotent(self, temp_gov) -> None:
        from governance_sync import sync_relations

        first = sync_relations(apply=True)
        assert first["src_rows"] > 0, "关系事实源为空，守卫失去意义"
        assert first["ok"] is True, first
        assert first["db_rows"] == first["src_rows"]

        second = sync_relations(apply=True)
        assert second["ok"] is True and second["db_rows"] == first["db_rows"]

    def test_check_mode_does_not_write(self, temp_gov) -> None:
        from governance_sync import sync_relations

        out = sync_relations(apply=False)
        assert out["applied"] is False
        # 未投影 ⇒ 库 0 行、源非空 ⇒ ok=False（正是"库过期"的判定）
        assert out["src_rows"] > 0 and out["db_rows"] == 0
        assert out["ok"] is False


class TestCliWiring:
    """C. 命令接线（静态契约：漏掉收尾就等于没做）。"""

    @staticmethod
    def _src() -> str:
        return open(os.path.join(ROOT, "commands", "relations.py"),
                       encoding="utf-8").read()

    def test_gen_syncs_by_default(self) -> None:
        s = self._src()
        assert "from governance_sync import sync_relations" in s
        assert "out = sync_relations(apply=True)" in s
        assert "if a.dry_run or a.no_sync:" in s, "缺 dry-run/--no-sync 的短路"

    def test_no_sync_flag_exists(self) -> None:
        assert '"--no-sync"' in self._src()

    def test_sync_subcommand_exists(self) -> None:
        s = self._src()
        assert 'if sub == "sync":' in s
        assert "def _sync(" in s

    def test_help_mentions_combined_semantics(self) -> None:
        cli = open(os.path.join(ROOT, "cli.py"), encoding="utf-8").read()
        assert "gen=抽取+投影" in cli
