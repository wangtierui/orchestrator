# -*- coding: utf-8 -*-
"""gov/gov_scraper — `XzfgkScraper`（司法部行政法规库：列表 + 详情 + 附件）。

为什么单独成文件（2026-10-20 第一百零五批）
------------------------------------------
`collector.py` 原 790 行里，`XzfgkScraper` 独占 **245 行**（6 个方法：列表翻页、
详情抽取、正文清洗、附件抓取、增量比对、结果组装），与 `main` 的编排职责完全不同。
它**不读**模块级可变状态（`_RESP_TEXT` 那组在 `gov_session`；这里经构造参数
`client: RobustSession` 拿会话实例）⇒ 与 session 层**无共享可变状态**，
可以安全分家。这与"session 三件必须同处"的约束正好形成对照。

依赖（AST 实测 14 个，全部来自 `gov_constants` 或标准库/三方）：
  `Any`/`re`/`time`/`BeautifulSoup` + `CONTENT_SELECTORS`/`_NOISE_CLASS_HINTS`/
  `LOG`/`MasterView`/`clean_text`/`extract_date`/`extract_doc_number`/
  `extract_issue_organ`/`fetch_gov_attachments`/`make_summary`。
注解里的 `ScrapeConfig`/`RobustSession` 因 `from __future__ import annotations`
不求值，但为 lint/IDE 友好仍导入。

**双形态**：包形态 / **脚本形态**（生产 `COLLECT_CMD`），故保留引导。
"""
from __future__ import annotations

# ---- 仓库引导（同款；`if not in sys.path` 幂等）----
import os as _os
import sys as _sys

_GUIDE_ROOT = _os.path.abspath(
    _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "..", "..", ".."))
if _GUIDE_ROOT not in _sys.path:
    _sys.path.insert(0, _GUIDE_ROOT)
del _GUIDE_ROOT, _os, _sys

# ---- 标准库 / 第三方（原样复制自 collector.py L44-59，避免漏 import）----
# W4（§7.10 问题 5）：logs 落点改引 paths.LOGS_DIR（原为 CWD 相对 "logs" 自拼，
# 链上 cwd=仓根时写仓根 logs/、standalone 时写 CWD/logs——两种语义隐式分叉）。
# import paths 置于**仓根引导之后**（引导前 import 必 ModuleNotFoundError——
# modules standalone 脚本的同款教训）；isort 将其归入 first-party 段（stdlib 之后）。
import re
import time
from typing import Any

# ---- 缓存会话层：**已下沉到 `gov_session.py`**（2026-10-20）----
# `_RESP_TEXT` 是**可变全局**，必须与 `RobustSession`/`_init_cache` 同处（见该模块

# ---- 常量与依赖别名：来自 `gov_constants`（单一出处）----
try:
    from .gov_constants import (
        _NOISE_CLASS_HINTS,
        CONTENT_SELECTORS,
        LOG,
        BeautifulSoup,
        MasterView,
        ScrapeConfig,
        clean_text,
        extract_date,
        extract_doc_number,
        extract_issue_organ,
        fetch_gov_attachments,
        make_summary,
    )
except ImportError:  # pragma: no cover  脚本形态
    from gov_constants import (  # type: ignore[no-redef]
        _NOISE_CLASS_HINTS,
        CONTENT_SELECTORS,
        LOG,
        BeautifulSoup,
        MasterView,
        ScrapeConfig,
        clean_text,
        extract_date,
        extract_doc_number,
        extract_issue_organ,
        fetch_gov_attachments,
        make_summary,
    )

try:
    from .gov_session import RobustSession
except ImportError:  # pragma: no cover  脚本形态
    from gov_session import RobustSession  # type: ignore[no-redef]


# ---- 原 collector.py L198-442，逐字搬运 ----
class XzfgkScraper:
    """司法部行政法规库：search2.html 列表 + /front/law/detail 详情。"""

    LIST_URL = "https://xzfg.moj.gov.cn/search2.html"
    DETAIL_URL = "https://xzfg.moj.gov.cn/front/law/detail"

    def __init__(self, cfg: ScrapeConfig, client: RobustSession, master_view=None):
        self.cfg = cfg
        self.client = client
        #: N-200：主库只读视图（由调用方共享；None 时自建——保证**全进程只解析一次**主库）
        self.master_view = master_view
        #: N-190：本次运行的采集统计（与 zhengceku 同口径字段名，便于统一落盘与比较）
        self.stats: dict = {}

    def _list_page(self, page_index: int) -> str | None:
        # 默认页即展示全部现行有效行政法规；分页参数 PageIndex 从 1 开始
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
            href = str(a.get("href", "") or "")   # bs4 的 get 可能返回 AttributeValueList → 收窄为 str
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
        # gov.cn 内容页为 HTML 4.01，lxml 解析会严重丢结构导致正文为空；
        # 改用 html.parser（内置，无需额外依赖）以正确抽取正文。
        soup = BeautifulSoup(html, "html.parser")
        title_el = soup.select_one(".text-title")
        title = clean_text(title_el.get_text()) if title_el else ""

        # 正文：按优先级尝试候选容器（.law-chapter 为行政法规详情正文）
        content_el = None
        for sel in CONTENT_SELECTORS:
            content_el = soup.select_one(sel)
            if content_el:
                break
        if content_el:
            # 剔除下载条 / 历史沿革 / 分享等噪声元素
            for hint in _NOISE_CLASS_HINTS:
                for el in content_el.select(f'[class*="{hint}"]'):
                    el.decompose()
            for el in content_el.select("script, style"):
                el.decompose()
        full_text = clean_text(content_el.get_text()) if content_el else ""

        # 发文字号 / 生效日期 / 发文机关 优先从正文前言抽取
        doc_no = extract_doc_number(full_text)
        # 生效日期：『自……起施行』
        eff = ""
        m = re.search(r"自(.{0,30}?)起施行", full_text)
        if m:
            eff = extract_date(m.group(1))
        if not eff:
            m = re.search(r"(\d{4}[-年]\d{1,2}[-月]\d{1,2}日?)\s*施行", full_text)
            eff = m.group(1) if m else ""
        organ = extract_issue_organ(full_text)
        # 原始公布日期（前言『YYYY年M月D日……公布』）
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

    def run(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        page = 1
        seen_urls = set()
        if self.cfg.resume:
            # N-200：走调用方共享的 MasterView（索引优先、全进程只解析一次）；未传入则自建
            _mv = self.master_view if self.master_view is not None else MasterView(self.cfg.out_dir)
            resumed, _ = _mv.keys()
            seen_urls = set(k[1] for k in resumed if k[1])
            LOG.info("【xzfgk resume】将跳过 %d 条已抓条目（来源：%s）",
                     len(seen_urls), "旁路索引" if _mv.from_index else "全量解析")
        # —— N-190 采集统计（字段名与 zhengceku 同口径，便于统一落盘与跨子源比较）——
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
                # xzfgk 的详情是**逐条随列表抓**（非"先收集再抓"）⇒ 待抓数即新条目数（口径与 zhengceku 对齐）
                "pending_details": new_items,
                "details_fetched": details_fetched,
                "details_failed": 0,
                # xzfgk 的跳过发生在**条目级**（resume 集含 (title,url)），无"已知待补正文"概念 → 0
                "details_skipped_master": 0,
                # 阶段归因：详情耗时**实测累加**，列表时长 = 总时长 − 详情时长（不含 sleep，如实标注）
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
                # 2026-09-04 修复：xzfgk 列表接口分页失效时每页返回同批条目 →
                # 整页全为已见即终止（曾致无限翻页，60 分钟空跑 61+ 页）
                LOG.info("第 %d 页无新增条目（全部已抓或分页失效），停止翻页", page)
                stopped_reason = "no_new_items"
                break
            page += 1
        self.stats = _mk_stats()
        return records

    def _fetch_detail(self, url: str) -> dict:
        # N-67（2026-09-28）：返回值为**异质**字典（str + list（attachments）+ int（count））
        # → 原注解 `dict[str, str]` 过窄（下方 `d["attachment_count"] = len(atts)` 等报 dict-item）。
        detail_html = self.client.get_text(url, referer=self.LIST_URL)
        if not detail_html:
            LOG.warning("详情获取失败：%s", url)
            return {"full_text": "", "document_number": "",
                    "issue_organ": "", "effective_date": "",
                    "attachments": [], "attachment_text": "", "attachment_count": 0}
        try:
            d: dict = self._parse_detail(detail_html)   # N-67：注解置于**首次绑定**处
        except Exception as e:  # noqa: BLE001
            LOG.warning("详情解析失败 %s：%s", url, e)
            d = {"full_text": "", "document_number": "",
                 "issue_organ": "", "effective_date": ""}
        # 修复：抓取详情页附件（Word/PDF/Excel），异常隔离不中断主流程
        try:
            atts, att_text = fetch_gov_attachments(
                detail_html, entry_id=url, entry_title=d.get("title", ""),
                out_dir=self.cfg.out_dir, base_url=url)
            d["attachments"] = atts
            # 批 51/W-A：**不再写记录级聚合副本**（`attachments[].text` 为唯一权威；
            # cleaned 侧由统一映射按真附件派生 attachment_content，交付口径不变）
            d["attachment_count"] = len(atts)
            # 批 50：`attachments` 只保留**真实附件**；详情页的正文载体（「下载Word/PDF」等，其文本已由
            # full_text 承载）移入 `body_docs`，避免把正文当附件重复录入
            from std_lib.scraper_std.attachments import split_record_body_docs
            split_record_body_docs(d)
            atts = list(d.get("attachments") or []) + list(d.get("body_docs") or [])
            # 表格结构化聚合到条目顶层（附件 rec 表字段 → 顶层表键，map_gov 透传 cleaned）
            _tbls = [t for a in atts for t in (a.get("table_structured") or [])]
            if _tbls:
                d["table_structured"] = _tbls
                d["table_recovery_method"] = "structured"
                _raws = [a.get("table_raw_text") for a in atts if a.get("table_raw_text")]
                if _raws:
                    d["table_raw_text"] = "\n\n".join(_raws)
            # 富内容聚合到条目顶层（rich_object 轨，pipeline 透传 cleaned JSONL）
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

__all__ = ["XzfgkScraper"]
