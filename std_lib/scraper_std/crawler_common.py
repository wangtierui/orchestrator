# -*- coding: utf-8 -*-
"""
crawler_common.py —— 四监管爬虫（gov / mof / nfra / pbc）共享加固工具层

定位：作为四个独立爬虫项目的「单一真相源」工具层，统一提供：
  1. 反爬加固的 HTTP 客户端（UA 轮换、富请求头、随机延时、指数退避、尊重 Retry-After）
  2. 魔数纠正的安全文件名
  3. 多格式文档文本抽取（PDF / docx / xlsx / 旧 .xls / 旧 .doc），带透明状态标记与 sha256
  4. 日期/全角数字归一、内容哈希、乱码比例检测

设计原则（对齐 DAMA / ISO 8000 数据质量基线）：
  - 优雅降级：缺失第三方库时标记 extract_status="library_missing"，不静默丢弃；
  - 透明标记：扫描件/损坏/加密等不可抽情形均返回结构化状态，源文件始终留存；
  - 零硬依赖：import 阶段不强制安装任何第三方库，库在真正调用时才惰性导入。

本文件为纯标准库 + 惰性第三方导入，可在隔离环境离线单元测试。
"""

from __future__ import annotations

import hashlib
import logging
import os
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

LOG = logging.getLogger("crawler_common")

# --------------------------------------------------------------------------- #
# 1. 反爬加固 HTTP 客户端
# --------------------------------------------------------------------------- #

# 桌面浏览器 UA 池（轮换以规避脚本识别）
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36 Edg/119.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

# 富请求头模板（模拟真实浏览器；不含 br 压缩，避免无 brotli 解码器导致乱码）
BASE_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
    "application/json;q=0.8,application/pdf;q=0.7,*/*;q=0.6",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

# 确定性的「不重试」状态码（资源不存在/无权限）
_NO_RETRY_STATUS = frozenset({403, 404, 410})
# 限流/服务端临时错误（退避重试）
_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})


def robust_get(
    url: str,
    *,
    binary: bool = False,
    timeout: int = 30,
    retries: int = 4,
    min_delay: float = 0.8,
    max_delay: float = 1.6,
    referer: str | None = None,
    extra_headers: dict[str, str] | None = None,
) -> tuple[int | None, Any]:
    """
    带反爬策略的 GET 请求，返回 (status, content)。

    - status: HTTP 状态码；失败（含重试耗尽）为 None。
    - content: 文本模式返回 str；binary 模式返回 bytes；失败返回错误说明 str。

    策略：
      * 每次请求随机轮换 UA + 富请求头（Accept/Accept-Language/Referer）；
      * 请求间隔在 [min_delay, max_delay] 随机化，规避固定节奏风控；
      * 指数退避重试（2**attempt 秒上限 16s）；
      * 命中 429/503 且响应带 Retry-After 时，优先采用服务端给出的等待时长；
      * 403/404/410 确定性错误立即返回，不浪费重试配额。
    """
    url = _safe_url(url)
    last_err: Any = None
    for attempt in range(1, retries + 1):
        # 礼貌限速：随机间隔
        gap = random.uniform(min_delay, max_delay)
        time.sleep(gap)
        try:
            req = urllib.request.Request(url, method="GET")
            req.add_header("User-Agent", random.choice(USER_AGENTS))
            for k, v in BASE_HEADERS.items():
                req.add_header(k, v)
            if referer:
                req.add_header("Referer", referer)
            if extra_headers:
                for k, v in extra_headers.items():
                    req.add_header(k, v)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = resp.status
                data = resp.read()
                if binary:
                    return status, data
                enc = resp.headers.get_content_charset() or "utf-8"
                try:
                    return status, data.decode(enc, errors="replace")
                except LookupError:
                    return status, data.decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            last_err = f"HTTP {e.code}"
            if e.code in _NO_RETRY_STATUS:
                return e.code, None
            if e.code in _RETRY_STATUS:
                wait = _backoff_wait(attempt, e.headers.get("Retry-After"))
                LOG.warning(
                    "HTTP %s @ %s 限流/暂不可用，%ss 后重试(%d/%d)",
                    e.code,
                    url,
                    round(wait, 1),
                    attempt,
                    retries,
                )
                time.sleep(wait)
                continue
            return e.code, None
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
            last_err = f"{type(e).__name__}: {e}"
            wait = _backoff_wait(attempt, None)
            LOG.warning(
                "网络异常 @ %s，%ss 后重试(%d/%d): %s", url, round(wait, 1), attempt, retries, e
            )
            time.sleep(wait)
            continue
    LOG.error("GET %s 重试 %d 次仍失败：%s", url, retries, last_err)
    return None, last_err


def _backoff_wait(attempt: int, retry_after: str | None) -> float:
    """退避时长：优先 Retry-After（秒），否则 2**attempt 上限 16s，加随机抖动。"""
    if retry_after and retry_after.isdigit():
        return float(int(retry_after))
    return min(2**attempt, 16) + random.uniform(0, 1)


def _safe_url(url: str) -> str:
    """简单清理：去除危险空白；保留原样其余部分。"""
    return (url or "").strip()


# --------------------------------------------------------------------------- #
# 2. 安全文件名（魔数纠正扩展名）
# --------------------------------------------------------------------------- #

_ILLEGAL = re.compile(r'[\\/:*?"<>|\r\n\t]+')
_ATTACH_EXT = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".wps", ".ceb", ".rtf", ".zip", ".rar")


def sniff_kind(data: bytes, name: str = "") -> str:
    """
    依据文件头魔数纠正真实格式（避免扩展名误标导致抽取失败）。
    返回：pdf / docx / xlsx / ole2(xls|doc) / zip / rar / unknown
    """
    if not data:
        return "empty"
    head = data[:8]
    if head[:4] == b"%PDF":
        return "pdf"
    if head[:4] == b"PK\x03\x04" or head[:4] == b"PK\x05\x06" or head[:4] == b"PK\x07\x08":
        # docx / xlsx / zip 均为 ZIP 容器，需进一步区分
        if _is_office_openxml(data):
            if (
                b"xl/" in data[: min(len(data), 2_000_000)]
                or b"workbook" in data[: min(len(data), 2_000_000)]
            ):
                return "xlsx"
            return "docx"
        # 无法判定为 OOXML（损坏/截断）→ 退而用扩展名，避免误标 zip
        ext = os.path.splitext(name)[1].lower()
        if ext == ".docx":
            return "docx"
        if ext == ".xlsx":
            return "xlsx"
        return "zip"
    if head[:8] == b"Rar!\x1a\x07" or head[:4] == b"Rar!":
        return "rar"
    if head[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return "ole2"  # 旧版 .xls / .doc
    # 退而求其次：靠扩展名
    ext = os.path.splitext(name)[1].lower()
    if ext in (".pdf",):
        return "pdf"
    if ext in (".docx",):
        return "docx"
    if ext in (".xlsx",):
        return "xlsx"
    if ext in (".xls", ".doc", ".wps", ".ceb", ".rtf"):
        return "ole2"
    return "unknown"


def _is_office_openxml(data: bytes) -> bool:
    """ZIP 容器中是否含 [Content_Types].xml（OOXML 标识）。"""
    try:
        import zipfile

        if not data.startswith(b"PK"):
            return False
        # 仅在头部探测，避免整文件加载
        with zipfile.ZipFile(__import__("io").BytesIO(data[: min(len(data), 5_000_000)])) as z:
            names = z.namelist()
        return "[Content_Types].xml" in names
    except Exception:  # noqa: BLE001
        return "docProps" in data[: min(len(data), 200_000)].decode("latin-1", "ignore")


def safe_filename(name: str, url: str = "", max_len: int = 120) -> str:
    """
    生成安全文件名：去除非法字符、限长、保留可读标题 + 以 URL 哈希前缀去重。
    扩展名交由调用方按魔数纠正后追加。
    """
    raw = (name or "").strip()
    raw = _ILLEGAL.sub("_", raw)
    raw = raw.strip("._ ")
    if not raw:
        raw = "attachment"
    if len(raw) > max_len:
        raw = raw[:max_len].rstrip("._ ")
    if url:
        prefix = hashlib.md5(url.encode("utf-8")).hexdigest()[:8]
        return f"{prefix}_{raw}"
    return raw


def is_attachment_url(url: str, text: str = "") -> bool:
    """判断链接是否指向文档下载（扩展名命中或链接文案含下载关键词）。"""
    if not url:
        return False
    low = url.split("?")[0].lower()
    if low.endswith(_ATTACH_EXT):
        return True
    kw = ("下载", "download", "word", "pdf", "全文", "附件", "doc", "xlsx", "excel")
    return any(k in (text or "").lower() for k in kw)


# --------------------------------------------------------------------------- #
# 3. 多格式文档文本抽取（透明标记 + sha256）
# --------------------------------------------------------------------------- #


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def garble_ratio(text: str) -> float:
    """替换字符（U+FFFD）占比，>1% 视为疑似乱码。"""
    if not text:
        return 0.0
    return text.count("\ufffd") / max(1, len(text))


_HTML_HEAD_RE = re.compile(rb"<(?:!doctype\s+html|html|body|table|div|p)\b", re.I)
_MIME_HEAD_RE = re.compile(
    rb"^(?:MIME-Version|Content-Type|Content-Transfer-Encoding|Content-Disposition)\s*:", re.I | re.M)
_HTTP_HEAD_RE = re.compile(rb"^HTTP/\d\.\d\s+\d{3}|^(?:Server|Date|Content-Length)\s*:", re.I | re.M)


def _strip_leading_headers(data: bytes) -> tuple[bytes, str]:
    """剥离**前导文本头**（MIME/HTTP 头）：返回 `(载荷, 证据)`；无分隔空行则不剥离。"""
    head = data[: min(len(data), 65536)]
    if not (_MIME_HEAD_RE.search(head) or _HTTP_HEAD_RE.search(head)):
        return data, ""
    for sep in (b"\r\n\r\n", b"\n\n", b"\r\r"):
        i = head.find(sep)
        if i >= 0:
            return data[i + len(sep):], "headers"
    return data, ""


def unmask_payload(data: bytes) -> tuple[bytes, dict]:
    """**拆封**（批 54/W-W）：识别并解开"外壳非内容"的伪装，返回 `(内层载荷, 证据)`。

    覆盖：① 前导 MIME/HTTP 头（实测 `83896.doc` 内容以 `MIME-Ver…` 开头 ⇒ xlrd/OLE2 解析全败）
    ② MIME multipart（取最大有效部分）③ BOM/空白等前缀噪声。
    仅在**确实拆封**时返回非空证据（`{unmasked, outer_head, inner_head}`），否则原字节 + `{}`。
    """
    ev: dict[str, Any] = {}
    # ① **MIME 容器优先**（整封含 MIME-Version/Content-Type + boundary）——须在剥离前判定，
    #    否则头部被剥离后 email 解析器无从识别 multipart。
    head = data[: min(len(data), 65536)]
    if _MIME_HEAD_RE.search(head[:4096]) and b"boundary=" in head[:4096].lower():
        try:
            import email

            msg = email.message_from_bytes(data)
            best: bytes = b""
            for part in msg.walk():
                payload = bytes(part.get_payload(decode=True) or b"")
                if len(payload) > len(best):
                    best = payload
            # 阈值 16B（批 54 实测：`EmailMessage` 小附件 <64B 会被漏拆；真实载荷均 ≥16B）
            if len(best) >= 16:
                ev = {"unmasked": "mime_multipart",
                      "outer_head": data[:24].decode("utf-8", "replace")}
                data = best
        except Exception:  # noqa: BLE001  拆封失败不阻断（保持原字节）
            pass
    # ② 前导 MIME/HTTP 头（外壳头 + 载荷）——仅在非 MIME 容器时适用
    if not ev:
        inner, why = _strip_leading_headers(data)
        if why:
            ev = {"unmasked": why, "outer_head": data[:24].decode("utf-8", "replace")}
            data = inner
    if not ev:
        return data, {}
    ev["inner_head"] = data[:12].decode("utf-8", "replace")
    return data, ev


def html_to_text(payload: bytes) -> str:
    """HTML → 可见文本（批 54/W-W：`html` 伪装轨；去 script/style 与标签）。"""
    s = payload.decode("utf-8", "replace")
    s = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    return re.sub(r"[ \t\r\f\v]+", " ", s).strip()


def extract_document_text(
    data: bytes,
    name: str = "",
    *,
    enable_ocr: bool = False,
    ocr_timeout: int = 60,
) -> dict[str, Any]:
    """
    抽取文档内部文本，返回结构化结果：
      {
        text, kind, extracted(bool), extract_status(str),
        sha256, needs_ocr(bool), garble_ratio(float), size_bytes(int)
      }
    extract_status 取值：
      ok | empty(抽取为空但非错误) | needs_ocr(扫描件PDF无文本层) |
      library_missing(依赖缺失) | unsupported(格式不支持) | corrupt(损坏)
    源字节调用方负责落盘留存，本函数不丢弃任何文件。
    """
    kind = sniff_kind(data, name)
    sha = sha256_of(data)
    # 批 54/W-W：**content-type/sniff 二次判定** —— 扩展名与魔数均不可信时（MIME 封装、前导
    # MIME/HTTP 头、HTML 冒充 .doc 等）先**拆封**再重新嗅探，全过程**留证据** `sniff`（可审计）。
    _sniff: dict[str, Any] = {"declared_ext": os.path.splitext(name or "")[1].lower(),
                              "sniffed_kind": kind}
    _inner, _ev = unmask_payload(data)
    if _ev:
        _sniff.update(_ev)
        data = _inner
        # 拆封后**不得再用（可能误导的）扩展名兜底** —— 扩展名正是伪装来源本身
        kind = sniff_kind(_inner, "")
        if kind == "unknown":
            try:
                if _inner.decode("utf-8").strip():
                    kind = "text"   # 外壳包裹的纯文本（实测 83896.doc 同类：MIME 头 + 文本载荷）
            except UnicodeDecodeError:
                pass
        _sniff["inner_kind"] = kind
        LOG.info("[sniff2] %s：%s → %s（%s）", name, _sniff["sniffed_kind"], kind,
                 _ev.get("unmasked"))
    if kind == "text":   # 批 54：拆封得到的纯文本（不做二进制解析）
        _txt = data.decode("utf-8", "replace")
        LOG.info("[sniff2] %s：按纯文本处理（%d 字符）", name, len(_txt))
        return {"text": _txt, "kind": "text", "extracted": bool(_txt.strip()),
                "extract_status": "ok" if _txt.strip() else "empty", "sha256": sha,
                "needs_ocr": False, "garble_ratio": 0.0, "size_bytes": len(data),
                "sniff": _sniff}
    _head_probe = data[:256].lstrip(b"\xef\xbb\xbf \t\r\n")
    if _HTML_HEAD_RE.match(_head_probe):
        _txt = html_to_text(data)
        kind = "html"
        _sniff["inner_kind"] = "html"
        LOG.info("[sniff2] %s：按 HTML 伪装处理（%d 字符可见文本）", name, len(_txt))
        return {"text": _txt, "kind": "html", "extracted": bool(_txt),
                "extract_status": "ok" if _txt else "empty", "sha256": sha,
                "needs_ocr": False, "garble_ratio": 0.0, "size_bytes": len(data),
                "sniff": _sniff}
    rec: dict[str, Any] = {
        "sniff": _sniff,
        "text": "",
        "kind": kind,
        "extracted": False,
        "extract_status": "unsupported",
        "sha256": sha,
        "needs_ocr": False,
        "garble_ratio": 0.0,
        "size_bytes": len(data),
    }
    if kind == "empty":
        rec["extract_status"] = "empty"
        return rec
    if kind in ("zip", "rar", "unknown"):
        rec["extract_status"] = "unsupported"
        return rec
    try:
        if kind == "pdf":
            return _merge(rec, _extract_pdf(data, enable_ocr, ocr_timeout))
        if kind == "docx":
            return _merge(rec, _extract_docx(data))
        if kind == "xlsx":
            return _merge(rec, _extract_xlsx(data))
        if kind == "ole2":
            return _merge(rec, _extract_ole2(data))
    except Exception as e:  # 抽取异常不崩溃，透明标记  # noqa: BLE001
        rec["extract_status"] = "corrupt"
        rec["text"] = ""
        LOG.warning("抽取 %s(%s) 异常：%s", name, kind, e)
        return rec
    rec["extract_status"] = "unsupported"
    return rec


def _merge(rec: dict[str, Any], sub: dict[str, Any]) -> dict[str, Any]:
    rec.update(sub)
    rec["garble_ratio"] = garble_ratio(sub.get("text", ""))
    return rec


_PAGENO_RE = re.compile(
    r"^\s*[—\-–一]\s*\d+\s*[—\-–]?\s*$|^\s*第\s*\d+\s*页(共\d+页)?\s*$|^\s*\d{1,3}\s*$"
)


def clean_pdf_text(text: str, min_repeat: int = 3) -> str:
    """PDF 文本页眉/页脚清理（2026-09-10）：
    按 \f 分页统计行频——跨页重复出现(>=min_repeat 次)的短行（<=40 字，多为红头/页眉/页脚）
    删除；页码行（— 1 — / 第N页 / 纯数字行）删除。正文长行不受影响。"""
    if not text:
        return text
    pages = text.split("\f")
    if len(pages) <= 1:
        pages = [text]
    freq: dict[str, int] = {}
    parsed = []
    for pg in pages:
        lines = [ln.strip() for ln in pg.splitlines()]
        body = [ln for ln in lines if ln]
        parsed.append(body)
        for ln in body[:3] + body[-3:]:  # 页眉/页脚只可能出现在页首尾
            if len(ln) <= 40:
                freq[ln] = freq.get(ln, 0) + 1
    out = []
    for body in parsed:
        keep = []
        for ln in body:
            if _PAGENO_RE.match(ln):
                continue
            if freq.get(ln, 0) >= min_repeat and len(ln) <= 40:
                continue
            keep.append(ln)
        out.append("\n".join(keep))
    return "\f".join(out).strip()


# 文本层"有效"判据（2026-09-13）：有效**汉字**数下限。
# 不用"总字符数"——页眉/页脚/水印（EOA 打印链接、页码、日期）会虚增总字符数，
# 使正文为整页图片的扫描件被误判为"有文字层"而跳过 OCR。
_TEXT_LAYER_MIN_CJK = 30


def cjk_count(s: str) -> int:
    """有效汉字数（文本层有效性 / 抽取质量判据，见 `_TEXT_LAYER_MIN_CJK`）。

    公开名（供 `internal_policy_base.extract` 的扫描件/OCR 目标判定复用，避免两份实现）。
    """
    return len(re.findall(r"[\u4e00-\u9fa5]", s or ""))


# 空引号对：引号内字符**丢失**的信号（`“”` / `""`）。实测成因：pypdf 对部分嵌入字体
# 逐 token 分行输出，下游把"2 汉字短行"（`楼兰`/`公司`）当水印删除 → 引号内内容为空。
_EMPTY_QUOTE_RE = re.compile(r'[“"][”"]')


def text_layer_ok(t: str, *, min_cjk: int = _TEXT_LAYER_MIN_CJK) -> bool:
    """文本层质量判据（抽取链内部使用）：**有效汉字足够 且 无缺字信号**。"""
    return cjk_count(t) >= min_cjk and not _EMPTY_QUOTE_RE.search(t)


def has_extraction_gap(t: str) -> bool:
    """缺字信号：存在空引号对（引号内字符丢失）。

    ⚠️ 判定对象必须是**库级抽取输出**（`clean_pdf_text` 后）。经 `extract.normalize_text`
    清洗后，"无 Unicode 映射的符号字形"（控制字符）会被删除并**同样**留下 `“”`——
    那是**原文特征**而非抽取缺陷（实测 `点击相应的影像类别序号前的“＋”`）。
    故该信号用于抽取链择优（`text_layer_ok`）与 reocr 的"**首次**提质尝试"触发，
    不作为永久重跑依据（否则 reocr 永不幂等）。
    """
    return bool(_EMPTY_QUOTE_RE.search(t))


def _extract_pdf(data: bytes, enable_ocr: bool, ocr_timeout: int) -> dict[str, Any]:
    """PDF 抽取：文本层（**pymupdf → pypdf → pdfplumber**，取首个"质量合格"者）→ 不足则 OCR。

    2026-09-13 修正（用户报告两批案例）：
      ① 文本层"有效"判据由 `text.strip()` 改为 **有效汉字数 ≥ `_TEXT_LAYER_MIN_CJK`**——
         原判据只看"有无文本"，会被页眉/页脚噪声虚增（实测某红头文件文字层 461 字全为
         `eoa.sinosig.com` 打印页眉，正文实为整页图片，却被判"有文字层"而跳过 OCR）；
      ② **pymupdf 提到首位**：pypdf 对部分嵌入字体会**逐 token 分行**输出
         （`关于下发\n《\n阳光人寿保险股份有限\n公司\n2\n022\n年\n“\n楼兰\n”\n…`），
         下游 `normalize_text._drop_junk_lines` 的水印启发式随即把"2 汉字短行"（`楼兰`/`公司`）
         当水印删除 → 正文**缺字**（实测留下空引号对 `“”`，共 11 份受影响）。
         pymupdf 按行输出完整文本，无此缺陷。
      ③ 质量判据 `text_layer_ok()`：有效汉字足够 **且** 无空引号对（缺字信号）。
    """
    cands: list[str] = []
    # ① pymupdf（首选：按行输出，不会把短词拆成独立行）
    try:
        import pymupdf

        with pymupdf.open(stream=data, filetype="pdf") as doc:
            t = clean_pdf_text("".join(p.get_text() for p in doc))
        if t.strip():
            if text_layer_ok(t):
                return {"text": t, "extracted": True, "extract_status": "ok", "needs_ocr": False}
            cands.append(t)
    except Exception:  # noqa: BLE001  缺库/解析失败 → 下一库
        pass
    # ② pypdf（轻量）
    try:
        from pypdf import PdfReader

        reader = PdfReader(__import__("io").BytesIO(data))
        text = ""
        for page in reader.pages:
            try:
                text += (page.extract_text() or "") + "\n"
            except Exception:  # noqa: BLE001  旁路设施/缓存降级（主路径不受影响）
                pass
        t = clean_pdf_text(text)
        if t.strip():
            if text_layer_ok(t):
                return {"text": t, "extracted": True, "extract_status": "ok", "needs_ocr": False}
            cands.append(t)
    except ImportError:
        pass
    # ③ 回退 pdfplumber
    try:
        import pdfplumber

        text = ""
        with pdfplumber.open(__import__("io").BytesIO(data)) as pdf:
            for page in pdf.pages:  # type: ignore[assignment]
                try:
                    text += (page.extract_text() or "") + "\n"
                except Exception:  # noqa: BLE001  旁路设施/缓存降级（主路径不受影响）
                    pass
        t = clean_pdf_text(text)
        if t.strip():
            if text_layer_ok(t):
                return {"text": t, "extracted": True, "extract_status": "ok", "needs_ocr": False}
            cands.append(t)
    except ImportError:
        pass
    # ③′ 取"有效汉字最多"的候选；三库皆缺 → library_missing
    best = max(cands, key=cjk_count) if cands else ""
    if not _pdf_lib_available():
        return {
            "text": "",
            "extracted": False,
            "extract_status": "library_missing",
            "needs_ocr": False,
        }
    # 库存在但文本层有效汉字不足（空 / 仅页眉页脚）→ 疑似扫描件 → OCR
    if enable_ocr and _ocr_available():
        try:
            ocr_text, ocr_engine = _run_ocr_detail(data, ocr_timeout)
            if ocr_text.strip():
                # 2026-09-12：透出实际生效引擎（reocr --force 提质重跑跳过标记用）
                return {
                    "text": ocr_text.strip(),
                    "extracted": True,
                    "extract_status": "ok",
                    "needs_ocr": True,
                    "ocr_engine": ocr_engine,
                }
        except Exception as e:  # noqa: BLE001
            LOG.warning("PDF OCR 失败：%s", e)
    if best.strip():
        # 文本层残量不足（多为页眉水印），保留但显式标记 needs_ocr（不再伪装 ok）
        return {"text": best, "extracted": True, "extract_status": "ok", "needs_ocr": True}
    return {"text": "", "extracted": False, "extract_status": "needs_ocr", "needs_ocr": True}


def _pdf_lib_available() -> bool:
    try:
        import pymupdf  # noqa: F401

        return True
    except ImportError:
        pass
    try:
        import pypdf  # noqa: F401

        return True
    except ImportError:
        pass
    try:
        import pdfplumber  # noqa: F401

        return True
    except ImportError:
        return False


def _extract_docx(data: bytes) -> dict[str, Any]:
    # 优先 python-docx
    try:
        import docx

        d = docx.Document(__import__("io").BytesIO(data))
        text = "\n".join(p.text for p in d.paragraphs if p.text)
        if text.strip():
            return {"text": text.strip(), "extracted": True, "extract_status": "ok"}
    except ImportError:
        pass
    # 零依赖回退：zipfile + word/document.xml
    try:
        import zipfile

        with zipfile.ZipFile(__import__("io").BytesIO(data)) as z:
            xml = z.read("word/document.xml").decode("utf-8", "replace")
        texts = re.findall(r"<w:t[^>]*>(.*?)</w:t>", xml, re.S)
        text = "".join(texts)
        if text.strip():
            return {
                "text": re.sub(r"\s+", " ", text).strip(),
                "extracted": True,
                "extract_status": "ok",
            }
    except Exception:  # noqa: BLE001  旁路设施/缓存降级（主路径不受影响）
        pass
    if not _docx_lib_available():
        return {"text": "", "extracted": False, "extract_status": "library_missing"}
    return {"text": "", "extracted": False, "extract_status": "empty"}


def _docx_lib_available() -> bool:
    try:
        import docx  # noqa: F401

        return True
    except ImportError:
        return False


#: XLSX 文本抽取的**内容边界与上限**（N-208，2026-10-09，批 49）。
#:
#: 为何必需（实测根因）：某公告 xlsx 的第 2 张表**声明 `max_col=16384`**（实际仅 6 列有值，
#: 全表声明 4374×16384 = **71,665,278 格**而**非空仅 10,923**，非空率 **0.015%**）。
#: 原实现 `for r in ws.iter_rows(values_only=True)` **按声明维度展开**（openpyxl 会为每行补齐到
#: 声明列宽）⇒ 抽出 **71,769,545 字符**（其中 **99.2% 是单元格分隔制表符**）⇒ 单条 cleaned 记录被撑到
#: **279MB**（占 gov 语料 **24.6%**），并让任何触碰该源的步骤付出 GB 级瞬时内存与 8 倍快照/备份复制。
#:
#: 修法（**按内容裁剪 + 显式上限**，不改语义）：
#:   ① 每行**右裁空单元格**——本 bug 的主因（16384 列声明 vs 6 列有值）；
#:   ② **连续空行**达 `XLSX_MAX_EMPTY_ROWS` 即视为表尾（防声明行数虚高；内部空行仍保留最多 2 行以便分块）；
#:   ③ 单附件文本上限 `XLSX_MAX_CHARS`，超出**截断并在文本尾部显式标注**（可审计，不静默丢）。
XLSX_MAX_CHARS = 2_000_000
XLSX_MAX_EMPTY_ROWS = 60
XLSX_MAX_ROWS = 200_000

#: 批 55：**旧二进制/不可解析 kind 的唯一事实源**（表格/富内容轨默认跳过这些 kind：
#: LibreOffice 转换约 6.5s/附件且多数产出为空）。gov/mof 等采集侧一律引用此处，禁止各自定义。
LEGACY_KINDS: frozenset[str] = frozenset({"ole2", "doc", "xls", "wps", "rtf", "ceb", "bin",
                                           "unknown"})


def iter_ws_text_rows(ws, *, max_rows: int = XLSX_MAX_ROWS,
                      max_empty_rows: int = XLSX_MAX_EMPTY_ROWS):
    """**单个工作表**的文本行迭代（批 52/W-L：xlsx 逐行读取的**唯一共享原语**）。

    行为：右裁空单元格（N-208 爆量主因）→ 连续空行达阈值视为表尾（前 2 个空行仍产出 `[]`
    以保留块间分隔）→ 累计行数受 `max_rows` 约束（产出即计数）。
    在用户（**不重复造轮子**）：`iter_xlsx_text_rows`（五源文本轨）、
    `table_recovery._tables_from_xlsx`（矩阵轨/⑮）、`tools/build_east_backlog.extract_xlsx`、
    `internal_policy_drafter/scripts/dump_related_systems.dump`。
    """
    emitted = 0
    empty_streak = 0
    for r in ws.iter_rows(values_only=True):
        if emitted >= max_rows:
            return
        cells = ["" if c is None else str(c) for c in r]
        while cells and cells[-1] == "":
            cells.pop()
        if not cells:
            empty_streak += 1
            if empty_streak >= max_empty_rows:
                return
            if empty_streak <= 2:
                yield []
                emitted += 1
            continue
        empty_streak = 0
        emitted += 1
        yield cells


def count_xlsx_formula_cells(data: bytes) -> int:
    """xlsx 内**公式单元格计数**（批 52/W-N⑧）：经 zip 内工作表 XML 的 `<f` 标签计数（有界扫描）。

    用途：`data_only=True` 取值**依赖缓存值**——公式无缓存值时读到 None 而无从分辨 ⇒ 本计数落
    `table_structured[].meta.formula_cells` + `formula_values="cached"`，把"静默丢"变**可审计**。
    """
    import re as _re
    import zipfile

    n = 0
    try:
        with zipfile.ZipFile(__import__("io").BytesIO(data)) as z:
            for nm in z.namelist()[:512]:
                if not _re.match(r"xl/worksheets/sheet\d+[.]xml$", nm):
                    continue
                with z.open(nm) as fh:
                    blob = fh.read(64 * 1024 * 1024)
                n += blob.count(b"<f")
    except Exception:  # noqa: BLE001  非 OOXML/损坏包：计数为 0（不阻断）
        return 0
    return n


def iter_xlsx_text_rows(data: bytes):
    """**共享原语**（批 49/50）：逐行产出 xlsx 的**已右裁空单元格**行（`list[str]`）。

    为何必须共享：N-208 根因是"按工作表**声明维度**展开空网格"（实测某 17KB xlsx 声明
    4374×16384 格、有值仅 10,923 ⇒ 抽出 71,769,545 字符）。其中
      · `crawler_common._extract_xlsx`（gov/mof/supp 走此）与
      · `excel_matrix._read_xlsx_bytes`（表格结构化）
    已在批 49 修复；批 50 复核发现 **nfra `nfra_attachments_extract.extract_xlsx` 与
    pbc `pbc_parse.extract_xls_text` 是自建实现、未走本修复**（前者**同型缺陷**）⇒ 统一到本原语，
    确保"写入阶段不再产生同类爆量"。

    行为：跨工作表连续产出；每行右裁空单元格后为空 ⇒ 记为一次"连续空行"（前 2 个空行仍产出空列表，
    以保留块间分隔），连续空行达 `XLSX_MAX_EMPTY_ROWS` 即视为表尾；总行数受 `XLSX_MAX_ROWS` 约束。
    """
    import openpyxl

    wb = openpyxl.load_workbook(__import__("io").BytesIO(data), data_only=True, read_only=True)
    for ws in wb.worksheets:
        yield from iter_ws_text_rows(ws)


def cap_xlsx_text(text: str, limit: int = XLSX_MAX_CHARS) -> str:
    """**共享上限**：超限即截断并**显式标注**（可审计，不静默丢）。"""
    if len(text) <= limit:
        return text
    return text[:limit] + chr(10) + "[表格超限：已按上限 %d 字符截断]" % limit


def _extract_xlsx(data: bytes) -> dict[str, Any]:
    try:
        rows: list[str] = []
        total = 0
        truncated = False
        for cells in iter_xlsx_text_rows(data):
            if total >= XLSX_MAX_CHARS:
                truncated = True
                break
            line = "\t".join(cells)
            rows.append(line)
            total += len(line) + 1
        text = cap_xlsx_text((chr(10)).join(rows))
        if truncated and "表格超限" not in text:      # 主动 break（未达上限长度）时**同样显式标注**
            text += chr(10) + "[表格超限：已按上限 %d 字符截断]" % XLSX_MAX_CHARS
        text = text.rstrip()
        if text.strip():
            return {"text": text.strip(), "extracted": True, "extract_status": "ok"}
    except ImportError:
        pass
    # 零依赖回退：sharedStrings.xml
    try:
        import zipfile

        with zipfile.ZipFile(__import__("io").BytesIO(data)) as z:
            names = z.namelist()
            if "xl/sharedStrings.xml" in names:
                xml = z.read("xl/sharedStrings.xml").decode("utf-8", "replace")
                strings = re.findall(r"<t[^>]*>(.*?)</t>", xml, re.S)
                # N-208：零依赖回退路径同样**受上限约束**（共享串表也可能被异常文件撑爆）
                while sum(len(s) for s in strings) > XLSX_MAX_CHARS and strings:
                    strings = strings[: int(len(strings) * 0.9)] or strings[:-1]
                text = "\n".join(strings)
                if text.strip():
                    return {
                        "text": re.sub(r"\s+", " ", text).strip(),
                        "extracted": True,
                        "extract_status": "ok",
                    }
    except Exception:  # noqa: BLE001  旁路设施/缓存降级（主路径不受影响）
        pass
    if not _xlsx_lib_available():
        return {"text": "", "extracted": False, "extract_status": "library_missing"}
    return {"text": "", "extracted": False, "extract_status": "empty"}


def _xlsx_lib_available() -> bool:
    try:
        import openpyxl  # noqa: F401

        return True
    except ImportError:
        return False


_WPS_AVAILABLE: bool | None = None  # 模块级缓存：WPS 是否安装（None=未探测）


def wps_available() -> bool:
    """探测本机是否安装 WPS（ProgID 注册表探测，O(1)，不启动任何进程）。

    ⚠️ 性能关键（2026-09-15 实测）：`_extract_doc_via_wps` 直接
    `Dispatch("KWPS.Application")`，在**未安装 WPS** 的机器上该调用不会立即失败，
    而是阻塞约 14 秒（等待 COM 激活超时）后才抛错，再回退 olefile。
    批量采集时每条 .doc 附件都白等一次 → 实测单条采集因此从 ~1s 劣化到 ~15s。
    故在此以前置探测短路：未安装即 `return None`，让调用方直接走 olefile 兜底。
    """
    global _WPS_AVAILABLE
    if _WPS_AVAILABLE is not None:
        return _WPS_AVAILABLE
    _WPS_AVAILABLE = False
    try:
        import winreg

        for prog in ("KWPS.Application", "WPS.Application", "ET.Application"):
            # ⚠️ 只查 ProgID 键**不够**：WPS 卸载后常残留 `KWPS.Application` 注册项
            # （实测本机即如此），据此判定"已安装"会照旧 Dispatch 并白等 ~14 秒。
            # 必须沿 ProgID → CLSID → LocalServer32/InprocServer32 取到 COM 服务器
            # 可执行文件路径，并确认该文件真实存在。
            try:
                k = winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, prog + r"\CLSID")
                clsid, _ = winreg.QueryValueEx(k, "")
                winreg.CloseKey(k)
            except OSError:
                continue
            exe = ""
            for view in ("LocalServer32", "InprocServer32"):
                try:
                    k2 = winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"CLSID\{clsid}\{view}")
                    raw, _ = winreg.QueryValueEx(k2, "")
                    winreg.CloseKey(k2)
                    exe = str(raw).strip().strip('"')
                    break
                except OSError:
                    continue
            if exe and os.path.exists(exe):
                _WPS_AVAILABLE = True
                break
    except Exception:  # noqa: BLE001  非 Windows / 无注册表 → 视为未安装
        _WPS_AVAILABLE = False
    return _WPS_AVAILABLE


def _extract_doc_via_wps(data: bytes, timeout: float = 45.0) -> str | None:
    """
    WPS COM 提取旧版 .doc 文本（LibreOffice 缺失/未接线时的替代路径，
    2026-08-20 实测验证：KWPS.Application 提取中文正文有效）。
    流程：写临时 .doc → KWPS.Application 打开 → 读 Content.Text → 关闭清理。
    失败返回 None，不抛异常（win32com 惰性导入，保持零硬依赖）。

    timeout：看门狗超时（2026-08-20 补强）。个别损坏 .doc 会导致 WPS COM
    调用永久挂起（WPS 进程消失但 python 仍阻塞在 COM 等待）。超过 timeout
    秒未返回时，定向终止本轮调用新派生的 wps.exe 实例（不影响调用前已存在
    的用户 WPS 窗口），并将该文件标记为失败继续后续处理。
    """
    # 前置短路：未安装 WPS 时 Dispatch 会阻塞 ~14s 才失败（2026-09-15 实测），
    # 直接返回 None 交由调用方走 olefile 兜底。
    if not wps_available():
        return None
    import os
    import subprocess
    import tempfile
    import threading

    def _wps_pids() -> set:
        try:
            out = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq wps.exe", "/FO", "CSV", "/NH"],
                capture_output=True,
                timeout=10,
            )
            raw = out.stdout
            try:
                text = raw.decode("gbk", errors="replace")
            except Exception:  # noqa: BLE001
                text = raw.decode("utf-8", errors="replace")
            pids = set()
            for line in text.strip().splitlines():
                if "wps.exe" not in line:
                    continue
                parts = line.split('","')
                if len(parts) > 1:
                    pids.add(parts[1].strip('"'))
            return pids
        except Exception:  # noqa: BLE001
            return set()

    tmp = ""
    before = _wps_pids()
    result: dict = {"text": None, "done": False}
    try:
        fd, tmp = tempfile.mkstemp(suffix=".doc", prefix="wps_tmp_")
        with os.fdopen(fd, "wb") as f:
            f.write(data)

        # 看门狗线程：仅负责超时后定向终止本轮新派生的 wps.exe，
        # 使主线程阻塞的 COM 调用抛错返回（COM 调用必须在主线程，见下）。
        def _watchdog() -> None:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline and not result["done"]:
                time.sleep(0.3)
            if not result["done"]:
                after = _wps_pids()
                for pid in after - before:
                    try:
                        subprocess.run(
                            ["taskkill", "/F", "/PID", pid], capture_output=True, timeout=10
                        )
                    except Exception:  # noqa: BLE001  旁路设施/缓存降级（主路径不受影响）
                        pass

        wt = threading.Thread(target=_watchdog, daemon=True)
        wt.start()
        # COM 调用必须在主线程：实测（2026-08-20）同一进程内 worker 线程
        # 只首次 Dispatch 成功，第二次起即快速失败；主线程连续调用稳定。
        import win32com.client

        app = win32com.client.Dispatch("KWPS.Application")
        try:
            app.Visible = False
        except Exception:  # noqa: BLE001  旁路设施/缓存降级（主路径不受影响）
            pass
        try:
            app.DisplayAlerts = 0
        except Exception:  # noqa: BLE001  旁路设施/缓存降级（主路径不受影响）
            pass
        try:
            doc = app.Documents.Open(tmp, ReadOnly=True, AddToRecentFiles=False)
            try:
                text = doc.Content.Text or ""
            finally:
                try:
                    doc.Close(False)
                except Exception:  # noqa: BLE001  旁路设施/缓存降级（主路径不受影响）
                    pass
        finally:
            try:
                app.Quit()
            except Exception:  # noqa: BLE001  旁路设施/缓存降级（主路径不受影响）
                pass
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
        text = re.sub(r"\r\n?", "\n", text)
        text = re.sub(r"[ \t]{2,}", " ", text)
        text = text.strip()
        result["text"] = text or None
        return result["text"]
    except Exception:  # noqa: BLE001
        return None
    finally:
        result["done"] = True
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _extract_ole2(data: bytes) -> dict[str, Any]:
    """旧版 .xls / .doc（OLE2 复合文档）。优先 xlrd（xls）；doc 走 WPS COM 优先，兜底 olefile。
    注：Excel 判定仅用精确的 b"Workbook" 魔数——b"Book" 过宽，.doc 二进制流常误命中。"""
    # 先判断是否 Excel：兼容 BIFF8("Workbook") 与 BIFF5 及更早("Book"，CFB 目录流 UTF-16LE)
    # 旧实现仅 b"Workbook" 会漏检 BIFF5 xls → 落到 doc 分支 utf-16-le 全流扫描产生乱码（2026-09-10）。
    if (
        b"Workbook" in data[:200_000]
        or b"\x00B\x00o\x00o\x00k" in data[:4096]
        or b"Book" in data[:512]
    ):
        try:
            import xlrd

            bk = xlrd.open_workbook(__import__("io").BytesIO(data))
            rows = []
            for sh in bk.sheets():
                for r in range(sh.nrows):
                    rows.append("\t".join(str(c.value) for c in sh.row(r)))
            text = "\n".join(rows)
            if text.strip():
                return {"text": text.strip(), "extracted": True, "extract_status": "ok"}
        except ImportError:
            return {"text": "", "extracted": False, "extract_status": "library_missing"}
        except Exception:  # noqa: BLE001  旁路设施/缓存降级（主路径不受影响）
            pass
    # 否则按 .doc 处理：优先 WPS COM（可靠，LibreOffice 缺失时的可行路径），
    # 失败再兜底 olefile 流解析。
    wps_text = _extract_doc_via_wps(data)
    if wps_text:
        return {"text": wps_text, "extracted": True, "extract_status": "ok_wps_com"}
    try:
        import olefile

        if not olefile.isOleFile(__import__("io").BytesIO(data)):
            return {"text": "", "extracted": False, "extract_status": "unsupported"}
        ol = olefile.OleFileIO(__import__("io").BytesIO(data))
        text_parts = []
        for stream in ol.listdir():
            try:
                raw = ol.openstream(stream).read()
                text_parts.append(raw.decode("utf-16-le", "ignore"))
            except Exception:  # noqa: BLE001  旁路设施/缓存降级（主路径不受影响）
                pass
        text = "\n".join(text_parts)
        text = re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            return {"text": text, "extracted": True, "extract_status": "ok"}
        return {"text": "", "extracted": False, "extract_status": "empty"}
    except ImportError:
        return {"text": "", "extracted": False, "extract_status": "library_missing"}
    except Exception:  # noqa: BLE001
        return {"text": "", "extracted": False, "extract_status": "corrupt"}


def _ocr_available() -> bool:
    """是否有任一可用 OCR 引擎（统一 OCR 模块：PaddleOCR / Tesseract）。

    委托 std_lib/scraper_std/ocr_engine 探测实际引擎可用性，取代此前的
    pytesseract/paddleocr 直接 import 探针；保持「无可用引擎时跳过 OCR」契约。
    """
    try:
        from .ocr_engine import get_ocr
    except ImportError:  # 兼容独立模块导入（scraper_std 目录在 sys.path）
        from ocr_engine import get_ocr  # type: ignore[no-redef]
    try:
        return any(get_ocr().health().values())
    except Exception as e:  # pragma: no cover - 模块缺失  # noqa: BLE001
        LOG.warning("统一 OCR 模块不可用：%s", e)
        return False


def _run_ocr(data: bytes, timeout: int) -> str:
    """对扫描件 PDF 字节流做 OCR（统一 OCR 模块：PaddleOCR 默认 + Tesseract 降级）。

    - 委托 std_lib/scraper_std/ocr_engine：PaddleOCR 3.7.0 作为默认引擎，失败自动
      降级 Tesseract v5；全部引擎失败返回空串（不向上抛未捕获异常，由调用方决策）。
    - 文本层充分的 PDF 已在 _extract_pdf 中经文本层优先提取，本函数仅在扫描件
      （无/极少文本层）路径被调用，故 force_ocr=True 跳过文本层再判定。
    - timeout 参数保留以兼容既有调用签名（统一 OCR 模块内部自有降级与异常处理）。
    """
    # 惰性导入统一 OCR 模块（保持本模块「纯标准库 + 惰性导入」不变量）
    try:
        from .ocr_engine import get_ocr
    except ImportError:  # 兼容独立模块导入（scraper_std 目录在 sys.path）
        from ocr_engine import get_ocr  # type: ignore[no-redef]
    import os
    import tempfile

    fd, tmp = tempfile.mkstemp(suffix=".pdf", prefix="ocr_run_")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        res = get_ocr().extract_pdf(tmp, force_ocr=True)
        return res.text or ""
    except Exception as e:
        raise RuntimeError(f"OCR 执行失败: {e}") from e
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def _run_ocr_detail(data: bytes, timeout: int) -> tuple[str, str]:
    """同 `_run_ocr` 但返回 (文本, 实际引擎名)；2026-09-12 新增（引擎可观测）。"""
    try:
        from .ocr_engine import get_ocr
    except ImportError:  # 兼容独立模块导入（scraper_std 目录在 sys.path）
        from ocr_engine import get_ocr  # type: ignore[no-redef]
    import os
    import tempfile

    fd, tmp = tempfile.mkstemp(suffix=".pdf", prefix="ocr_run_")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        res = get_ocr().extract_pdf(tmp, force_ocr=True)
        return (res.text or ""), (getattr(res, "engine", "") or "")
    except Exception as e:
        raise RuntimeError(f"OCR 执行失败: {e}") from e
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


# --------------------------------------------------------------------------- #
# 4. 归一化 / 哈希工具
# --------------------------------------------------------------------------- #

_DATE_RE1 = re.compile(r"(\d{4})[-年](\d{1,2})[-月](\d{1,2})日?")
_DATE_RE2 = re.compile(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})")


def normalize_date(text: str) -> str:
    """抽取并归一为 YYYY-MM-DD；无法识别返回空串。"""
    if not isinstance(text, str) or not text:
        return ""
    m = _DATE_RE1.search(text) or _DATE_RE2.search(text)
    if not m:
        return ""
    y, mo, d = m.groups()
    try:
        return "%04d-%02d-%02d" % (int(y), int(mo), int(d))
    except ValueError:
        return ""


_FULLWIDTH = str.maketrans("０１２３４５６７８９", "0123456789")


def normalize_digits(s: str) -> str:
    """全角数字归一为半角。"""
    if not s:
        return ""
    return str(s).translate(_FULLWIDTH)


def content_sha256(text: str) -> str:
    """正文内容哈希，用于内容级去重（同源多 URL / 改版重发）。"""
    return sha256_of((text or "").encode("utf-8"))


# --------------------------------------------------------------------------- #
# 5. 标准化附件记录构造
# --------------------------------------------------------------------------- #


def build_attachment_record(
    file_name: str,
    file_url: str,
    local_path: str,
    data: bytes,
    *,
    entry_id: str = "",
    text: str = "",
    extracted: bool = False,
    extract_status: str = "unsupported",
    needs_ocr: bool = False,
) -> dict[str, Any]:
    """
    构造与 nfra 质量基线一致的标准化附件记录，便于四项目审计追溯统一。
    """
    kind = sniff_kind(data, file_name)
    return {
        "entry_id": entry_id,
        "file_name": file_name,
        "file_url": file_url,
        "local_path": local_path,
        "size_bytes": len(data),
        "extension": os.path.splitext(file_name)[1].lower(),
        "kind": kind,
        "sha256": sha256_of(data),
        "extracted": extracted,
        "extract_status": extract_status,
        "needs_ocr": needs_ocr,
        "garble_ratio": garble_ratio(text),
        "text": text,
        "fetch_status": "ok",
    }


if __name__ == "__main__":
    # 离线自检（无需网络/第三方库）
    logging.basicConfig(level=logging.INFO)
    fn = safe_filename("测试:文件*名?.pdf", "http://x/1")
    assert re.match(r"^[0-9a-f]{8}_.+?\.pdf$", fn), fn
    assert sniff_kind(b"%PDF-1.4", "a.pdf") == "pdf"
    assert sniff_kind(b"PK\x03\x04", "a.docx") == "docx"
    assert sniff_kind(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "a.xls") == "ole2"
    assert sniff_kind(b"Rar!\x1a\x07", "a.rar") == "rar"
    assert normalize_date("2024年03月05日") == "2024-03-05"
    assert normalize_date("2024-3-5") == "2024-03-05"
    assert normalize_digits("２０２４") == "2024"
    assert garble_ratio("ab�c") > 0
    # 抽取路由在缺库时透明标记（不崩溃）
    r = extract_document_text(b"%PDF-1.4 fake", "a.pdf")
    assert r["extract_status"] in ("library_missing", "needs_ocr", "corrupt", "empty"), r
    print("[crawler_common] 离线自检通过")
