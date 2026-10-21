# -*- coding: utf-8 -*-
"""gov 爬虫（重构版）：双子源编排，继承 CollectorPipeline。

站点：xzfgk（司法部行政法规库）+ zhengceku（国务院政策文件库·部门文件）。
产物仍为 gov_laws.json（与下游 clean 既有输入契约一致）。

通用流程（列表→known/new/dup 分流→落库）由 CollectorPipeline 承担；gov 的特殊步骤：
  - iter_list_entries：串联两子源的列表生成器，每个生成器**只 yield new 条目**
    （用基类 tracker 判 known/dup 并跳过），并实现各自的"无新即停"早停；
  - handle_new：按 entry["source"] 分发到 xzfgk.build_record / zhengceku.fetch_one 抓详情；
  - save：调 merge_with_master（旧主库 ∪ 新条目，新优先）后 save_master；
  - batch_key_of：按列表页号分批落盘（每页详情抓完即 checkpoint，替代原 partial 续跑）；
  - 双子源编排 + resume（主库已知即跳）+ merge，全部落在上述钩子/编排层。
HTML 解析（_parse_list/_parse_detail/scan_list/parse_detail/fetch_attachments）逐字保留。
"""
import argparse
import csv
import logging
import os
import re
import sys
import time

from ..base import REPO_ROOT, SourceCollector
from ..storage import open_storage
from ..lib.text_utils import flatten_attachment_extras
from .parse import (
    SOURCES,
    ScrapeConfig,
    CSV_COLUMNS,
    build_config,
    clean_text,
    decode_html,
    extract_date,
    extract_doc_number,
    extract_issue_organ,
    make_summary,
    merge_with_master,
)
from .net import (
    DEFAULT_HEADERS,
    RobustSession,
)
from .attachments import fetch_gov_attachments
from .zhengceku import ZhengcekuScraper, SUB_SOURCE, CATEGORY

#: 输出目录（自包含，不指向外部 modules/）。产物文件名与结构保持重构前一致。
SCRAPERS_ROOT = os.path.join(REPO_ROOT, "data", "raw")
DEFAULT_OUT = SCRAPERS_ROOT

try:
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover
    BeautifulSoup = None  # type: ignore

LOG = logging.getLogger("gov_scraper")


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
        self._detail_fetched = 0

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

    # ---- 重构：run() 拆为 iter_list（列表生成器）+ build_record（详情）----
    def iter_list(self, ctx) -> "Iterator[dict]":
        """逐页翻列表，**只 yield new 条目**；一页无新即停（增量早停，N-190）。

        用基类 tracker 判 known/dup 并跳过；max_pages/max_items/max_seconds 同样生效。
        """
        tracker = ctx.tracker
        seen = set()  # 本地运行内去重（只读判重，不污染基类 tracker.seen_in_run）
        page = 1
        max_pages = getattr(self.cfg, "max_pages", 0) or 0
        max_items = getattr(self.cfg, "max_items", 0) or 0
        budget = float(getattr(self.cfg, "max_seconds", 0.0) or 0.0)
        t0 = time.time()
        yielded = 0
        while True:
            if max_pages and page > max_pages:
                LOG.info("xzfgk 已达 max_pages=%d，停止翻页", max_pages)
                break
            if budget and (time.time() - t0) >= budget:
                LOG.warning("xzfgk 已达 --max-seconds=%s 预算 → 停止翻页", budget)
                break
            html = self._list_page(page)
            if not html:
                LOG.warning("xzfgk 第 %d 页获取失败，停止", page)
                break
            items = self._parse_list(html)
            if not items:
                LOG.info("xzfgk 第 %d 页无条目，视为末页，停止", page)
                break
            new_in_page = 0
            for it in items:
                key = it["detail_url"]
                # 只读判重（不跳过）：交给基类 classify 路由 known→复用 / new→抓详情；
                # 仅用于本页"无新即停"早停计数，不动基类 tracker 状态（避免重复 classify）。
                is_new = key not in tracker.known and key not in seen
                seen.add(key)
                yield {
                    "title": it["title"], "detail_url": it["detail_url"],
                    "publish_date": it["publish_date"], "effective_date": it["effective_date"],
                    "issue_organ": it.get("issue_organ", ""),
                    "document_number": it.get("document_number", ""),
                    "category": self.cfg.category, "source": "xzfgk", "page": page,
                }
                if is_new:
                    new_in_page += 1
                    yielded += 1
                    if max_items and yielded >= max_items:
                        return
            if new_in_page == 0:
                LOG.info("xzfgk 第 %d 页无新增条目，停止翻页", page)
                break
            page += 1

    def build_record(self, entry: dict) -> dict:
        """new 态：抓详情 + 摘要，产出 gov 主库契约记录。"""
        rec = {k: v for k, v in entry.items() if k != "page"}
        cfg = self.cfg
        detail_cap = int(getattr(cfg, "details_limit", 0) or 0)
        if cfg.fetch_details and entry["detail_url"]:
            if detail_cap == 0 or self._detail_fetched < detail_cap:
                self._detail_fetched += 1
                rec.update(self._fetch_detail(entry["detail_url"]))
            else:
                rec.setdefault("full_text", "")
        else:
            rec.setdefault("full_text", "")
            rec.setdefault("attachments", [])
            rec.setdefault("attachment_text", "")
            rec.setdefault("attachment_count", 0)
        rec["summary"] = make_summary(rec.get("full_text", ""), cfg.summary_len)
        return rec

    def _fetch_detail(self, url: str) -> dict:
        detail_html = self.client.get_text(url, referer=self.LIST_URL)
        if not detail_html:
            LOG.warning("xzfgk 详情获取失败：%s", url)
            return {"full_text": "", "document_number": "",
                    "issue_organ": "", "effective_date": "",
                    "attachments": [], "attachment_text": "", "attachment_count": 0}
        try:
            d: dict = self._parse_detail(detail_html)
        except Exception as e:  # noqa: BLE001
            LOG.warning("xzfgk 详情解析失败 %s：%s", url, e)
            d = {"full_text": "", "document_number": "",
                 "issue_organ": "", "effective_date": ""}
        try:
            atts, att_text = fetch_gov_attachments(
                detail_html, entry_id=url, entry_title=d.get("title", ""),
                out_dir=self.cfg.out_dir, base_url=url)
            d["attachments"] = atts
            d["attachment_text"] = att_text
            d["attachment_count"] = len(atts)
            d.update(flatten_attachment_extras(atts))
        except Exception as e:  # noqa: BLE001
            LOG.warning("xzfgk 附件抓取异常 %s：%s", url, e)
            d.setdefault("attachments", [])
            d["attachment_text"] = ""
            d["attachment_count"] = 0
        return d


# --------------------------------------------------------------------------- #
# gov 主采集器（串联双子源，继承 CollectorPipeline）
# --------------------------------------------------------------------------- #
from ..lib.pipeline import CollectorPipeline  # noqa: E402


def _write_csv(records: list, out_dir: str) -> str:
    csv_path = os.path.join(out_dir, "gov_laws.csv")
    tmp = csv_path + ".tmp"
    with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for r in records:
            writer.writerow({k: r.get(k, "") for k in CSV_COLUMNS})
    os.replace(tmp, csv_path)
    return csv_path


class GovCollector(CollectorPipeline):
    """gov 双子源爬虫；继承通用管线，双子源编排表达为 iter_list/handle_new。"""

    source_id = "gov"
    master_filename = "gov_laws.json"

    def key_of(self, item: dict):
        return item.get("detail_url")

    def _setup(self, args):
        self._args = args
        os.makedirs(args.out_dir, exist_ok=True)
        # 两子源各自 cfg（base_url/category 由 SOURCES 决定）
        def _cfg(source):
            a = argparse.Namespace(**vars(args))
            a.source = source
            return build_config(a)
        self._cfg_xz = _cfg("xzfgk")
        self._cfg_zc = _cfg("zhengceku")
        self._client = RobustSession(self._cfg_xz)
        self._xz = XzfgkScraper(self._cfg_xz, self._client)
        self._zc = ZhengcekuScraper(self._cfg_zc, self._client,
                                    seen_urls=set(), known_urls=set())

    def iter_list_entries(self, ctx):
        yield from self._xz.iter_list(ctx)
        yield from self._zc.iter_list(ctx)

    def handle_new(self, entry, ctx):
        if entry["source"] == "xzfgk":
            return self._xz.build_record(entry)
        return self._zc.fetch_one(
            entry["detail_url"], title_hint=entry["title"], date_hint=entry["publish_date"])

    def batch_key_of(self, entry):
        return entry.get("page")  # 按列表页分批落盘（每页详情抓完即 checkpoint）

    def max_items(self) -> int:
        return int(getattr(self._args, "max_items", 0) or 0)

    def save(self, records, ctx):
        merged = merge_with_master(records, ctx.out_dir, storage=ctx.storage)
        cat = self._args.env_category or (
            "行政法规+部门文件" if self._args.source == "all" else self._cfg_xz.category)
        locator = ctx.storage.save_master(merged, extra={"category": cat})
        if getattr(self._args, "csv", False):
            _write_csv(merged, ctx.out_dir)
        return locator

    # ----------------------------- SourceCollector 协议入口 -----------------------------
    def collect(self, out_dir: str, *, source="all", max_pages=0, max_items=0,
                no_details=False, details_limit=0, full=False, resume=None,
                delay_min=1.5, delay_max=3.5, timeout=30, retries=4,
                summary_len=200, cache_dir="", offline=False, csv=False,
                stop_on_error=False, checkpoint_every=200, stop_after_empty_pages=1,
                backfill=False, max_seconds=0.0, env_source="", env_category="",
                log_file="") -> str:
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
        self._setup(args)
        return self.run(out_dir)


def main():
    parser = argparse.ArgumentParser(
        description="抓取 gov.cn 行政法规库（xzfg.moj.gov.cn）+ 国务院政策文件库·部门文件")
    parser.add_argument("--source", choices=["xzfgk", "zhengceku", "all"], default="all",
                        help="数据源（默认 all）")
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
    GovCollector().collect(
        args.out_dir, source=args.source, max_pages=args.max_pages, max_items=args.max_items,
        no_details=args.no_details, details_limit=args.details_limit, full=args.full,
        resume=args.resume, delay_min=args.delay_min, delay_max=args.delay_max,
        timeout=args.timeout, retries=args.retries, summary_len=args.summary_len, csv=args.csv,
        stop_on_error=args.stop_on_error, checkpoint_every=args.checkpoint_every,
        stop_after_empty_pages=args.stop_after_empty_pages, backfill=args.backfill,
        max_seconds=args.max_seconds, env_source=args.env_source, env_category=args.env_category,
        log_file=args.log_file,
    )


if __name__ == "__main__":
    main()
