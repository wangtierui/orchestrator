# -*- coding: utf-8 -*-
"""tests/test_console_lan_feedback.py — 内网开放分级 + 留言板 + 全局图谱 + 监管统计（第一百一十一批）。

本批四件事，每件都有"看起来对、其实错"的坑，守卫逐个钉住：

1. **内网开放**（用户要求"对公司内网开放，待办闭环仅本机可见"）
   坑：只在**前端隐藏页签**。内网任何机器 `curl` 一下就能写库、下制度原件。
   正解：`web/policy.py` 单一事实源 + 服务端 403；前端隐藏只是"别让人误点"。

2. **留言板**（任何使用者可记录问题，留域账号/IP/时间）
   坑①：把"自报账号"当成"已认证账号" —— 服务端根本拿不到访问者的 Windows 账号；
   坑②：前端传什么就存什么 —— 那 `ip`/时间可伪造，失去审计意义；
   坑③：治理库异常时抛错 —— 确认台是旁路设施，不能因此影响其它功能。

3. **全局关系图谱**（未从列表进入时默认展示）
   坑：把 4197 条边全画出来 —— 一张不可读的黑网；正解是"能全画的画全（24 条）+ 枢纽榜"。

4. **监管统计（EAST）**
   坑：只有 1 条直接相关资料却画趋势图 —— 用图表撒谎。正解是"清单 + 覆盖度披露 + 缺口说明"。

守卫分组：A 访问策略  B 服务端拦截（真实内网来源）  C 留言板  D 全局图谱  E 监管统计  F 前端契约
"""

from __future__ import annotations

import os
import sqlite3

import pytest

pytestmark = pytest.mark.contract

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML = os.path.join(ROOT, "web", "assets", "index.html")


def _html() -> str:
    return open(HTML, encoding="utf-8").read()


class TestAccessPolicy:
    """A. 访问分级策略（纯函数，最容易被改坏的边界）。"""

    def test_loopback_is_local(self) -> None:
        from web.policy import access_scope

        for ip in ("127.0.0.1", "::1", "127.0.0.5"):
            assert access_scope(ip) == "local", ip

    def test_ipv4_mapped_v6_is_local(self) -> None:
        """`::ffff:127.0.0.1` 必须判本机 —— 某些 Windows 环境就是这么给的。

        不平移会出现的怪事：**本机用户自己用不了"仅本机"的功能**。
        """
        from web.policy import access_scope

        assert access_scope("::ffff:127.0.0.1") == "local"

    def test_lan_ip_is_lan(self) -> None:
        from web.policy import access_scope

        assert access_scope("10.64.22.13") == "lan"
        assert access_scope("192.168.1.20") == "lan"

    def test_unknown_is_not_local(self) -> None:
        from web.policy import access_scope

        for ip in ("", "not-an-ip", "169.254.1.1", "0.0.0.0"):
            assert access_scope(ip) != "local", ip

    @pytest.mark.parametrize("path", ["/api/worklist/resolve",
                                      "/api/feedback/resolve",
                                      "/api/local/file",
                                      "/api/diagnose",
                                      "/api/commit",
                                      "/api/diagnose/backlog"])
    def test_local_only_routes(self, path: str) -> None:
        from web.policy import route_is_local_only

        assert route_is_local_only("GET", path) is True, path
        assert route_is_local_only("POST", path) is True, path

    def test_query_string_does_not_bypass(self) -> None:
        from web.policy import route_is_local_only

        assert route_is_local_only("GET", "/api/local/file?path=x") is True

    def test_feedback_post_allowed_remotely(self) -> None:
        from web.policy import route_is_local_only

        assert route_is_local_only("POST", "/api/feedback") is False

    def test_other_posts_denied_by_default(self) -> None:
        """**deny by default**：新增写接口忘了加白名单 ⇒ 内网用不了（安全侧失败）。"""
        from web.policy import route_is_local_only

        assert route_is_local_only("POST", "/api/whatever/new") is True

    def test_readonly_get_allowed(self) -> None:
        from web.policy import route_is_local_only

        for p in ("/api/overview", "/api/records", "/api/rules",
                  "/api/relations", "/api/regstats", "/api/feedback"):
            assert route_is_local_only("GET", p) is False, p

    def test_may_access_matrix(self) -> None:
        from web.policy import may_access

        assert may_access("POST", "/api/worklist/resolve", "127.0.0.1")[0] is True
        assert may_access("POST", "/api/worklist/resolve", "10.0.0.9")[0] is False
        assert may_access("GET", "/api/local/file", "10.0.0.9")[0] is False
        assert may_access("GET", "/api/overview", "10.0.0.9")[0] is True
        assert may_access("POST", "/api/feedback", "10.0.0.9")[0] is True

    def test_no_forwarded_header_trust(self) -> None:
        """**绝不信** `X-Forwarded-For`（客户端可伪造）。策略函数只接受 IP 参数。"""
        src = open(os.path.join(ROOT, "web", "policy.py"), encoding="utf-8").read()
        assert "X-Forwarded-For" in src, "应保留「不要信它」的说明"
        assert "headers.get" not in src, "策略层不该直接读请求头判来源"


class TestServerEnforcement:
    """B. 服务端真的拦得住（用**本机内网 IP** 连，TCP 对端即非回环）。"""

    @pytest.fixture(scope="class")
    def lan_server(self, tmp_path_factory):
        import json as _json
        import os
        import subprocess
        import sys
        import time
        import urllib.request

        from web import auth
        from web.policy import lan_ipv4

        ips = lan_ipv4()
        if not ips:
            pytest.skip("无内网 IP，无法模拟内网来源")
        port = 8813
        # ⚠️ 必须给**临时治理库**：该测试会真的 POST 一条留言，若沿用默认路径就写进
        # **生产库**（实测踩到：守卫自身让看板多了 5 条测试留言）。
        tmp_db = str(tmp_path_factory.mktemp("gov") / "governance.db")
        env = dict(os.environ, REG_ORCH_GOVERNANCE_DB=tmp_db)
        from std_lib.common_lib import governance_store as gs

        old = os.environ.get("REG_ORCH_GOVERNANCE_DB")
        os.environ["REG_ORCH_GOVERNANCE_DB"] = tmp_db
        try:
            gs.init_db()          # 先把表建好，服务端起来就能写
        finally:
            if old is None:
                os.environ.pop("REG_ORCH_GOVERNANCE_DB", None)
            else:
                os.environ["REG_ORCH_GOVERNANCE_DB"] = old
        proc = subprocess.Popen([sys.executable, "cli.py", "serve", "--lan",
                                 "--auth", "token", "--port", str(port)],
                                cwd=ROOT, env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        base = f"http://{ips[0]}:{port}"
        for _ in range(60):
            try:
                urllib.request.urlopen(base + "/api/auth/status", timeout=3).read()
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.5)

        # 2026-10-23：对内网开放后**必须登录**（`--lan` 自动 token 模式）。
        # 用共享口令换会话 + CSRF，后续请求带上 —— 否则一律 401/403。
        req = urllib.request.Request(
            base + "/api/auth/login",
            data=_json.dumps({"password": auth.access_token()}).encode(),
            method="POST", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            body = _json.loads(r.read().decode("utf-8"))
            raw = r.headers.get_all("Set-Cookie") or []
        sess = next((c.split(";")[0] for c in raw if c.startswith("wb_sess=")), "")
        csrf_ck = next((c.split(";")[0].split("=", 1)[1]
                        for c in raw if c.startswith("wb_csrf=")), "")
        cookie = sess + ("; wb_csrf=" + csrf_ck if csrf_ck else "")
        yield {"base": base, "cookie": cookie,
               "csrf": str(body.get("csrf") or csrf_ck)}
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    @staticmethod
    def _get(srv, path: str, method: str = "GET", body: dict | None = None):
        import json
        import urllib.error
        import urllib.request

        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json",
                   "Cookie": srv["cookie"], "X-CSRF": srv["csrf"]}
        req = urllib.request.Request(srv["base"] + path, data=data,
                                     method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8") or "{}")

    def test_health_reports_scope(self, lan_server) -> None:
        st, h = self._get(lan_server, "/api/health")
        assert st == 200
        assert h["scope"] == "lan", f"内网来源被判成 {h['scope']}"
        assert h["local_only"] is False
        assert "/api/worklist/resolve" in h["local_only_routes"]

    @pytest.mark.parametrize("path,method", [
        ("/api/worklist/resolve", "POST"),
        ("/api/feedback/resolve", "POST"),
        ("/api/local/file?path=README.md", "GET"),
        ("/api/diagnose", "GET"),
        ("/api/commit?sha=x", "GET"),
    ])
    def test_local_only_returns_403(self, lan_server, path: str, method: str) -> None:
        st, d = self._get(lan_server, path, method, {} if method == "POST" else None)
        assert st == 403, f"{method} {path} 内网竟然放行（{st}）"
        assert "仅限本机" in str(d.get("error") or "")

    @pytest.mark.parametrize("path", ["/api/overview", "/api/records?size=1",
                                      "/api/rules?size=1", "/api/relations",
                                      "/api/regstats", "/api/feedback"])
    def test_readonly_routes_open(self, lan_server, path: str) -> None:
        st, _d = self._get(lan_server, path)
        assert st == 200, f"{path} 内网读不到（{st}）"

    def test_feedback_post_open(self, lan_server) -> None:
        st, d = self._get(lan_server, "/api/feedback", "POST",
                          {"title": "守卫自检留言", "account": "guardtest",
                           "kind": "other", "severity": "low"})
        assert st == 200 and d.get("fid"), d


class TestFeedback:
    """C. 留言板业务规则（DB 用临时库，绝不写生产）。"""

    @pytest.fixture(autouse=True)
    def _tmp_db(self, tmp_path, monkeypatch):
        monkeypatch.setenv("REG_ORCH_GOVERNANCE_DB", str(tmp_path / "gov.db"))
        from std_lib.common_lib import governance_store as gs

        gs.init_db()
        yield

    def test_add_and_list_roundtrip(self) -> None:
        from interfaces import feedback_api as F

        out = F.add({"title": "第一个问题", "detail": "详情", "account": "zhangsan",
                     "kind": "data", "severity": "high", "page": "政策法规"},
                    ip="10.1.1.1", user_agent="UA-test")
        assert out["ok"] is True and out["fid"].startswith("FB-")
        d = F.items()
        row = d["items"][0]
        assert row["account"] == "zhangsan" and row["account_src"] == "self"
        assert row["ip"] == "10.1.1.1", "IP 必须由服务端写入"
        assert row["created_at"], "时间必须由服务端写入"
        assert row["kind_label"] == "数据质量"
        assert row["account_src_label"] == "填写者自报", (
            "自报必须显式标注 —— 不能让人误以为已认证")

    def test_header_account_overrides_and_marks_source(self) -> None:
        from interfaces import feedback_api as F

        out = F.add({"title": "网关认证的留言", "account": "ignored"},
                    ip="10.1.1.2", header_account="corp\\lisi")
        assert out["account_src"] == "header"
        row = F.items()["items"][0]
        assert row["account"] == "corp\\lisi"
        assert row["account_src_label"] == "网关认证"

    @pytest.mark.parametrize("payload,needle", [
        ({"title": "短", "account": "zhangsan"}, "至少 4"),
        ({"title": "合格标题", "account": ""}, "域账号"),
        ({"title": "合格标题", "account": "张三"}, "格式"),
        ({"title": "合格标题", "account": "zhangsan", "kind": "xx"}, "分类非法"),
        ({"title": "合格标题", "account": "zhangsan", "severity": "yy"}, "严重度非法"),
    ])
    def test_validation_rejects(self, payload, needle: str) -> None:
        from interfaces import feedback_api as F

        out = F.add(payload, ip="10.1.2.1")
        assert out["ok"] is False and needle in out["reason"], out

    def test_rate_limit_per_ip(self) -> None:
        from interfaces import feedback_api as F

        oks = [F.add({"title": f"限频 {i}", "account": f"u{i}"}, ip="10.9.9.9")["ok"]
               for i in range(8)]
        assert oks[:6] == [True] * 6, oks
        assert oks[6] is False and oks[7] is False, "同 IP 未限频"

    def test_resolve_requires_handling_text(self) -> None:
        from interfaces import feedback_api as F

        fid = F.add({"title": "待处置留言", "account": "zhangsan"}, ip="10.1.3.1")["fid"]
        assert F.resolve(fid, handling="", status="closed")["ok"] is False
        assert F.resolve(fid, handling="已修复并加了守卫", status="closed")["ok"] is True
        row = F.items()["items"][0]
        assert row["status"] == "closed" and row["handling"]

    def test_resolve_is_append_only(self) -> None:
        """处置**不删行**（删了就失去"问题曾经存在"的证据）。"""
        from interfaces import feedback_api as F

        fid = F.add({"title": "只追加验证", "account": "zhangsan"}, ip="10.1.4.1")["fid"]
        F.resolve(fid, handling="闭环说明", status="closed")
        items = F.items()["items"]
        assert len(items) == 1, "处置后行数变了（应只改状态，不删不加）"
        assert items[0]["fid"] == fid and items[0]["status"] == "closed"

    def test_stats_counts(self) -> None:
        from interfaces import feedback_api as F

        F.add({"title": "统计自检 1", "account": "a1", "kind": "bug"}, ip="10.1.5.1")
        F.add({"title": "统计自检 2", "account": "a2", "kind": "data"}, ip="10.1.5.2")
        st = F.items()["stats"]
        assert st["total"] == 2 and st["by_kind"]["bug"] == 1

    def test_table_exists(self) -> None:
        from std_lib.common_lib.governance_store import db_path

        with sqlite3.connect(db_path()) as c:
            cols = {r[1] for r in c.execute("PRAGMA table_info(feedback)")}
        must = {"fid", "kind", "severity", "page", "title", "detail", "account",
                "account_src", "ip", "user_agent", "host_user", "created_at",
                "status", "handled_by", "handled_at", "handling"}
        assert must <= cols, f"feedback 表缺列：{must - cols}"


class TestGlobalGraph:
    """D. 全局关系图谱（默认视图）。"""

    def test_global_mode_shape(self) -> None:
        from interfaces import relations_api as R

        g = R.global_graph()
        assert g["mode"] == "global" and g["available"] is True
        bp = g["bipartite"]
        assert bp["edges"], "全局视图没有可画的边"
        # 一条边必须两端都在节点表里（否则前端画不出来）
        left = {x["id"] for x in bp["left"]}
        right = {x["id"] for x in bp["right"]}
        for e in bp["edges"]:
            assert e["from"] in left and e["to"] in right, e

    def test_counts_use_by_cross_shape(self) -> None:
        """`stat()["relations"]` 的形状是 `{total, by_kind, by_cross}` —— 取错键会全 0。

        实测踩到：页面写"监管关系 0 条"，比不显示更糟（看起来像事实）。
        """
        from interfaces import relations_api as R

        c = R.global_graph()["counts"]
        assert c["total_relations"] > 0
        assert c["regulatory_relations"] > 0 and c["cross_relations"] > 0

    def test_unresolved_is_disclosed(self) -> None:
        from interfaces import relations_api as R

        g = R.global_graph()
        assert g["counts"]["unresolved"] > 0, "应披露未定位条数"
        # 逐视图**四桶**披露（2026-10-23 扩）：entity / corpus / organ / external。
        # 只有总数对读者没意义（"2632 条没定位" 里 509 条只差一次登记、1026 条是机关名）。
        uv = g["counts"]["unresolved_by_view"]
        assert uv and all({"entity", "corpus", "organ", "external"} <= set(v)
                          for v in uv.values()), uv
        assert "外部法规" in g["note"] or "RFN-EXT" in g["note"]

    def test_hubs_sorted_by_degree(self) -> None:
        from interfaces import relations_api as R

        hubs = R.global_graph()["hubs"]
        assert hubs, "枢纽榜为空"
        degs = [h["degree"] for h in hubs]
        assert degs == sorted(degs, reverse=True), "枢纽未按被依据次数排序"

    def test_snapshots_defaults_to_global(self) -> None:
        from web import snapshots as S

        d = S.relation_graph("")
        assert d["mode"] == "global"
        assert d["source_name"] == "relations_api.global_graph"

    def test_ego_still_works(self) -> None:
        from interfaces import relations_api as R
        from web import snapshots as S

        rows = R.load("cross")
        ref = next(r["src_ref"] for r in rows if r.get("dst_ref"))
        d = S.relation_graph(ref)
        assert d["ref"] == ref and d["edges"]


class TestRegstats:
    """E. 监管统计（EAST）：清单 + 覆盖度披露，**不造图**。"""

    def test_categories_and_counts(self) -> None:
        from interfaces import base_api as B

        d = B.regstats_docs()
        assert d["available"] is True
        keys = [c["key"] for c in d["categories"]]
        assert keys == ["east", "stat_rule", "metric", "fin_stat"]
        assert sum(c["count"] for c in d["categories"]) == d["total"]
        assert d["coverage"]["east_direct"] >= 1, "EAST 直接相关资料不应为 0"

    def test_deduped_across_categories(self) -> None:
        """同一文件可能同时命中多个类别的关键词 ⇒ 只能出现一次。"""
        from interfaces import base_api as B

        d = B.regstats_docs()
        seen = [it["rfn"] for c in d["categories"] for it in c["items"] if it.get("rfn")]
        assert len(seen) == len(set(seen)), "跨类别重复计入"

    def test_capability_boundary_is_disclosed(self) -> None:
        """必须明确写"未接入 EAST 字段级校验"，否则会被当成"EAST 已支持"。"""
        from interfaces import base_api as B

        cov = B.regstats_docs()["coverage"]
        assert cov["east_field_validation"] is False
        assert "未接入" in cov["note"]

    def test_web_layer_adds_theme_label(self) -> None:
        from web import snapshots as S

        d = S.regstats()
        assert d["available"] is True
        for c in d["categories"]:
            for it in c["items"]:
                assert "theme_label" in it


class TestFrontendContract:
    """F. 前端契约（页签存在 + 关键接线 + 零外链）。"""

    def test_new_tabs(self) -> None:
        h = _html()
        for tid, name in (("regstats", "监管统计"), ("feedback", "留言板"),
                          ("graph", "关系图谱")):
            assert f'{{id:"{tid}"' in h, f"缺页签 {tid}"
            assert name in h

    def test_scope_badge_and_hiding(self) -> None:
        h = _html()
        assert 'id="scope"' in h, "缺访问范围徽标"
        assert "LOCAL_ONLY_TABS" in h and '"worklist"' in h, "未声明本机专属页签"
        assert "h.local_only" in h, "未按 /api/health.local_only 判定"

    def test_feedback_form_wiring(self) -> None:
        h = _html()
        for el in ("fbkind", "fbsev", "fbpage", "fbacc", "fbtitle", "fbdetail",
                   "fbsubmit", "fbfilter"):
            assert f'id="{el}"' in h, f"缺表单元素 {el}"
        assert "function bindFeedback(" in h
        assert 'api("/api/feedback"' in h

    def test_global_graph_view(self) -> None:
        h = _html()
        assert "function viewGraphGlobal(" in h
        assert 'd.mode === "global"' in h, "未按 mode 分派全局/局部"
        assert "id=\"gback\"" in h, "局部图缺'回到全局'入口"

    def test_regstats_no_fake_charts(self) -> None:
        """监管统计页**不得**出现趋势/图表调用（只有 1 条数据，画图即误导）。"""
        h = _html()
        i = h.find("function viewRegstats(")
        seg = h[i:i + 3000]
        for bad in ("bars(", "sparkline(", "lineChart("):
            assert bad not in seg, f"监管统计页出现图表调用 {bad}（数据不足以支撑）"

    def test_zero_external_links(self) -> None:
        h = _html()
        assert "http://" not in h and "https://" not in h
