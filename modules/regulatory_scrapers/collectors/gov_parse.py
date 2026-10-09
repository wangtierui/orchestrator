#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gov_regulations_scraper.py
===========================

自动抓取行政法规库（xzfg.moj.gov.cn）的行政法规条目，并结构化输出（含正文全文）：
  - xzfgk —— 行政法规库（司法部 xzfg.moj.gov.cn）
             列表与详情页均为服务端渲染（HTML），可用 requests 直接抓取；
             详情页含正文全文/发文字号/生效日期/发文机关。

2026-09-04：仅保留 xzfgk 数据源。flk（国家法律法规数据库，flk.npc.gov.cn）
正文原文存储于站内 OBS 外部不可达，仅元数据无正文，已停止抓取并清理相关代码。

通用能力：
  - 会话复用 + 连接池；请求头 / User-Agent 轮换；
  - 随机延时 + 指数退避重试，规避反爬、控制请求频率；
  - 自动遍历分页（?PageIndex= 或 点击"下一页"）；
  - 对每条目二次请求详情页，抽取正文全文、发文字号、生效日期、发文机关；
  - 输出结构化 JSON（含全文）与 CSV 表格（标题/类别/发布日期/正文链接/摘要）；
  - 完善的异常捕获与运行日志，单条失败不影响整体。

用法示例：
  # 完整抓取行政法规库（含全部详情正文），输出 gov_laws.jsonl/.csv（覆盖更新；N-206/S-A 起为 JSONL）
  python scraper.py

  # 快速验证（前 2 页、5 条详情）
  python scraper.py --max-pages 2 --max-items 5

依赖：
  pip install requests beautifulsoup4 lxml
"""

from __future__ import annotations

# ---- P3b 仓库引导（migrate_collectors_p3b.py 注入）：使 std_lib/config 可导入 ----
import os as _os
import sys as _sys

_GUIDE_ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "..", ".."))
if _GUIDE_ROOT not in _sys.path:
    _sys.path.insert(0, _GUIDE_ROOT)
del _GUIDE_ROOT, _os, _sys
import csv
import json
import logging
import os
import re
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any

try:
    import requests  # noqa: F401
    from bs4 import BeautifulSoup  # noqa: F401
    from requests.adapters import HTTPAdapter  # noqa: F401
    from urllib3.util.retry import Retry  # noqa: F401
except ImportError as e:  # pragma: no cover
    sys.stderr.write(
        "缺少依赖，请先执行：\n"
        "  pip install requests beautifulsoup4 lxml\n"
        f"原始错误：{e}\n"
    )
    sys.exit(2)

# 附件下载 + 文本抽取（修复：gov 此前完全不抓附件）
try:
    from gov_fetch_attachments import fetch_gov_attachments
except ImportError:  # pragma: no cover
    def fetch_gov_attachments(*a, **k):
        return [], ""

LOG = logging.getLogger("gov_scraper")

# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #

# 常用桌面浏览器 UA 池（轮换以降低被识别为脚本的概率）
# （共享库对齐审计 阶段 4）实测：本文件原有 UA 池与 ``crawler_common.USER_AGENTS``
# **逐字相同**（md5 一致、n=5），故改为复用共享库，消除重复副本；
# 沿用 nfra 既有「try 导入 + 缺模块回退单 UA」降级样板，避免缺依赖时启动失败。
_STD_LIB_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _STD_LIB_ROOT not in sys.path:
    sys.path.insert(0, _STD_LIB_ROOT)
try:
    from std_lib.scraper_std.crawler_common import USER_AGENTS
except ImportError:  # pragma: no cover
    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ]

# 统一文号提取器（2026-09-05 五源共享：doc_number 模块，15+ 优先级正则 + 规范化）
try:
    from std_lib.scraper_std.doc_number import extract_doc_number as _unified_doc_number
except ImportError:  # pragma: no cover
    _unified_doc_number = None  # type: ignore[assignment]

# 默认请求头（模拟真实浏览器）
# 通用缓存模块（五源统一抽象层：std_lib/scraper_std/cache_store，2026-09-05）
# gov xzfgk 列表/详情页均为服务端渲染 HTML → 委托 TextResponseCache（存储 HTML 文本）；
# 命中读盘跳过网络、离线缺失抛 OfflineMiss、仅成功响应（非空）落盘，不缓存错误/拦截页。
try:
    from std_lib.scraper_std.cache_store import OfflineMiss, bind_source_cache  # noqa: F401
except ImportError:  # pragma: no cover
    from std_lib.scraper_std.cache_store import OfflineMiss

_RESP_TEXT = None  # TextResponseCache 实例；None 表示未启用缓存
_OfflineMiss = OfflineMiss  # 兼容别名



DEFAULT_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
    "application/json;q=0.8,*/*;q=0.7",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    # 注意：不要包含 br（brotli）。本机 urllib3 未安装 brotli 解码器，
    # 一旦服务器回 brotli 压缩，resp.content 会是未解压乱码字节，
    # decode_html 解出乱码 → 选择器全部落空 → 详情正文=0（海量 gov 子站因此被误判为空）。
    # 去掉 br 后服务器回退 gzip，urllib3 可正常解压。
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

CONTENT_SELECTORS = [
    ".law-chapter",        # 司法部行政法规详情正文
    "#content",
    "#UCAP-CONTENT",
    ".content-text",
    ".pages_content",
    ".article",
    "#zoom",
    "#article-content",
    ".TRS_Editor",
    "div.content",
]

TITLE_SELECTORS = ["h1", ".article-title", "#title", ".title", "h2"]

_ORGAN_KW = r"(?:国务院|部|委员会|局|人民政府|政府|厅|署|中央军委)"

_NOISE_CLASS_HINTS = ["download", "fold", "historical", "tip", "btn", "share", "qr", "vconsole"]

_SCHEME_UPGRADE_RE = re.compile(r'targetProtocol\s*=\s*["\']https:', re.I)

CSV_COLUMNS = [
    "title", "category", "publish_date", "pub_date_original", "effective_date",
    "issue_organ", "document_number", "detail_url", "summary", "source",
]

SOURCES = {
    # 2026-09-04：仅保留 xzfgk（行政法规库）。flk（国家法律法规数据库）正文存站内
    # OBS 外部不可达、仅元数据无正文，已停止抓取并清理（见 docs/gov正文缺失排查报告）。
    "xzfgk": ("https://www.gov.cn/zhengce/xzfgk/", "行政法规"),
    # 2026-09-15：纳入「国务院政策文件库·国务院部门文件」（zhengceku/bmwj，629 页）
    # 为 gov 源第 2 个子源。此前该栏目完全缺采，导致其下规范性文件（如银发〔2019〕316号）
    # 在 gov 源无任何记录；且该栏目正文常以附件(.doc/.pdf)形式发布，须同时抓附件原文。
    # 实现见 collectors/gov_zhengceku.py（ZhengcekuScraper）。
    "zhengceku": ("https://www.gov.cn/zhengce/zhengceku/bmwj/home.htm", "部门文件"),
}
# ⚠️ 本 dict 为 gov 源子源登记表的**唯一事实源**；gov_collector.py 从此处导入，
# 不在采集器主文件中重复定义（避免两处漂移，见 2026-09-15 收敛）。


_SCRAPERS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if _SCRAPERS_ROOT not in sys.path:
    sys.path.insert(0, _SCRAPERS_ROOT)


@dataclass
class ScrapeConfig:
    source: str
    base_url: str
    category: str
    out_dir: str = "data/raw"
    max_pages: int = 0          # 0 = 不限制
    max_items: int = 0          # 0 = 不限制（仅列表条目数）
    fetch_details: bool = True
    details_limit: int = 0      # 仅对前 N 条抓取详情正文（0=全部，需配合 fetch_details）
    resume: bool = False        # 增量续抓：基于 out_dir 最新同源输出跳过已抓条目
    delay_min: float = 1.5      # 列表/详情请求间最小延时（秒）
    delay_max: float = 3.5      # 最大延时（秒）
    timeout: int = 30
    retries: int = 4
    summary_len: int = 200
    # 详情阶段断点落盘间隔（条；0=不落暂存）。供长时子源（zhengceku，全量约 30 小时）
    # 在中断后可续跑——主库只在全部结束后写一次，中途中断必须靠暂存文件兜底。
    checkpoint_every: int = 200
    # ---- N-190（2026-10-08）**增量治理**参数（两子源共用；判据见 gov_zhengceku.should_stop_paging）----
    #: 连续 N 页无「新条目」（detail_url 不在主库）即停止翻页（**仅 zhengceku**；xzfgk 早有等价逻辑）。
    #: 为何需要：zhengceku 列表 629 页、原实现**恒扫全部页**，实测单轮 ~20+ 分钟纯空转
    #: （页面全是主库既有条目）。列表按发布日期倒序 ⇒ 首页起后续只会更旧。
    stop_after_empty_pages: int = 1
    #: 历史存量补全模式：**忽略早停**、扫完全部列表页（月度作业用；不进周度增量）。
    #: 保留原"持续补全 zhengceku 历史正文"的意图，但把它与"追新"**分离**，各自独立排程与计时。
    backfill: bool = False
    #: 详情阶段**软预算**（秒；0=不限）——到达即停止取新详情并**正常落盘退出**（rc=0，
    #: `stopped_reason=budget`）。为何要软预算：撞上层 7200s **硬超时**会被强杀，
    #: 而"未走完流程的产物"仍会落盘并被下游采用（N-152 的教训）。
    max_seconds: float = 0.0


def clean_text(s: Any) -> str:
    if s is None:
        return ""
    if not isinstance(s, str):
        s = str(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def decode_html(content: bytes) -> str:
    """
    鲁棒解码中文网页字节：先尝试 UTF-8，再尝试 GB18030/GBK。
    以「汉字占比最高、替换符最少」为准，避免把 GBK 页面误当 UTF-8 解码成乱码。
    """
    if not content:
        return ""
    cands: list[str] = []
    try:
        cands.append(content.decode("utf-8"))
    except UnicodeDecodeError:
        pass
    for enc in ("gb18030", "gbk"):
        try:
            cands.append(content.decode(enc))
        except UnicodeDecodeError:
            pass
    if not cands:
        return content.decode("utf-8", errors="replace")

    def _score(s: str) -> int:
        cjk = sum(1 for ch in s if "一" <= ch <= "鿿")
        return cjk - s.count("�") * 10

    return max(cands, key=_score)


def extract_date(text: str) -> str:
    """从文本中抽取第一个 YYYY年MM月DD日 / YYYY-MM-DD 日期。"""
    if not isinstance(text, str) or not text:
        return ""
    m = re.search(r"\d{4}[-年]\d{1,2}[-月]\d{1,2}日?", text)
    if not m:
        m = re.search(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}", text)
    return m.group(0) if m else ""


def extract_doc_number(text: str) -> str:
    """抽取发文字号，如『国务院令第300号』→ 国务院令第300号。

    2026-09-05：优先委托五源统一模块 ``std_lib.scraper_std.doc_number``
    （15+ 优先级正则 + 半角〔〕规范化 + 标题内嵌文号）；模块缺失或未命中时
    回退原本地正则，保证抓取行为不劣化。
    """
    if not text:
        return ""
    if _unified_doc_number is not None:
        dn = _unified_doc_number(text)
        if dn:
            return dn
    m = re.search(r"([\u4e00-\u9fa5]{2,14}?" + _ORGAN_KW + r"令第\s*\d+\s*号)", text)
    if not m:
        m = re.search(r"[\u4e00-\u9fa5（(][\u4e00-\u9fa5\d（）()]+?〔\d{4}〕\d+\s*号", text)
    if not m:
        return ""
    # 第二条正则（〔YYYY〕N号 格式）无捕获组，统一用 group(0) 避免 IndexError
    s = re.sub(r"^[年月日（）()\s]+", "", m.group(0))  # 去掉前缀多余的日期字
    return clean_text(s)


def extract_issue_organ(text: str) -> str:
    """从发文字号上下文抽取发文机关，如『中华人民共和国国务院令第300号』。"""
    if not text:
        return ""
    m = re.search(r"([\u4e00-\u9fa5]{2,14}?" + _ORGAN_KW + r")令第", text)
    if not m:
        return ""
    s = re.sub(r"^[年月日（）()\s]+", "", m.group(1))
    return clean_text(s)


def make_summary(full_text: str, length: int) -> str:
    s = clean_text(full_text)
    # 跳过开头的『（…公布…修订）』前言括号，直接取正文摘要
    if s.startswith("（") or s.startswith("("):
        end = s.find("）")
        if end == -1:
            end = s.find(")")
        if end != -1:
            s = s[end + 1:].strip()
    return s[:length]


def _longest_text_block(soup: Any) -> Any | None:
    """通用正文兜底：当 CONTENT_SELECTORS 均未命中时，返回页面中纯文本最长、
    且非导航（链接文本占比不过高）的内容块。用于覆盖各市政府子站异构结构。

    第四轮增强：
    - 候选标签扩展到 table/td/main（覆盖正文塞在表格里的政府详情页）；
    - 链接占比阈值放宽到 0.5（避免误杀正文含脚注/相关链接的页面，如 jinan）；
    - 若无任何块达标，兜底取 <body>（剔除 script/style/噪声），覆盖正文容器
      既非 div 也非 table 的情形（如 jinan 的 swiper 结构，正文确在 HTML 中
      但因被整体当作导航而误杀）。仅当 body 文本足够长才采用，极薄/拦截页
      仍保持为空，符合预期。
    """
    best, best_len = None, 200  # 阈值：过滤极短块
    for tag in soup.find_all(["div", "article", "section", "table", "main"]):
        links = tag.find_all("a")
        text = tag.get_text(" ", strip=True)
        if len(text) <= best_len:
            continue
        # 链接文本占比 >50% 视为导航/侧栏，跳过
        if links and len(text):
            link_chars = sum(len(a.get_text(strip=True)) for a in links)
            if link_chars / len(text) > 0.5:
                continue
        best, best_len = tag, len(text)
    if best is None:
        body = soup.body
        if body is not None:
            for el in body.select("script, style"):
                el.decompose()
            for hint in _NOISE_CLASS_HINTS:
                for el in body.select(f'[class*="{hint}"]'):
                    el.decompose()
            bt = body.get_text(" ", strip=True)
            if len(bt) > best_len:
                return body
    return best


def _is_https_scheme_upgrade(html: str) -> bool:
    return bool(_SCHEME_UPGRADE_RE.search(html or ""))


#: 主库文件名（N-206 / S-A，2026-10-09）：**改为 JSONL**（`gov_laws.jsonl`）。
#: 为何换格式：`.json` 单数组解析 1GB 主库实测峰值 **5.24~6.13GB**（≈6× 体积：JSON 文本→对象膨胀
#: + 内部全量副本）；JSONL 可**逐行流式**读写 ⇒ 峰值与语料体积解耦（实测流式遍历同体量语料仅 37MB）。
#: 兼容：迁移期若 JSONL 缺失而旧 `gov_laws.json` 仍在，读取侧按其**旧格式流式**解析（见 `_iter_master_records`）。
MASTER_NAME = "gov_laws.jsonl"
MASTER_NAME_LEGACY = "gov_laws.json"


def master_path(out_dir: str) -> str:
    """gov 主库**写入**路径（JSONL，两子源合并落点、唯一事实源）。"""
    return os.path.join(out_dir, MASTER_NAME)


def _find_master(out_dir: str) -> str:
    """实际存在的主库文件（JSONL 优先，回退旧 `.json`；都不存在 → 返回 JSONL 路径）。"""
    p = master_path(out_dir)
    if os.path.exists(p):
        return p
    legacy = os.path.join(out_dir, MASTER_NAME_LEGACY)
    return legacy if os.path.exists(legacy) else p


def _iter_master_records(out_dir: str) -> Iterator[dict[str, Any]]:
    """**流式**迭代主库记录（JSONL 逐行 / 旧 `.json` 用 `raw_decode` 逐条）——峰值与单条同阶。

    统一入口：`MasterView.records()` 与 `build_master_index_streaming()` 都经此迭代，
    避免"两处各写一套读法"（本仓一贯的 SSOT 纪律）。
    """
    p = _find_master(out_dir)
    if not os.path.exists(p):
        return
    if p.endswith(".jsonl"):
        with open(p, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                rec = json.loads(line)
                if isinstance(rec, dict) and len(rec) == 1 and "_meta" in rec:
                    continue                      # 信封行
                yield rec
        return
    yield from _iter_json_array_records(p)


def _iter_json_array_records(path: str) -> Iterator[dict[str, Any]]:
    """旧 `.json` 主库的**流式**逐条迭代（`raw_decode`；不构造整表副本）。"""
    dec = json.JSONDecoder()
    with open(path, encoding="utf-8") as f:
        buf = ""
        start = -1
        while start < 0:
            chunk = f.read(1 << 20)
            if not chunk:
                return
            buf += chunk
            start = buf.find('"records"')
            if start >= 0:
                b = buf.find("[", start + 9)
                if b >= 0:
                    buf = buf[b + 1:]
                    break
                start = -1
        while True:
            buf = buf.lstrip()
            if buf.startswith(","):
                buf = buf[1:]
                continue
            if buf.startswith("]"):
                return
            if not buf:
                chunk = f.read(1 << 22)
                if not chunk:
                    return
                buf += chunk
                continue
            try:
                rec, end = dec.raw_decode(buf)
            except ValueError:
                chunk = f.read(1 << 22)
                if not chunk:
                    return
                buf += chunk
                continue
            if isinstance(rec, dict):
                yield rec
            buf = buf[end:]


def master_index_path(out_dir: str) -> str:
    """主库**旁路索引**路径（N-200）。"""
    return os.path.join(out_dir, "gov_laws.index.json")


def write_master_index(out_dir: str, records: list[dict[str, Any]]) -> str:
    """写主库旁路索引（与主库**同批**落盘；供下次增量**免解析 1GB 正文**）。

    为何必需（批 46 实测）：`gov_laws.json` 1132MB，`json.load` 的**峰值 RSS 6.2GB**（≈6× 体积，
    Python 对象膨胀 —— 实测 `collect:gov_zhengceku` 与 `clean:gov` 均因此触顶）；而**周度增量只需**
    ① 哪些 `detail_url` 已抓（跳过）　　② 其中哪些**已有正文**（其余仍需补详情）。
    索引只存 `[detail_url, title, has_full_text]`（13k 条 ≈1MB）⇒ 读取毫秒级、峰值可忽略。

    **失效即回退**：索引记录主库 `size/mtime/count`，任一不符即视为陈旧 → 调用方回退全量解析
    （幂等安全：索引只是加速器，任何异常都不得改变语义）。
    """
    mp = master_path(out_dir)
    st = os.stat(mp) if os.path.exists(mp) else None
    payload = {
        "master": os.path.basename(mp),
        "master_size": st.st_size if st else 0,
        "master_mtime": int(st.st_mtime) if st else 0,
        "count": len(records),
        # 极简三元组（URL/标题/是否有正文）——刻意不存正文，索引体积与语料正文解耦
        "entries": [[r.get("detail_url") or "", r.get("title") or "", 1 if r.get("full_text") else 0]
                    for r in records],
    }
    p = master_index_path(out_dir)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    os.replace(tmp, p)
    return p


def build_master_index_streaming(out_dir: str) -> str:
    """**流式**扫描主库并重建旁路索引（峰值 ≈ 单条记录，与语料体积解耦）。

    为何必需（N-200b，2026-10-09）：索引缺失时若走 `json.load` 全量解析，会再次触发**实测 6.2GB**
    的瞬时峰值（1GB 文件 ≈6× 对象膨胀）。本函数经 `_iter_master_records` 逐条迭代
    （JSONL 逐行 / 旧 `.json` 用 `raw_decode`）⇒ 峰值与单条记录（≤1MB）同阶。
    返回索引路径（主库不存在 → 空串）。
    """
    mp = _find_master(out_dir)
    if not os.path.exists(mp):
        return ""
    entries: list[list] = []
    for rec in _iter_master_records(out_dir):
        entries.append([rec.get("detail_url") or "", rec.get("title") or "",
                        1 if rec.get("full_text") else 0])
    st = os.stat(mp)
    payload = {
        "master": os.path.basename(mp),
        "master_size": st.st_size,
        "master_mtime": int(st.st_mtime),
        "count": len(entries),
        "entries": entries,
    }
    p = master_index_path(out_dir)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    os.replace(tmp, p)
    LOG.info("【resume】旁路索引已**流式重建**：%d 条 → %s", len(entries), os.path.basename(p))
    return p


class MasterView:
    """主库只读视图（N-200）：**索引优先**，需要正文时才全量解析，且**整个进程只解析一次**。

    背景（实测）：原实现 `load_resume` 与 `merge_with_master` **各解析一次** 1GB 主库
    ⇒ 同一进程两轮 6GB 级瞬时峰值（`collect:gov_zhengceku` 实测触顶被中止）。
    本类把「跳过集判定」与「合并所需记录」收敛到同一份解析结果，并在索引可用时**完全不解析正文**。
    """

    def __init__(self, out_dir: str):
        self.out_dir = out_dir
        self.from_index = False
        self._records: list[dict[str, Any]] | None = None
        self._seen: set | None = None
        self._detailed: set | None = None

    def _read_index(self) -> dict | None:
        # N-206：主库可能仍是旧 `.json`（迁移期）⇒ 用 `_find_master` 定位实际文件，
        # 保证索引的 size/mtime 校验对准**真实读取对象**（否则恒判失效 → 每次重建，白付开销）。
        ip, mp = master_index_path(self.out_dir), _find_master(self.out_dir)
        if not os.path.exists(mp):
            return None
        if not os.path.exists(ip):
            # N-200b：索引缺失（首次/被删）→ **流式重建**（峰值≈单条记录），避免为判定跳过集
            # 而全量解析 1GB 主库（实测 6.2GB 瞬时峰值）。
            try:
                if not build_master_index_streaming(self.out_dir):
                    return None
            except Exception as e:  # noqa: BLE001  重建失败 → 回退全量解析（安全侧）
                LOG.warning("【resume】旁路索引流式重建失败 → 回退全量解析：%s", e)
                return None
        try:
            with open(ip, encoding="utf-8") as f:
                d = json.load(f)
            st = os.stat(mp)
            if (int(d.get("master_size") or -1) != st.st_size
                    or int(d.get("master_mtime") or -1) != int(st.st_mtime)
                    or int(d.get("count") or -1) != len(d.get("entries") or [])):
                LOG.info("【resume】旁路索引与主库不一致（size/mtime/count）→ 流式重建")
                try:
                    if not build_master_index_streaming(self.out_dir):
                        return None
                except Exception as e:  # noqa: BLE001
                    LOG.warning("【resume】旁路索引流式重建失败 → 回退全量解析：%s", e)
                    return None
                with open(ip, encoding="utf-8") as f:
                    d = json.load(f)
            return d
        except Exception as e:  # noqa: BLE001  索引只是加速器：失败必须回退而非阻断
            LOG.warning("【resume】旁路索引读取失败 → 回退全量解析：%s", e)
            return None

    def keys(self) -> tuple[set, set]:
        """(seen, detailed)——seen=`(title, url)` 全集；detailed=**已有正文**的 url 集。"""
        if self._seen is not None and self._detailed is not None:
            return self._seen, self._detailed
        d = self._read_index()
        if d is not None:
            seen: set = set()
            detailed: set = set()
            for ent in d.get("entries") or []:
                url = ent[0] if len(ent) > 0 else ""
                title = ent[1] if len(ent) > 1 else ""
                has = ent[2] if len(ent) > 2 else 0
                seen.add((title, url))
                if has and url:
                    detailed.add(url)
            self.from_index = True
            LOG.info("【resume】命中旁路索引：%d 条（其中 %d 条已有正文；**未解析主库正文**）",
                     len(seen), len(detailed))
            self._seen, self._detailed = seen, detailed
            return seen, detailed
        seen, detailed = set(), set()
        for r in self.records():
            seen.add((r.get("title", ""), r.get("detail_url", "")))
            if r.get("full_text"):
                detailed.add(r.get("detail_url", ""))
        LOG.info("【resume】已从 %s 加载 %d 条已抓条目（其中 %d 条已有正文）",
                 os.path.basename(master_path(self.out_dir)), len(seen), len(detailed))
        self._seen, self._detailed = seen, detailed
        return seen, detailed

    def records(self) -> list[dict[str, Any]]:
        """全量记录（**流式**读取并缓存复用；主库缺失/损坏 → 空表，由调用方按现状处理）。

        N-206：改用 `_iter_master_records`（JSONL 逐行 / 旧 `.json` 逐条 raw_decode）——
        原实现 `json.load` 对 1GB 主库有 6× 瞬时峰值；现峰值与**单条记录**同阶。
        """
        if self._records is None:
            if not os.path.exists(_find_master(self.out_dir)):
                self._records = []
            else:
                try:
                    self._records = list(_iter_master_records(self.out_dir))
                except Exception as e:  # noqa: BLE001
                    LOG.warning("【merge】读取现主库失败，按空表处理：%s", e)
                    self._records = []
        return self._records


def load_resume(out_dir: str, source: str):
    """读取 out_dir 下最新的同源输出 JSON，返回 (已抓条目key集合, 已有正文的detail_url集合)。
    用于 --resume 增量续抓：跳过已存在的列表条目，且对已含 full_text 的条目不再抓详情。

    N-200（2026-10-09）：**兼容入口**——等价于 `MasterView(out_dir).keys()`（旁路索引优先、
    全量解析至多一次；索引陈旧/缺失时行为与旧实现一致）。
    """
    return MasterView(out_dir).keys()


def write_outputs(records: list[dict[str, Any]], cfg: ScrapeConfig,
                  source_label: str | None = None,
                  write_csv: bool = False) -> dict[str, str]:
    """落盘主库（**JSONL**，N-206 / S-A）与可选 CSV。

    格式（`gov_laws.jsonl`）：**首行 `_meta` 信封**（source/category/captured_at/format），
    其后**每行一条记录**（`json.dumps` 会把字符串内换行转义 ⇒ 严格一行一记录）。
    ⚠️ **记录数不入 `_meta`**：单遍流式写入无法先知总数；计数由**旁路索引** `gov_laws.index.json`
    的 `count` 承载（与主库同批原子写），需要时读索引即可（2MB 级，毫秒）。
    """
    os.makedirs(cfg.out_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    jsonl_path = master_path(cfg.out_dir)
    csv_path = os.path.join(cfg.out_dir, "gov_laws.csv")

    _tmp = jsonl_path + ".tmp"
    with open(_tmp, "w", encoding="utf-8") as f:
        f.write(_meta_line(cfg.source, cfg.category, stamp))
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(_tmp, jsonl_path)
    # N-200：与主库**同批**写旁路索引（供下次增量免解析正文）。索引是**加速器**：
    # 写入失败只告警，不影响主库正确性（下次自动回退流式重建）。
    try:
        write_master_index(cfg.out_dir, records)
    except Exception as e:  # noqa: BLE001
        LOG.warning("【resume】旁路索引写入失败（不影响主库；下次将流式重建）：%s", e)

    # CSV：仅 --csv 显式开启时输出表格视图（默认仅 JSONL 主库，2026-09-09 规范）
    if write_csv:
        _tmpc = csv_path + ".tmp"
        with open(_tmpc, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
            writer.writeheader()
            for r in records:
                writer.writerow({k: r.get(k, "") for k in CSV_COLUMNS})
        os.replace(_tmpc, csv_path)

    LOG.info("输出完成：\n  JSONL: %s%s", jsonl_path,
             ("\n  CSV  : %s" % csv_path) if write_csv else "")
    return {"json": jsonl_path, "csv": csv_path if write_csv else ""}


def _meta_line(source: str, category: str, stamp: str) -> str:
    """JSONL 首行信封（单键 `_meta`，读取侧按此跳过）。"""
    return json.dumps(
        {"_meta": {"source": source, "category": category, "captured_at": stamp,
                   "format": "jsonl/1"}},
        ensure_ascii=False,
    ) + "\n"


def merge_and_write(new_records: list[dict[str, Any]], cfg: ScrapeConfig, *,
                    source_label: str | None = None, write_csv: bool = False) -> dict[str, Any]:
    """增量合并**流式**落盘（N-206 / **S-B**）——替代"整表合并 + 整表落盘"。

    为何必需（实测）：原 `merge_with_master` 把旧主库**全量读进内存**再与新记录合并
    （1GB 级 ⇒ 与 clean 同源的 6× 瞬时峰值）。本函数：
      ① 旧主库经 `_iter_master_records` **逐条**读（JSONL 逐行 / 旧 `.json` raw_decode）；
      ② 命中同键（`detail_url` 优先、退化标题）者**用新记录替换**（新优先，语义与原实现一致）；
      ③ 逐条写 `.tmp` ⇒ **峰值 = 新记录 + 键集**，与旧主库体积解耦；④ 原子替换 + 重建索引。
    返回 `{"json", "csv", "merged", "replaced", "csv_path"}`（供调用方日志与统计）。
    """
    os.makedirs(cfg.out_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = master_path(cfg.out_dir)
    csv_path = os.path.join(cfg.out_dir, "gov_laws.csv")

    pending: dict[str, dict[str, Any]] = {}
    for r in new_records:
        pending[str(r.get("detail_url") or r.get("title") or "")] = r
    merged = replaced = 0
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as out:
        out.write(_meta_line(cfg.source, cfg.category, stamp))
        for old in _iter_master_records(cfg.out_dir):
            k = str(old.get("detail_url") or old.get("title") or "")
            if k in pending:
                out.write(json.dumps(pending.pop(k), ensure_ascii=False) + "\n")
                replaced += 1
            else:
                out.write(json.dumps(old, ensure_ascii=False) + "\n")
            merged += 1
        for r in pending.values():                    # 旧库中不存在的新条目
            out.write(json.dumps(r, ensure_ascii=False) + "\n")
            merged += 1
    os.replace(tmp, out_path)
    # 索引：从**刚落盘的主库**流式重建（与主库保证同批一致；失败只告警）
    try:
        build_master_index_streaming(cfg.out_dir)
    except Exception as e:  # noqa: BLE001
        LOG.warning("【resume】旁路索引重建失败（下次将自动重建）：%s", e)
    if write_csv:
        _tmpc = csv_path + ".tmp"
        with open(_tmpc, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
            writer.writeheader()
            for r in _iter_master_records(cfg.out_dir):
                writer.writerow({k: r.get(k, "") for k in CSV_COLUMNS})
        os.replace(_tmpc, csv_path)
    LOG.info("【merge】流式合并完成：%d 条（其中**新覆盖** %d 条，新发现 %d 条）→ %s",
             merged, replaced, len(new_records) - replaced, os.path.basename(out_path))
    return {"json": out_path, "csv": csv_path if write_csv else "", "merged": merged,
            "replaced": replaced}


#: 采集统计落点（相对仓库根）；每源**只保留最新一份**（覆盖写）
_COLLECT_STATS_REL = ("data", "run_state", "collect_stats")


def collect_stats_path(label: str) -> str:
    """采集统计文件路径：`<repo>/data/run_state/collect_stats/<label>.json`（N-190）。

    为何放这里（不是 reports/ 也不是 raw/）：
      · `data/run_state/` 是**既有运行状态目录**（`last_run_steps.json` 同处），且**未纳入 git**
        ⇒ 每次采集更新统计**不产生提交噪声**，同时给"无效时长"留下可比的历史基线；
      · **不写进 `data/raw/`**：该目录是**事实源**，统计属旁路观测，不得混入（本仓一贯纪律）。

    根路径用 `os.pardir` 拼（零转义写法）：本文件在 `collectors/`，上溯三级即仓库根。
    """
    here = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.normpath(os.path.join(here, os.pardir, os.pardir, os.pardir))
    return os.path.join(repo_root, *_COLLECT_STATS_REL, "%s.json" % label)


def write_collect_stats(label: str, stats: dict) -> str:
    """原子写采集统计（旁路观测；写失败**不得中断采集**）。返回路径（失败返回空串）。

    指标口径（供"无效时长"评估，字段含义见 `gov_zhengceku.should_stop_paging` 与批 45 报告）：
      `list_pages_fetched` / `list_pages_without_new` → **空转页占比** = 后者/前者；
      `details_skipped_master` → 因"主库已有正文"而**未发请求**的条目（省下的详情请求数）；
      `elapsed_list_s` / `elapsed_detail_s` → 时长按阶段归因。
    """
    p = collect_stats_path(label)
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(stats, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
        # R-E 处置（2026-10-09）：**追加历史样本**（原实现只留最新一份 ⇒ 无法看趋势）。
        # 形状：同目录 `history/<label>.jsonl`，仅保留**紧凑指标子集**，滚动截断到最近 N 条。
        _append_stats_history(label, stats, p)
        return p
    except Exception as e:  # noqa: BLE001  旁路观测：失败只告警
        LOG.warning("【collect-stats】统计落盘失败（不影响采集）：%s", e)
        return ""


#: 统计历史保留条数（每子源一份 jsonl；仅紧凑指标，滚动截断）
STATS_HISTORY_KEEP = 90
#: 进入历史文件的**紧凑指标**（与评估"无效时长"直接相关的字段）
_STATS_HISTORY_FIELDS = ("sub_source", "list_pages_fetched", "list_pages_without_new",
                         "new_items", "pending_details", "details_fetched",
                         "details_skipped_master", "elapsed_list_s", "elapsed_detail_s",
                         "stopped_reason")


def _append_stats_history(label: str, stats: dict, latest_path: str) -> None:
    """追加一条紧凑历史样本并**滚动截断**（R-E；失败只告警，不影响采集）。"""
    hp = os.path.join(os.path.dirname(latest_path), "history", label + ".jsonl")
    os.makedirs(os.path.dirname(hp), exist_ok=True)
    st = stats.get("stats") or {}
    row = {"run_at": stats.get("run_at"), "backfill": stats.get("backfill")}
    row.update({k: st.get(k) for k in _STATS_HISTORY_FIELDS})
    lines: list[str] = []
    if os.path.exists(hp):
        with open(hp, encoding="utf-8") as f:
            lines = [x for x in f.read().splitlines() if x.strip()]
    lines.append(json.dumps(row, ensure_ascii=False))
    if len(lines) > STATS_HISTORY_KEEP:
        lines = lines[-STATS_HISTORY_KEEP:]
    tmp = hp + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, hp)


def build_config(args) -> ScrapeConfig:
    base, category = SOURCES[args.source]
    return ScrapeConfig(
        source=args.source,
        base_url=base,
        category=category,
        out_dir=args.out_dir,
        max_pages=args.max_pages,
        max_items=args.max_items,
        fetch_details=not args.no_details,
        details_limit=args.details_limit,
        resume=args.resume,
        delay_min=args.delay_min,
        delay_max=args.delay_max,
        timeout=args.timeout,
        retries=args.retries,
        summary_len=args.summary_len,
        checkpoint_every=getattr(args, "checkpoint_every", 200),
        # N-190：增量治理参数（老调用方无这些属性时退回默认，保持向后兼容）
        stop_after_empty_pages=int(getattr(args, "stop_after_empty_pages", 1) or 1),
        backfill=bool(getattr(args, "backfill", False)),
        max_seconds=float(getattr(args, "max_seconds", 0.0) or 0.0),
    )


def merge_with_master(new_records: list[dict[str, Any]], out_dir: str,
                      master_records: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """增量续抓合并：现主库旧记录 ∪ 本次新条目（detail_url 去重，新优先）。

    2026-09-05：定长覆盖更新 + ``--resume`` 使本次仅产出新增条目，
    若直接覆盖写会丢失历史条目；合并后覆盖保证主库 = 历史全量 + 本周新增/更新。

    N-200（2026-10-09）：新增 `master_records` 参数 —— 调用方若已通过 `MasterView.records()`
    解析过主库（如为判定跳过集而必须全量解析时），**直接复用**，避免同一进程重复解析 1GB 主库
    （原实现 `load_resume` 与 `merge_with_master` 各解析一次 ⇒ 两轮 6GB 级瞬时峰值）。
    """
    if master_records is not None:
        old = master_records
    else:
        old = MasterView(out_dir).records()
        if not old and not os.path.exists(master_path(out_dir)):
            return new_records
    merged: dict[str, dict[str, Any]] = {}
    for r in old:
        merged[str(r.get("detail_url") or r.get("title"))] = r
    for r in new_records:
        merged[str(r.get("detail_url") or r.get("title"))] = r
    LOG.info("【merge】现主库 %d 条 + 本次 %d 条 → 合并 %d 条",
             len(old), len(new_records), len(merged))
    return list(merged.values())


__all__ = ["ScrapeConfig", "_is_https_scheme_upgrade", "_longest_text_block", "build_config", "clean_text",
           "collect_stats_path", "write_collect_stats", "decode_html", "extract_date", "extract_doc_number",
           "extract_issue_organ", "load_resume", "make_summary", "master_index_path", "master_path",
           "merge_and_write", "merge_with_master", "write_master_index", "write_outputs", "MasterView"]
