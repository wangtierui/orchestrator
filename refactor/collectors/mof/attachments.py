# -*- coding: utf-8 -*-
"""mof 源：附件清单获取 / 下载 / 本地抽取（重构版；逻辑与旧 mof_attachments 等价）。"""
import ipaddress
import logging
import os
import urllib.parse
from . import net

logger = logging.getLogger("mof_scraper")

ATTACH_PATH = "/file/f/get"  # GET ?infoId=<id>&fileType=0(相关附件)/90(文字版)
_ATTACH_KIND = {"0": "attachment", "90": "text_version"}

# 附件下载：内网/不可路由地址（如 mof 接口返回 serverIp=10.1.60.36 这类内网 IP）
# 只尝试一次、用短超时快速失败，保留 file_url 等元信息并在附件明细的
# extract_status 标记 unreachable_private_ip，确保"有附件但不可达"这一事实落库不丢。
# 该诊断值属站点数据缺陷，已在 lib/fetch_status 显式豁免，不把主记录降级：
# 有正文仍为 ok，无正文仍为 no_content；否则 full 模式会无限重抓这些坏链接。


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


def _is_non_routable_host(url):
    """URL 的 host 若为私有/环回/链路本地/保留等不可公网路由的 IP 则返回 True；
    域名无法静态判定（可能解析到任意地址），一律返回 False（照常尝试下载）。"""
    try:
        host = urllib.parse.urlparse(url).hostname
        if not host:
            return False
        ip = ipaddress.ip_address(host)
    except ValueError:  # 非 IP（域名）无法静态判断
        return False
    return not ip.is_global


def _attachment_meta_record(att, law_id, download_status, *, error=None, unreachable=False):
    """未成功落盘时的占位记录：保留 file_url/file_name 等全部元信息，仅标记下载状态，不丢数据。"""
    rec = dict(att)  # file_id/file_name/file_type/kind/extension/file_url 等原样保留
    rec.update({
        "law_id": law_id,
        "local_path": None,
        "size_bytes": 0,
        "text": "",
        "extracted": False,
        # 附件明细只用 extract_status 表达下载结果（原 extract_status / download_status
        # 同值双写已清理）；主记录状态由 canonicalize 统一上卷，附件不再自带状态字段。
        "extract_status": download_status,
        "attachment_kind": "unknown",
        "sha256": "",
        "needs_ocr": False,
        "garble_ratio": 0.0,
        "unreachable": unreachable,
        "download_error": error or "",
    })
    return rec


def download_attachment(att, law_id, outdir, rate, timeout=60, max_retries=6):
    """下载单个附件到 attachments/<law_id>/，始终返回一条元数据记录（成功或失败都不丢）。

    重试/退避/熔断统一走 lib/http（robust_get）。对不可公网路由的内网 IP 不再预跳过，
    而是照常发一次请求；若失败且命中该规则，标记 unreachable_private_ip 并保留 file_url
    等元信息（交由落库层可见）；其它失败标记 download_failed。内网地址仅发一次、用较短
    超时快速失败，避免 60s 连接超时干等。
    """
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
    non_routable = _is_non_routable_host(att["file_url"])
    if rate is not None:
        rate.wait()
    from ..lib.http import robust_get
    # 命中内网/不可路由规则：只发一次请求、较短超时快速失败（不预跳过）；其余地址走正常重试
    st, data = robust_get(
        att["file_url"], binary=True,
        timeout=min(timeout, 10) if non_routable else timeout,
        retries=0 if non_routable else max_retries,
    )
    if st == 200 and data:
        return _finalize_attachment(att, data, fname, law_id, dest)
    # 失败：命中内网/不可路由规则 → unreachable_private_ip；否则 download_failed
    if non_routable:
        logger.warning("附件地址为内网/不可路由 IP，请求失败（保留元信息）：%s", att.get("file_url"))
        return _attachment_meta_record(att, law_id, "unreachable_private_ip", unreachable=True)
    err = "HTTP %s" % st if st is not None else "network/timeout"
    logger.warning("附件下载失败 %s：%s", att.get("file_url"), err)
    return _attachment_meta_record(att, law_id, "download_failed", error=err)


def collect_attachments(law_id, rate, timeout=60):
    """抓取并下载某条法规的全部附件，返回元数据列表（含下载失败/不可达记录，均不丢弃）。"""
    meta = fetch_attachments(law_id, rate, timeout=timeout)
    if not meta:
        return []
    result = []
    for att in meta:
        result.append(download_attachment(att, law_id, net.ATTACHMENTS_DIR, rate, timeout=timeout))
    return result
