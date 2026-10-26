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
    """A. EAST 四件产物与接口（2026-10-26 改为全附件 + 懒加载）。"""

    @staticmethod
    def _structure() -> dict:
        import json

        from interfaces import east_api

        return json.load(open(east_api.S_JSON, encoding="utf-8"))

    def test_artifact_check(self) -> None:
        from interfaces import east_api
        from tools.build_east_structure import check

        assert east_api.available(), "产物缺失（运行 python -m tools.build_east_structure）"
        bad = check(self._structure())
        assert not bad, bad[:5]

    def test_stats_cover_all_attachments(self) -> None:
        """四类附件都要有产出：1-3 表/项、1-4 代码表、1-5 规则、1-6→E-R。"""
        from interfaces import east_api

        d = east_api.structure()
        st = d["stats"]
        assert st["attachments"] >= 12, st
        assert st["tables"] >= 100 and st["items"] >= 2000, st
        assert st["code_tables"] >= 60 and st["codes"] >= 5000, st
        assert st["rules"] >= 1500, st
        assert set(d["rule_index"]) == {"A", "B", "C", "D", "E"}, d["rule_index"]
        assert st["er_edges"] >= 300, st

    def test_table_list_omits_items(self) -> None:
        """结构件**不得**携带数据项（首屏 423KB 与 9.8MB 的区别就在这里）。"""
        from interfaces import east_api

        d = east_api.structure()
        for t in d["tables"]:
            assert "items" not in t, t["table_no"]
            assert t["item_count"] > 0

    def test_lazy_items(self) -> None:
        from interfaces import east_api

        first = east_api.structure()["tables"][0]
        d = east_api.table_items(first["table_no"])
        assert d["available"] is True and d["items"]
        assert len(d["items"]) == first["item_count"]
        assert d["meta"]["table_code"] == first["table_code"]
        for k in ("item_id", "item_name", "is_pk", "nullable", "data_format"):
            assert k in d["items"][0], k

    def test_lazy_rules_and_codes(self) -> None:
        from interfaces import east_api

        idx = east_api.rules()
        assert [k["kind"] for k in idx["kinds"]] == ["A", "B", "C", "D", "E"]
        a = east_api.rules("A")
        assert a["available"] and a["count"] == len(a["items"])
        assert a["items"][0]["no"].startswith("A")

        codes = east_api.codes()
        assert codes["count"] >= 60
        one = east_api.code_table(codes["tables"][0]["sheet"])
        assert one["available"] and one["rows"] and one["columns"]

    def test_unknown_degrades(self) -> None:
        from interfaces import east_api

        assert east_api.table_items("00000")["available"] is False
        assert east_api.code_table("不存在")["available"] is False
        assert east_api.rules("Z")["available"] is False

    def test_routes(self) -> None:
        from web import api

        st, d = api.handle_request("GET", "/api/east")
        assert st == 200 and d["available"] is True
        no = d["tables"][0]["table_no"]
        assert api.handle_request("GET", f"/api/east/table?no={no}")[1]["items"]
        assert api.handle_request("GET", "/api/east/rules?kind=E")[1]["count"] > 0
        sheet = d["code_index"][0]["sheet"]
        assert api.handle_request("GET", f"/api/east/code?sheet={sheet}")[1]["rows"]

    def test_frontend_contract(self) -> None:
        h = _html()
        assert "function eastSection(" in h and "function bindRegstats(" in h
        for tab in ("overview", "tables", "codes", "rules", "er"):
            assert f'k: "{tab}"' in h, f"缺分页 {tab}"
        assert "/api/east/table?no=" in h
        assert "/api/east/rules?kind=" in h
        assert "/api/east/code?sheet=" in h
        assert "不校验报文" in h or "不校验报文字段" in h


class TestEastSourceAndLayouts:
    """A2. 附件**事实源**与版式自适应。

    2026-10-26 变更：不再去 `data/corpus/**` 目录 glob 文件，改为读采集事实源
    （`supplementary_regulations.jsonl` 的 `attachments[]`，含 local_path/sha256/正文）。
    仓里同一批文件曾存三份（采集目录 / 人工归档 / 提前解压），是本批要消除的冗余。
    """

    def test_source_is_fact_source(self) -> None:
        from interfaces import east_api

        d = east_api.structure()
        src = d["source"]
        assert src["index"].endswith("supplementary_regulations.jsonl"), src
        assert "supplementary_regulations_scraper" in src["attachment_root"], src
        for a in src["attachments"]:
            assert a["local_path"].startswith("modules/regulatory_scrapers/data/docs/"), a
            assert a["sha256"], a

    def test_attachment_api_is_single_entry(self) -> None:
        """附件清单只经 `attachment_api`（禁止各模块自己 glob 目录）。"""
        from interfaces import attachment_api as A

        d = A.attachments_for_docno("金非银检函〔2025〕82号")
        assert d["available"] and d["count"] >= 12
        nos = {it["no"] for it in d["items"]}
        assert {"1", "1-1", "1-2", "1-3", "1-4", "1-5", "1-6"} <= nos, nos
        # 每个附件都要有采集侧结论（正文类应已抽出）
        for it in d["items"]:
            assert it["extract_status"] or it["kind"] == "jpg", it

    def test_both_layouts_parse_identically(self) -> None:
        """1-3 的两种版式（24 列 WTR / 16 列官方修订版）解析结果必须一致。"""
        import glob

        from interfaces import attachment_api as A
        from tools.build_east_structure import parse_tables

        official = A.attachment_file("金非银检函〔2025〕82号", "1-3")[0]
        cands = [p for p in glob.glob(
            "data/corpus/regulatory_stats/**/*数据结构一览表*WTR.xlsx", recursive=True)]
        cur = parse_tables(official)
        if not cands:
            pytest.skip("仓内没有 WTR 版样本，跳过跨版式比对")
        wtr = parse_tables(cands[0])
        assert len(wtr["tables"]) == len(cur["tables"]), (
            len(wtr["tables"]), len(cur["tables"]))
        assert sum(len(t["items"]) for t in wtr["tables"]) == \
            sum(len(t["items"]) for t in cur["tables"])

    def test_data_format_filled_by_element_dict(self) -> None:
        """官方版数据项行不带 数据格式，靠 `数据元说明` 字典 join 回来。"""
        from interfaces import east_api

        st = east_api.structure()["stats"]
        assert st["with_format"] == st["items"], (
            f"{st['with_format']}/{st['items']} 项有数据格式 —— 字典 join 失效")
        assert east_api.structure()["format_legend"], "缺「数据格式说明」图例"

    def test_frontend_resolves_parameterized_format(self) -> None:
        h = _html()
        assert "function eastFmtDesc(" in h
        assert 'replace(/\\d+/g, "M")' in h, "未做 M/N 参数化归一，tooltip 会全空"


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
