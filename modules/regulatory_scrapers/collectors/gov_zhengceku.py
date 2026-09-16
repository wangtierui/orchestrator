# -*- coding: utf-8 -*-
"""gov_zhengceku.py —— gov 源「国务院政策文件库·国务院部门文件」子采集器。

【纳入常规采集范围（2026-09-15）】
  此前 gov 源只有 `xzfgk` 一个子源（= `www.gov.cn/zhengce/xzfgk/` 行政法规库，详情落司法部
  `xzfg.moj.gov.cn`），**「国务院政策文件库·国务院部门文件」
  `https://www.gov.cn/zhengce/zhengceku/`（列表 `bmwj/home.htm`，629 页 × 28 条）从未纳入采集**，
  导致该栏目下的规范性文件在 gov 源完全缺采；且该栏目详情页正文常以附件（.doc/.pdf）形式发布，
  页面正文为空或仅一个文件名，不抓附件则正文仅数十字，无法支撑条款级引用核验。
  本模块把该栏目补齐为 gov 源的第 2 个子源，产物并入 gov 主库 `gov_laws.json`，
  由统一清洗管道 `clean/run_clean_pipeline.py --project gov` 正常消费。

【与 gov 主链的契约一致性（务必遵守）】
  记录字段与 `XzfgkScraper` 完全同构，供 `std_lib.scraper_std.unified_schema.map_gov` 读取：
    title / detail_url / publish_date / pub_date_original / effective_date / issue_organ /
    document_number / category / source / full_text / summary /
    attachments / attachment_text / attachment_count
  ⚠️ 正文键必须是 `full_text`（不是 body_text）、详情链接键必须是 `detail_url`（不是 source_url），
  否则 `map_gov` 取不到值，cleaned 层正文落空。
  `source="zhengceku"` 经 `config/enums.SOURCE_ALIASES` 归并为 `gov`，子源名进 `_raw_fields.子源`。

用法（通常由 gov_collector.py 分发调用，也可独立单条补录）：
  python gov_collector.py --source zhengceku              # 常规增量（resume 默认开）
  python gov_collector.py --source zhengceku --full       # 全量（629 页）
  python gov_zhengceku.py --url <详情页URL>                # 单条补录（独立入口，便于人工核验）
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from urllib.parse import urljoin

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC_ROOT = os.path.dirname(_HERE)                                    # .../modules/regulatory_scrapers
_REPO_ROOT = os.path.dirname(os.path.dirname(_SRC_ROOT))              # 仓库根
for _p in (_SRC_ROOT, _REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

LOG = logging.getLogger("gov_zhengceku")

# 列表：首页无后缀，第 i 页为 home_{i}.htm（0-based，实测 nPageCount=629）
LIST_BASE = "https://www.gov.cn/zhengce/zhengceku/bmwj/home.htm"
CATEGORY = "部门文件"
SUB_SOURCE = "zhengceku"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

CONTENT_SELECTORS = [
    ".pages_content", ".article-content", "#UCAP-CONTENT", ".TRS_Editor",
    ".content_box", ".article", "#content_body", ".pages-content", "#zoom",
]
_TAG = re.compile(r"<[^>]+>", re.DOTALL)
_WS = re.compile(r"[ \t\u3000]{2,}")
_BLANK = re.compile(r"\n{3,}")
_A = re.compile(r'<a\b[^>]*\bhref="([^"]+)"[^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
_LI = re.compile(r"<li\b[^>]*>(.*?)</li>", re.IGNORECASE | re.DOTALL)
_DATE = re.compile(r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})")
_META = re.compile(r"(发文字号|发文机关|成文日期|主题分类|公文种类|来\s*源|标\s*题)\s*[：:]", re.U)


def list_page_url(index: int) -> str:
    """第 index 页（0-based）的 URL。"""
    return LIST_BASE if index == 0 else LIST_BASE.replace("home.htm", f"home_{index}.htm")


def total_pages(html: str) -> int:
    """从列表页 JS 变量 `nPageCount` 取总页数（取不到返回 1）。"""
    m = re.search(r"nPageCount\s*=\s*(\d+)", html or "")
    return int(m.group(1)) if m else 1


def _to_text(maybe) -> str:
    """响应体 → 文本，兼容 ① 文本 ② 原始 bytes ③ **未解压的 gzip 体**。

    ⚠️ 踩坑：`crawler_common.robust_get(binary=False)` 以 `errors="replace"` 解码响应体，
    gzip magic 的 `0x8b` 会变成 U+FFFD，既无法按 magic 识别也无法解压 → HTML 解析全空
    （表现为「正文 0 字、附件 0 个」却不报错）。故一律 binary=True 取原始字节后自行处理。
    """
    import gzip
    import zlib
    if isinstance(maybe, bytes):
        b = maybe
    elif isinstance(maybe, str) and maybe[:2] == "\x1f\x8b":
        b = maybe.encode("latin-1", "ignore")
    else:
        return maybe if isinstance(maybe, str) else ""
    if b[:2] == b"\x1f\x8b":
        try:
            b = gzip.decompress(b)
        except Exception:  # noqa: BLE001
            try:
                b = zlib.decompress(b, 16 + zlib.MAX_WBITS)
            except Exception:  # noqa: BLE001
                pass
    for enc in ("utf-8", "gb18030"):
        try:
            return b.decode(enc)
        except Exception:  # noqa: BLE001
            continue
    return b.decode("utf-8", "replace")


def fetch(url: str, binary: bool = False, referer: str | None = None, timeout: int = 35):
    """HTTP GET。优先 crawler_common.robust_get，失败回退 urllib。"""
    try:
        if _REPO_ROOT not in sys.path:
            sys.path.insert(0, _REPO_ROOT)
        from std_lib.scraper_std.crawler_common import robust_get  # type: ignore
        st, data = robust_get(url, binary=True, referer=referer or LIST_BASE, timeout=timeout)
        if st == 200 and data:
            if binary:
                return data if isinstance(data, bytes) else data.encode("latin-1", "ignore")
            txt = _to_text(data)
            if txt and "<" in txt:
                return txt
    except Exception:  # noqa: BLE001
        pass
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": referer or LIST_BASE})
    with urllib.request.urlopen(req, timeout=timeout) as f:
        b = f.read()
    return b if binary else _to_text(b)


def clean_html_text(html: str) -> str:
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", html or "")
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = re.sub(r"(?i)</(p|div|tr|li|h[1-6])>", "\n", s)
    s = _TAG.sub("", s)
    s = (s.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<")
          .replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'"))
    s = _WS.sub(" ", s)
    s = "\n".join(x.strip() for x in s.split("\n"))
    return _BLANK.sub("\n\n", s).strip()


def _iso(datestr: str) -> str:
    m = _DATE.search(datestr or "")
    return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else ""


def make_summary(full_text: str, length: int = 200) -> str:
    """摘要：与 gov 主链 `gov_parse.make_summary` 同口径（压缩空白后截断）。"""
    s = re.sub(r"\s+", " ", full_text or "").strip()
    return s if len(s) <= length else s[:length]


# --------------------------------------------------------------------------- #
# 附件处理模式（全量采集提速 / 减重）
# --------------------------------------------------------------------------- #
# `ZC_ATTACH_FAST=1` → 正文已完整的条目**不下载、不抽取附件原文**（正文不足者仍完整处理）。
# 依据：附件原文（OCR + Word COM）是全量采集的耗时与体积主因——实测 1600 条暂存 516 MB
# （均 322 KB/条），全量 1.22 万条约需 5.5 GB，内存压力是进程中断的首要嫌疑。
# 默认关闭（保持常规增量采集行为不变）；如需附件原文，对目标条目用
# `python collectors/gov_zhengceku.py <url>` 单条补录即可（该入口始终完整抽取）。
_ATTACH_FAST = os.environ.get("ZC_ATTACH_FAST", "").strip().lower() in ("1", "true", "yes", "on")
_FAST_MIN_BODY = int(os.environ.get("ZC_FAST_MIN_BODY", "200") or 200)
_skipped = [0]                 # 本次运行跳过附件抽取的条目计数（供收尾日志）


# --------------------------------------------------------------------------- #
# 列表
# --------------------------------------------------------------------------- #
def scan_list(html: str, base_url: str = LIST_BASE):
    """列表页 → [(绝对URL, 标题, 发布日期ISO)]。按 <li> 块提取，回退按 <a> 提取。"""
    out, seen = [], set()
    for li in _LI.findall(html or ""):
        m = _A.search(li)
        if not m or not re.search(r"content_\d+\.htm", m.group(1)):
            continue
        u = urljoin(base_url, m.group(1).strip())
        if u in seen:
            continue
        seen.add(u)
        out.append((u, clean_html_text(m.group(2)).replace("\n", " ").strip()[:160],
                    _iso(clean_html_text(li))))
    if out:
        return out
    for m in _A.finditer(html or ""):
        href = m.group(1).strip()
        if not re.search(r"content_\d+\.htm", href):
            continue
        u = urljoin(base_url, href)
        if u in seen:
            continue
        seen.add(u)
        out.append((u, clean_html_text(m.group(2)).replace("\n", " ").strip()[:160], ""))
    return out


# --------------------------------------------------------------------------- #
# 详情页
# --------------------------------------------------------------------------- #
# 「值」其实是另一个字段标签时的识别（gov.cn 页面字段区相邻，取空值会串到下一标签）
_LABEL_LIKE = re.compile(r"^\s*(发文字号|发文机关|成文日期|主题分类|公文种类|来\s*源|标\s*题)\s*[：:]")
# 文号形态：〔2009〕68号 / 令〔2010〕4号 / 公告〔2026〕第9号 / 银保监办发〔2020〕118号 / 保监发〔2012〕87号
_DOCNO_RE = re.compile(
    r"[\u4e00-\u9fff]{2,12}(?:发|办|令|公告|函|字)?\s*[〔\[【]\s*(?:19|20)\d{2}\s*[〕\]】]\s*第?\s*\d+\s*号"
    r"|[\u4e00-\u9fff]{2,12}令\s*第?\s*\d+\s*号")


def _extract_docno(text: str) -> str:
    """从标题/正文首部提取发文字号（`_meta_field` 取不到时的兜底）。"""
    m = _DOCNO_RE.search(text or "")
    return re.sub(r"\s+", "", m.group(0)) if m else ""


def _meta_field(html: str, label: str, maxlen: int = 120) -> str:
    for m in _META.finditer(html):
        if m.group(1).replace(" ", "").replace("\u3000", "") != label.replace(" ", ""):
            continue
        frag = clean_html_text(html[m.end():m.end() + 400]).replace("|", " ").strip()
        if not frag:
            continue
        val = frag.split("\n")[0].strip()
        # ⚠️ 该页此字段为空时，窗口会滑到相邻标签 → 取到「来 源：」之类标签文本，
        # 会污染 document_number/issue_organ 等字段（实测暂存中曾出现 document_number='来 源：'）。
        if not val or _LABEL_LIKE.match(val) or len(val) > 160:
            continue
        return val[:maxlen]
    return ""


def pick_content_html(html: str) -> str:
    for sel in CONTENT_SELECTORS:
        cls, idv = sel[1:], sel[1:]
        if sel.startswith("."):
            m = re.search(r'<div[^>]*class="[^"]*\b' + re.escape(cls) + r'\b[^"]*"[^>]*>(.*?)</div>\s*(?:</div>|<div)',
                          html, re.I | re.S)
        else:
            m = re.search(r'<div[^>]*id="' + re.escape(idv) + r'"[^>]*>(.*?)</div>\s*(?:</div>|<div)',
                          html, re.I | re.S)
        if m and len(clean_html_text(m.group(1))) > 120:
            return m.group(1)
    best, blen = "", 0
    for m in re.finditer(r"<div[^>]*>(.*?)</div>", html, re.S):
        t = clean_html_text(m.group(1))
        if len(t) > blen:
            best, blen = m.group(1), len(t)
    return best


def parse_detail(html: str, url: str) -> dict:
    """详情页 → gov 主库契约的部分字段（不含附件）。"""
    title = ""
    m = re.search(r"(?is)<title>(.*?)</title>", html or "")
    if m:
        title = re.split(r"[_\-|]", _TAG.sub("", m.group(1)).strip())[0].strip()
    for m in _META.finditer(html):
        if m.group(1).replace(" ", "").replace("\u3000", "") == "标题":
            t2 = clean_html_text(html[m.end():m.end() + 300]).split("\n")[0].strip()
            if len(t2) > len(title):
                title = t2
            break
    body = clean_html_text(pick_content_html(html))
    pub = _iso(_meta_field(html, "成文日期", 40))
    docno = _meta_field(html, "发文字号", 60)
    if not docno:
        # 兜底：部分部门文件页无「发文字号」标签（或标签后为空），但**标题/正文首部**载明文号。
        docno = _extract_docno(title) or _extract_docno(body[:600])
    return {
        "title": title,
        "detail_url": url,
        "document_number": docno,
        "issue_organ": _meta_field(html, "发文机关", 60),
        "publish_date": pub,
        "pub_date_original": _meta_field(html, "成文日期", 40),
        "full_text": body,
    }


# --------------------------------------------------------------------------- #
# 附件（下载 + 原文抽取；旧 .doc 优先 Word COM）
# --------------------------------------------------------------------------- #
_WORD_APP = None          # Word COM 单例（跨条目复用，避免每条目重启 Word 进程）
_WORD_DEAD = False        # 单例失效标记（Word 崩溃/不可用后不再重试）


def _word_app():
    """获取（并缓存）Word.Application 单例；不可用返回 None。

    ⚠️ 性能关键：`Dispatch` + `Quit` 每次约 2–3 秒。17,600 条级别采集若每条目启停
    Word 会额外消耗十余小时，故全流程复用同一实例，仅在进程退出时释放
    （`atexit` 注册）。实例失效则置 `_WORD_DEAD` 短路，回退通用抽取。
    """
    global _WORD_APP, _WORD_DEAD
    if _WORD_APP is not None or _WORD_DEAD:
        return _WORD_APP
    try:
        import atexit
        import win32com.client as wc  # type: ignore
        app = wc.Dispatch("Word.Application")
        app.Visible = False
        app.DisplayAlerts = False
        _WORD_APP = app

        def _quit():
            global _WORD_APP
            try:
                if _WORD_APP is not None:
                    _WORD_APP.Quit()
            except Exception:  # noqa: BLE001
                pass
            _WORD_APP = None
        atexit.register(_quit)
    except Exception as e:  # noqa: BLE001
        LOG.warning("Word COM 不可用，回退通用抽取：%s", e)
        _WORD_DEAD = True
    return _WORD_APP


def extract_doc_via_word(path: str) -> str:
    """Word COM 抽取 .doc/.docx 正文（复用单例）；不可用/失败返回空串（由调用方回退）。"""
    global _WORD_DEAD
    app = _word_app()
    if app is None:
        return ""
    doc = None
    try:
        doc = app.Documents.Open(os.path.abspath(path), ReadOnly=True,
                                 AddToRecentFiles=False, Visible=False)
        return (doc.Content.Text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    except Exception as e:  # noqa: BLE001
        LOG.warning("Word COM 抽取失败 %s：%s", path, e)
        _WORD_DEAD = True          # 连续失败视为实例已坏，后续走通用抽取
        return ""
    finally:
        try:
            if doc is not None:
                doc.Close(False)
        except Exception:  # noqa: BLE001
            pass


def _table_top_level(recs: list) -> dict:
    """把附件记录中的表格结构化字段聚合到记录顶层（与 XzfgkScraper 同口径）。"""
    ts, raw, meth = [], [], ""
    for r in recs:
        if r.get("table_structured"):
            ts.append(r["table_structured"])
        if r.get("table_raw_text"):
            raw.append(r["table_raw_text"])
        if not meth and r.get("table_recovery_method"):
            meth = r["table_recovery_method"]
    out = {}
    if ts:
        out["table_structured"] = ts[0] if len(ts) == 1 else ts
    if raw:
        out["table_raw_text"] = "\n\n".join(raw)
    if meth:
        out["table_recovery_method"] = meth
    return out


def _cjk_ratio(t: str) -> float:
    if not t:
        return 0.0
    return sum(1 for c in t if "\u4e00" <= c <= "\u9fff") / len(t)


def fetch_attachments(html: str, url: str, entry_id: str) -> tuple[list, str]:
    """下载并抽取详情页附件；返回 (附件记录, 附件原文)。

    对 .doc/.docx 用 Word COM **重抽并择优**：Word COM 交付的是 Word 自身渲染的正文
    （段落完整、CJK 占比 ~0.9），而通用抽取对旧版 .doc 走 olefile，会把 OLE 二进制
    噪声一并带出（实测同一文件：olefile 10895 字但 CJK 占比仅 0.398、全文挤在 1 行、
    首尾均为乱码；Word COM 3099 字、CJK 占比 0.906、35 段落）。
    **故择优规则以「文本洁净度」为主、长度为辅**，不能只比长度——否则会选中有噪声的长文本。
    """
    try:
        from collectors.gov_fetch_attachments import fetch_gov_attachments  # type: ignore
    except Exception:  # noqa: BLE001
        from gov_fetch_attachments import fetch_gov_attachments  # type: ignore
    recs, att_text = fetch_gov_attachments(
        html, entry_id=entry_id, entry_title="", out_dir=None, base_url=url)
    improved = 0
    for r in recs:
        lp = r.get("local_path") or ""
        if not lp.lower().endswith((".doc", ".docx")):
            continue
        ap = lp if os.path.isabs(lp) else os.path.join(_SRC_ROOT, lp)
        if not os.path.exists(ap):
            continue
        t2 = extract_doc_via_word(ap)
        cur = r.get("text") or ""
        if not t2:
            continue
        # 择优：Word COM 结果洁净（CJK 占比 ≥0.6）即采用；否则仅在明显更长时采用
        if _cjk_ratio(t2) >= 0.6 or len(t2) > len(cur):
            if t2 != cur:
                r["text"], r["extracted"] = t2, True
                r["extract_status"], r["extract_engine"] = "ok_word_com", "word_com"
                improved += 1
    if improved:
        att_text = "\n\n".join(r["text"] for r in recs if r.get("extracted") and r.get("text"))
    return recs, att_text


# --------------------------------------------------------------------------- #
# 采集器（与 XzfgkScraper 同构，供 gov_collector 分发）
# --------------------------------------------------------------------------- #
class ZhengcekuScraper:
    """国务院政策文件库·部门文件（bmwj）子采集器。"""

    def __init__(self, cfg, client=None, seen_urls=None):
        self.cfg = cfg
        self.client = client
        # 已抓 detail_url 集合（由 gov_collector 从主库 load_resume 取得）→ --resume 增量跳过
        self.seen_urls = seen_urls if seen_urls is not None else set()

    def _list_url(self, index: int) -> str:
        return list_page_url(index)

    def _parse_list(self, html: str, base_url: str):
        return scan_list(html, base_url)

    def _partial_path(self) -> str:
        """断点续跑暂存文件（位于 out_dir，即 data/raw/ 内，不新增 data 子目录）。"""
        return os.path.join(self.cfg.out_dir, "_zhengceku_partial.json")

    def _load_partial(self) -> list:
        """载入上次未合并的已抓记录（进程中断后的续跑基线）。"""
        p = self._partial_path()
        if not os.path.exists(p):
            return []
        try:
            recs = json.load(open(p, encoding="utf-8"))
            if isinstance(recs, list) and recs:
                LOG.info("【续跑】从 %s 载入上次未合并的 %d 条", os.path.basename(p), len(recs))
                for r in recs:
                    if r.get("detail_url"):
                        self.seen_urls.add(r["detail_url"])
                return recs
        except Exception as e:  # noqa: BLE001
            LOG.warning("【续跑】暂存文件读取失败，忽略：%s", e)
        return []

    def _save_partial(self, records: list) -> None:
        """原子落盘已抓记录。

        ⚠️ 必要性：`gov_parse.write_outputs` 只在**全部结束后**写一次主库，
        本栏目全量约 1.7 万条 / 30 小时，中途任何中断（网络、进程被杀、机器重启）
        都会让已抓数据**全部丢失**。故详情阶段按 `checkpoint_every` 周期落盘暂存，
        下次运行先载入暂存再续抓，最后统一并入主库。
        """
        p = self._partial_path()
        tmp = p + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(records, f, ensure_ascii=False)
            os.replace(tmp, p)
        except Exception as e:  # noqa: BLE001
            LOG.warning("暂存落盘失败：%s", e)

    def run(self) -> list:
        cfg = self.cfg
        max_pages = getattr(cfg, "max_pages", 0) or 0
        max_items = getattr(cfg, "max_items", 0) or 0
        no_details = not getattr(cfg, "fetch_details", True)
        delay = getattr(cfg, "delay_min", 1.5)
        # checkpoint 间隔（条）；0=不落暂存。来自 --checkpoint-every，缺省 200。
        ckpt = int(getattr(cfg, "checkpoint_every", 200) or 0)
        carried = self._load_partial() if ckpt else []

        first = fetch(self._list_url(0))
        n_pages = total_pages(first)
        if max_pages:
            n_pages = min(n_pages, max_pages)
        LOG.info("zhengceku 栏目：共 %d 页，本次抓取 %d 页", total_pages(first), n_pages)

        seen = self.seen_urls
        n_before = len(seen)          # 主库既有（resume 跳过基线）
        n_listed = 0                  # 列表命中总数（含已跳过）
        pending = []
        for i in range(n_pages):
            html = first if i == 0 else fetch(self._list_url(i))
            if not html:
                LOG.warning("列表页 %d 抓取失败，跳过", i)
                continue
            items = self._parse_list(html, self._list_url(i))
            n_listed += len(items)
            for u, t, d in items:
                if u in seen:
                    continue
                seen.add(u)
                pending.append((u, t, d))
            LOG.info("列表页 %d/%d → 列表命中 %d，本次累计待抓 %d 条", i + 1, n_pages, n_listed, len(pending))
            if max_items and len(pending) >= max_items:
                break
            if delay and i + 1 < n_pages:
                time.sleep(delay)
        if max_items:
            pending = pending[:max_items]
        LOG.info("列表级完成：列表命中 %d 条，本次待抓 %d 条（跳过主库既有 %d 条）",
                 n_listed, len(pending), n_listed - len(pending))

        if no_details:
            # 仅列表字段：正文留空会导致 clean 层 core 空值率超阈值 → 显式标记，不参与主库合并由调用方决定
            LOG.warning("--no-details：仅产出列表级记录（无正文），请勿直接并入主库")
            return [{"title": t, "detail_url": u, "publish_date": d, "pub_date_original": "",
                     "effective_date": "", "issue_organ": "", "document_number": "",
                     "category": CATEGORY, "source": SUB_SOURCE, "full_text": "",
                     "summary": "", "attachments": [], "attachment_text": "",
                     "attachment_count": 0} for u, t, d in pending]

        records = list(carried)
        if carried:
            LOG.info("【续跑】本次在此基础上继续，已计入 %d 条", len(carried))
        for k, (u, t, d) in enumerate(pending, 1):
            try:
                rec = self.fetch_one(u, title_hint=t, date_hint=d)
            except Exception as e:  # noqa: BLE001
                LOG.warning("详情页失败 %s：%s", u, e)
                continue
            records.append(rec)
            if k % 20 == 0 or k == len(pending):
                LOG.info("详情进度 %d/%d（累计 %d 条；最近：%s）",
                         k, len(pending), len(records), rec["title"][:34])
            if ckpt and len(records) % ckpt == 0:
                self._save_partial(records)
                LOG.info("【checkpoint】已落暂存 %d 条", len(records))
            if delay:
                time.sleep(delay)
        if ckpt and records:
            self._save_partial(records)      # 收尾落盘：保证最后不足一批的增量也持久化
        if _ATTACH_FAST and _skipped[0]:
            LOG.info("【提速模式】本次跳过附件原文抽取 %d 条（正文≥%d 字）；"
                     "如需附件原文，对目标条目用 gov_zhengceku.py <url> 单条补录",
                     _skipped[0], _FAST_MIN_BODY)
        return records

    def fetch_one(self, url: str, title_hint: str = "", date_hint: str = "") -> dict:
        """抓取单条详情（含附件原文），产出 gov 主库契约记录。"""
        html = fetch(url)
        if isinstance(html, bytes):
            html = html.decode("utf-8", "replace")
        d = parse_detail(html, url)
        if not d["title"]:
            d["title"] = title_hint
        if not d["publish_date"]:
            d["publish_date"] = date_hint
        try:
            if _ATTACH_FAST and len(d["full_text"]) >= _FAST_MIN_BODY:
                # 提速/减重模式：正文已完整时不下载、不抽取附件原文。
                # 依据：附件原文（OCR + Word COM）是全量采集的耗时与体积主因——
                # 实测 1600 条暂存达 516 MB（均 322 KB/条），全量 1.22 万条约需 5.5 GB，
                # 内存压力是进程中断的首要嫌疑；且正文已完整的条目无需附件补正文。
                # 仅当正文不足（正文以附件形式发布）时才做完整抽取，保证正文不缺失。
                atts, att_text = [], ""
                _skipped[0] += 1
            else:
                atts, att_text = fetch_attachments(html, url, entry_id=url)
        except Exception as e:  # noqa: BLE001
            LOG.warning("附件处理失败 %s：%s", url, e)
            atts, att_text = [], ""
        body = d["full_text"]
        # 正文以附件形式发布（页面正文为空/仅文件名）→ 以附件原文补足
        if len(body) < 200 and len(att_text) > len(body):
            body = att_text
        rec = {
            "title": d["title"],
            "detail_url": url,
            "publish_date": d["publish_date"],
            "pub_date_original": d["pub_date_original"],
            "effective_date": "",
            "issue_organ": d["issue_organ"],
            "document_number": d["document_number"],
            "category": CATEGORY,
            "source": SUB_SOURCE,
            "full_text": body,
            "attachments": atts,
            "attachment_text": att_text,
            "attachment_count": len(atts),
            "summary": make_summary(body),
        }
        rec.update(_table_top_level(atts))
        return rec


def build_record(url: str, with_attachments: bool = True) -> dict:
    """独立入口：单条 URL 补录（便于人工核验），返回 gov 主库契约记录。"""
    class _Cfg:
        pass
    return ZhengcekuScraper(_Cfg()).fetch_one(url)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="gov 源「国务院政策文件库·部门文件」子采集器（单条补录入口；批量请用 gov_collector.py）")
    ap.add_argument("--url", required=True, help="详情页 URL")
    ap.add_argument("--out", default="", help="输出 JSON 路径（可选，默认仅打印）")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    rec = build_record(args.url)
    print(f"[ok] {rec['document_number'] or '(无文号)'} | 正文 {len(rec['full_text'])} 字 | "
          f"附件 {rec['attachment_count']} 个（附件原文 {len(rec['attachment_text'])} 字） | {rec['title'][:50]}")
    if args.out:
        json.dump([rec], open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("[out]", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
