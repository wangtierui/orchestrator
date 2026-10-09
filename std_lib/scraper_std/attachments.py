# -*- coding: utf-8 -*-
"""
attachments.py —— 附件下载与全文提取（第六节 6.2/6.3）

能力：
  - 流式下载（复用 http.AdaptiveHttpClient.download_file），连接 10s / 读取 30s；
  - 全文提取：PDF(pdfplumber/pypdf 表格优先) / docx(python-docx) / xlsx(openpyxl) /
    xls(xlrd) / doc(OLE2) —— 复用 crawler_common.extract_document_text；
  - 正文文档优先下载：Word > PDF > OFD/CEB（6.2 优先级）；
  - 附件元数据：文件名/URL/MD5/下载状态/提取文本字数/提取状态；
  - 解析失败 → attachment_content="[附件解析失败: 原因]"，严禁留空；
  - 超大附件折中：全文写同名 .txt，主数据填写 attachment_content_path + MD5；
  - 标准重命名（6.4）+ 映射记录（原始文件名/原始 URL 溯源）。
"""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Any

from .http import AdaptiveHttpClient
from .naming import standard_filename

LOG = logging.getLogger("scraper_std.attachments")

# --------------------------------------------------------------------------- #
# 正文载体 vs 真实附件（批 50）：写入侧统一口径
#
# 命题（用户 2026-10-09）：五源写入 raw 时，`attachments` 里混入了**大量并非公告实际附件**的条目
# —— 它们是「正文以 doc/pdf/word 形式发布」时的**正文载体**（例：gov 政策文件库的「下载Word/下载PDF」
# 按钮、mof 接口 fileType=90「下载文字版」、supp 收录时 role=body 的正文 PDF）。
# 实测（批 50 数据实测）：gov 2907 个附件条目中 **1632 条（文本 43.1M 字符，占附件文本 59%）**
# 其文本已包含在正文里；mof 有 **478 条 html_disguised_doc（14.4M 字符）**（≈每记录一份「文字版」）。
#
# 统一口径：`attachments` 只保留**真实附件**；正文载体移入 `body_docs`（保留 URL/本地路径/校验和
# 等**溯源元数据**，其文本在正文已覆盖时不再重复携带，未覆盖时**并入正文**以保证内容完整）。
# 判据只用两档、皆可解释：① 条目**来源角色**（采集器已知的 role/fileType）② **内容判据**
# （附件文本头部出现在正文中）。二者都不命中 ⇒ 视为真实附件。
# --------------------------------------------------------------------------- #

#: 载体角色标记（`body_version_role` 返回值域；`html_disguised_doc` 为「HTML 冒充 .doc」的站点
#: 生成「文字版」，mof 实测 ≈ 每记录一份 ⇒ 计入载体标记）。
BODY_VERSION_ROLE = "body_version"
_BODY_ROLE_MARKERS = ("body_version", "body", "text_version", "90", "html_disguised_doc")

#: 内容判据参数：归一化后取附件文本**头部** `_MATCH_HEAD` 字符，长度 ≥ `_MATCH_MIN` 才判定。
_MATCH_HEAD = 200
_MATCH_MIN = 60
_MATCH_LIMIT = 400_000
#: 归一化时剔除的标点（**不含反斜杠字符**：遵守"零转义原语"约定，避免编辑链路二次转义）
_PUNCT_CHARS = "()<>[]{}.,;:'\"!?-/|_+=*&^%$#@~`\u3000\u3001\u3002\uff0c\uff1b\uff1a\uff1f\uff01\u201c\u201d\u2018\u2019\u2014\u2026\u00b7\uff08\uff09\u3010\u3011\u300a\u300b"
_NL = chr(10)
_SEP = _NL + _NL


def match_norm(s: str, limit: int = _MATCH_LIMIT) -> str:
    """匹配用归一化：去空白/常见标点、转小写（零依赖；`isspace()` 判空白，避免转义字面量）。"""
    out = []
    for ch in str(s or "")[:limit]:
        if ch.isspace() or ch in _PUNCT_CHARS:
            continue
        out.append(ch.lower())
    return "".join(out)


def body_version_role(entry: Any) -> str:
    """条目自带的**来源角色**线索（采集器已知信息：`role` / `file_type=90` 等）；无则空串。"""
    if not isinstance(entry, dict):
        return ""
    for k in ("role", "attachment_kind", "kind", "file_type"):
        v = str(entry.get(k) or "").strip().lower()
        if v in _BODY_ROLE_MARKERS:
            return v
    return ""


def is_body_version(entry: Any, body_text: str) -> bool:
    """**内容判据**：附件文本（归一化头部）已包含在正文中 ⇒ 该条目是正文的另一种格式。"""
    if not isinstance(entry, dict) or not body_text:
        return False
    head = match_norm(str(entry.get("text") or ""), _MATCH_HEAD)
    if len(head) < _MATCH_MIN:
        return False
    return head in match_norm(body_text)


def covered_by_body(text: str, body_norm: str, k: int = 10) -> bool:
    """文本是否**整体**已被正文覆盖（取 k 段 + 末段的分段头部逐一在正文中查得）。

    为何不用"只看头部"：载体可能是"正文 + 附录/附注"（头部命中而尾部是增量）
    ⇒ 只看头部就丢弃全文会**丢内容**。本函数用于二选一：整体覆盖 ⇒ 仅留溯源元数据；
    否则 ⇒ **并入正文**（内容完整不丢）。
    """
    if not text or not body_norm:
        return False
    n = len(text)
    step = max(1, n // k)
    for i in range(k):
        head = match_norm(text[i * step:(i + 1) * step + _MATCH_HEAD], _MATCH_HEAD)
        if len(head) >= _MATCH_MIN and head not in body_norm:
            return False
    tail = match_norm(text[-_MATCH_HEAD:], _MATCH_HEAD)
    return not (len(tail) >= _MATCH_MIN and tail not in body_norm)


def dedupe_attachments(atts: Any) -> tuple[list, int]:
    """**同一条记录内完全重复**的附件条目去重（返回 `(去重后列表, 去除条数)`）。

    键＝名称 + URL + 本地路径 + sha256 + 文本 sha256 的组合 ⇒ 只有**完全相同**的条目才合并
    （保守：名称不同者即使文本相同也保留，避免误合并真实存在的同名同文附件）。
    起因（批 50 实测）：supp 一条记录出现 **9 个完全相同条目**（`supp_ingest.enrich_attachments`
    多次摄取反复 append；写入侧已加去重，此处兜底并用于历史数据处置）。
    """
    out: list = []
    seen: set = set()
    dup = 0
    for a in (atts or []):
        if not isinstance(a, dict):
            out.append(a)
            continue
        text = a.get("text")
        key = (str(a.get("file_name") or a.get("name") or a.get("title") or ""),
               str(a.get("file_url") or a.get("url") or ""),
               str(a.get("local_path") or ""),
               str(a.get("sha256") or ""),
               hashlib.sha256(str(text).encode("utf-8", "replace")).hexdigest() if text else "")
        if key in seen:
            dup += 1
            continue
        seen.add(key)
        out.append(a)
    return out, dup


def partition_attachments(atts: Any, body_text: str) -> tuple[list, list]:
    """把附件条目分流为 `(真实附件, 正文载体)`（判据见模块头注释）。非 dict 条目按真实附件保留。"""
    real: list = []
    carriers: list = []
    for a in (atts or []):
        if not isinstance(a, dict):
            real.append(a)
            continue
        if body_version_role(a) or is_body_version(a, body_text):
            carriers.append(a)
        else:
            real.append(a)
    return real, carriers


def split_record_body_docs(rec: dict, *, body_keys: tuple = ("full_text", "content_text",
                                                             "content", "body_text"),
                           carrier_field: str = "body_docs",
                           rejoin_keys: tuple = ("attachment_text", "attachment_content")) -> dict:
    """**记录级**分流（五源通用，写入侧与历史数据处置共用同一判据 ⇒ 结果一致）。

    行为：
      · `attachments` → 只留真实附件；正文载体移入 `carrier_field`（默认 `body_docs`，**幂等合并**）；
      · 载体文本若**未**被正文覆盖（如 mof 的「文字版」），**并入正文**（内容完整不丢）；
      · 载体文本已被正文覆盖（gov 的「下载Word/PDF」）⇒ 条目只留**溯源元数据**，不再重复携带全文；
      · 重算 `attachment_count` 与记录级聚合（`attachment_text` / `attachment_content`，按**真附件**）。

    返回统计 `{"kept": n, "moved": m, "body_filled": bool}`（便于调用方与处置脚本核对）。
    """
    atts = rec.get("attachments")
    if not isinstance(atts, list) or not atts:
        return {"kept": 0, "moved": 0, "body_filled": False}
    body_key = ""
    body = ""
    for k in body_keys:
        v = rec.get(k)
        if isinstance(v, str) and v.strip():
            body_key, body = k, v
            break
    real, carriers = partition_attachments(atts, body)
    real, n_dup = dedupe_attachments(real)        # 完全重复条目（如 supp 的 9 条同款）先归并
    if not carriers and not n_dup:
        return {"kept": len(real), "moved": 0, "body_filled": False, "deduped": 0}
    body_filled = False
    body_norm = match_norm(body) if body else ""
    text_in = sum(len(str(a.get("text") or "")) for a in real if isinstance(a, dict))
    text_kept = text_in
    text_to_body = 0
    text_dropped = 0
    for c in carriers:
        txt = str(c.get("text") or "")
        text_in += len(txt)
        if txt and body_key:
            if covered_by_body(txt, body_norm):
                text_dropped += len(txt)              # 整体已被正文覆盖 ⇒ 条目只留溯源元数据
            else:                                     # 正文未覆盖（或仅部分覆盖）⇒ 并入正文，不丢内容
                body = (body + _SEP + txt).strip() if body else txt
                body_norm = body_norm + match_norm(txt)   # 增量维护（正文尾部追加）
                text_to_body += len(txt)
                body_filled = True
        # 正文已（或已并入）覆盖 ⇒ 载体条目仅留溯源元数据，避免同一正文在记录里存两份
        c.pop("text", None)
        c.pop("attachment_content", None)
    if body_key:
        rec[body_key] = body
    prev = rec.get(carrier_field)
    merged = list(prev) if isinstance(prev, list) else []
    seen = {match_norm(str(x.get("file_name") or x.get("name") or "") + str(x.get("file_url")
                                                                         or x.get("url") or x.get("local_path") or ""))
            for x in merged if isinstance(x, dict)}
    for c in carriers:
        key = match_norm(str(c.get("file_name") or c.get("name") or "") + str(c.get("file_url")
                                                                              or c.get("url") or c.get("local_path") or ""))
        if key and key in seen:
            continue
        merged.append(c)
        seen.add(key)
    rec["attachments"] = real
    if merged:
        rec[carrier_field] = merged
    rec["attachment_count"] = len(real)
    for k in rejoin_keys:
        if k in rec:
            texts = [str(a.get("text") or "") for a in real
                     if isinstance(a, dict) and a.get("text")]
            rec[k] = _SEP.join(texts) if texts else ""
    assert text_in == text_kept + text_to_body + text_dropped, "内容守恒台账不平（附件文本账）"
    LOG.info("[附件分流] 真附件 %d / 正文载体 %d（正文并入=%s；文本 %d = 保留 %d + 并入 %d + 已覆盖 %d）",
             len(real), len(carriers), body_filled, text_in, text_kept, text_to_body, text_dropped)
    return {"kept": len(real), "moved": len(carriers), "body_filled": body_filled,
            "deduped": n_dup,
            "text_in": text_in, "text_kept": text_kept, "text_to_body": text_to_body,
            "text_dropped": text_dropped}

# 正文文档优先级（6.2 ①）
_DOC_PRIORITY = [".docx", ".doc", ".pdf", ".ofd", ".ceb", ".wps", ".rtf"]
_ATTACH_EXTS = {
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".ofd",
    ".ceb",
    ".wps",
    ".rtf",
    ".zip",
    ".rar",
    ".txt",
}


def pick_body_doc(urls: list[str]) -> str | None:
    """
    6.2 ① 正文文档下载优先级：Word > PDF > OFD/CEB。
    从候选链接列表中选出应下载的正文文档 URL（无匹配返回 None）。
    """
    if not urls:
        return None

    def _rank(u: str) -> int:
        path = (u or "").split("?")[0].lower()
        for i, ext in enumerate(_DOC_PRIORITY):
            if path.endswith(ext):
                return i
        return 99

    best = min(urls, key=_rank)
    return best if _rank(best) < 99 else None


def is_attachment_url(url: str) -> bool:
    """附件链接判定（扩展名命中）。"""
    path = (url or "").split("?")[0].lower()
    return any(path.endswith(e) for e in _ATTACH_EXTS)


def download_and_extract(
    url: str,
    *,
    dest_dir: str,
    client: AdaptiveHttpClient,
    file_name_hint: str = "",
    index_no: str = "",
    title: str = "",
    pub_date: str = "",
    file_type: str = "附件",
    seq: int | None = None,
    max_bytes: int | None = None,
    text_extract: bool = True,
) -> dict[str, Any]:
    """
    下载 + 提取附件全文，返回标准化附件记录：
      {
        file_name(重命名后), original_name, file_url, local_path, size_bytes,
        sha256, fetch_status(ok/fail), extract_status, text(全文),
        text_length, attachment_content, needs_ocr, garble_ratio, extension
      }
    下载/解析失败均返回结构化记录（不抛异常）。
    """
    from .crawler_common import sniff_kind

    rec: dict[str, Any] = {
        "file_url": url,
        "original_name": file_name_hint or os.path.basename(url.split("?")[0]) or url,
        "fetch_status": "fail",
        "extract_status": "unsupported",
        "text": "",
        "text_length": 0,
        "attachment_content": "",
        "sha256": "",
        "size_bytes": 0,
        "needs_ocr": False,
        "garble_ratio": 0.0,
    }
    os.makedirs(dest_dir, exist_ok=True)

    # 1) 下载（流式 + 超时）
    tmp_name = f"_dl_{hashlib.md5(url.encode('utf-8')).hexdigest()[:8]}.bin"
    tmp_path = os.path.join(dest_dir, tmp_name)
    ok, sha_or_msg, size = client.download_file(url, tmp_path, max_bytes=max_bytes)
    if not ok:
        rec["fetch_status"] = "fail"
        rec["attachment_content"] = f"[附件下载失败: {sha_or_msg}]"
        LOG.error("附件下载失败 %s → %s", url, sha_or_msg)
        return rec
    # 2) 魔数纠正真实格式
    with open(tmp_path, "rb") as f:
        data = f.read()
    kind = sniff_kind(data, file_name_hint or tmp_name)
    ext = {
        "pdf": ".pdf",
        "docx": ".docx",
        "xlsx": ".xlsx",
        "ole2": ".xls",
        "zip": ".zip",
        "rar": ".rar",
    }.get(kind, os.path.splitext(file_name_hint)[1].lower() or ".bin")

    # 3) 标准重命名（6.4）
    final_name = standard_filename(
        index_no=index_no,
        title=title or file_name_hint,
        pub_date=pub_date,
        ext=ext,
        file_type=file_type,
        seq=seq,
        url=url,
    )
    final_path = os.path.join(dest_dir, final_name)
    try:
        if os.path.abspath(tmp_path) != os.path.abspath(final_path):
            os.replace(tmp_path, final_path)
    except OSError as e:
        LOG.warning("重命名失败，保留临时名 %s：%s", tmp_path, e)
        final_path = tmp_path
        final_name = os.path.basename(tmp_path)

    # 4) 全文提取（6.3 硬性要求）
    from .crawler_common import extract_document_text

    rec.update(
        {
            "file_name": final_name,
            "local_path": final_path,
            "sha256": sha_or_msg,
            "size_bytes": size,
            "fetch_status": "ok",
            "extension": ext,
        }
    )
    if not text_extract:
        rec["extract_status"] = "skipped"
        rec["attachment_content"] = "[附件文本提取已跳过]"
        return rec
    try:
        ex = extract_document_text(data, final_name, enable_ocr=False)
        text = (ex.get("text") or "").strip()
        rec["extract_status"] = ex.get("extract_status", "unsupported")
        rec["needs_ocr"] = bool(ex.get("needs_ocr"))
        rec["garble_ratio"] = float(ex.get("garble_ratio") or 0.0)
        rec["text"] = text
        rec["text_length"] = len(text)
        if text:
            rec["attachment_content"] = text
            LOG.info(
                "附件文本提取成功 %s（%d 字，%s）", final_name, len(text), rec["extract_status"]
            )
        else:
            status = rec["extract_status"]
            rec["attachment_content"] = f"[附件解析失败: {status}]"
            LOG.warning("附件文本提取为空 %s（%s）", final_name, status)
    except Exception as e:  # noqa: BLE001
        rec["extract_status"] = "error"
        rec["attachment_content"] = f"[附件解析失败: {type(e).__name__}: {e}]"
        LOG.error("附件解析异常 %s：%s", final_name, e)
    return rec


def sidecar_record_content(
    rec: dict,
    content_dir: str,
    *,
    threshold: int = 200_000,
) -> dict:
    """**记录级**超大附件文本外置（批 51/W-B：把超大即外置机制接入交付链）。

    交付记录（cleaned）中 `attachment_content` 超阈值时：全文写 `content_dir/<内容寻址名>.txt`，
    主数据置 `attachment_content=""` + `attachment_content_path` + `attachment_content_md5`，并在
    `_metadata` 记录 `attachment_content_sha256` 与 `attachment_content_sidecar=True`（可审计）。
    """
    text = rec.get("attachment_content")
    if not isinstance(text, str) or len(text) <= threshold:
        return rec
    _sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    os.makedirs(content_dir, exist_ok=True)
    _path = os.path.join(content_dir, _sha[:16] + ".txt")
    if not os.path.exists(_path):
        with open(_path, "w", encoding="utf-8") as _f:
            _f.write(text)
    rec["attachment_content"] = ""
    rec["attachment_content_path"] = _path.replace(os.sep, "/")
    rec["attachment_content_md5"] = _sha     # 与既有实现一致：该列存 sha256（算法明示于 _metadata）
    _meta = rec.setdefault("_metadata", {})
    _meta["attachment_content_sha256"] = _sha
    _meta["attachment_content_sidecar"] = True
    return rec


def write_large_content_sidecar(
    attachment: dict[str, Any],
    content_dir: str,
    *,
    threshold: int = 200_000,
) -> dict[str, Any]:
    """
    超大附件折中策略（6.3 运维备注）：全文 > threshold 字时压缩到 content_dir
    下同名 .txt，主数据字段填 attachment_content_path + attachment_content_md5。
    返回更新后的附件记录。
    """
    text = attachment.get("text") or attachment.get("attachment_content") or ""
    if len(text) <= threshold:
        return attachment
    os.makedirs(content_dir, exist_ok=True)
    base = os.path.splitext(attachment.get("file_name", "attach"))[0]
    txt_path = os.path.join(content_dir, f"{base}.txt")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(text)
    attachment["attachment_content"] = ""
    attachment["attachment_content_path"] = txt_path
    attachment["attachment_content_md5"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return attachment


if __name__ == "__main__":  # 离线自检（不联网）
    assert pick_body_doc(["http://x/a.pdf", "http://x/a.docx"]) == "http://x/a.docx"
    assert pick_body_doc(["http://x/a.pdf"]) == "http://x/a.pdf"
    assert pick_body_doc(["http://x/a.ofd"]) == "http://x/a.ofd"
    assert pick_body_doc(["http://x/a.html"]) is None
    assert is_attachment_url("http://x/1.xlsx")
    assert not is_attachment_url("http://x/detail.html")
    print("[scraper_std.attachments] 离线自检通过")
