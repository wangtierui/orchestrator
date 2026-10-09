# -*- coding: utf-8 -*-
"""gov 爬虫：主流程 + 多子源分发（实现 SourceCollector）。

原 modules/regulatory_scrapers/collectors/gov_collector.py 主流程迁出，逻辑逐函数等价；
XzfgkScraper / RobustSession / 多子源分发 / 增量合并均保留；仅 import 改为相对导入，
并抽出 `_run(args)` 供 GovCollector.collect() 复用。产物仍为 gov_laws.json
（与下游 clean 既有输入契约一致）。
"""
import argparse
import atexit
import logging
import os
import re
import sys

from ..base import REPO_ROOT
from .attachments import fetch_gov_attachments
from .net import (
    RobustSession,
    _init_cache,
)
from .parse import (
    SOURCES,
    ScrapeConfig,
    build_config,
    clean_text,
    extract_date,
    extract_doc_number,
    extract_issue_organ,
    load_resume,
    make_summary,
    merge_with_master,
    write_collect_stats,
    write_outputs,
)
from .zhengceku import ZhengcekuScraper

#: regulatory_scrapers 根（默认 out 与历史路径口径，字节兼容）。
SCRAPERS_ROOT = os.path.join(REPO_ROOT, "modules", "regulatory_scrapers")
DEFAULT_OUT = os.path.join(SCRAPERS_ROOT, "data", "raw")

# 本地 strip_tags（原 gov_collector 引用但未导出；此处就近定义，行为一致）。
def strip_tags(s):
    return re.sub(r"<[^>]+>", "", s or "")


try:
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover
    BeautifulSoup = None  # type: ignore

LOG = logging.getLogger("gov_scraper")

from config.exitcodes import ExitCode
from std_lib.common_lib import fs_lock


# --------------------------------------------------------------------------- #
# 数据源 1：行政法规库（xzfgk，服务端渲染，requests 可抓）
# --------------------------------------------------------------------------- #
class XzfgkScraper:
    """司法部行政法规库：search2.html 列表 + /front/law/detail 详情。"""

    LIST_URL = "https://xzfg.moj.gov.cn/search2.html"
    DETAIL_URL = "https://xzfg.moj.gov.cn/front/law/detail"

    def __init__(self, cfg: ScrapeConfig, client: RobustSession):
        self.cfg = cfg
        self.client = client
        self.stats: dict = {}

    def _list_page(self, page_index: int) -> str | None:
        return self.client.get_text(
            self.LIST_URL, referer=self.LIST_URL,
            params={"PageIndex": page_index},
        )

    def _parse_list(self, html: str) -> list[dict[str, str]]:
        soup = BeautifulSoup(html, "lxml")
        items: list[dict[str, str]] = []
        for li in soup.select("li.list-item"):
            a = li.select_one(".title a")
            if not a:
                continue
            title = clean_text(a.get_text())
            href = str(a.get("href", "") or "")
            if href and not href.startswith("http"):
                href = "https://xzfg.moj.gov.cn" + href
            pub = li.select_one(".publish-date")
            impl = li.select_one(".implemented-date")
            items.append({
                "title": title,
                "detail_url": href,
                "publish_date": extract_date(pub.get_text()) if pub else "",
                "effective_date": extract_date(impl.get_text()) if impl else "",
                "issue_organ": "",
                "document_number": "",
            })
        return items

    def _parse_detail(self, html: str) -> dict[str, str]:
        soup = BeautifulSoup(html, "html.parser")
        title_el = soup.select_one(".text-title")
        title = clean_text(title_el.get_text()) if title_el else ""

        content_el = None
        for sel in self._content_selectors():
            content_el = soup.select_one(sel)
            if content_el:
                break
        if content_el:
            for hint in self._noise_hints():
                for el in content_el.select(f'[class*="{hint}"]'):
                    el.decompose()
            for el in content_el.select("script, style"):
                el.decompose()
        full_text = clean_text(content_el.get_text()) if content_el else ""

        doc_no = extract_doc_number(full_text)
        eff = ""
        m = re.search(r"自(.{0,30}?)起施行", full_text)
        if m:
            eff = extract_date(m.group(1))
        if not eff:
            m = re.search(r"(\d{4}[-年]\d{1,2}[-月]\d{1,2}日?)\s*施行", full_text)
            eff = m.group(1) if m else ""
        organ = extract_issue_organ(full_text)
        m = re.search(r"(\d{4}年\d{1,2}月\d{1,2}日).{0,40}公布", full_text)
        pub_orig = m.group(1) if m else ""
        return {
            "title": title,
            "full_text": full_text,
            "document_number": doc_no,
            "issue_organ": organ,
            "effective_date": eff,
            "pub_date_original": pub_orig,
        }

    @staticmethod
    def _content_selectors():
        return [
            ".law-chapter", "#content", "#UCAP-CONTENT", ".content-text",
            ".pages_content", ".article", "#zoom", "#article-content",
            ".TRS_Editor", "div.content",
        ]

    @staticmethod
    def _noise_hints():
        return ["download", "fold", "historical", "tip", "btn", "share", "qr", "vconsole"]

    def run(self) -> list[dict]:
        records: list[dict] = []
        page = 1
        seen_urls = set()
        if self.cfg.resume:
            resumed, _ = load_resume(self.cfg.out_dir, self.cfg.source)
            seen_urls = set(k[1] for k in resumed if k[1])
            LOG.info("【xzfgk resume】将跳过 %d 条已抓条目", len(seen_urls))
        pages_fetched = 0
        pages_without_new = 0
        items_seen = 0
        new_items = 0
        details_fetched = 0
        detail_seconds = 0.0
        stopped_reason = "end"
        budget = float(getattr(self.cfg, "max_seconds", 0.0) or 0.0)
        t0 = time.time()

        def _mk_stats() -> dict:
            total = time.time() - t0
            return {
                "sub_source": "xzfgk",
                "list_pages_fetched": pages_fetched,
                "list_pages_without_new": pages_without_new,
                "list_items_seen": items_seen,
                "new_items": new_items,
                "pending_details": new_items,
                "details_fetched": details_fetched,
                "details_failed": 0,
                "details_skipped_master": 0,
                "elapsed_list_s": round(max(0.0, total - detail_seconds), 1),
                "elapsed_detail_s": round(detail_seconds, 1),
                "stopped_reason": stopped_reason,
                "backfill": False,
                "budget_s": budget,
            }

        while True:
            if self.cfg.max_pages and page > self.cfg.max_pages:
                LOG.info("已达 max_pages=%d，停止翻页", self.cfg.max_pages)
                stopped_reason = "max_pages"
                break
            if budget and (time.time() - t0) >= budget:
                stopped_reason = "budget"
                LOG.warning("已达 --max-seconds=%s 预算 → 停止翻页（已抓 %d 条正常落盘）",
                            budget, len(records))
                break
            LOG.info("【xzfgk】抓取列表第 %d 页", page)
            html = self._list_page(page)
            if not html:
                LOG.warning("第 %d 页获取失败，停止", page)
                stopped_reason = "list_fetch_failed"
                break
            items = self._parse_list(html)
            if not items:
                LOG.info("第 %d 页无条目，视为末页，停止", page)
                stopped_reason = "no_items"
                break
            pages_fetched += 1
            items_seen += len(items)
            new_in_page = 0
            for it in items:
                if it["detail_url"] in seen_urls:
                    continue
                seen_urls.add(it["detail_url"])
                new_in_page += 1
                new_items += 1
                rec = dict(it)
                rec["category"] = self.cfg.category
                rec["source"] = "xzfgk"
                if (self.cfg.fetch_details and it["detail_url"]
                        and (self.cfg.details_limit == 0
                             or len(records) < self.cfg.details_limit)):
                    _t_d = time.time()
                    d = self._fetch_detail(it["detail_url"])
                    detail_seconds += time.time() - _t_d
                    rec.update(d)
                    if d:
                        details_fetched += 1
                rec["summary"] = make_summary(
                    rec.get("full_text", ""), self.cfg.summary_len)
                records.append(rec)
                if self.cfg.max_items and len(records) >= self.cfg.max_items:
                    LOG.info("已达 max_items=%d，停止", self.cfg.max_items)
                    stopped_reason = "max_items"
                    self.stats = _mk_stats()
                    return records
            if new_in_page == 0:
                pages_without_new += 1
                LOG.info("第 %d 页无新增条目（全部已抓或分页失效），停止翻页", page)
                stopped_reason = "no_new_items"
                break
            page += 1
        self.stats = _mk_stats()
        return records

    def _fetch_detail(self, url: str) -> dict:
        detail_html = self.client.get_text(url, referer=self.LIST_URL)
        if not detail_html:
            LOG.warning("详情获取失败：%s", url)
            return {"full_text": "", "document_number": "",
                    "issue_organ": "", "effective_date": "",
                    "attachments": [], "attachment_text": "", "attachment_count": 0}
        try:
            d: dict = self._parse_detail(detail_html)
        except Exception as e:  # noqa: BLE001
            LOG.warning("详情解析失败 %s：%s", url, e)
            d = {"full_text": "", "document_number": "",
                 "issue_organ": "", "effective_date": ""}
        try:
            atts, att_text = fetch_gov_attachments(
                detail_html, entry_id=url, entry_title=d.get("title", ""),
                out_dir=self.cfg.out_dir, base_url=url)
            d["attachments"] = atts
            d["attachment_text"] = att_text
            d["attachment_count"] = len(atts)
            _tbls = [t for a in atts for t in (a.get("table_structured") or [])]
            if _tbls:
                d["table_structured"] = _tbls
                d["table_recovery_method"] = "structured"
                _raws = [a.get("table_raw_text") for a in atts if a.get("table_raw_text")]
                if _raws:
                    d["table_raw_text"] = "\n\n".join(_raws)
            _richo = [o for a in atts for o in (a.get("rich_structured") or [])]
            if _richo:
                d["rich_structured"] = _richo
                d["rich_count"] = len(_richo)
                _rtext = [a.get("rich_text") for a in atts if a.get("rich_text")]
                if _rtext:
                    d["rich_text"] = "\n".join(_rtext)
        except Exception as e:  # noqa: BLE001
            LOG.warning("附件抓取异常 %s：%s", url, e)
            d.setdefault("attachments", [])
            d["attachment_text"] = ""
            d["attachment_count"] = 0
        return d


# --------------------------------------------------------------------------- #
# 主流程（由 main() / GovCollector.collect() 共用）
# --------------------------------------------------------------------------- #
import time


def _run(args) -> int:
    # 增量默认（2026-09-08 周调度增量改造）：resume = 非 --full。
    args.resume = not args.full

    _init_cache(args.cache_dir or None, args.offline)

    os.makedirs(args.out_dir, exist_ok=True)
    os.makedirs("logs", exist_ok=True)
    log_file = args.log_file or os.path.join("logs", "scraper.log")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )

    # —— 调度可靠性：跨进程单实例锁防并发重复（统一 fs_lock 公共库，N-8）——
    _lock = fs_lock.ProcessLock(os.path.join(args.out_dir, "scrape.lock"))
    if not _lock.acquire():
        LOG.warning("已有抓取任务在运行（锁存在且 PID 存活），本次跳过以避免重复抓取。")
        return ExitCode.OK
    atexit.register(_lock.release)

    if args.source == "all":
        want = list(SOURCES)
    else:
        want = [args.source]

    _cfg_args = argparse.Namespace(**vars(args))
    _cfg_args.source = want[0]
    cfg = build_config(_cfg_args)
    client = RobustSession(cfg)

    seen_urls = set()
    known_urls = set()
    if cfg.resume:
        _seen, detailed = load_resume(cfg.out_dir, "gov")
        seen_urls = set(detailed)
        known_urls = set(k[1] for k in _seen if k[1])
        LOG.info("【增量判据】主库 %d 条（已有正文 %d 条）", len(known_urls), len(seen_urls))

    records: list = []
    failed: list = []
    sub_stats: dict = {}
    scrapers: dict = {}
    for s in want:
        sub = argparse.Namespace(**vars(args))
        sub.source = s
        scfg = build_config(sub)
        LOG.info("开始抓取子源：%s（%s）", scfg.source, scfg.category)
        scraper = None
        try:
            if s == "xzfgk":
                scraper = XzfgkScraper(scfg, client)
            else:
                scraper = ZhengcekuScraper(scfg, client, seen_urls, known_urls)
            recs = scraper.run()
        except Exception as e:
            LOG.exception("子源 %s 抓取过程发生致命错误：%s", s, e)
            failed.append(s)
            continue
        sub_stats[s] = dict(getattr(scraper, "stats", {}) or {})
        scrapers[s] = scraper
        LOG.info("子源 %s 抓取到 %d 条", s, len(recs))
        records.extend(recs)
        for r in recs:
            if r.get("full_text"):
                seen_urls.add(r.get("detail_url", ""))

    def _emit_stats() -> None:
        _run_at = time.strftime("%Y-%m-%dT%H:%M:%S")
        for _s, _st in sub_stats.items():
            _p = write_collect_stats(
                "gov_%s" % _s,
                {
                    "source": "gov",
                    "sub_source": _s,
                    "run_at": _run_at,
                    "out_dir": cfg.out_dir,
                    "resume": cfg.resume,
                    "backfill": bool(getattr(args, "backfill", False)),
                    "max_seconds": float(getattr(args, "max_seconds", 0.0) or 0.0),
                    "new_records": None,
                    "stats": _st,
                },
            )
            _pf = int(_st.get("list_pages_fetched") or 0)
            _pw = int(_st.get("list_pages_without_new") or 0)
            LOG.info(
                "[collect-stats] gov_%s：翻页 %d（无新页 %d，**空转占比 %s**），新条目 %s，"
                "待抓详情 %s（已抓 %s/失败 %s，跳过主库既有 %s），用时 list=%ss detail=%ss，"
                "停止原因=%s → %s",
                _s, _pf, _pw, ("%.0f%%" % (100.0 * _pw / _pf) if _pf else "n/a"),
                _st.get("new_items"), _st.get("pending_details"), _st.get("details_fetched"),
                _st.get("details_failed"), _st.get("details_skipped_master"),
                _st.get("elapsed_list_s"), _st.get("elapsed_detail_s"),
                _st.get("stopped_reason"), _p or "(落盘失败)",
            )

    if failed and args.stop_on_error:
        _emit_stats()
        return ExitCode.FAIL
    if not records:
        LOG.warning("未抓取到任何条目（失败子源：%s）。", failed or "无")
        _emit_stats()
        return ExitCode.OK

    if cfg.resume:
        records = merge_with_master(records, cfg.out_dir)

    env_source = args.env_source or (cfg.source if len(want) == 1 else "gov")
    env_cat = args.env_category or (cfg.category if len(want) == 1 else "行政法规+部门文件")
    cfg.source, cfg.category = env_source, env_cat
    paths = write_outputs(records, cfg, source_label=env_source, write_csv=args.csv)
    for _sc in scrapers.values():
        if hasattr(_sc, "clear_partial"):
            _sc.clear_partial()
    _emit_stats()
    LOG.info("成功抓取 %d 条（子源：%s；失败：%s）。文件：%s",
             len(records), ",".join(want), ",".join(failed) or "无", paths)
    return ExitCode.OK


class GovCollector:
    """gov 爬虫（xzfgk + zhengceku 双子源）；实现 SourceCollector。"""

    source_id = "gov"

    def collect(self, out_dir: str, *, source="all", max_pages=0, max_items=0,
                no_details=False, details_limit=0, full=False, resume=None,
                delay_min=1.5, delay_max=3.5, timeout=30, retries=4,
                summary_len=200, cache_dir="", offline=False, csv=False,
                stop_on_error=False, checkpoint_every=200, stop_after_empty_pages=1,
                backfill=False, max_seconds=0.0, env_source="", env_category="",
                log_file="") -> str:
        # resume 缺省时按 full 反推（与 CLI 默认一致：非 --full 即增量）
        _resume = (not full) if resume is None else resume
        args = argparse.Namespace(
            source=source, out_dir=out_dir, max_pages=max_pages, max_items=max_items,
            no_details=no_details, details_limit=details_limit, full=full,
            resume=_resume, delay_min=delay_min, delay_max=delay_max, timeout=timeout,
            retries=retries, summary_len=summary_len, cache_dir=cache_dir, offline=offline,
            csv=csv, stop_on_error=stop_on_error, checkpoint_every=checkpoint_every,
            stop_after_empty_pages=stop_after_empty_pages, backfill=backfill,
            max_seconds=max_seconds, env_source=env_source, env_category=env_category,
            log_file=log_file,
        )
        _run(args)
        return os.path.join(out_dir, "gov_laws.json")


def main():
    parser = argparse.ArgumentParser(
        description="抓取 gov.cn 行政法规库（xzfg.moj.gov.cn）行政法规条目（含正文全文）")
    parser.add_argument("--source", choices=["xzfgk", "zhengceku", "all"], default="all",
                        help="数据源（默认 all，gov 源常规采集范围）")
    parser.add_argument("--out-dir", default=DEFAULT_OUT, help="输出目录（默认统一 data/raw/）")
    parser.add_argument("--max-pages", type=int, default=0, help="最大翻页数（0=不限制）")
    parser.add_argument("--max-items", type=int, default=0, help="最大条目数（0=不限制）")
    parser.add_argument("--no-details", action="store_true", help="不抓取详情页")
    parser.add_argument("--details-limit", type=int, default=0, help="仅对前 N 条抓取详情正文")
    parser.add_argument("--resume", action="store_true", help="增量续抓")
    parser.add_argument("--full", action="store_true", help="全量重抓")
    parser.add_argument("--delay-min", type=float, default=1.5, help="请求最小延时（秒）")
    parser.add_argument("--delay-max", type=float, default=3.5, help="请求最大延时（秒）")
    parser.add_argument("--timeout", type=int, default=30, help="请求超时（秒）")
    parser.add_argument("--retries", type=int, default=4, help="重试次数")
    parser.add_argument("--summary-len", type=int, default=200, help="内容摘要字数")
    parser.add_argument("--cache-dir", default="", help="请求缓存目录")
    parser.add_argument("--offline", action="store_true", help="纯离线模式")
    parser.add_argument("--csv", action="store_true", help="额外输出 CSV")
    parser.add_argument("--stop-on-error", action="store_true", help="多子源抓取时任一失败即中止")
    parser.add_argument("--checkpoint-every", type=int, default=200, help="详情阶段断点落盘间隔")
    parser.add_argument("--stop-after-empty-pages", type=int, default=1, help="（仅 zhengceku）连续 N 页无新条目即停")
    parser.add_argument("--backfill", action="store_true", help="（仅 zhengceku）历史存量补全")
    parser.add_argument("--max-seconds", type=float, default=0.0, help="详情阶段软预算（秒）")
    parser.add_argument("--env-source", default="", help="覆盖写主库信封 source")
    parser.add_argument("--env-category", default="", help="覆盖写主库信封 category")
    parser.add_argument("--log-file", default="", help="日志文件路径")
    args = parser.parse_args()
    sys.exit(_run(args))


if __name__ == "__main__":
    main()
