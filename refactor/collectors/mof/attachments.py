# -*- coding: utf-8 -*-
"""mof 源：附件清单获取 / 下载 / 本地抽取（重构版；逻辑与旧 mof_attachments 等价）。"""
import logging
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request

from . import net

logger = logging.getLogger("mof_scraper")

ATTACH_PATH = "/file/f/get"  # GET ?infoId=<id>&fileType=0(相关附件)/90(文字版)
_ATTACH_KIND = {"0": "attachment", "90": "text_version"}

# 附件主机 502 熔断（2026-09-10 增量策略）：连续 502/503 达阈值即熔断，剩余附件跳过。
# 注：真身在此处（原 mof_collector.py 内曾有一份"从不读写"的死副本，重构时已删除）。
_ATT_502_STREAK = 0
_ATT_CIRCUIT_OPEN = False
_MAX_ATT_502_STREAK = 8


def fetch_attachments(law_id, rate, file_types=("0", "90"), timeout=20):
    """获取某条法规的附件清单（JSON 元数据）。"""
    items = []
    for ft in file_types:
        try:
            url = f"{ATTACH_PATH}?infoId={urllib.parse.quote(str(law_id))}&fileType={ft}"
            resp = net._request("GET", url, rate=rate, timeout=timeout)
            if resp.get("code") != 200 or not resp.get("data"):
                continue
            for it in resp["data"]:
                if not isinstance(it, dict):
                    continue
                server = (it.get("serverIp") or "").strip()
                furl = (it.get("fileUrl") or "").strip()
                if not server or not furl:
                    continue
                if not server.lower().startswith("http"):
                    server = "http://" + server
                full = server.rstrip("/") + (furl if furl.startswith("/") else "/" + furl)
                items.append({
                    "file_id": it.get("id"),
                    "file_name": it.get("fileName") or f"{law_id}_{ft}",
                    "file_type": ft,
                    "kind": _ATTACH_KIND.get(str(ft), "other"),
                    "extension": (it.get("extension") or "").lower(),
                    "file_url": full,
                })
        except Exception as e:  # noqa: BLE001
            logger.warning("附件清单获取失败 id=%s fileType=%s：%s", law_id, ft, e)
    return items


def _finalize_attachment(att, data, fname, law_id, dest):
    """附件字节 → 落盘 + 文本/表格/富内容抽取，回填 att（下载与本地复用共用）。"""
    with open(dest, "wb") as f:
        f.write(data)
    att["local_path"] = os.path.relpath(dest, net.REPO_ROOT).replace("\\", "/")
    att["size_bytes"] = len(data)
    try:
        ext = net.extract_document_text(data, fname)
        att["text"] = ext.get("text", "")
        att["extracted"] = ext.get("extracted", False)
        att["extract_status"] = ext.get("extract_status", "unsupported")
        att["attachment_kind"] = ext.get("kind", "unknown")
        att["sha256"] = ext.get("sha256", "")
        att["needs_ocr"] = ext.get("needs_ocr", False)
        att["garble_ratio"] = ext.get("garble_ratio", 0.0)
        try:
            att.update(net.structured_table_fields(data, fname))
        except Exception:  # noqa: BLE001
            pass
        try:
            att.update(net.rich_object_fields(data, fname,
                                              image_dir=net.docs_root("mof", "diagrams"),
                                              rec_key=str(law_id)))
        except Exception:  # noqa: BLE001
            pass
    except Exception as e:  # noqa: BLE001
        logger.warning("附件文本抽取异常 %s：%s", att.get("file_url"), e)
        att["extracted"] = False
        att["extract_status"] = "extract_error"
    return att


def download_attachment(att, law_id, outdir, rate, timeout=60, max_retries=6):
    """下载单个附件到 attachments/<law_id>/，返回带本地相对路径与大小的元数据；失败返回 None。

    增量策略：幂等复用（本地已有非空副本 → 本地重抽）；502/503 仅重试 2 次即跳过，
    连续达阈值熔断；429/500 指数退避；404 等不可恢复直接放弃。
    """
    global _ATT_502_STREAK, _ATT_CIRCUIT_OPEN
    if _ATT_CIRCUIT_OPEN:
        return None
    attach_dir = os.path.join(net.ATTACHMENTS_DIR, str(law_id))
    os.makedirs(attach_dir, exist_ok=True)
    fname = net.safe_filename(att.get("file_name"), att.get("extension"),
                              f"{law_id}_{att.get('file_type')}")
    dest = os.path.join(attach_dir, fname)
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        try:
            with open(dest, "rb") as f:
                return _finalize_attachment(att, f.read(), fname, law_id, dest)
        except OSError:
            pass
    last_err = None
    for attempt in range(1, max_retries + 1):
        if rate is not None:
            rate.wait()
        try:
            req = urllib.request.Request(att["file_url"], headers=net.DEFAULT_HEADERS)
            req.add_header("User-Agent", random.choice(net.CC_USER_AGENTS))
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
            return _finalize_attachment(att, data, fname, law_id, dest)
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code in (502, 503):
                _ATT_502_STREAK += 1
                if _ATT_502_STREAK >= _MAX_ATT_502_STREAK:
                    _ATT_CIRCUIT_OPEN = True
                    logger.error("附件主机连续 %d 次 502/503，熔断：本次运行剩余附件跳过。",
                                 _ATT_502_STREAK)
                    return None
                if attempt >= 2:
                    logger.warning("附件下载 HTTP %s @ %s：跳过（熔断计数 %d）",
                                   e.code, att.get("file_url"), _ATT_502_STREAK)
                    return None
                backoff = min(2 ** attempt * 2.0, 20) + random.uniform(0, 1)
                if rate is not None:
                    rate.penalize(backoff)
                time.sleep(backoff)
                continue
            if e.code in (429, 500):
                backoff = min(2 ** attempt * 2.0, 60) + random.uniform(0, 1)
                if rate is not None:
                    rate.penalize(backoff)
                logger.warning(
                    "附件下载 HTTP %s @ %s (尝试 %d/%d)，%ss 后重试",
                    e.code, att.get("file_url"), attempt, max_retries, round(backoff, 1))
                time.sleep(backoff)
                continue
            logger.warning("附件下载不可恢复 HTTP %s @ %s：%s",
                           e.code, att.get("file_url"), e)
            return None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last_err = e
            backoff = min(2 ** attempt * 1.5, 30) + random.uniform(0, 1)
            logger.warning(
                "附件下载网络异常 (尝试 %d/%d)：%s，%ss 后重试",
                attempt, max_retries, e, round(backoff, 1))
            time.sleep(backoff)
            continue
    logger.warning("附件下载失败 %s：%s", att.get("file_url"), last_err)
    return None


def collect_attachments(law_id, rate, timeout=60):
    """抓取并下载某条法规的全部附件，返回带本地路径的元数据列表。"""
    if _ATT_CIRCUIT_OPEN:
        return []
    meta = fetch_attachments(law_id, rate, timeout=timeout)
    if not meta:
        return []
    result = []
    for att in meta:
        d = download_attachment(att, law_id, net.ATTACHMENTS_DIR, rate, timeout=timeout)
        if d:
            result.append(d)
    return result
