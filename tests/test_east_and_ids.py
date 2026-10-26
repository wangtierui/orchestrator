# -*- coding: utf-8 -*-
"""tests/test_east_and_ids.py — EAST 数据结构 / RFN 恒有值 / 标题清洗 / 个人令牌。

四件事的共同点：**都在"数据从哪儿来"这一层解决**，页面只负责展示。

  A. EAST 数据结构（T5）：产物可校验、接口分页正确、页面懒加载数据项
  B. RFN 恒有值（T3）：每条监管记录都有编号，且**入库号与派生号可分辨**
  C. 标题清洗（T4）：脏标题在**发布层**被清洗（不是列表页）
  D. 个人令牌（T6）：审计能落到人；共享口令必须显式标注"不可定位到人"
"""

from __future__ import annotations

import os
import sqlite3

import pytest

pytestmark = pytest.mark.contract

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _html() -> str:
    return open(os.path.join(ROOT, "web", "assets", "index.html"), encoding="utf-8").read()


class TestEastStructure:
    """A. EAST 数据结构产物与接口。"""

    def test_artifact_check(self) -> None:
        from interfaces import east_api
        from tools.build_east_structure import check

        assert east_api.available(), "产物缺失（运行 tools/build_east_structure --xlsx …）"
        bad = check(east_api.load())
        assert not bad, bad[:5]

    def test_stats_are_plausible(self) -> None:
        from interfaces import east_api

        d = east_api.structure()
        st = d["stats"]
        assert d["available"] is True
        assert int(st["tables"]) >= 100, st
        assert int(st["items"]) >= 2000, st
        assert int(st["pk_items"]) > 0
        assert len(d["domains"]) >= 10, d["domains"]

    def test_table_list_omits_items(self) -> None:
        """列表**不得**携带数据项（3062 条 ≈ 900KB，首屏会卡）。"""
        from interfaces import east_api

        d = east_api.structure()
        for t in d["tables"]:
            assert "items" not in t, t["table_no"]
            assert t["item_count"] > 0

    def test_table_items_lazy(self) -> None:
        from interfaces import east_api

        first = east_api.structure()["tables"][0]
        d = east_api.table_items(first["table_no"])
        assert d["available"] is True and d["items"]
        assert len(d["items"]) == first["item_count"]
        item = d["items"][0]
        for k in ("item_id", "item_name", "is_pk", "nullable", "data_format"):
            assert k in item, k

    def test_unknown_table_degrades(self) -> None:
        from interfaces import east_api

        d = east_api.table_items("00000")
        assert d["available"] is False and d["items"] == []

    def test_routes(self) -> None:
        from web import api

        st, d = api.handle_request("GET", "/api/east")
        assert st == 200 and d["available"] is True
        no = d["tables"][0]["table_no"]
        st2, d2 = api.handle_request("GET", f"/api/east/table?no={no}")
        assert st2 == 200 and d2["items"]

    def test_frontend_contract(self) -> None:
        h = _html()
        assert "function eastSection(" in h and "function bindRegstats(" in h
        assert 'api("/api/east/table?no="' in h, "未按表懒加载数据项"
        assert "eastwrap" in h and "data-etbl" in h
        # 页面必须写明"不校验报文"——避免被当成 EAST 校验工具
        assert "不校验报文" in h or "不校验报文字段" in h


class TestRfnAlwaysPresent:
    """B. RFN 恒有值 + 来源可分辨。"""

    @staticmethod
    def _rows():
        from interfaces import base_api

        c = sqlite3.connect("file:" + base_api._db("external") + "?mode=ro", uri=True)
        return c.execute("SELECT rfn, rfn_origin FROM records").fetchall()

    def test_every_record_has_rfn(self) -> None:
        rows = self._rows()
        empty = [r for r in rows if not (r[0] or "").strip()]
        assert not empty, f"{len(empty)} 条记录没有编号（前台会显示 —）"

    def test_origin_is_known(self) -> None:
        origins = {r[1] for r in self._rows()}
        assert origins <= {"registry", "derived", ""}, origins
        assert "registry" in origins and "derived" in origins, "两类来源都应有样本"

    def test_derived_not_marked_registry(self) -> None:
        """派生号**绝不能**标成 registry（否则等于宣称"已入册"）。"""
        from interfaces import base_api, rfn_api

        attr = {(r.get("监管文件编号") or "").strip() for r in rfn_api.attr_rows()}
        c = sqlite3.connect("file:" + base_api._db("external") + "?mode=ro", uri=True)
        bad = [r for r in c.execute(
            "SELECT rfn FROM records WHERE rfn_origin='derived'") if r[0] in attr]
        assert not bad, f"派生号与归属表撞号（会被误认为入册）：{bad[:3]}"

    def test_frontend_shows_origin_badge(self) -> None:
        h = _html()
        assert "function idCell(" in h
        assert "派生" in h and "未入册" in h


class TestTitleCleaning:
    """C. 标题清洗（SSOT + 落库结果）。"""

    @pytest.mark.parametrize("raw,want", [
        ("第1页（共13页）阳光人寿保险股份有限公司操作风险管理办法",
         "阳光人寿保险股份有限公司操作风险管理办法"),
        ("虚开发票相关法律法规序号责任分类法律法规相关内容行政责任国务院关于修改"
         "《中华人民共和国发票管理办法》的决定", "虚开发票相关法律法规"),
        ("附件：阳光保险集团股份有限公司招聘管理办法阳光保险集团股份有限公司"
         "2022年5月20日阳光保险集团股份有限公司招聘管理办法",
         "阳光保险集团股份有限公司招聘管理办法"),
        ("", ""),
    ])
    def test_cases(self, raw: str, want: str) -> None:
        from std_lib.common_lib.text_clean import clean_policy_title

        assert clean_policy_title(raw) == want

    def test_idempotent(self) -> None:
        from std_lib.common_lib.text_clean import clean_policy_title

        once = clean_policy_title("第1页（共13页）阳光人寿保险股份有限公司操作风险管理办法")
        assert clean_policy_title(once) == once

    def test_published_titles_are_clean(self) -> None:
        from interfaces import base_api

        c = sqlite3.connect("file:" + base_api._db("internal") + "?mode=ro", uri=True)
        bad = [r for r in c.execute("SELECT ipn, title FROM policies")
               if "页（共" in (r[1] or "") or "序号责任分类" in (r[1] or "")]
        assert not bad, f"落库标题仍脏：{bad[:3]}"

    def test_reported_ipns(self) -> None:
        """用户点名的两条必须已修（回归锚点）。"""
        from interfaces import base_api

        c = sqlite3.connect("file:" + base_api._db("internal") + "?mode=ro", uri=True)
        got = dict(c.execute(
            "SELECT ipn, title FROM policies WHERE ipn IN "
            "('IPN-1abf474f002e4f40','IPN-48eac84c11f8bb1f')"))
        assert got["IPN-1abf474f002e4f40"] == "阳光人寿保险股份有限公司操作风险管理办法"
        assert got["IPN-48eac84c11f8bb1f"] == "虚开发票相关法律法规"


class TestPerUserToken:
    """D. 个人令牌（审计到人）。"""

    def test_issue_and_identify(self, tmp_path, monkeypatch) -> None:
        from web import auth

        monkeypatch.setattr(auth, "TOKENS_PATH", str(tmp_path / "tokens.yaml"))
        tok = auth.issue_user_token("zhangsan", note="合规部")
        assert auth.identify_token(tok) == ("zhangsan", "user")
        # 共享口令仍可用，但级别是 shared
        assert auth.identify_token(auth.access_token())[1] == "shared"
        assert auth.identify_token("BAD-TOKEN") == ("", "")

    def test_registry_stores_hash_only(self, tmp_path, monkeypatch) -> None:
        from web import auth

        monkeypatch.setattr(auth, "TOKENS_PATH", str(tmp_path / "tokens.yaml"))
        tok = auth.issue_user_token("lisi")
        raw = open(auth.TOKENS_PATH, encoding="utf-8").read()
        assert tok not in raw, "注册表里出现了明文令牌"
        assert auth.load_user_tokens()["lisi"]["hash"]

    def test_revoke(self, tmp_path, monkeypatch) -> None:
        from web import auth

        monkeypatch.setattr(auth, "TOKENS_PATH", str(tmp_path / "tokens.yaml"))
        tok = auth.issue_user_token("wangwu")
        assert auth.revoke_user_token("wangwu") is True
        assert auth.identify_token(tok) == ("", "")
        assert auth.revoke_user_token("wangwu") is False

    def test_session_carries_level(self) -> None:
        from web import auth

        ck, _c, _e = auth.issue_session("zhangsan", level="shared")
        assert auth.parse_session(ck)["level"] == "shared"
        ck2, _c2, _e2 = auth.issue_session("zhangsan", level="user")
        assert auth.parse_session(ck2)["level"] == "user"

    def test_health_reports_audit_level(self) -> None:
        from web import api

        st, d = api.handle_request("GET", "/api/health", ctx={
            "scope": "lan", "client_ip": "10.0.0.9", "auth_mode": "token",
            "authenticated": True, "identity_user": "内网共享口令用户",
            "identity_src": "session", "level": "shared"})
        assert st == 200
        assert d["audit_level"] == "shared"
        assert "无法定位到人" in d["audit_level_label"]

    def test_frontend_marks_shared(self) -> None:
        h = _html()
        assert "auditLevel" in h and "共享" in h
        assert "无法定位到人" in h
