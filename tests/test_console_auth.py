# -*- coding: utf-8 -*-
"""tests/test_console_auth.py — 确认台认证 / CSRF / 集成身份 / resume 语义（第一百一十二批）。

本批三件事，每件都有"假安全"陷阱：

1. **内网开放不能等于匿名可读**（TD-27 第二项）
   坑：把"分级"当成"认证"。分级只限制**动作**，不限制**人** —— 内网任何机器
   仍能读 16607 份监管文件。正解：`auth_mode != off` 时除 `/api/auth/*` 外全部 401。

2. **CSRF 不能有"没有 token 就跳过"的分支**
   坑（本批实测踩到）：原实现 `if need and not compare(...)` —— **可信代理认证**
   路径没有会话 cookie，`need` 为空 ⇒ 校验被整体跳过 ⇒ 免 CSRF 直接写入。
   正解：没有 token **也拒绝**，token 由 `/api/auth/status` 下发（proxy 场景的唯一入口）。

3. **留言板的"域账号"必须能去掉自报**
   坑：接上认证后仍在要求填写者自报 ⇒ 登录了还要手打账号，且身份不可信。
   正解：身份优先级 `已认证会话/代理 > 可信头 > 自报`，`account_src` 如实标注。

另含 TD-27 的 `--resume` 语义守卫（`always_run` 与退出码分层）。
"""

from __future__ import annotations

import os
import time

import pytest

pytestmark = pytest.mark.contract

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML = os.path.join(ROOT, "web", "assets", "index.html")


def _html() -> str:
    return open(HTML, encoding="utf-8").read()


class TestAuthPolicy:
    """A. 访问策略：登录路由必须内网可达。"""

    def test_login_routes_not_local_only(self) -> None:
        """登录/登出必须能在内网调用，否则"开了认证"= "谁也进不来"。"""
        from web.policy import route_is_local_only

        assert route_is_local_only("POST", "/api/auth/login") is False
        assert route_is_local_only("POST", "/api/auth/logout") is False

    def test_feedback_still_remote_postable(self) -> None:
        from web.policy import route_is_local_only

        assert route_is_local_only("POST", "/api/feedback") is False

    def test_other_posts_denied(self) -> None:
        from web.policy import route_is_local_only

        assert route_is_local_only("POST", "/api/anything-else") is True


class TestCredentials:
    """B. 口令与令牌（不得明文、不得可预测）。"""

    def test_password_hash_roundtrip(self) -> None:
        from web import auth

        h = auth.hash_password("s3cret-pass")
        assert h.startswith("pbkdf2_sha256$") and "s3cret-pass" not in h
        assert auth.verify_password("s3cret-pass", h) is True
        assert auth.verify_password("wrong", h) is False

    def test_bad_hash_format_is_false_not_raise(self) -> None:
        from web import auth

        for bad in ("", "plain", "pbkdf2_sha256$notint$a$b", "md5$1$2$3"):
            assert auth.verify_password("x", bad) is False

    def test_token_is_stable_and_rotatable(self) -> None:
        from web import auth

        t1 = auth.access_token()
        assert t1 == auth.access_token(), "口令不应每次调用都变"
        t2 = auth.access_token(rotate=True)
        assert t2 != t1 and t2 == auth.access_token()
        for part in t2.split("-"):
            assert len(part) == 4 and part.isalnum()


class TestSession:
    """C. 会话 cookie（签名、过期、防篡改）。"""

    def test_roundtrip(self) -> None:
        from web import auth

        ck, csrf, exp = auth.issue_session("alice", src="session")
        d = auth.parse_session(ck)
        assert d and d["user"] == "alice" and d["src"] == "session"
        assert exp > time.time() and len(csrf) >= 16

    def test_tamper_rejected(self) -> None:
        from web import auth

        ck, _csrf, _ = auth.issue_session("alice")
        raw, _, sig = ck.rpartition(".")
        forged = auth._b64e(b'{"u":"root","s":"session","e":9999999999,"n":"x"}')
        assert auth.parse_session(f"{forged}.{sig}") is None, "改载荷后签名应失配"
        assert auth.parse_session(f"{raw}.{'0' * 32}") is None

    def test_expiry(self, monkeypatch) -> None:
        from web import auth

        monkeypatch.setattr(auth, "SESSION_TTL_S", -1)
        ck, _c, _ = auth.issue_session("bob")
        assert auth.parse_session(ck) is None, "过期会话应失效"

    def test_cookie_flags(self) -> None:
        """会话 cookie 必须 HttpOnly；CSRF cookie 必须**可被 JS 读取**。"""
        from web import auth

        sess = auth.cookie_header(auth.COOKIE_SESS, "v", max_age=60)
        csrf = auth.cookie_header(auth.COOKIE_CSRF, "v", max_age=60, http_only=False)
        assert "HttpOnly" in sess
        assert "HttpOnly" not in csrf, "CSRF cookie 设成 HttpOnly 会让 double-submit 失效"
        assert "SameSite=Strict" in sess and "SameSite=Strict" in csrf

    def test_cookie_parse(self) -> None:
        from web import auth

        d = auth.parse_cookies("a=1; wb_sess=xyz; wb_csrf=t")
        assert d["wb_sess"] == "xyz" and d["wb_csrf"] == "t"


class TestIdentity:
    """D. 身份解析（代理只在受信 IP、绝不信野生头）。"""

    def test_proxy_only_from_trusted_ip(self) -> None:
        from web import auth

        h = {"X-Remote-User": "corp\\zhangsan"}
        ok = auth.identity(cookies={}, client_ip="10.0.0.5", headers=h,
                           mode="token", trusted_proxies=("10.0.0.5",))
        assert ok["authenticated"] and ok["user"] == "corp\\zhangsan"
        assert ok["src"] == "proxy"
        bad = auth.identity(cookies={}, client_ip="10.0.0.6", headers=h,
                            mode="token", trusted_proxies=("10.0.0.5",))
        assert bad["authenticated"] is False, "非受信 IP 的 X-Remote-User 不得采信"

    def test_wild_headers_ignored(self) -> None:
        from web import auth

        h = {"X-Forwarded-User": "ceo", "X-User": "ceo"}
        d = auth.identity(cookies={}, client_ip="10.0.0.9", headers=h, mode="token")
        assert d["authenticated"] is False and d["user"] == ""

    def test_session_identity(self) -> None:
        from web import auth

        ck, _c, _ = auth.issue_session("alice")
        d = auth.identity(cookies={auth.COOKIE_SESS: ck}, client_ip="127.0.0.1",
                          headers={}, mode="token")
        assert d["authenticated"] and d["user"] == "alice" and d["src"] == "session"


class TestLoginThrottle:
    """E. 登录失败限频（防口令爆破）。"""

    def test_throttle_after_limit(self) -> None:
        from web import auth

        ip = "10.9.9.9"
        auth.clear_failures(ip)
        for _ in range(auth.FAIL_MAX):
            ok, _wait = auth.note_failure(ip)
            assert ok is True
        ok, wait = auth.note_failure(ip)
        assert ok is False and wait > 0
        assert auth.throttled(ip) > 0
        auth.clear_failures(ip)
        assert auth.throttled(ip) == 0


class TestFeedbackIdentity:
    """F. 留言板身份优先级（自报 → 认证身份的迁移）。"""

    @pytest.fixture(autouse=True)
    def _tmp_db(self, tmp_path, monkeypatch):
        monkeypatch.setenv("REG_ORCH_GOVERNANCE_DB", str(tmp_path / "gov.db"))
        from std_lib.common_lib import governance_store as gs

        gs.init_db()
        yield

    def test_session_identity_wins_over_self_report(self) -> None:
        from interfaces import feedback_api as F

        out = F.add({"title": "认证身份留言", "account": "自称的名字"},
                    ip="10.1.1.1", identity_user="alice", identity_src="session")
        assert out["ok"] and out["account_src"] == "session"
        row = F.items()["items"][0]
        assert row["account"] == "alice", "已认证时应以认证身份为准"
        assert row["account_src_label"] == "登录身份"

    def test_proxy_identity_labelled(self) -> None:
        from interfaces import feedback_api as F

        out = F.add({"title": "代理身份留言"}, ip="10.1.1.2",
                    identity_user="corp\\bob", identity_src="proxy")
        assert out["account_src"] == "proxy"
        assert F.items()["items"][0]["account_src_label"] == "网关认证"

    def test_no_identity_still_requires_self_report(self) -> None:
        from interfaces import feedback_api as F

        bad = F.add({"title": "没有身份的留言"}, ip="10.1.1.3")
        assert bad["ok"] is False and "域账号" in bad["reason"]
        good = F.add({"title": "自报的留言", "account": "carol"}, ip="10.1.1.3")
        assert good["ok"] and good["account_src"] == "self"


class TestResumeSemantics:
    """G. TD-27：`--resume` 不得跳过周期性检查；退出码要分层。"""

    @staticmethod
    def _mod():
        import importlib.util as u
        import sys

        spec = u.spec_from_file_location(
            "rpr_under_test", os.path.join(ROOT, "tools", "run_production_refresh.py"))
        m = u.module_from_spec(spec)
        sys.modules["rpr_under_test"] = m
        spec.loader.exec_module(m)
        return m

    def test_always_run_not_skipped(self) -> None:
        m = self._mod()
        m._RESUME_SKIP = {"gates", "timeliness:verify", "clean:gov"}
        m._DRY_RUN = False
        m._ONLY_STEPS = ()
        m._FROM_STEP = ""
        m._FORCED_BY_ALWAYS.clear()
        assert m._skip_reason("gates") == "", "gates 被 resume 跳过了"
        assert m._skip_reason("timeliness:verify") == "", "时效核验被 resume 跳过了"
        assert m._skip_reason("clean:gov") != "", "普通步骤应仍可跳过"
        assert m._FORCED_BY_ALWAYS == {"gates", "timeliness:verify"}

    def test_dry_run_still_skips_everything(self) -> None:
        """`--dry-run` 是"只列 argv"，不能被 always_run 破例去真跑。"""
        m = self._mod()
        m._RESUME_SKIP = {"gates"}
        m._DRY_RUN = True
        m._ONLY_STEPS = ()
        m._FROM_STEP = ""
        assert m._skip_reason("gates") != ""

    def test_exit_code_layering(self) -> None:
        """FAIL/ENV → 1；纯 DATA → 2（原来一律 2，门禁失败被报成"数据问题"）。"""
        src = open(os.path.join(ROOT, "tools", "run_production_refresh.py"),
                      encoding="utf-8").read()
        assert 'return 1 if (_codes & {"FAIL", "ENV"}) else 2' in src
        assert "_ALL_SKIPPED" in src, "全 SKIP 的运行未在台账里标注"


class TestFrontendContract:
    """H. 前端契约（登录卡片 / CSRF / 域账号条件渲染）。"""

    def test_login_view_and_wiring(self) -> None:
        h = _html()
        assert "function viewLogin(" in h and "function bindLogin(" in h
        assert 'api("/api/auth/login"' in h
        assert 'id="lgpw"' in h

    def test_csrf_from_cookie(self) -> None:
        """可信代理认证没有登录响应体 ⇒ 必须能**从 cookie 读** CSRF。"""
        h = _html()
        assert "function csrfToken(" in h
        assert "wb_csrf" in h
        assert '"X-CSRF"' in h

    def test_auth_gate_before_data(self) -> None:
        h = _html()
        assert "ensureHealth()" in h
        assert "authenticated === false" in h, "未按 authenticated 拦截渲染"

    def test_feedback_account_conditional(self) -> None:
        """已认证时**不渲染**域账号输入框（用户要求：去除自报）。"""
        h = _html()
        assert 'id="fbacc"' in h, "自报输入框应仍在（未认证场景）"
        assert "whoCell" in h, "应按身份条件渲染"
        assert "登录身份" in h

    def test_zero_external_links(self) -> None:
        h = _html()
        assert "http://" not in h and "https://" not in h
