# -*- coding: utf-8 -*-
"""mof 财政部法规库爬虫（重构版）：实现 SourceCollector。

站点：fgk.mof.gov.cn（layui SPA，直连 REST 接口）。
产物：data/raw/mof_laws.json，结构 {source, captured_at, count, items}（与重构前一致）。
"""
import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
UTC = timezone.utc

from ..base import (
    REPO_ROOT,
    SourceCollector,
    acquire_lock,
    atomic_write,
    release_lock,
    write_master_json,
)
from . import net
from .attachments import collect_attachments

logger = logging.getLogger("mof_scraper")

# 默认落盘收口在 refactor/data/raw（自包含，不指向外部 modules/）。产物文件名与结构保持重构前一致。
DATA_RAW = os.path.join(REPO_ROOT, "data", "raw")

DEFAULT_CATEGORIES = {
    "1000000000000300000": "财政法律法规（财政部规章）",
    "1000000000000400000": "财政部规范性文件",
}


def fetch_detail(law_id, rate):
    """抓取单条详情，返回 dict（失败返回 None）。"""
    try:
        resp = net._request("GET", f"{net.BASE}/lawFile/get/{law_id}", rate=rate)
        if resp.get("code") == 200 and resp.get("data"):
            return resp["data"]
    except Exception as e:  # 单条失败不影响整体  # noqa: BLE001
        logger.warning("详情获取失败 id=%s：%s", law_id, e)
    return None


def fetch_list_page(lfgcc, page, size, rate):
    """抓取某一页列表，返回 (records, total, pages)。"""
    payload = {"queryMap": {"lfgcc": lfgcc, "linvalid": "0"}, "size": size, "current": page}
    resp = net._request("POST", f"{net.BASE}/lawFile/list", payload, rate=rate)
    if resp.get("code") != 200 or "data" not in resp:
        raise RuntimeError(f"列表接口返回异常：{resp.get('message')} | {str(resp)[:200]}")
    d = resp["data"]

    def _to_int(v, default=0):
        try:
            return int(v)
        except (TypeError, ValueError):
            return default

    return d.get("records", []), _to_int(d.get("total")), _to_int(d.get("pages"))


def fetch_category_records(lfgcc, category_name, *, size=50, rate, max_items=None):
    """分页拉取某类别的列表原始记录（含 lcontent），不抓详情、不做条目级构建。"""
    logger.info("拉取类别 [%s] %s 列表", lfgcc, category_name)
    records = []
    expected_total = 0
    page = 1
    seen_ids = set()
    while True:
        recs, total, pages = fetch_list_page(lfgcc, page, size, rate)
        if page == 1:
            expected_total = total  # 首页总量作为"预期条数"，用于漏抓自检
        logger.info("  第 %d/%d 页，本页 %d 条（全量共 %d 条）",
                    page, max(pages, 1), len(recs), total)
        for rec in recs:
            rid = rec.get("id")
            if rid in seen_ids:
                continue
            seen_ids.add(rid)
            records.append(rec)
            if max_items and len(records) >= max_items:
                logger.info("  已达到 --max-items 上限 %d，停止。", max_items)
                return records, expected_total
        if page >= max(pages, 1) or not recs:
            break
        page += 1
    logger.info("类别 [%s] 列表拉取完成，共 %d 条。", lfgcc, len(records))
    return records, expected_total


def build_entry(rec, detail, category_id, category_name, fetch_detail_enabled, attachments=None):
    """将列表记录 + 详情合并为结构化条目。"""
    content_html = ""
    if fetch_detail_enabled and detail:
        content_html = detail.get("lcontent") or ""
    if not content_html:
        content_html = rec.get("lcontent") or ""
    content_text = net.html_to_text(content_html)

    summary = (detail or rec).get("lcaption") if fetch_detail_enabled else rec.get("lcaption")
    if not summary:
        summary = content_text[:200]

    law_id = rec.get("id") or (detail or {}).get("id")

    return {
        "id": law_id,
        "title": rec.get("title"),
        "category_id": category_id,
        "category_name": category_name or rec.get("ltflbName"),
        "publish_date": net.normalize_date((detail or rec).get("lBuDate") if fetch_detail_enabled else rec.get("lBuDate")),
        "effective_date": net.normalize_date((detail or rec).get("lSsDate") if fetch_detail_enabled else rec.get("lSsDate")),
        "issue_org": (detail or rec).get("lbbdw") if fetch_detail_enabled else rec.get("lbbdw"),
        "doc_no": (detail or rec).get("lwh") if fetch_detail_enabled else rec.get("lwh"),
        "summary": summary,
        "content_text": content_text,
        "content_length": len(content_text),
        "status": (detail or rec).get("lstate") if fetch_detail_enabled else rec.get("lstate"),
        "create_dept": rec.get("createDeptName"),
        "file_count": rec.get("fileCount"),
        "expire_date": net.normalize_date((detail or rec).get("expireDate") if fetch_detail_enabled else rec.get("expireDate")),
        "abolish_date": net.normalize_date((detail or rec).get("abolishDate") if fetch_detail_enabled else rec.get("abolishDate")),
        "detail_link": f"{net.HOST}/ui/src/views/law_html/{law_id}.html" if law_id else None,
        "api_link": f"{net.BASE}/lawFile/get/{law_id}" if law_id else None,
        "source": "fgk.mof.gov.cn",
        "fetch_time": datetime.now(UTC).isoformat(),
        "attachments": attachments or [],
        "attachment_count": len(attachments or []),
        "attachment_content": "\n\n".join(a.get("text") for a in (attachments or []) if a.get("text")),
        "table_structured": [t for a in (attachments or []) for t in (a.get("table_structured") or [])] or [],
        "table_raw_text": "\n\n".join(a.get("table_raw_text") for a in (attachments or []) if a.get("table_raw_text")),
        "table_recovery_method": "structured" if any(a.get("table_structured") for a in (attachments or [])) else "",
        "rich_structured": [o for a in (attachments or []) for o in (a.get("rich_structured") or [])] or [],
        "rich_text": "\n".join(a.get("rich_text") for a in (attachments or []) if a.get("rich_text")),
        "rich_count": sum((a.get("rich_count") or 0) for a in (attachments or [])),
        "attachment_names": "; ".join((a.get("file_name") or "") for a in (attachments or [])),
    }


def _sig_current(rec):
    """当前列表记录的变更指纹（标题/正文/日期/文号/机关/题注）。"""
    return (
        rec.get("title"),
        net.html_to_text(rec.get("lcontent") or ""),
        net.normalize_date(rec.get("lBuDate")),
        net.normalize_date(rec.get("lSsDate")),
        rec.get("lwh"),
        rec.get("lbbdw"),
        rec.get("lcaption"),
    )


def _sig_prev(entry):
    """历史存储条目的变更指纹（与 _sig_current 口径对齐）。"""
    return (
        entry.get("title"),
        entry.get("content_text"),
        entry.get("publish_date"),
        entry.get("effective_date"),
        entry.get("doc_no"),
        entry.get("issue_org"),
        entry.get("summary"),
    )


def _sig_eq(a, b):
    """指纹等价比较：把 None 归一为 '' 后再比。

    背景（2026-09-22 周调度 P0）：列表侧缺失 lSsDate 走 normalize_date → None，
    而主库历史值多为 ''；None != '' 使 254 条/周被误判为"变更"。该字段在列表接口
    本就不可得，排除 null 形态差异不损失真实可检测的变更能力。
    """
    return (tuple("" if x is None else x for x in a)
            == tuple("" if x is None else x for x in b))


def _canon_law_id(v):
    """法规主键统一到单一类型（主库约定：纯数字 → int）。

    背景（2026-09-22 周调度 P0）：列表接口现以字符串返回 id，而主库历史为 int；
    原实现按原类型取值 → 恒不命中 → 每条被判为 added，增量彻底失效。
    """
    if v is None:
        return None
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int):
        return v
    s = str(v).strip()
    return int(s) if s.lstrip("-").isdigit() else s


def write_status(outdir, status):
    atomic_write(os.path.join(os.path.dirname(outdir), "state", "status.json"),
                 json.dumps(status, ensure_ascii=False, indent=2))


class MofCollector:
    """财政部法规库爬虫；实现 SourceCollector。"""

    source_id = "mof"

    def collect(self, out_dir: str, *, categories=None, max_items=None,
                no_detail=False, no_attachments=False, refetch_attachments=False,
                size=50, delay_min=0.3, delay_max=0.7, timeout=30,
                offline=False, cache_dir="") -> str:
        categories = list(categories or DEFAULT_CATEGORIES.keys())
        cat_map = {k: DEFAULT_CATEGORIES.get(k, k) for k in categories}
        net._init_cache(cache_dir or None, offline)
        os.makedirs(out_dir, exist_ok=True)
        rate = net.RateLimiter(delay_min, delay_max)

        lock_path = os.path.join(out_dir, "scrape.lock")
        if not acquire_lock(lock_path):
            logger.error("已有抓取任务在运行（锁文件存在且未超龄），本次跳过以避免重复抓取。")
            sys.exit(0)

        status = {
            "last_run_time": datetime.now(UTC).isoformat(),
            "mode": "incremental",
            "last_success_time": None, "last_success_count": None,
            "active_count": None, "expected_count": None,
            "added": 0, "updated": 0, "removed": 0, "unchanged": 0,
            "warning": None, "last_error": None, "last_error_time": None,
        }
        store_path = os.path.join(out_dir, "mof_laws.json")
        try:
            # —— 载入历史存储（mof_laws.json 本身即为持久化存储）——
            prev_items = []
            if os.path.exists(store_path):
                try:
                    pd = json.load(open(store_path, encoding="utf-8"))
                    if isinstance(pd, dict):
                        prev_items = pd.get("items", []) or []
                    elif isinstance(pd, list):
                        prev_items = pd
                except Exception as e:  # noqa: BLE001
                    logger.warning("历史存储读取失败，本次将作为全量新增处理：%s", e)
                    prev_items = []
            prev_by_id = {str(it.get("id")): it for it in prev_items if it.get("id")}

            new_store = []
            current_raw_by_id = {}
            expected_total = 0
            added = updated = unchanged = 0

            for lfgcc in categories:
                raw_records, exp = fetch_category_records(
                    lfgcc, cat_map.get(lfgcc, lfgcc), size=size, rate=rate, max_items=max_items)
                expected_total += exp
                refetch_att = refetch_attachments and not no_detail
                fetch_att = (not no_detail) and not no_attachments
                for rec in raw_records:
                    rid = rec.get("id")
                    current_raw_by_id[str(rid)] = rec
                    prev = prev_by_id.get(str(rid))
                    sig_changed = prev is not None and not _sig_eq(_sig_current(rec), _sig_prev(prev))
                    if prev is None:
                        detail = fetch_detail(rid, rate) if not no_detail else None
                        atts = collect_attachments(rid, rate) if fetch_att else []
                        entry = build_entry(rec, detail, lfgcc, cat_map.get(lfgcc, lfgcc),
                                           not no_detail, attachments=atts)
                        entry["id"] = _canon_law_id(entry.get("id"))
                        entry["active"] = True
                        entry["change"] = "added"
                        added += 1
                    elif sig_changed:
                        detail = fetch_detail(rid, rate) if not no_detail else None
                        atts = collect_attachments(rid, rate) if fetch_att else []
                        entry = build_entry(rec, detail, lfgcc, cat_map.get(lfgcc, lfgcc),
                                           not no_detail, attachments=atts)
                        entry["id"] = _canon_law_id(entry.get("id"))
                        entry["active"] = True
                        entry["change"] = "updated"
                        updated += 1
                    else:
                        entry = dict(prev)
                        if refetch_att and fetch_att:
                            atts = collect_attachments(rid, rate)
                            entry["attachments"] = atts
                            entry["attachment_count"] = len(atts)
                            entry["attachment_names"] = "; ".join(
                                a.get("file_name", "") for a in atts)
                        entry["active"] = True
                        entry["change"] = "unchanged"
                        unchanged += 1
                    new_store.append(entry)

            # —— 历史中存在但本次列表已无 → 视为已移除（保留记录，标记 inactive）——
            removed = 0
            for rid, prev in prev_by_id.items():
                if rid not in current_raw_by_id:
                    rec = dict(prev)
                    rec["active"] = False
                    rec["change"] = "removed"
                    rec["removed_at"] = datetime.now(UTC).isoformat()
                    new_store.append(rec)
                    removed += 1

            active_count = sum(1 for e in new_store if e.get("active"))

            # —— 漏抓自检：活跃条数应与当前列表预期总量一致（验证模式除外）——
            if expected_total and not max_items and active_count != expected_total:
                status["warning"] = (
                    f"活跃条目 {active_count} 与预期 {expected_total} 不一致，"
                    "可能抓取期间数据发生变更，建议复核。"
                )
                logger.warning(status["warning"])

            # —— 原子写输出（失败时不覆盖上一次成功结果）——
            write_master_json(store_path, net.HOST, new_store)

            status.update({
                "last_success_time": datetime.now(UTC).isoformat(),
                "last_success_count": len(new_store),
                "active_count": active_count,
                "expected_count": expected_total,
                "added": added, "updated": updated, "removed": removed, "unchanged": unchanged,
            })
            logger.info("✅ 增量更新完成：新增 %d / 更新 %d / 移除 %d / 未变 %d；活跃 %d 条。",
                        added, updated, removed, unchanged, active_count)
            logger.info("   JSON : %s", store_path)
        except Exception as e:  # noqa: BLE001
            # —— 异常状态处理：记录错误，保留上一次成功结果（不覆盖）——
            status["last_error"] = f"{type(e).__name__}: {e}"
            status["last_error_time"] = datetime.now(UTC).isoformat()
            logger.exception("抓取失败：%s", e)
        finally:
            write_status(out_dir, status)
            release_lock(lock_path)

        if status["last_error"]:
            sys.exit(1)
        return store_path


def main():
    ap = argparse.ArgumentParser(description="财政部法规数据库自动抓取（重构版·增量 diff）")
    ap.add_argument("--categories", nargs="*", default=list(DEFAULT_CATEGORIES.keys()))
    ap.add_argument("--outdir", default=DATA_RAW)
    ap.add_argument("--size", type=int, default=50, help="列表每页条数（默认50）")
    ap.add_argument("--delay-min", type=float, default=0.3, help="请求最小间隔(秒)")
    ap.add_argument("--delay-max", type=float, default=0.7, help="请求最大间隔(秒)")
    ap.add_argument("--no-detail", action="store_true", help="仅抓列表，不逐条请求详情页（同时跳过附件下载）")
    ap.add_argument("--no-attachments", action="store_true", help="跳过附件下载（仅主数据全量）")
    ap.add_argument("--refetch-attachments", action="store_true", help="强制重新抓取并下载全部条目的附件")
    ap.add_argument("--max-items", type=int, default=None, help="每类别最多抓取条目数（验证用）")
    ap.add_argument("--timeout", type=int, default=30, help="单请求超时(秒)")
    ap.add_argument("--cache-dir", default="", help="请求缓存目录（显式覆盖）")
    ap.add_argument("--offline", action="store_true", help="纯离线模式（仅读缓存，缺失即跳过）")
    args = ap.parse_args()
    MofCollector().collect(
        args.outdir, categories=args.categories, max_items=args.max_items,
        no_detail=args.no_detail, no_attachments=args.no_attachments,
        refetch_attachments=args.refetch_attachments, size=args.size,
        delay_min=args.delay_min, delay_max=args.delay_max,
        timeout=args.timeout, offline=args.offline, cache_dir=args.cache_dir,
    )


if __name__ == "__main__":
    main()
