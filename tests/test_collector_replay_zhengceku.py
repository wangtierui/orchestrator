# -*- coding: utf-8 -*-
"""tests/test_collector_replay_zhengceku.py — gov/zhengceku 纯函数层的夹具回放（D-遗留第二批）。

为什么现在补（TD-1 的等价性保障）：`gov/zhengceku.py`（802 行）里
`scan_list` / `total_pages` / `parse_detail` / `_meta_field` / `pick_content_html`
都是**纯函数**（html 进、结构出），是按层拆分（E2）的天然边界——
但拆分前必须先有夹具锁定行为，否则拆分后无从证明"零行为变更"。

锁定的是**各函数的真实契约**（实测得出，非想当然）：
  · `total_pages` 读 JS 变量 `nPageCount`（不是 easysite 的 totalpage 属性）；
  · `scan_list` 要求条目 URL 匹配 `content_<数字>.htm`，且做绝对化 + 去重；
  · `parse_detail.title` 取自 `<title>` 标签（**不是** `ArticleTitle` meta）；
  · `publish_date` 取自**成文日期**并归一为 ISO（不是"发布日期"行）——
    这是 zhengceku.py:282 的设计（`pub = _iso(_meta_field(html, "成文日期", 40))`），
    写进用例以防有人"顺手改成发布日期"而无人察觉。
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.contract

from modules.regulatory_scrapers.collectors.gov import zhengceku as Z

LIST_URL = "https://www.gov.cn/zhengce/zhengceku/bmwj/home.htm"
DETAIL_URL = "https://www.gov.cn/zhengce/content/202401/content_1001.htm"


@pytest.fixture(scope="module")
def list_html() -> str:
    from tests import http_cassette as HC

    return HC.CassettePlayer("zhengceku_list_page").get("GET", LIST_URL).read().decode("utf-8")


@pytest.fixture(scope="module")
def detail_html() -> str:
    from tests import http_cassette as HC

    return (HC.CassettePlayer("zhengceku_detail_page").get("GET", DETAIL_URL)
            .read().decode("utf-8"))


# --------------------------------------------------------------------------- #
# 列表层
# --------------------------------------------------------------------------- #
def test_total_pages_reads_js_variable(list_html: str) -> None:
    """锁定契约①：分页取自 JS 变量 `nPageCount = N`（夹具置 3）。"""
    assert Z.total_pages(list_html) == 3


def test_total_pages_defaults_to_one() -> None:
    """负例：取不到分页变量时返回 1（不是 0、不是抛错）。"""
    assert Z.total_pages("<html><body>无分页变量</body></html>") == 1
    assert Z.total_pages("") == 1


def test_scan_list_extracts_and_dedupes(list_html: str) -> None:
    """锁定契约②：条目抽取 + 相对 URL 绝对化 + 同 URL 去重（夹具 3 条 → 2 条）。"""
    items = Z.scan_list(list_html, LIST_URL)
    assert len(items) == 2, f"去重应把 3 条收敛为 2 条：{items}"
    urls = [u for u, _, _ in items]
    assert urls[0].endswith("/202401/content_1001.htm"), urls
    assert urls[0].startswith("https://www.gov.cn/"), f"相对 URL 未绝对化：{urls}"
    assert urls[1].endswith("/202312/content_9988.htm"), urls
    # 标题与日期
    assert items[0][1] == "国务院关于印发某某管理办法的通知"
    assert items[0][2] == "2024-01-05"


def test_scan_list_ignores_non_content_urls() -> None:
    """负例：非 `content_<数字>.htm` 的链接不计入（真实契约）。"""
    html = ('<ul><li><a href="/other/abc.htm" title="甲">甲</a>'
            '<a href="/x/content_12.htm" title="乙">乙</a></ul>')
    items = Z.scan_list(html, LIST_URL)
    assert [t for _, t, _ in items] == ["乙"], items


# --------------------------------------------------------------------------- #
# 详情层
# --------------------------------------------------------------------------- #
def test_parse_detail_fields(detail_html: str) -> None:
    """正例：详情字段全链路（title/文号/机关/日期/正文）。"""
    d = Z.parse_detail(detail_html, DETAIL_URL)
    assert d["title"] == "国务院关于印发某某管理办法的通知"
    assert d["document_number"] == "国发〔2024〕3号"
    assert d["issue_organ"] == "国务院"
    assert d["publish_date"] == "2024-01-03", "成文日期应归一为 ISO"
    assert d["pub_date_original"] == "2024年1月3日", "原文形态须保留"
    assert "第一条" in d["full_text"] and "第三条" in d["full_text"]


def test_parse_detail_title_comes_from_title_tag(detail_html: str) -> None:
    """锁定契约③：title 取自 `<title>`（站点后缀被分隔符截断），非 ArticleTitle meta。"""
    d = Z.parse_detail(detail_html, DETAIL_URL)
    # 夹具 <title> 为 "国务院关于印发某某管理办法的通知 - 中国政府网"
    # 代码用 re.split(r"[_\-|]")[0] 截断 ⇒ 不含站点后缀
    assert d["title"] == "国务院关于印发某某管理办法的通知"
    assert "中国政府网" not in d["title"], "站点后缀应被截断"


def test_publish_date_comes_from_chengwen_not_fabuqi(detail_html: str) -> None:
    """锁定契约④：`publish_date` 取**成文日期**（zhengceku.py:282 的设计，非发布日期）。

    这条容易被"顺手改掉"——若有人改成读「发布日期」行，本用例会失败并指出原因。
    """
    html = detail_html.replace(
        "<tr><td>成文日期：2024年1月3日</td></tr>",
        "<tr><td>发布日期：2030年9月9日</td></tr><tr><td>成文日期：2024年1月3日</td></tr>")
    d = Z.parse_detail(html, DETAIL_URL)
    assert d["publish_date"] == "2024-01-03", "publish_date 应来自成文日期，而非发布日期"


def test_meta_field_rejects_label_like_values(detail_html: str) -> None:
    """负例：字段为空时不得取到相邻标签文本（zhengceku.py:242 的历史修复）。"""
    html = detail_html.replace(
        "<tr><td>发文字号：国发〔2024〕3号</td></tr>",
        "<tr><td>发文字号：</td></tr><tr><td>来 源：国务院</td></tr>")
    assert Z._meta_field(html, "发文字号") == "", "空值窗口滑到相邻标签会造成污染"


def test_pick_content_html_prefers_largest(detail_html: str) -> None:
    """正例：正文容器挑选取文本量最大者。"""
    body = Z.pick_content_html(detail_html)
    assert "第一条" in body and "第三条" in body


def test_should_stop_paging() -> None:
    """正例：分页终止判据（连续空页达阈值即停；backfill 模式不早停）。"""
    assert Z.should_stop_paging(0, 3, False) is False
    assert Z.should_stop_paging(3, 3, False) is True
    assert Z.should_stop_paging(0, 3, True) is False, "backfill 模式不因空页早停"


# --------------------------------------------------------------------------- #
# 附件层（复用第 3 处修复：中文名URL 编码）
# --------------------------------------------------------------------------- #
def test_chinese_attachment_urls_are_encoded(detail_html: str) -> None:
    """正例：详情页的中文名附件链接被 percent-encode（否则下载层崩溃）。"""
    from modules.regulatory_scrapers.collectors.gov.attachments import (
        scan_attachment_links,
    )

    links = [u for u, _ in scan_attachment_links(detail_html, DETAIL_URL)]
    cn = [u for u in links if "%E9%99%84" in u]
    assert len(cn) == 2, f"两个中文名附件应被编码：{links}"
    for u in cn:
        u.encode("ascii")  # 不抛即通过（缺陷的直接症状）
