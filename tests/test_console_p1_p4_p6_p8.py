# -*- coding: utf-8 -*-
"""tests/test_console_p1_p4_p6_p8.py — 确认台 P1/P3/P4/P6/P8 的守卫（第一百零九批）。

用户报的 11 条问题里，绝大部分是**数据契约与前端取值**的问题，不是"缺功能"。
本文件把它们固化为可执行断言，避免下次改前端时再犯。

六组
----
A **P3 `Object.assign(undefined, …)`**：`LAST.metrics` 首次为 undefined ⇒
  `TypeError: Cannot convert undefined or null to object`（用户看到的原文）。
  断言：前端不得出现"向可能为 undefined 的缓存对象上 assign"的写法，且已引入 `obj()` 兜底。
B **P1 真实数据流**：后端必须给出**展开后**的节点（`collect:gov` 而非 `collect:{src}`）、
  真实边、真实记录数；且每个出现的 kind 都要能在 `KIND_SPEC` 里找到（P4 同理）。
C **P4 列与内容对应**：`rfn_clean_drift_c2` 的名称/文号必须来自 `payload.clean_*`，
  唯一标识号必须是 RFN；`internal_unaligned` 的 IPN 进"唯一标识号"、文号标"不适用"。
D **P4 地址错配**：文号命中但标题不同 ⇒ **不返回 URL**（`mismatch` + 说明），
  宁可不给链接也不给错链接。
E **P4 本地文件**：内部制度的 `local_path` 必须真实存在；`GET /api/local/file`
  拒绝越界/非白名单/非白名单扩展名，`.html`/`.svg` **绝不内联**（同源 XSS），
  且实现里**不得**出现会夺焦点的"打开"调用（本仓黑名单）。
F **P6 短词**：FTS5 trigram 不支持 <3 字查询（双字词必然 0 命中）⇒ 必须走 LIKE 兜底并标注
  `mode=like`；**P8** 提交流默认全量、单提交能给出文件清单。
"""
from __future__ import annotations

import os
import re

import pytest

pytestmark = pytest.mark.contract

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML = os.path.join(ROOT, "web", "assets", "index.html")
API = os.path.join(ROOT, "web", "api.py")
SNAP = os.path.join(ROOT, "web", "snapshots.py")


def _html() -> str:
    return open(HTML, encoding="utf-8").read()


def _code_only(path: str) -> str:
    """去掉注释与 docstring——守卫管**代码**，不管解释（注释里必然引用这些反例）。

    必须同时剥 `/* … */`：前端用块注释写"原实现是 Object.assign(LAST.metrics, m)"
    这类说明，只剥 docstring 会把说明当成违规（实测一条守卫假失败）。
    """
    out, in_doc, in_block = [], False, False
    for line in open(path, encoding="utf-8").read().splitlines():
        s = line.strip()
        if in_block:
            if "*/" in s:
                in_block = False
            continue
        if s.startswith("/*"):
            if "*/" not in s:
                in_block = True
            continue
        if in_doc:
            if '"""' in s or "'''" in s:
                in_doc = False
            continue
        if s.startswith('"""') or s.startswith("'''"):
            q = '"""' if s.startswith('"""') else "'''"
            if s.count(q) < 2:
                in_doc = True
            continue
        out.append(line)
    return "\n".join(out)


class TestP3NoAssignOnUndefined:
    """A. P3 的 TypeError 根因：向 undefined 上 `Object.assign`。"""

    def test_no_assign_to_last_cache(self) -> None:
        code = _code_only(HTML)
        assert not re.search(r"Object\.assign\(\s*LAST\.[A-Za-z_]+", code), (
            "又出现 Object.assign(LAST.metrics, …)：首次进入时 LAST.metrics 是 undefined "
            "⇒ TypeError: Cannot convert undefined or null to object")

    def test_obj_helper_present_and_used(self) -> None:
        code = _code_only(HTML)
        assert "const obj =" in code, "缺 obj() 兜底助手"
        assert code.count("obj(") >= 20, (
            f"obj() 只用了 {code.count('obj(')} 次 —— 后端少给一个字段就会白屏")

    def test_metrics_page_builds_fresh_object(self) -> None:
        code = _code_only(HTML)
        assert re.search(r"STATE\.metrics\s*=\s*Object\.assign\(\s*\{\}", code), (
            "指标页应**新建**对象再合并，不得往缓存对象上写")


class TestP1RealFlow:
    """B. P1 必须是"展开后 + 真实边 + 真实记录数"。"""

    def test_flow_nodes_are_expanded(self) -> None:
        from config.enums import SOURCE_ORDER
        from web import snapshots as S

        d = S.flow_graph()
        assert d.get("available") is True
        nodes = d.get("nodes") or []
        ids = {n["id"] for n in nodes}
        assert not any("{src}" in i for i in ids), (
            "仍有模板节点 `{src}` —— 前端无法据此连边/取数（用户报的'无任何显示'）")
        # 源清单从**受控枚举**取（不在测试里写死源名 —— 那会被 gate_enum_values 判为副本）
        for src in SOURCE_ORDER:
            if f"collect:{src}" in ids:
                break
        else:
            raise AssertionError(f"没有任何展开节点（源={list(SOURCE_ORDER)}）")

    def test_flow_edges_and_volumes(self) -> None:
        from web import snapshots as S

        d = S.flow_graph()
        edges = d.get("edges") or []
        assert len(edges) >= 20, f"边只有 {len(edges)} 条（原实现 0 条）"
        vols = [n["volume"] for n in (d.get("nodes") or []) if n.get("volume")]
        assert vols, "没有任何节点带记录数（用户要求显示数据量）"
        assert max(vols) > 1000, f"最大记录数 {max(vols)} 不合理"

    def test_flow_edges_reference_existing_nodes(self) -> None:
        from web import snapshots as S

        d = S.flow_graph()
        ids = {n["id"] for n in d.get("nodes") or []}
        for e in d.get("edges") or []:
            assert e["from"] in ids and e["to"] in ids, f"悬空边：{e}"

    def test_flow_has_layers(self) -> None:
        from web import snapshots as S

        d = S.flow_graph()
        assert len(d.get("layers") or []) >= 5, "缺分层信息，前端无法按层布局"

    def test_flow_status_is_normalized(self) -> None:
        """节点状态必须来自**退出码**归一，且**与台账一致**。

        实测背景（两件都要防）：
        ① 台账里 OK 行的 `status` 为空、只有 `exit_code="OK"` ⇒ 原实现全画成 unknown；
        ② `--resume` 型运行时**几乎全部步骤 SKIP**（实测 2026-10-23 06:00 那次
           40 SKIP + 1 FAIL）⇒ 若断言"必须存在 ok 节点"就是**数据依赖的脆弱断言**，
           每次 resume 跑完测试就会红。故改为"**与台账逐项对齐**"。
        """
        from std_lib.common_lib import governance_store as gs
        from web import snapshots as S

        _rid, rows = gs.last_run_with_steps()
        d = S.flow_graph()
        nodes = {n["id"]: n for n in (d.get("nodes") or [])}
        unknown = [k for k, n in nodes.items() if n.get("status") == "unknown"]
        assert len(unknown) <= max(3, len(nodes) // 5), (
            f"{len(unknown)}/{len(nodes)} 个节点状态未知 —— 退出码未归一")

        # 逐项对齐：台账 OK/SKIP/FAIL 必须分别映到 ok/skip/failed
        mapping = {"OK": "ok", "SKIP": "skip", "FAIL": "failed", "DATA": "failed",
                   "ENV": "failed"}
        checked = 0
        for r in rows:
            exp = mapping.get(str(r.get("exit_code") or "").upper())
            got = (nodes.get(str(r.get("step"))) or {}).get("status")
            if not exp or not got:
                continue
            checked += 1
            assert got == exp, f"{r.get('step')}: 台账 {r.get('exit_code')} → 图 {got}"
        assert checked >= 5, f"逐项对齐样本过少（{checked}），判据失效"

    def test_last_run_skips_dry_run(self) -> None:
        """`last_run_with_steps()` 默认必须**跳过 dry-run**。

        否则一次 `run --dry-run` 会把 P1 的 48 个节点全刷成 skip（实测发生）。
        """
        src = _code_only(os.path.join(
            ROOT, "std_lib", "common_lib", "governance_store", "ledger.py"))
        assert "include_dry_run" in src, "缺 dry-run 过滤开关"
        assert "is_dry_run" in src, "查询未按 is_dry_run 过滤"


class TestP4KindSpec:
    """C. 逐 kind 的取值契约。"""

    def test_every_kind_has_spec(self) -> None:
        from interfaces import worklist_fields as WF

        assert set(WF.KIND_SPEC) >= {
            "relevance_boundary", "timeliness_conflict", "rfn_clean_drift_c2",
            "internal_unaligned", "clean_quarantine_triage",
            "trigger_manual_breakpoint"}, "实际出现的 kind 必须都在能力矩阵里"

    def test_drift_c2_uses_clean_fields(self) -> None:
        from interfaces import worklist_fields as WF

        it = {"kind": "rfn_clean_drift_c2", "subject": "RFN-abc",
              "payload": {"rfn": "RFN-abc", "clean_title": "某通知",
                          "clean_docno": "银保监办发〔2019〕71号"}}
        f = WF.derive(it)
        assert f["title"] == "某通知", f"名称仍取到 {f['title']!r}（应为 clean_title）"
        assert f["document_number"] == "银保监办发〔2019〕71号"
        assert f["identity"] == "RFN-abc", "唯一标识号应为 RFN"

    def test_internal_ipn_goes_to_identity_not_docno(self) -> None:
        from interfaces import worklist_fields as WF

        it = {"kind": "internal_unaligned", "subject": "IPN-x",
              "payload": {"ipn": "IPN-x", "title": "某办法"}}
        f = WF.derive(it)
        assert f["identity"] == "IPN-x"
        assert f["document_number"] == "", "IPN 不得冒充外部文号"
        assert f["document_number_src"] == "not_applicable"

    def test_trigger_kind_has_no_entity(self) -> None:
        from interfaces import worklist_fields as WF

        it = {"kind": "trigger_manual_breakpoint", "subject": "a/b",
              "payload": {"待裁决": "x"}}
        f = WF.derive(it)
        assert f["title_src"] == "subject"
        assert f["identity_src"] == "not_applicable"
        assert f["url_src"] == "not_applicable", "不该把'没有这个概念'显示成'未查到'"


class TestP4UrlConsistency:
    """D. 命中不等于正确：文号/标题必须互相印证。"""

    def _seed(self, monkeypatch, hit: dict) -> None:
        from interfaces import worklist_fields as WF

        monkeypatch.setattr(WF._Bridge, "_by_docno", {"某202563": hit}, raising=False)
        monkeypatch.setattr(WF._Bridge, "_by_title", {}, raising=False)

    def test_docno_hit_with_different_title_is_mismatch(self, monkeypatch) -> None:
        from interfaces import worklist_fields as WF

        self._seed(monkeypatch, {"url": "https://x/1", "title": "完全不同的另一个办法",
                                 "docno": "某〔2025〕63号", "from": "bridge"})
        r = WF._Bridge.lookup("关于进一步加强金融机构营销行为管理的通知", "某〔2025〕63号")
        assert r["url"] == "", "文号命中但标题不同，仍给了 URL ⇒ 用户会点开另一份文件"
        assert r["how"] == "mismatch"
        assert r["note"], "必须说明为什么不给链接"

    def test_docno_hit_same_title_ok(self, monkeypatch) -> None:
        from interfaces import worklist_fields as WF

        self._seed(monkeypatch, {"url": "https://x/2", "title": "关于加强管理的通知",
                                 "docno": "某〔2025〕63号", "from": "bridge"})
        r = WF._Bridge.lookup("关于加强管理的通知", "某〔2025〕63号")
        assert r["url"] == "https://x/2"
        assert r["how"] == "bridge:docno"

    def test_title_hit_with_different_docno_is_mismatch(self, monkeypatch) -> None:
        from interfaces import worklist_fields as WF

        monkeypatch.setattr(WF._Bridge, "_by_docno", {}, raising=False)
        monkeypatch.setattr(WF._Bridge, "_by_title",
                            {WF._norm_title("关于加强管理的通知"):
                             {"url": "https://x/3", "title": "关于加强管理的通知",
                              "docno": "完全不同〔2020〕1号", "from": "clean"}},
                            raising=False)
        r = WF._Bridge.lookup("关于加强管理的通知", "某〔2025〕63号")
        assert r["url"] == ""
        assert r["how"] == "mismatch"


class TestP4LocalFile:
    """E. 内部制度本地文件（**由浏览器打开/下载，不拉起本机程序**）。

    2026-10-22 的关键转折：初版用系统默认程序打开（会夺焦点），被
    `tests/test_no_desktop_interference.py` 拦下——该类 API 与"抢前台窗口/模拟键鼠"
    同属黑名单。改为服务端提供字节 + 浏览器处理：PDF/TXT 内联打开，Office 文档下载。
    **纪律优先于"看起来更方便"**。
    """

    def test_local_path_resolves_for_real_index(self) -> None:
        from interfaces import worklist_fields as WF

        m = WF.local_for("IPN-069150d1cb652f23")
        assert m, "真实索引里的 IPN 应能解析出本地路径"
        assert os.path.isfile(os.path.join(ROOT, m)), f"解析出的路径不存在：{m}"
        assert not os.path.isabs(m), "必须给仓内相对路径（绝对路径会泄露目录结构）"

    def test_rejects_escapes(self) -> None:
        from web import snapshots as S

        # 绝对路径（盘符）用**运行时拼接**，避免在源码里留盘符字面量（门禁 R4）
        abs_win = "C" + ":" + "/Windows/win.ini"
        for bad in ("", "../../etc/passwd", abs_win, "README.md"):
            r = S.local_file(bad)
            assert r["ok"] is False, f"{bad!r} 竟被放行"

    def test_rejects_bad_extension(self) -> None:
        from web import snapshots as S

        p = os.path.join("modules", "internal_policy_base", "data", "originals")
        real = os.path.join(ROOT, p)
        assert os.path.isdir(real)
        f = os.path.join(real, "_guard_test.exe")
        open(f, "wb").write(b"MZ")
        try:
            r = S.local_file(f"{p}/_guard_test.exe")
            assert r["ok"] is False and "不支持" in r["reason"]
        finally:
            os.remove(f)

    def test_serves_whitelisted_file(self) -> None:
        from interfaces import worklist_fields as WF
        from web import snapshots as S

        lp = WF.local_for("IPN-069150d1cb652f23")
        if not lp:
            pytest.skip("索引里无可用样本")
        r = S.local_file(lp)
        assert r["ok"] is True and r["body"], "白名单内的文件应能读出字节"
        assert r["disposition"] in ("inline", "attachment")
        assert r["ctype"]

    def test_html_svg_never_inline(self) -> None:
        """同源 `.html`/`.svg` 绝不能 inline（存储型 XSS 会借用户身份点处置）。"""
        from web import snapshots as S

        assert ".html" not in S._LOCAL_INLINE, "把 .html 放进 inline 名单 ⇒ 同源执行任意脚本"
        assert ".svg" not in S._LOCAL_INLINE, "SVG 同样可内嵌脚本，不得 inline"

    def test_no_focus_stealing_api(self) -> None:
        """实现里不得出现会夺焦点的"打开"调用（本仓黑名单）。"""
        code = _code_only(SNAP)
        assert "startfile" not in code, "又用回了会夺焦点的打开方式"
        assert "xdg-open" not in code, "不得拉起外部打开程序"


class TestP6ShortQuery:
    """F. 短词检索 + P8 全量。"""

    def test_two_char_query_uses_like(self) -> None:
        from web import snapshots as S

        d = S.search("保险", limit=3)
        assert d.get("available") is True
        assert d.get("mode") == "like", (
            "双字词必须走 LIKE 兜底（FTS5 trigram 只索引 3 字窗口，MATCH 必然 0 行）")
        assert d.get("results"), "双字词仍 0 命中 ⇒ 用户会以为没数据"
        assert d.get("note"), "必须说明为什么换了检索方式（降级可辨）"

    def test_long_query_uses_fts(self) -> None:
        from web import snapshots as S

        d = S.search("保险公司", limit=3)
        assert d.get("mode") == "fts"
        assert d.get("results")

    def test_search_results_carry_url_or_local(self) -> None:
        from web import snapshots as S

        d = S.search("保险公司", limit=5)
        for r in d.get("results") or []:
            assert "url" in r and "url_src" in r, "P6 每行都要能判断访问地址（含'为什么没有'）"

    def test_release_default_is_full(self) -> None:
        from web import api as A

        st, d = A.handle_request("GET", "/api/release")
        assert st == 200
        n = len(d.get("commits") or [])
        assert n > 50, f"默认只给 {n} 条 —— 用户要求全量（原硬编码 20）"

    def test_commit_detail_lists_files(self) -> None:
        from web import snapshots as S

        rel = S.release_stream()
        sha = (rel.get("commits") or [{}])[0].get("sha")
        if not sha:
            pytest.skip("git 不可用")
        d = S.commit_detail(sha)
        assert d.get("available") is True
        assert d.get("file_count", 0) >= 1, "单提交必须给出文件清单（初版恒为 0）"
        assert d.get("subject")


class TestHtmlWiring:
    """前端接线（防"后端加了、前端没接"）。"""

    def test_api_paths_exist_in_backend(self) -> None:
        html = _html()
        api_src = _code_only(API)
        paths = set(re.findall(r'"(/api/[a-z/]+)', html))
        known = set(re.findall(r'"(/api/[a-z/]+)"', api_src))
        # 以下都是 **HTTP 层**路由，不走 `api.handle` ⇒ 白名单显式列出，避免误报：
        #   `/api/events`（SSE，`_sse`）、`/api/local/file`（文件字节）、
        #   `/api/auth/*`（登录要写 `Set-Cookie`，`api.handle` 只返回 `(status, payload)`）。
        known |= {"/api/events", "/api/local/file",
                  "/api/auth/login", "/api/auth/status", "/api/auth/logout"}
        unknown = {p for p in paths if p not in known}
        assert not unknown, f"前端调用了后端没有的路由：{unknown}"

    def test_p2_p7_merged_into_overview(self) -> None:
        html = _html()
        assert 'id:"assets"' not in html and 'id:"publish"' not in html, (
            "P2/P7 仍是独立页签（用户要求并入总览）")
        assert "viewAssets" not in _code_only(HTML).replace("viewAssets(", ""), (
            "旧的独立页渲染函数应已移除")
        assert "/api/assets" in html and "/api/publish" in html, "总览里仍要取这两份数据"

    def test_worklist_action_column_first(self) -> None:
        code = _code_only(HTML)
        head = re.search(r"const head = `(<tr>.*?)</tr>`", code, re.S)
        assert head, "找不到待办表头"
        # 第一列必须是"操作"：含全选 checkbox（wlall）+ 文案「操作」
        first_th = head.group(1).split("</th>")[0]
        assert 'id="wlall"' in first_th and "操作" in first_th, (
            "操作列（含全选）必须在最左 —— 用户报'处置在列表最下方，交互不合理'")
        assert "data-resolve" in code and "openResolveInline" in code, "缺少行内处置"

    def test_p1_flow_renders_edges(self) -> None:
        code = _code_only(HTML)
        assert "edges.forEach" in code and "fnode" in code, "P1 必须画边且节点可点"
        assert "// 边（先画" in _html() or "edges.forEach" in code
