# -*- coding: utf-8 -*-
"""gov_zhengceku.py —— gov 源「国务院政策文件库·国务院部门文件」子采集器。

【纳入常规采集范围（2026-09-15）】
  此前 gov 源只有 `xzfgk` 一个子源，本模块把该栏目补齐为 gov 源的第 2 个子源，
  产物并入 gov 主库 `gov_laws.json`。

【与 gov 主链的契约一致性（务必遵守）】
  记录字段与 `XzfgkScraper` 完全同构，供 `std_lib.scraper_std.unified_schema.map_gov` 读取。

原 modules/regulatory_scrapers/collectors/gov_zhengceku.py 迁出，逻辑逐函数等价；
仅 import 路径与路径基址改为相对/规范，仓库引导与 pbc 同口径。
"""
from __future__ import annotations

import logging
import os
import re
import sys
import time
from urllib.parse import urljoin

from ..base import REPO_ROOT as _ORCH  # refactor 项目根（自包含，不指向外部）
from .parse import make_summary  # 复用 gov 主链摘要口径（消除 zhengceku 副本）
from ..lib.text_utils import normalize_date  # 复用统一日期归一化（消除 _iso 副本）
# local_path 相对基址收口到 refactor/data/raw（自包含）。
SCRAPERS_ROOT = os.path.join(_ORCH, "data", "raw")

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

_META = re.compile(r"(发文字号|发文机关|成文日期|主题分类|公文种类|来\s*源|标\s*题)\s*[：:]", re.U)


def list_page_url(index: int) -> str:
    """第 index 页（0-based）的 URL。"""
    return LIST_BASE if index == 0 else LIST_BASE.replace("home.htm", f"home_{index}.htm")


#: 增量早停阈值缺省：连续 1 页无「新条目」即停（N-190，2026-10-08）
DEFAULT_STOP_AFTER_EMPTY_PAGES = 1

# 早停判据抽至通用模块（collectors/lib/incremental.py，G7），此处仅再导出以保持兼容。
from ..lib.incremental import KnownTracker, should_stop_paging  # noqa: E402


def total_pages(html: str) -> int:
    """从列表页 JS 变量 `nPageCount` 取总页数（取不到返回 1）。"""
    m = re.search(r"nPageCount\s*=\s*(\d+)", html or "")
    return int(m.group(1)) if m else 1


def _to_text(maybe) -> str:
    """响应体 → 文本，兼容 ① 文本 ② 原始 bytes ③ **未解压的 gzip 体**。"""
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
    """HTTP GET。优先 lib.http.robust_get，失败回退 urllib。"""
    try:
        from ..lib.http import robust_get  # type: ignore
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








# --------------------------------------------------------------------------- #
# 附件处理模式（全量采集提速 / 减重）
# --------------------------------------------------------------------------- #
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
                    normalize_date(clean_html_text(li))))
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
_LABEL_LIKE = re.compile(r"^\s*(发文字号|发文机关|成文日期|主题分类|公文种类|来\s*源|标\s*题)\s*[：:]")
# 文号形态
_DOCNO_RE = re.compile(
    r"[\u4e00-\u9fff]{2,12}(?:发|办|令|公告|函|字)?\s*[〔\[【]\s*(?:19|20)\d{2}\s*[〕\]】]\s*第?\s*\d+\s*号"
    r"|[\u4e00-\u9fff]{2,12}令\s*第?\s*\d+\s*号")


def _extract_docno(text: str) -> str:
    """从标题/正文首部提取发文字号（兜底）。"""
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
        # 该页此字段为空时，窗口会滑到相邻标签 → 取到「来 源：」之类标签文本，会污染字段。
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
    pub = normalize_date(_meta_field(html, "成文日期", 40))
    docno = _meta_field(html, "发文字号", 60)
    if not docno:
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
_WORD_APP = None          # Word COM 单例
_WORD_DEAD = False        # 单例失效标记


def _word_app():
    """获取（并缓存）Word.Application 单例；不可用返回 None。"""
    global _WORD_APP, _WORD_DEAD
    if _WORD_DEAD:
        return None
    if _WORD_APP is not None:
        return _WORD_APP
    if os.environ.get("ZC_NO_WORD_COM", "").strip().lower() in ("1", "true", "yes", "on"):
        LOG.info("Word COM 已按 ZC_NO_WORD_COM 禁用，.doc 走通用抽取")
        _WORD_DEAD = True
        return None
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


def extract_doc_via_word(path: str, timeout: float = 25.0) -> str:
    """Word COM 抽取 .doc/.docx 正文（复用单例）；不可用/失败/超时返回空串。"""
    global _WORD_DEAD
    app = _word_app()
    if app is None:
        return ""
    import threading
    box: dict = {}

    def _work() -> None:
        doc = None
        try:
            doc = app.Documents.Open(os.path.abspath(path), ReadOnly=True,
                                     AddToRecentFiles=False, Visible=False)
            box["text"] = (doc.Content.Text or "").replace(
                "\r\n", "\n").replace("\r", "\n").strip()
        except Exception as e:  # noqa: BLE001
            box["err"] = e
        finally:
            try:
                if doc is not None:
                    doc.Close(False)
            except Exception:  # noqa: BLE001
                pass

    t = threading.Thread(target=_work, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        _WORD_DEAD = True
        LOG.warning("Word COM 超时（%.0fs，疑 Office 阻止策略弹窗）→ 标记不可用并回退通用抽取：%s",
                    timeout, path)
        return ""
    if "err" in box:
        LOG.warning("Word COM 抽取失败 %s：%s", path, box["err"])
        _WORD_DEAD = True          # 失败视为实例已坏，后续走通用抽取
        return ""
    return box.get("text", "")


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
    """下载并抽取详情页附件；返回 (附件记录, 附件原文)。"""
    from .attachments import fetch_gov_attachments  # 相对导入
    recs, att_text = fetch_gov_attachments(
        html, entry_id=entry_id, entry_title="", out_dir=None, base_url=url)
    improved = 0
    for r in recs:
        lp = r.get("local_path") or ""
        if not lp.lower().endswith((".doc", ".docx")):
            continue
        ap = lp if os.path.isabs(lp) else os.path.join(SCRAPERS_ROOT, lp)
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

    def __init__(self, cfg, client=None, seen_urls=None, known_urls=None):
        self.cfg = cfg
        self.client = client
        # 已知态（主库已有正文 → 跳过详情；含本轮已列，由 seen_in_run 去重）
        # 收口到通用 KnownTracker（key=detail_url）；known_urls 仅用于"新条目计数"指标。
        self.known_urls = known_urls if known_urls is not None else set()
        self.tracker = KnownTracker(key_of=lambda r: r.get("detail_url"))
        if seen_urls:
            self.tracker.load_known([{"detail_url": u} for u in seen_urls])

    def _list_url(self, index: int) -> str:
        return list_page_url(index)

    def _parse_list(self, html: str, base_url: str):
        return scan_list(html, base_url)

    # ---- 重构：列表生成器 iter_list + 详情 fetch_one（原 run() 全流程已移除）----
    def iter_list(self, ctx) -> "Iterator[dict]":
        """逐页翻列表，**只 yield new 条目**；连续 N 页无新即停（增量早停，N-190）。

        用基类 tracker 判 known/dup 并跳过；backfill 模式不早停（扫完全部）。
        原 partial 断点续跑由"主库 resume + 基类按页 checkpoint"自然取代，此处删除。
        """
        cfg = self.cfg
        max_pages = getattr(cfg, "max_pages", 0) or 0
        max_items = getattr(cfg, "max_items", 0) or 0
        delay = getattr(cfg, "delay_min", 1.5)
        stop_after = int(getattr(cfg, "stop_after_empty_pages", DEFAULT_STOP_AFTER_EMPTY_PAGES) or 1)
        backfill = bool(getattr(cfg, "backfill", False))
        first = fetch(self._list_url(0))
        if not first:
            return
        n_pages = total_pages(first)
        if max_pages:
            n_pages = min(n_pages, max_pages)
        seen = set()  # 本地运行内去重（只读判重，不污染基类 tracker.seen_in_run）
        empty_streak = 0
        yielded = 0
        for i in range(n_pages):
            html = first if i == 0 else fetch(self._list_url(i))
            if not html:
                continue
            items = self._parse_list(html, self._list_url(i))
            new_in_page = 0
            for u, t, d in items:
                # 只读判重：主库已知 → 跳过（由 merge 保活）；本轮已列 → 跳过
                if u in ctx.tracker.known or u in seen:
                    continue
                seen.add(u)
                yield {"title": t, "detail_url": u, "publish_date": d,
                       "pub_date_original": "", "effective_date": "",
                       "issue_organ": "", "document_number": "",
                       "category": CATEGORY, "source": SUB_SOURCE, "page": i}
                new_in_page += 1
                yielded += 1
                if max_items and yielded >= max_items:
                    return
            if new_in_page:
                empty_streak = 0
            else:
                empty_streak += 1
            if should_stop_paging(empty_streak, stop_after, backfill):
                break
            if delay and i + 1 < n_pages:
                time.sleep(delay)

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
                atts: list = []
                att_text: str = ""
                _skipped[0] += 1
            else:
                atts, att_text = fetch_attachments(html, url, entry_id=url)
        except Exception as e:  # noqa: BLE001
            LOG.warning("附件处理失败 %s：%s", url, e)
            atts, att_text = [], ""
        body = d["full_text"]
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
