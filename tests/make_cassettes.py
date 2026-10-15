"""生成 TD-1 的**合成夹具**（origin=synthetic）——人工最小样本，覆盖四类解析入口。

口径声明（重要）：这些样本是**人工构造的最小 HTML/JSON**，不是真实抓取，
用于「给定输入 ⇒ 解析出预期结构」的回归锁定；`origin` 字段与门禁/测试都显式披露这一点，
不冒充真实抓取。真实抓取可用 `python -m tools.record_cassettes --list` 后按源补录
（录制备选会写 origin=recorded 并登记 manifest）。

样本刻意覆盖各解析器的**多路兜底分支**（标题 meta ↔ h2、#zoom ↔ class 容器、
分页隐藏域 ↔ 单页），使回放能同时锁住主路径与兜底路径。
"""
from __future__ import annotations

import os

from bootstrap import bootstrap

bootstrap("all")

from tests import http_cassette as HC

STAMP = "2026-10-15T00:00:00+08:00"
HTML_HEADERS = {"Content-Type": "text/html; charset=utf-8"}
JSON_HEADERS = {"Content-Type": "application/json; charset=utf-8"}

GOV_LIST_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>国务院政策文件库</title></head>
<body>
<ul class="news-list">
  <li><a href="/zhengce/content/2024-01/05/content_1.htm"
         title="国务院关于印发某某管理办法的通知">国务院关于印发某某管理办法的通知</a>
      <span class="date">2024-01-05</span></li>
  <li><a href="/zhengce/content/2024-02/11/content_2.htm"
         title="关于进一步优化营商环境的公告">关于进一步优化营商环境的公告</a>
      <span class="date">2024-02-11</span></li>
</ul>
</body></html>
"""

# 令文形态样本（与 gov.parse 的三项抽取器契约对齐）：
#   · extract_issue_organ 只匹配「X令第N号」⇒ 机关抽取需令文；
#   · extract_doc_number 覆盖令第N号与发文字号两类；
#   · extract_date 覆盖「YYYY年M月D日」。
GOV_DETAIL_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="ArticleTitle"
  content="中华人民共和国国务院令第300号"></head>
<body>
<div class="pages_content">
  <p>中华人民共和国国务院令第300号</p>
  <p>《某某管理办法》已经2024年1月3日国务院第134次常务会议通过，现予公布。</p>
  <p>自2024年3月1日起施行。</p>
  <p>总理　某某</p>
  <p>2024年1月5日</p>
</div>
</body></html>
"""

PBC_LIST_HTML = """<!DOCTYPE html>
<html><body>
<form><input name="article_paging_list_hidden" moduleid="21885abc"
  modulekey="abc123" totalpage="3"></form>
<div class="list">
  <a href="/tiaofasi/2024/202401/t20240105_1001.html" title="中国人民银行公告〔2024〕第1号">
    中国人民银行公告〔2024〕第1号</a>
  <a href="/tiaofasi/2024/202402/t20240211_1002.html" title="中国人民银行公告〔2024〕第2号">
    中国人民银行公告〔2024〕第2号</a>
</div>
</body></html>
"""

PBC_DETAIL_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="ArticleTitle" content="中国人民银行公告〔2024〕第1号"></head>
<body>
<span id="shijian">2024-01-05</span>
<div id="zoom">
  <p>中国人民银行公告〔2024〕第1号</p>
  <p>为做好某某工作，现予发布。</p>
  <p>中国人民银行</p>
  <p>2024年1月5日</p>
</div>
</body></html>
"""

SPECS = [
    ("gov_list_page", "gov", [
        {"method": "GET", "url": "https://www.gov.cn/zhengce/zhengceku/home/index.htm",
         "status": 200, "headers": HTML_HEADERS, "body": GOV_LIST_HTML},
        {"method": "GET", "url": "https://www.gov.cn/zhengce/zhengceku/home/index.htm?_=1720000000",
         "status": 200, "headers": HTML_HEADERS, "body": GOV_LIST_HTML},
    ], "国务院政策文件库列表页（两条；含易变参数变体，验证 URL 归一命中同键）"),
    ("gov_detail_page", "gov", [
        {"method": "GET", "url": "https://www.gov.cn/zhengce/content/2024-01/05/content_1.htm",
         "status": 200, "headers": HTML_HEADERS, "body": GOV_DETAIL_HTML},
    ], "国务院政策详情页（title meta + pages_content 兜底容器 + 文号/发文机关/生效日期）"),
    ("pbc_list_page", "pbc", [
        {"method": "GET", "url": "http://www.pbc.gov.cn/tiaofasi/144941/144951/index.html",
         "status": 200, "headers": HTML_HEADERS, "body": PBC_LIST_HTML},
    ], "人行条法司列表页（分页隐藏域 moduleid/totalpage + /tiaofasi/ 条目）"),
    ("pbc_detail_page", "pbc", [
        {"method": "GET",
         "url": "http://www.pbc.gov.cn/tiaofasi/2024/202401/t20240105_1001.html",
         "status": 200, "headers": HTML_HEADERS, "body": PBC_DETAIL_HTML},
    ], "人行详情页（ArticleTitle meta + #shijian + #zoom 正文）"),
]


def main() -> int:
    for name, source, inter, note in SPECS:
        doc = HC.make_cassette(source=source, interactions=inter,
                               origin="synthetic", note=note, recorded_at=STAMP)
        p = HC.save_cassette(name, doc)
        print(f"[ok] {name}: {len(doc['interactions'])} 条 -> {os.path.relpath(p)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
