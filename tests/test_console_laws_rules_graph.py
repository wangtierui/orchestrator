# -*- coding: utf-8 -*-
"""tests/test_console_laws_rules_graph.py — 政策法规 / 公司制度 / 关系图谱 / 检索相关性（第一百一十批）。

本批解决的五个用户可见问题
==========================
1. logo 换成 GitHub 标记 → **必须内联 SVG**（本仓零 CDN / 零外链纪律）；
2. 新增「政策法规」页签（16607 条，8 列，表头可筛选可排序）；
3. 新增「公司制度」页签（861 条，访问地址链接**本地文件**）；
4. 新增「关系图谱」页签（点列表"查看"跳转，以该文件为中心的一跳图）；
5. **文档检索出现多个无关文件** —— 根因是 FTS **没有 ORDER BY**：
   `records_fts` 匹配后不加排序时按 **rowid（入库顺序）** 返回，`LIMIT 20`
   就成了"最早入库的 20 条含该词的文档"。实测 `保险公司` 不排序返回
   「快递暂行条例 / 森林病虫害防治条例」，加 `bm25(...)` 后返回
   「保险公司控股股东管理办法 / 保险公司股权管理办法」。

守卫分组
--------
A 分页与排序白名单（含注入尝试）      B 逐列筛选（q_title/q_docno/q_rfn、EMPTY_FILTER）
C 检索相关性（bm25 排序、多词 AND、短词判定、去重）
D 关系图谱（中心/邻居/双向/弱线索披露）
E web 层补全（主题标签归一、本地文件、kind_label）
F 前端契约（页签、可排序表头、筛选控件、内联 logo、零外链、跳转联动）
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.contract

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML = os.path.join(ROOT, "web", "assets", "index.html")


def _html() -> str:
    return open(HTML, encoding="utf-8").read()


def _code_only(path: str) -> str:
    """去掉注释与 docstring，只留代码（守卫要管代码，不管解释文字）。"""
    out, in_doc = [], False
    for line in open(path, encoding="utf-8").read().splitlines():
        s = line.strip()
        if in_doc:
            if '"""' in s or "'''" in s:
                in_doc = False
            continue
        if s.startswith('"""') or s.startswith("'''"):
            q = '"""' if s.startswith('"""') else "'''"
            if s.count(q) < 2:
                in_doc = True
            continue
        out.append(line.split("#", 1)[0])
    return "\n".join(out)


class TestPaging:
    """A. 分页 + 排序白名单。"""

    def test_first_page_shape(self) -> None:
        from interfaces import base_api as B

        d = B.list_external_page(size=5)
        assert d["total"] > 10000, "外部底座应有万级记录"
        assert len(d["items"]) == 5
        assert d["pages"] == max(1, (d["total"] + 4) // 5)
        assert d["page"] == 1

    def test_page_beyond_end_is_empty_but_total_kept(self) -> None:
        from interfaces import base_api as B

        d = B.list_external_page(page=999999999, size=50)
        assert d["items"] == []
        assert d["total"] > 0, "越界页仍须给出总数（否则前端以为没数据）"

    def test_size_is_clamped(self) -> None:
        from interfaces import base_api as B

        assert len(B.list_external_page(size=100000)["items"]) <= B._MAX_PAGE_SIZE
        assert len(B.list_internal_page(size=0)["items"]) >= 1, "size=0 应回退到默认"

    def test_sort_is_whitelisted(self) -> None:
        """列名直接进 SQL ⇒ 必须白名单（否则就是注入面）。"""
        from interfaces import base_api as B

        bad = B.list_external_page(sort="title; DROP TABLE records--", size=1)
        assert bad["sort"] == "publish_date", "非法排序列未回退"
        assert bad["total"] > 0, "表还在（注入未生效）"

    def test_sort_direction_works(self) -> None:
        from interfaces import base_api as B

        asc = B.list_external_page(size=3, sort="title", desc=False)["items"]
        desc = B.list_external_page(size=3, sort="title", desc=True)["items"]
        assert asc[0]["title"] != desc[0]["title"]


class TestColumnFilters:
    """B. 逐列筛选（"表头可筛选"的后端保证）。"""

    def test_source_filter(self) -> None:
        from interfaces import base_api as B

        d = B.list_external_page(source="mof", size=1)
        assert 0 < d["total"] < 2000
        assert d["items"][0]["source"] == "mof"

    def test_docno_column_filter(self) -> None:
        from interfaces import base_api as B

        d = B.list_external_page(q_docno="银保监办发", size=3)
        assert d["total"] > 0
        assert all("银保监办发" in (it.get("document_number") or "")
                   for it in d["items"])

    def test_empty_filter_sentinel(self) -> None:
        """`__none__` = 筛"该字段为空"（未定主题 / 未核验）。"""
        from interfaces import base_api as B

        d = B.list_external_page(theme=B.EMPTY_FILTER, size=1)
        assert d["total"] > 0
        assert not (d["items"][0].get("theme") or ""), "空筛仍返回了有主题的行"

    def test_multi_token_q_is_and(self) -> None:
        from interfaces import base_api as B

        both = B.list_external_page(q="保险公司 营销", size=3)
        one = B.list_external_page(q="保险公司", size=1)
        assert 0 < both["total"] < one["total"], "多词应是 AND（结果少于单词）"

    def test_facets_have_counts(self) -> None:
        # 源清单从**受控枚举**取（在测试里写死源名会被 gate_enum_values 判为副本）
        from config.enums import SOURCE_ORDER
        from interfaces import base_api as B

        f = B.external_facets()
        values = {x["value"] for x in f["sources"]}
        assert set(SOURCE_ORDER) <= values, f"缺少源：{set(SOURCE_ORDER) - values}"
        assert all(x["count"] > 0 for x in f["sources"])
        i = B.internal_facets()
        assert i["themes"], "内部制度应有主题分布"


class TestSearchRelevance:
    """C. 检索相关性（用户报"出现多个无关文件"）。"""

    def test_fts_sorting_is_present(self) -> None:
        """FTS 查询**必须**带 bm25 排序 —— 不加就是按 rowid（入库顺序）返回。"""
        src = _code_only(os.path.join(ROOT, "interfaces", "base_api.py"))
        assert "ORDER BY bm25(records_fts" in src, "外部 FTS 缺相关性排序"
        assert "ORDER BY bm25(policies_fts" in src, "内部 FTS 缺相关性排序"
        assert "ORDER BY bm25(clauses_fts" in src

    def test_result_is_relevant_by_title(self) -> None:
        """回归实测：`保险公司` 的**首条**必须与词直接相关。"""
        from interfaces import base_api as B

        rows = B.search_external("保险公司", limit=3)
        assert rows, "应能检索到结果"
        assert "保险公司" in (rows[0]["title"] or ""), (
            f"首条不相关：{rows[0]['title']!r}（bm25 权重可能被改坏）")

    def test_multi_token_and(self) -> None:
        from interfaces import base_api as B

        q = B.fts_query("保险公司 营销")
        assert q == '"保险公司" AND "营销"', f"多词未按 AND 组合：{q}"

    def test_short_term_detection_is_per_token(self) -> None:
        """按**词元**判定，而不是整串长度（原实现 `len(q)<3` 的坑）。"""
        from interfaces import base_api as B

        assert B.fts_capable("保险公司") is True
        assert B.fts_capable("保险") is False
        assert B.fts_capable("保险公司 营销") is False, "含 2 字词元时 FTS 必然 0 命中"
        assert B.fts_capable("   ") is False

    def test_like_fallback_is_and_and_deduped(self) -> None:
        from interfaces import base_api as B

        rows = B.search_external_like("农业 保险", limit=5)
        for r in rows:
            assert "农业" in (r["title"] or "") and "保险" in (r["title"] or "")
        keys = [(r.get("title"), r.get("document_number")) for r in rows]
        assert len(keys) == len(set(keys)), "同标题同文号的重复行未去重"

    def test_results_carry_url(self) -> None:
        """检索结果必须带 `url`（底座 16602/16607 有值），否则页签"访问地址"列全空。"""
        from interfaces import base_api as B

        rows = B.search_external("保险公司", limit=3)
        assert any(r.get("url") for r in rows), "检索结果未带 url"


@pytest.fixture(scope="module")
def sample():
    """从真实关系产物里各取一个"有关系的" IPN / RFN 做样本（只读）。

    放在**模块级**而不是类内：类内 `scope="class"` 的 fixture 在 pytest 新版
    已弃用（PytestRemovedIn10Warning），且类内定义的实例方法无法共享状态。
    """
    from interfaces import relations_api as R

    rows = R.load("all")
    ipn = next(r["src_ref"] for r in rows
               if r.get("src_kind") == "internal" and r.get("dst_ref"))
    rfn = next(r["dst_ref"] for r in rows
               if r.get("dst_kind") == "regulatory" and r.get("dst_ref"))
    return ipn, rfn


class TestRelationGraph:
    """D. 关系图谱数据。"""

    def test_internal_ego(self, sample) -> None:
        from interfaces import relations_api as R

        ipn, _ = sample
        e = R.ego(ipn)
        assert e["available"] and e["ref"] == ipn
        assert e["edges"], "内部制度应能取到依据关系"
        assert e["center"]["name"], "中心节点缺名称"
        assert e["center"]["kind"] == "internal", "中心节点 kind 未补全"

    def test_regulatory_ego_includes_inbound(self, sample) -> None:
        from interfaces import relations_api as R

        _, rfn = sample
        e = R.ego(rfn)
        assert e["counts"]["in"] >= 1, "反向（谁依据了我）未取到"
        assert all(x["kind"] in ("internal", "regulatory", "") for x in e["nodes"])

    def test_counts_match_drawn_edges(self, sample) -> None:
        """`counts.out+in` 必须等于**画出的**边数（否则读者以为渲染丢边）。"""
        from interfaces import relations_api as R

        ipn, _ = sample
        e = R.ego(ipn)
        assert e["counts"]["out"] + e["counts"]["in"] == len(e["edges"])

    def test_weak_links_disclosed_not_silent(self, sample) -> None:
        """弱线索（`dst_ref` 为空、只靠 `dst_key` 命中）必须**显式计数**，不能静默丢。

        注意：`raw_out+raw_in > len(edges)` 也可能来自**去重**（同一 relation_id）
        或节点 id 缺失，所以不能反推"必有弱线索"——这里直接对事实源算一遍再比对。
        """
        from interfaces import relations_api as R

        _, rfn = sample
        e = R.ego(rfn)
        expect = sum(1 for r in (R.by_src(rfn) + R.by_dst(rfn)) if not r.get("dst_ref"))
        assert e["counts"]["weak_skipped"] == expect, "弱线索计数与事实源不一致"
        # 显式开启时应把弱线索一并画出（数量不小于默认）
        e2 = R.ego(rfn, include_weak=True)
        assert len(e2["edges"]) >= len(e["edges"])

    def test_unknown_ref_is_empty_not_error(self) -> None:
        from interfaces import relations_api as R

        e = R.ego("RFN-0000000000000000")
        assert e["available"] is True
        assert e["edges"] == []

    def test_weak_only_file_shows_no_fake_edges(self) -> None:
        """"只有弱线索"的文件：**不画边**，但必须披露条数。

        实测：有关系的 340 个实体里，**180 个（53%）**的关系全部指向未登记文件
        （只有 `dst_key`）。若图上一律显示"暂无关系"，读者会以为"这份文件没有依据关系"，
        而事实是"关系存在、只是对方没登记 RFN"——两种结论对合规判断完全不同。
        """
        from interfaces import relations_api as R

        rows = R.load("all")
        stat: dict[str, list[int]] = {}
        for r in rows:
            s = str(r.get("src_ref") or "")
            if not s:
                continue
            stat.setdefault(s, [0, 0])
            stat[s][0] += 1
            if r.get("dst_ref"):
                stat[s][1] += 1
        weak_only = [k for k, (tot, strong) in stat.items() if tot and not strong]
        assert weak_only, "样本缺失（应存在仅弱线索的实体）"
        ref = weak_only[0]
        e = R.ego(ref)
        rows = [r for r in (R.by_src(ref) + R.by_dst(ref)) if not r.get("dst_ref")]
        # 两类必须区分（2026-10-23）：
        #   · `corpus`（有 dst_key）：**不画**（画出来等于把"未登记"伪装成"已定位"），
        #     只在 `weak_skipped` 里计数；
        #   · `external`（语料外文件）：**画**，但每条边必须 `external=True`
        #     （编号 RFN-EXT-*，前端虚线，不冒充语料内实体）。
        corpus_rows = [r for r in rows if r.get("dst_key")]
        ext_rows = [r for r in rows if not r.get("dst_key")]
        if corpus_rows:
            assert e["counts"]["weak_skipped"] >= len(corpus_rows), "未披露被过滤的弱线索条数"
        if ext_rows:
            assert e["edges"], "语料外目标应当画出（已有 RFN-EXT 编号）"
            assert all(x.get("external") for x in e["edges"]), (
                "语料外边未标 external ⇒ 前端会把它画成'已定位实体'")
        assert not any(str(x["to"]).startswith("key:") for x in e["edges"]), (
            "corpus 弱线索被画成节点（会伪装成已定位）")


class TestWebLayer:
    """E. web 层补全（标签归一 / 本地文件 / kind_label）。"""

    def test_records_page_labels_and_url(self) -> None:
        from web import snapshots as S

        d = S.records_page(size=3, source="nfra")
        assert d["total"] > 0
        for it in d["items"]:
            assert "theme_label" in it
            assert it.get("url"), "外部记录应带官方链接"

    def test_theme_label_normalized(self) -> None:
        """内部存代号（T1）、外部存全名 —— 必须归一，否则同页出现两种写法。"""
        from web import snapshots as S

        assert S._theme_label("T1").startswith("T1销售")
        assert S._theme_label("T1销售行为与消费者保护").startswith("T1销售")
        assert S._theme_label("UNALIGNED") == "未对齐"
        assert S._theme_label("") == ""

    def test_rules_page_has_local_path(self) -> None:
        from web import snapshots as S

        d = S.rules_page(size=5)
        assert d["total"] > 0
        assert any(it.get("local_path") for it in d["items"]), (
            "公司制度页签必须有本地文件路径（用户要求'访问地址复用链接本地文件'）")

    def test_relation_graph_labels(self) -> None:
        from interfaces import relations_api as R
        from web import snapshots as S

        rows = R.load("all")
        ref = next(r["src_ref"] for r in rows if r.get("dst_ref"))
        d = S.relation_graph(ref)
        assert d["available"]
        assert all("kind_label" in n for n in d["nodes"])

    def test_routes_registered(self) -> None:
        from web import api

        for path in ("/api/records?size=1", "/api/records/facets",
                     "/api/rules?size=1", "/api/rules/facets",
                     "/api/relations?ref=RFN-x"):
            st, _d = api.handle_request("GET", path)
            assert st in (200, 404), f"{path} 异常状态 {st}"
            assert st == 200, f"{path} 未注册"


class TestFrontendContract:
    """F. 前端契约（静态断言，防"功能做了但页面没接"）。"""

    def test_tabs_added(self) -> None:
        h = _html()
        for tid, name in (("laws", "政策法规"), ("rules", "公司制度"),
                          ("graph", "关系图谱")):
            assert f'{{id:"{tid}"' in h, f"缺页签 {tid}"
            assert name in h, f"缺页签名称 {name}"

    def test_github_logo_is_inline_svg(self) -> None:
        """批 124：logo 改由**本地 Octicons** 注入（仍是内联 SVG，只是不再硬编码在 HTML 里）。

        判据从"HTML 里有没有那段 path"改为"注入点 + 图标源"：
        `#brandIco` 存在、`initChrome` 里注入 `mark-github`、`octicons.js` 里确有该图标。
        """
        h = _html()
        assert 'id="brandIco"' in h, "缺 logo 注入点"
        chrome = h[h.index("function initChrome("):]
        chrome = chrome[:chrome.index("function ", 10)]
        assert 'setIco("#brandIco", "mark-github", 16)' in chrome, "logo 未使用官方 mark-github"
        oct_js = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "web", "assets", "octicons.js")
        assert '"mark-github"' in open(oct_js, encoding="utf-8").read(), "octicons.js 缺 mark-github"

    def test_zero_external_links(self) -> None:
        """零 CDN 纪律：页面不得出现 http(s) 外链（logo 必须内联）。"""
        h = _html()
        assert "http://" not in h and "https://" not in h, "出现外链（破坏离线可用）"

    def test_sortable_headers_and_filters(self) -> None:
        """2026-10-27：列表页改 GitHub 风格后，**逐列输入框**换成"筛选 Popover + 表头菜单"。

        判据从"表头里有没有 input"改为"筛选是否仍然落到后端参数"：
        切片/筛选写回 `STATE.laws|rules` 的同名字段（theme/timeliness/source/q_*），
        由 `toQuery` 拼进 `/api/records|rules` —— 数据层一行没动。
        """
        h = _html()
        assert "function lvRender(" in h, "缺列表视图引擎"
        assert "function lvBind(" in h
        # 服务端筛选参数仍在状态里被读写
        for k in ("q_title", "q_docno", "theme", "timeliness", "source"):
            assert k in h, k
        # 排序：表头按钮 + 后端 sort 参数
        assert "data-lvsort" in h and "st.sort" in h
        # 切片：必须映射到后端分面字段（不是只过滤当前页）
        assert "lvSliceOptions" in h and "f.slice" in h

    def test_rules_uses_local_file_link(self) -> None:
        h = _html()
        assert "/api/local/file?path=" in h, "公司制度未接本地文件链接"

    def test_graph_jump_wired(self) -> None:
        h = _html()
        assert "function goGraph(" in h, "缺跳转函数"
        assert 'data-rel=' in h, "列表缺'关系·查看'入口"
        assert "g.gnode" in h, "关系图节点未绑定点击"
        assert "relations_api.ego" in h or "/api/relations?ref=" in h, "关系图未接数据"

    def test_server_side_paging_not_full_dump(self) -> None:
        """必须服务端分页：16607 条整包下发会让首屏卡死（同 22s 教训）。

        判据：前端只把 `page/size/sort/desc/筛选` 拼成 query 发给后端，
        由后端 `LIMIT/OFFSET` 取一页 —— 断言"分页参数进 query"+"列表端点被调用"。
        """
        h = _html()
        assert "const toQuery" in h, "未把分页条件拼成 query"
        assert 'api("/api/records?" + toQuery(st))' in h, "政策法规未走后端分页"
        assert 'api("/api/rules?" + toQuery(st))' in h, "公司制度未走后端分页"
        assert "page:1" in h and "size:50" in h, "缺默认分页参数"
        assert "size=16607" not in h and "limit=16607" not in h, "疑似整包下发"
