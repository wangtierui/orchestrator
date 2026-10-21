# -*- coding: utf-8 -*-
"""mof 财政部法规库爬虫（重构版）：实现 SourceCollector，继承 CollectorPipeline。

站点：fgk.mof.gov.cn（layui SPA，直连 REST 接口）。
产物：data/raw/mof_laws.json，结构 {source, captured_at, count, items}（与重构前一致）。

通用流程（列表→known/new/dup 分流→落库）由 CollectorPipeline 承担；
mof 的"变更指纹 diff"特殊步骤表达为钩子：
  - iter_list_entries：逐类别翻页，yield 列表原始记录（附类别信息）；
  - handle_new：added（无主库旧记录 → 抓详情+附件）；
  - handle_known：用当前列表记录与主库旧记录比对指纹，区分 unchanged（复用）/ updated（重抓）；
  - handle_missing：主库有但列表缺失 → removed（active=False）；
  - preserve_missing_master：True（驱动 handle_missing）。
status.json / 锁 / 漏抓自检等运维逻辑保留在 collect()。
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
    atomic_write,
)
from ..storage import get_storage, StorageSettings, open_storage
from ..lib.pipeline import CollectorPipeline
from ..lib.incremental import KnownTracker
from . import net
from ..lib.text_utils import flatten_attachment_extras
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
    tracker = KnownTracker(key_of=lambda r: r.get("id"))
    while True:
        recs, total, pages = fetch_list_page(lfgcc, page, size, rate)
        if page == 1:
            expected_total = total  # 首页总量作为"预期条数"，用于漏抓自检
        logger.info("  第 %d/%d 页，本页 %d 条（全量共 %d 条）",
                    page, max(pages, 1), len(recs), total)
        for rec in recs:
            if tracker.classify(rec) == "dup":
                continue
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
        **flatten_attachment_extras(attachments),
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
    """指纹等价比较：把 None 归一为 '' 后再比。"""
    return (tuple("" if x is None else x for x in a)
            == tuple("" if x is None else x for x in b))


def _canon_law_id(v):
    """法规主键统一到单一类型（主库约定：纯数字 → int）。"""
    if v is None:
        return None
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int):
        return v
    s = str(v).strip()
    return int(s) if s.lstrip("-").isdigit() else s


def _audit_missing_captures(active_count, expected_total, max_items) -> str | None:
    """治理闸门：活跃条数应与当前列表预期总量一致（验证模式/限量模式除外）。

    属采集边界的运维治理，非采集逻辑本身；返回告警文案或 None。
    """
    if expected_total and not max_items and active_count != expected_total:
        return (f"活跃条目 {active_count} 与预期 {expected_total} 不一致，"
                "可能抓取期间数据发生变更，建议复核。")
    return None


# ── 治理步骤（采集边界，非采集逻辑）：运行态 sidecar 落盘 ──
def write_status(outdir, status):
    """治理 sidecar：运行态对账结果落 data/state/status.json（运维观测用，不阻断交付）。"""
    atomic_write(os.path.join(os.path.dirname(outdir), "state", "status.json"),
                 json.dumps(status, ensure_ascii=False, indent=2))


class MofCollector(CollectorPipeline):
    """财政部法规库爬虫；继承通用管线，变更 diff 表达为钩子。"""

    source_id = "mof"
    master_filename = "mof_laws.json"

    # ----------------------------- CollectorPipeline 钩子 -----------------------------
    def key_of(self, item: dict):
        return _canon_law_id(item.get("id"))

    def preserve_missing_master(self, ctx) -> bool:
        return True  # 主库有但列表缺失 → handle_missing 标 removed

    def _setup(self, args):
        self._args = args
        self._categories = list(args.categories or DEFAULT_CATEGORIES.keys())
        self._cat_map = {k: DEFAULT_CATEGORIES.get(k, k) for k in self._categories}
        self._size = args.size
        self._rate = net.RateLimiter(args.delay_min, args.delay_max)
        self._fetch_detail_enabled = not args.no_detail
        self._fetch_att = (not args.no_detail) and not args.no_attachments
        self._refetch_att = args.refetch_attachments and not args.no_detail
        self._expected_total = 0

    def iter_list_entries(self, ctx):
        for lfgcc in self._categories:
            page = 1
            while True:
                recs, total, pages = fetch_list_page(lfgcc, page, self._size, self._rate)
                if page == 1:
                    self._expected_total += total
                for rec in recs:
                    yield {**rec, "_cat": lfgcc, "_cat_name": self._cat_map.get(lfgcc, lfgcc)}
                if page >= max(pages, 1) or not recs:
                    break
                page += 1

    def handle_new(self, entry, ctx):
        rid = self.key_of(entry)
        detail = fetch_detail(rid, self._rate) if self._fetch_detail_enabled else None
        atts = collect_attachments(rid, self._rate) if self._fetch_att else []
        rec = build_entry(entry, detail, entry["_cat"], entry["_cat_name"],
                          self._fetch_detail_enabled, attachments=atts)
        rec["id"] = _canon_law_id(rec.get("id"))
        rec["active"] = True
        rec["change"] = "added"
        return rec

    def handle_known(self, key, entry, ctx):
        prev = ctx.master_by_key.get(key)
        if prev is None:
            # 理论上不会进入（known 必来自主库），兜底当 added 处理
            return self.handle_new(entry, ctx)
        rid = key
        if not _sig_eq(_sig_current(entry), _sig_prev(prev)):
            # 指纹变更 → updated（重抓详情+附件）
            detail = fetch_detail(rid, self._rate) if self._fetch_detail_enabled else None
            atts = collect_attachments(rid, self._rate) if self._fetch_att else []
            rec = build_entry(entry, detail, entry["_cat"], entry["_cat_name"],
                              self._fetch_detail_enabled, attachments=atts)
            rec["id"] = _canon_law_id(rec.get("id"))
            rec["active"] = True
            rec["change"] = "updated"
            return rec
        # 不变 → 复用旧记录
        rec = dict(prev)
        rec["active"] = True
        rec["change"] = "unchanged"
        if self._refetch_att and self._fetch_att:
            atts = collect_attachments(rid, self._rate)
            rec["attachments"] = atts
            rec["attachment_count"] = len(atts)
            rec["attachment_names"] = "; ".join(a.get("file_name", "") for a in atts)
        return rec

    def handle_missing(self, key, ctx):
        prev = ctx.master_by_key.get(key)
        if prev is None:
            return None
        rec = dict(prev)
        rec["active"] = False
        rec["change"] = "removed"
        rec["removed_at"] = datetime.now(UTC).isoformat()
        return rec

    # ----------------------------- SourceCollector 协议入口 -----------------------------
    def collect(self, out_dir: str, *, categories=None, max_items=None,
                no_detail=False, no_attachments=False, refetch_attachments=False,
                size=50, delay_min=0.3, delay_max=0.7, timeout=30) -> str:
        args = argparse.Namespace(
            out_dir=out_dir, categories=categories, max_items=max_items,
            no_detail=no_detail, no_attachments=no_attachments,
            refetch_attachments=refetch_attachments, size=size,
            delay_min=delay_min, delay_max=delay_max, timeout=timeout)

        os.makedirs(out_dir, exist_ok=True)
        self._setup(args)
        self._status = {
            "last_run_time": datetime.now(UTC).isoformat(),
            "mode": "incremental",
            "last_success_time": None, "last_success_count": None,
            "active_count": None, "expected_count": None,
            "added": 0, "updated": 0, "removed": 0, "unchanged": 0,
            "warning": None, "last_error": None, "last_error_time": None,
        }
        try:
            locator = self.run(out_dir)
            self._governance_sidecar(locator)
        except Exception as e:  # noqa: BLE001
            self._status["last_error"] = f"{type(e).__name__}: {e}"
            self._status["last_error_time"] = datetime.now(UTC).isoformat()
            logger.exception("抓取失败：%s", e)
            write_status(out_dir, self._status)
            sys.exit(1)
        return locator

    def _governance_sidecar(self, locator: str) -> None:
        """采集完成后的治理副作用（采集边界，非采集逻辑）：写 status.json + 漏抓自检闸口。"""
        records = getattr(self, "_last_records", [])
        st = self._status
        added = sum(1 for e in records if e.get("change") == "added")
        updated = sum(1 for e in records if e.get("change") == "updated")
        removed = sum(1 for e in records if e.get("change") == "removed")
        unchanged = sum(1 for e in records if e.get("change") == "unchanged")
        active_count = sum(1 for e in records if e.get("active"))

        st["warning"] = _audit_missing_captures(
            active_count, self._expected_total, getattr(self._args, "max_items", None))
        if st["warning"]:
            logger.warning(st["warning"])

        st.update({
            "last_success_time": datetime.now(UTC).isoformat(),
            "last_success_count": len(records),
            "active_count": active_count,
            "expected_count": self._expected_total,
            "added": added, "updated": updated, "removed": removed, "unchanged": unchanged,
        })
        logger.info("✅ 增量更新完成：新增 %d / 更新 %d / 移除 %d / 未变 %d；活跃 %d 条。",
                    added, updated, removed, unchanged, active_count)
        logger.info("   JSON : %s", locator)
        write_status(self._args.out_dir, st)


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
    args = ap.parse_args()
    MofCollector().collect(
        args.outdir, categories=args.categories, max_items=args.max_items,
        no_detail=args.no_detail, no_attachments=args.no_attachments,
        refetch_attachments=args.refetch_attachments, size=args.size,
        delay_min=args.delay_min, delay_max=args.delay_max,
        timeout=args.timeout,
    )


if __name__ == "__main__":
    main()
