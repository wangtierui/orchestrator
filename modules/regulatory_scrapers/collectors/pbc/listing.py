# -*- coding: utf-8 -*-
"""pbc.listing — **列表层**：列表页分页探测 + 条目抽取 + 分页 URL 生成。

纯函数层：输入 HTML 文本 ⇒ 输出 `(moduleid, totalpage, [(title, abs_url), ...])` 或
分页 URL 列表；**不触网、不落盘** ⇒ 可被 `tests/cassettes/` 夹具直接驱动（TD-1 回放
用例经此层锁定行为，见 `tests/test_collector_replay.py`）。

历史坑（有回归用例锁定，勿「顺手简化」）：
  · easysite 分页用 moduleid **前 8 位**而非完整 moduleid——hex 栏目用完整值会全 404；
  · 绝对 URL 以 `BASE`（**https** 站点）解析。
"""
import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
for _p in (_os.path.abspath(_os.path.join(_HERE, "..", "..", "..", "..")),
           _os.path.dirname(_HERE)):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)
del _p, _os, _sys, _HERE

import re
import urllib.parse

# 依赖导入**双形态**（W4 L1 重组纪律）：包形态相对导入 / 脚本形态裸名。
try:  # 包形态
    from .constants import BASE
    from .parse import clean_text, strip_tags
except ImportError:  # 脚本形态（python collectors/pbc/listing.py）
    from constants import BASE  # type: ignore[no-redef]
    from parse import clean_text, strip_tags  # type: ignore[no-redef]

# ----------------------------------------------------------------------------------
# 列表页正则
# ----------------------------------------------------------------------------------
RE_PAGING = re.compile(
    r'name="article_paging_list_hidden"\s+moduleid="([^"]+)"\s+modulekey="[^"]*"\s+totalpage="(\d+)"'
)
RE_ENTRY = re.compile(
    r'<a\s+href="(/tiaofasi/[^"]+)"[^>]*?\btitle="([^"]*)"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)


# ----------------------------------------------------------------------------------
# 列表页解析
# ----------------------------------------------------------------------------------
# ----------------------------------------------------------------------------------
# 列表页解析
# ----------------------------------------------------------------------------------
def parse_listing(html_text, column_dir):
    """
    返回 (moduleid, totalpage, [(title, abs_url), ...])
    column_dir: 栏目目录的绝对 URL（不含 /index.html）
    """
    paging = RE_PAGING.search(html_text)
    moduleid, totalpage = (paging.group(1), int(paging.group(2))) if paging else (None, 1)

    entries = []
    seen = set()
    for m in RE_ENTRY.finditer(html_text):
        href = m.group(1)
        title = clean_text(m.group(2)) or clean_text(strip_tags(m.group(3)))
        if not title:
            continue
        abs_url = urllib.parse.urljoin(BASE + "/", href)
        if abs_url in seen:
            continue
        seen.add(abs_url)
        entries.append((title, abs_url))
    return moduleid, totalpage, entries

def listing_page_urls(index_url, moduleid, totalpage):
    """
    生成列表所有分页 URL（第 1 页为 index.html，后续为 {short}-{n}.html）。
    关键修正：easysite 分页实际使用的是 moduleid 的前 8 位字符，而非隐藏字段里的
    完整 moduleid。数字栏目（如 21885）前 8 位即自身，故原规律碰巧成立；
    hex 栏目（如 3b3662a6db7145c0a025d2d410570ae1）必须用前 8 位 '3b3662a6' 拼接，
    否则全部 404。统一截断前 8 位可同时覆盖两类栏目。
    """
    column_dir = index_url.rsplit("/index.html", 1)[0]
    urls = [index_url]
    if moduleid and totalpage > 1:
        short = moduleid[:8]
        for n in range(2, totalpage + 1):
            urls.append(f"{column_dir}/{short}-{n}.html")
    return urls

