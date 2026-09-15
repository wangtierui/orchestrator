# -*- coding: utf-8 -*-
"""gov_zhengceku.py —— 中国政府网「国务院政策文件库·国务院部门文件」采集（gov 源第 2 数据源）。

【本模块要修复的缺陷】
  gov 源此前的唯一数据源是 `xzfgk`（= `https://www.gov.cn/zhengce/xzfgk/` 行政法规库，
  详情页落在司法部 `xzfg.moj.gov.cn`）。而 **国务院部门文件栏目
  `https://www.gov.cn/zhengce/zhengceku/`（bmwj）从未纳入采集范围**，导致：
    · 该栏目下的规范性文件（如《关于进一步规范金融营销宣传行为的通知》银发〔2019〕316号）
      在 gov 源中**完全缺采**；
    · 该栏目详情页的**正文常以附件（.doc/.pdf）形式发布**，页面正文为空或仅有一个文件名，
      若不抓附件则正文仅数十字（实测 pbc 源对同一文仅取到 77 字的 PDF 文件名），
      无法支撑条款级引用核验。
  本模块补齐「列表发现 → 详情页解析 → 正文抽取 → **附件下载与原文抽取**」全链路，
  并对旧版 .doc 优先使用 Word COM 抽取（比行内 OLE 解析更完整，实测多出「证券业」
  「字体」「首府」「（十一）」等内容且无尾部二进制噪声），失败时回退
  `gov_fetch_attachments` 的通用抽取。

用法：
  python gov_zhengceku.py --url <详情页URL>            # 单条补录（含附件原文）
  python gov_zhengceku.py --pages 2                   # 列表前 2 页批量
  python gov_zhengceku.py --url <URL> --dry-run       # 仅解析预览，不写盘

输出：data/raw/gov_zhengceku.json（与 gov 源 raw 同构，供统一清洗管道消费）
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
_REPO_ROOT = os.path.dirname(os.path.dirname(_SRC_ROOT))              # .../regulatory_compliance_orchestrator
for _p in (_SRC_ROOT, _REPO_ROOT):                                    # std_lib 在 _SRC_ROOT 下
    if _p not in sys.path:
        sys.path.insert(0, _p)

LOG = logging.getLogger("gov_zhengceku")

LIST_URL = "https://www.gov.cn/zhengce/zhengceku/bmwj/home.htm"
LIST_URLS = {
    "bmwj": "https://www.gov.cn/zhengce/zhengceku/bmwj/home.htm",                    # 国务院部门文件
    "gwywj": "https://www.gov.cn/zhengce/zhengceku/gwywj/home.htm",                  # 国务院文件
}
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# 详情页正文容器候选（gov.cn 政策文件库页面结构，按优先级）
CONTENT_SELECTORS = [
    ".pages_content", ".article-content", "#UCAP-CONTENT", ".TRS_Editor",
    ".content_box", ".article", "#content_body", ".pages-content", "#zoom",
]
_TAG = re.compile(r"<[^>]+>", re.DOTALL)
_WS = re.compile(r"[ \t\u3000]{2,}")
_BLANK = re.compile(r"\n{3,}")
_A = re.compile(r'<a\b[^>]*\bhref="([^"]+)"[^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
_META = re.compile(r"(发文字号|发文机关|成文日期|主题分类|公文种类|来\s*源|标\s*题)\s*[：:]", re.U)


def _to_text(maybe) -> str:
    """把响应体统一为文本：处理 ① 已是文本 ② 原始 bytes ③ **gzip/deflate 未解压体**。

    ⚠️ 踩坑：`crawler_common.robust_get` 在带 `Accept-Encoding: gzip` 时可能返回**未解压**的
    响应体，且以 str 形态承载原始字节（latin-1 风格）。直接当文本用会得到乱码、
    页面解析全空。此处按 magic（`\\x1f\\x8b`）判定并解压后再按 utf-8/gb18030 解码。
    """
    import gzip
    import zlib
    if isinstance(maybe, bytes):
        b = maybe
    elif isinstance(maybe, str) and maybe[:2] == "\x1f\x8b":
        b = maybe.encode("latin-1", "ignore")       # str 承载的是原始字节
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


def _fetch(url: str, binary: bool = False, referer: str | None = None, timeout: int = 35):
    """HTTP GET。优先复用 crawler_common.robust_get（UA 轮换/退避），失败则退回 urllib。"""
    try:
        if _REPO_ROOT not in sys.path:
            sys.path.insert(0, _REPO_ROOT)
        from std_lib.scraper_std.crawler_common import robust_get  # type: ignore
        # ⚠️ 必须 binary=True 取回原始字节：binary=False 时返回体会被 errors="replace"
        # 解码，gzip magic 的 0x8b 变成 U+FFFD，既无法识别也无法解压（实测页面全空）。
        st, data = robust_get(url, binary=True, referer=referer or LIST_URL, timeout=timeout)
        if st == 200 and data:
            if binary:
                return data if isinstance(data, bytes) else data.encode("latin-1", "ignore")
            txt = _to_text(data)
            if txt and ("<" in txt):
                return txt
    except Exception:  # noqa: BLE001
        pass
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": referer or LIST_URL})
    with urllib.request.urlopen(req, timeout=timeout) as f:
        b = f.read()
    return b if binary else _to_text(b)


def clean_html_text(html: str) -> str:
    """HTML → 纯文本（保留段落换行）。"""
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", html or "")
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = re.sub(r"(?i)</(p|div|tr|li|h[1-6])>", "\n", s)
    s = _TAG.sub("", s)
    s = (s.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<")
          .replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'"))
    s = _WS.sub(" ", s)
    s = "\n".join(x.strip() for x in s.split("\n"))
    return _BLANK.sub("\n\n", s).strip()


def _meta_field(html: str, label: str, maxlen: int = 120) -> str:
    """从详情页元数据区取字段值（gov.cn 用「标签：值」且常夹杂标签）。"""
    for m in _META.finditer(html):
        if m.group(1).replace(" ", "").replace("\u3000", "") != label.replace(" ", ""):
            continue
        frag = clean_html_text(html[m.end():m.end() + 400])
        frag = frag.replace("|", " ").strip()
        if frag:
            return frag.split("\n")[0].strip()[:maxlen]
    return ""


def pick_content_html(html: str) -> str:
    """定位正文容器 HTML；全部选择器失败时退回「最长文本块」启发式。"""
    for sel in CONTENT_SELECTORS:
        cls = sel[1:] if sel.startswith(".") else ""
        idv = sel[1:] if sel.startswith("#") else ""
        if cls:
            m = re.search(r'<div[^>]*class="[^"]*\b' + re.escape(cls) + r'\b[^"]*"[^>]*>(.*?)</div>\s*(?:</div>|<div)',
                          html, re.I | re.S)
        elif idv:
            m = re.search(r'<div[^>]*id="' + re.escape(idv) + r'"[^>]*>(.*?)</div>\s*(?:</div>|<div)',
                          html, re.I | re.S)
        else:
            m = None
        if m and len(clean_html_text(m.group(1))) > 120:
            return m.group(1)
    # 回退：文本最长的 <div>
    best, blen = "", 0
    for m in re.finditer(r"<div[^>]*>(.*?)</div>", html, re.S):
        t = clean_html_text(m.group(1))
        if len(t) > blen:
            best, blen = m.group(1), len(t)
    return best


def parse_detail(html: str, url: str) -> dict:
    """解析详情页 → 结构化记录（不含附件实体下载）。"""
    title = ""
    m = re.search(r"(?is)<title>(.*?)</title>", html or "")
    if m:
        title = _TAG.sub("", m.group(1)).strip()
        title = re.split(r"[_\-|]", title)[0].strip()
    for m in _META.finditer(html):
        if m.group(1).replace(" ", "").replace("\u3000", "") == "标题":
            t2 = clean_html_text(html[m.end():m.end() + 300]).split("\n")[0].strip()
            if len(t2) > len(title):
                title = t2
            break
    body_html = pick_content_html(html)
    body = clean_html_text(body_html)
    return {
        "title": title,
        "source_url": url,
        "document_number": _meta_field(html, "发文字号", 60),
        "issue_organ": _meta_field(html, "发文机关", 60),
        "publish_date": _meta_field(html, "成文日期", 40),
        "category": _meta_field(html, "主题分类", 60),
        "doc_type": _meta_field(html, "公文种类", 20),
        "body_text": body,
        "body_len": len(body),
    }


def scan_list(html: str, base_url: str = LIST_URL):
    """列表页 → [(绝对URL, 标题, 日期)]。"""
    out, seen = [], set()
    for m in _A.finditer(html or ""):
        href = m.group(1).strip()
        if not re.search(r"content_\d+\.htm", href):
            continue
        u = urljoin(base_url, href)
        if u in seen:
            continue
        seen.add(u)
        txt = clean_html_text(m.group(2)).replace("\n", " ").strip()
        out.append((u, txt[:120]))
    return out


# ---------------- 附件：下载 + 原文抽取（旧 .doc 优先 Word COM） ----------------
def extract_doc_via_word(path: str) -> str:
    """用 Word COM 抽取 .doc/.docx 正文；不可用时返回空串（由调用方回退）。"""
    try:
        import win32com.client as wc  # type: ignore
    except Exception:  # noqa: BLE001
        return ""
    app = doc = None
    try:
        app = wc.Dispatch("Word.Application")
        app.Visible = False
        doc = app.Documents.Open(os.path.abspath(path), ReadOnly=True, AddToRecentFiles=False)
        txt = doc.Content.Text
        return (txt or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    except Exception as e:  # noqa: BLE001
        LOG.warning("Word COM 抽取失败 %s：%s", path, e)
        return ""
    finally:
        try:
            if doc is not None:
                doc.Close(False)
            if app is not None:
                app.Quit()
        except Exception:  # noqa: BLE001
            pass


def fetch_attachments(html: str, url: str, entry_id: str) -> tuple[list, str]:
    """下载并抽取详情页附件。返回 (附件记录列表, 附件原文合并串)。

    策略：先走 gov 既有 `fetch_gov_attachments`（扫描 <a> → 落盘 → 通用抽取），
    再对其中的 .doc/.docx 用 Word COM **重抽**，文本更长者胜出（修复 OLE 解析掉字）。
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
        if len(t2) > len(r.get("text") or ""):
            r["text"] = t2
            r["extracted"] = True
            r["extract_status"] = "ok_word_com"
            r["extract_engine"] = "word_com"
            improved += 1
    if improved:
        att_text = "\n\n".join(r["text"] for r in recs if r.get("extracted") and r.get("text"))
    return recs, att_text


def build_record(url: str, *, with_attachments: bool = True, sleep: float = 0.0) -> dict:
    html = _fetch(url)
    if isinstance(html, bytes):
        html = html.decode("utf-8", "replace")
    rec = parse_detail(html, url)
    rec["source"] = "gov"
    rec["source_origin"] = "zhengceku"
    rec["column_name"] = "国务院部门文件"
    if with_attachments:
        try:
            atts, att_text = fetch_attachments(html, url, entry_id=url)
        except Exception as e:  # noqa: BLE001
            LOG.warning("附件处理失败 %s：%s", url, e)
            atts, att_text = [], ""
        rec["attachments"] = atts
        rec["attachment_count"] = len(atts)
        rec["attachment_text"] = att_text
        # 正文为空的页面（正文以附件形式发布）→ 用附件原文补正文
        if len(rec["body_text"]) < 200 and len(att_text) > len(rec["body_text"]):
            rec["body_text"] = att_text
            rec["body_source"] = "attachment"
        else:
            rec["body_source"] = "webpage"
    if sleep:
        time.sleep(sleep)
    return rec


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="gov 源「国务院政策文件库·部门文件」采集（含附件原文）")
    ap.add_argument("--url", default="", help="详情页 URL（单条补录）")
    ap.add_argument("--pages", type=int, default=0, help="列表页翻页数（0=不抓列表）")
    ap.add_argument("--list-kind", choices=sorted(LIST_URLS), default="bmwj", help="栏目")
    ap.add_argument("--out", default=os.path.join(_SRC_ROOT, "data", "raw", "gov_zhengceku.json"),
                    help="输出 JSON 路径")
    ap.add_argument("--no-attachments", action="store_true", help="不抓附件")
    ap.add_argument("--dry-run", action="store_true", help="仅解析预览，不写盘")
    ap.add_argument("--sleep", type=float, default=1.5, help="请求间隔（秒）")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    urls = []
    if args.url:
        urls.append(args.url)
    if args.pages:
        base = LIST_URLS[args.list_kind]
        for p in range(1, args.pages + 1):
            u = base if p == 1 else base.replace("home.htm", f"home_{p - 1}.htm")
            try:
                h = _fetch(u, referer=base)
                if isinstance(h, bytes):
                    h = h.decode("utf-8", "replace")
                found = scan_list(h, u)
                LOG.info("列表页 %s → %d 条", u, len(found))
                urls += [x[0] for x in found]
            except Exception as e:  # noqa: BLE001
                LOG.warning("列表页失败 %s：%s", u, e)

    seen, recs = set(), []
    for u in urls:
        if u in seen:
            continue
        seen.add(u)
        try:
            rec = build_record(u, with_attachments=not args.no_attachments, sleep=args.sleep)
        except Exception as e:  # noqa: BLE001
            LOG.warning("详情页失败 %s：%s", u, e)
            continue
        recs.append(rec)
        print(f"[ok] {rec['document_number'] or '(无文号)':22s} 正文 {rec['body_len']:>6d} 字 "
              f"附件 {rec.get('attachment_count', 0)} 个（附件原文 {len(rec.get('attachment_text') or '')} 字） "
              f"| {rec['title'][:44]}")

    if not args.dry_run and recs:
        os.makedirs(os.path.dirname(args.out), exist_ok=True)
        old = []
        if os.path.exists(args.out):
            try:
                old = json.load(open(args.out, encoding="utf-8"))
                if isinstance(old, dict):
                    old = old.get("records", [])
            except Exception:  # noqa: BLE001
                old = []
        keep = {r.get("source_url") for r in recs}
        merged = [r for r in old if r.get("source_url") not in keep] + recs
        json.dump(merged, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"[out] 写入 {args.out}：本次 {len(recs)} 条 / 合并后 {len(merged)} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
