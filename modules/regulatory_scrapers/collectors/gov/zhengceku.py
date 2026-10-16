# -*- coding: utf-8 -*-
"""collectors/gov/zhengceku.py —— gov 源「国务院政策文件库·国务院部门文件」子采集器。

【纳入常规采集范围（2026-09-15）】
  此前 gov 源只有 `xzfgk` 一个子源（= `www.gov.cn/zhengce/xzfgk/` 行政法规库，详情落司法部
  `xzfg.moj.gov.cn`），**「国务院政策文件库·国务院部门文件」
  `https://www.gov.cn/zhengce/zhengceku/`（列表 `bmwj/home.htm`，629 页 × 28 条）从未纳入采集**，
  导致该栏目下的规范性文件在 gov 源完全缺采；且该栏目详情页正文常以附件（.doc/.pdf）形式发布，
  页面正文为空或仅一个文件名，不抓附件则正文仅数十字，无法支撑条款级引用核验。
  本模块把该栏目补齐为 gov 源的第 2 个子源，产物并入 gov 主库 `gov_laws.jsonl`（N-206/S-A 起为 JSONL），
  由统一清洗管道 `clean/run_clean_pipeline.py --project gov` 正常消费。

【与 gov 主链的契约一致性（务必遵守）】
  记录字段与 `XzfgkScraper` 完全同构，供 `std_lib.scraper_std.unified_schema.map_gov` 读取：
    title / detail_url / publish_date / pub_date_original / effective_date / issue_organ /
    document_number / category / source / full_text / summary /
    attachments / attachment_text / attachment_count
  ⚠️ 正文键必须是 `full_text`（不是 body_text）、详情链接键必须是 `detail_url`（不是 source_url），
  否则 `map_gov` 取不到值，cleaned 层正文落空。
  `source="zhengceku"` 经 `config/enums.SOURCE_ALIASES` 归并为 `gov`，子源名进 `_raw_fields.子源`。

用法（通常由 collectors/gov/collector.py 分发调用，也可独立单条补录）：
  python collectors/gov/collector.py --source zhengceku              # 常规增量（resume 默认开）
  python collectors/gov/collector.py --source zhengceku --full       # 全量（629 页）
  python collectors/gov/zhengceku.py --url <详情页URL>                # 单条补录（独立入口，便于人工核验）
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
# 引导块**自包含**：原实现依赖后面才 re-export 的 `_SRC_ROOT`/`_REPO_ROOT`
# ⇒ 引导先执行时 NameError（E3 拆分后暴露）。改为就地现算路径。
_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC_ROOT_P = os.path.dirname(_HERE)
_REPO_ROOT_P = os.path.dirname(_SRC_ROOT_P)
for _p in (_HERE, _SRC_ROOT_P, _REPO_ROOT_P):
    if _p not in sys.path:
        sys.path.insert(0, _p)

LOG = logging.getLogger("zhengceku")

# 列表：首页无后缀，第 i 页为 home_{i}.htm（0-based，实测 nPageCount=629）
CATEGORY = "部门文件"
SUB_SOURCE = "zhengceku"





#: 增量早停阈值缺省：连续 1 页无「新条目」即停（N-190，2026-10-08）
DEFAULT_STOP_AFTER_EMPTY_PAGES = 1
















# --------------------------------------------------------------------------- #
# 附件处理模式（全量采集提速 / 减重）
# --------------------------------------------------------------------------- #
# `ZC_ATTACH_FAST=1` → 正文已完整的条目**不下载、不抽取附件原文**（正文不足者仍完整处理）。
# 依据：附件原文（OCR + Word COM）是全量采集的耗时与体积主因——实测 1600 条暂存 516 MB
# （均 322 KB/条），全量 1.22 万条约需 5.5 GB，内存压力是进程中断的首要嫌疑。
# 默认关闭（保持常规增量采集行为不变）；如需附件原文，对目标条目用
# `python collectors/gov/zhengceku.py <url>` 单条补录即可（该入口始终完整抽取）。
_skipped = [0]                 # 本次运行跳过附件抽取的条目计数（供收尾日志）


# --------------------------------------------------------------------------- #
# 列表
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# 详情页
# --------------------------------------------------------------------------- #
# 「值」其实是另一个字段标签时的识别（gov.cn 页面字段区相邻，取空值会串到下一标签）
# 文号形态：〔2009〕68号 / 令〔2010〕4号 / 公告〔2026〕第9号 / 银保监办发〔2020〕118号 / 保监发〔2012〕87号










# --------------------------------------------------------------------------- #
# 附件（下载 + 原文抽取；旧 .doc 优先 Word COM）
# --------------------------------------------------------------------------- #












# --------------------------------------------------------------------------- #
# 采集器（与 XzfgkScraper 同构，供 collector 分发）
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# E3 拆分（2026-10-16，D-遗留第三批）：列表层 / 详情层 / 附件层迁出
#   列表层 → zhengceku_list（分页 URL / 停翻页 / 总页数 / 列表抓取 / HTML 清洗 / 摘要）
#   详情层 → zhengceku_detail（文号 / meta 字段 / 正文选择 / parse_detail）
#   附件层 → zhengceku_attach（Word COM / 表格层级 / CJK 阈值 / 附件下载）
# 依赖方向：detail → list；attach 独立；本模块（编排）依赖三者。
# **re-export 保持外部调用面不变**（P3 既有约定）：`gov_collector.py` 从本模块取
# `ZhengcekuScraper`；`tools/run_production_refresh.py` 的 N-190 判据引用
# `zhengceku.should_stop_paging` —— 外部调用点一行未改。
try:  # 包形态
    from . import zhengceku_attach as _attach
    from . import zhengceku_detail as _detail
    from . import zhengceku_list as _list
except ImportError:  # 脚本形态
    import zhengceku_attach as _attach  # type: ignore[no-redef]
    import zhengceku_detail as _detail  # type: ignore[no-redef]
    import zhengceku_list as _list  # type: ignore[no-redef]

# 列表层
CONTENT_SELECTORS = _detail.CONTENT_SELECTORS
LIST_BASE = _list.LIST_BASE
UA = _list.UA
_REPO_ROOT = _list._REPO_ROOT
_TAG = _list._TAG
_WS = _list._WS
_BLANK = _list._BLANK
_A = _list._A
_LI = _list._LI
_DATE = _list._DATE
clean_html_text = _list.clean_html_text
fetch = _list.fetch
list_page_url = _list.list_page_url
make_summary = _list.make_summary
scan_list = _list.scan_list
should_stop_paging = _list.should_stop_paging
total_pages = _list.total_pages
_iso = _list._iso
_to_text = _list._to_text
# 详情层
_META = _detail._META
_DOCNO_RE = _detail._DOCNO_RE
_LABEL_LIKE = _detail._LABEL_LIKE
parse_detail = _detail.parse_detail
pick_content_html = _detail.pick_content_html
_extract_docno = _detail._extract_docno
_meta_field = _detail._meta_field
# 附件层
_SRC_ROOT = _attach._SRC_ROOT
_WORD_APP = _attach._WORD_APP
_WORD_DEAD = _attach._WORD_DEAD
_ATTACH_FAST = _attach._ATTACH_FAST
_FAST_MIN_BODY = _attach._FAST_MIN_BODY
_word_app = _attach._word_app
extract_doc_via_word = _attach.extract_doc_via_word
_table_top_level = _attach._table_top_level
_cjk_ratio = _attach._cjk_ratio
fetch_attachments = _attach.fetch_attachments


class ZhengcekuScraper:
    """国务院政策文件库·部门文件（bmwj）子采集器。"""

    def __init__(self, cfg, client=None, seen_urls=None, known_urls=None):
        self.cfg = cfg
        self.client = client
        # 已抓 detail_url 集合（由 collector 从主库 load_resume 取得）→ --resume 增量跳过
        self.seen_urls = seen_urls if seen_urls is not None else set()
        # N-190：主库**全部** detail_url（含**尚无正文**者）——用于区分两类条目：
        #   · **新条目**   = detail_url ∉ known_urls → **增量早停的判据**（连续无新即停翻页）
        #   · **已知待补** = ∈ known_urls 且 ∉ seen_urls（无正文）→ 仍抓详情（顶部窗口内自然补全）
        # 未传入（老调用方）时退化为**空集** ⇒ 一切条目按"新"计 ⇒ **不早停**（与旧行为等价，防误停）。
        self.known_urls = known_urls if known_urls is not None else set()
        #: 本次运行的**采集统计**（供上游写 `data/run_state/collect_stats/*.json`，见 N-190）
        self.stats: dict = {}

    def _list_url(self, index: int) -> str:
        return list_page_url(index)

    def _parse_list(self, html: str, base_url: str):
        return scan_list(html, base_url)

    def _partial_path(self) -> str:
        """断点续跑暂存文件（位于 out_dir，即 data/raw/ 内，不新增 data 子目录）。"""
        return os.path.join(self.cfg.out_dir, "_zhengceku_partial.json")

    def _load_partial(self) -> list:
        """载入上次未合并的已抓记录（进程中断后的续跑基线）。"""
        p = self._partial_path()
        if not os.path.exists(p):
            return []
        try:
            recs = json.load(open(p, encoding="utf-8"))
            if isinstance(recs, list) and recs:
                LOG.info("【续跑】从 %s 载入上次未合并的 %d 条", os.path.basename(p), len(recs))
                for r in recs:
                    if r.get("detail_url"):
                        self.seen_urls.add(r["detail_url"])
                return recs
        except Exception as e:  # noqa: BLE001
            LOG.warning("【续跑】暂存文件读取失败，忽略：%s", e)
        return []

    def clear_partial(self) -> bool:
        """清理**已消费**的续跑暂存（N-192，2026-10-08）。

        为何必须清（实测依据）：`_load_partial` 会把暂存**全量载回**并计入本次 `records`，
        再由 `merge_with_master` 逐条去重合并 —— 若暂存在成功落库后**仍保留**，
        此后**每一轮增量**都要白付一次"载入 899.7MB / 12573 条 + 全量合并"的代价
        （本轮实测 merge 阶段 **~10 分钟**，占采集步总时长的一半以上，且**产出零变化**）。

        时序安全性：本方法**只在主库写成功之后**被调用（见 `collector.main`）——
        主库是**原子替换**（`.tmp` + `os.replace`）写出的，写成功即数据已持久化，
        此时暂存已完全冗余（其记录已并入主库）⇒ 删除不会丢数据；
        反之若主库写失败/进程被杀，暂存**必须保留**（下次续跑的唯一依据）⇒ 故不在别处清理。
        """
        p = self._partial_path()
        removed = False
        for cand in (p, p + ".tmp"):
            try:
                if os.path.exists(cand):
                    os.remove(cand)
                    removed = True
            except OSError as e:
                LOG.warning("【续跑】暂存清理失败（不影响主库）：%s", e)
        if removed:
            LOG.info("【续跑】主库已落盘 → 清理已消费的续跑暂存 %s", os.path.basename(p))
        return removed

    def _save_partial(self, records: list) -> None:
        """原子落盘已抓记录。

        ⚠️ 必要性：`parse.write_outputs` 只在**全部结束后**写一次主库，
        本栏目全量约 1.7 万条 / 30 小时，中途任何中断（网络、进程被杀、机器重启）
        都会让已抓数据**全部丢失**。故详情阶段按 `checkpoint_every` 周期落盘暂存，
        下次运行先载入暂存再续抓，最后统一并入主库。
        """
        p = self._partial_path()
        tmp = p + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(records, f, ensure_ascii=False)
            os.replace(tmp, p)
        except Exception as e:  # noqa: BLE001
            LOG.warning("暂存落盘失败：%s", e)

    def run(self) -> list:
        cfg = self.cfg
        max_pages = getattr(cfg, "max_pages", 0) or 0
        max_items = getattr(cfg, "max_items", 0) or 0
        no_details = not getattr(cfg, "fetch_details", True)
        delay = getattr(cfg, "delay_min", 1.5)
        # checkpoint 间隔（条）；0=不落暂存。来自 --checkpoint-every，缺省 200。
        ckpt = int(getattr(cfg, "checkpoint_every", 200) or 0)
        carried = self._load_partial() if ckpt else []
        # N-190 增量治理参数（缺省安全：不早停的旧行为不会造成漏抓，只会多花时间）
        backfill = bool(getattr(cfg, "backfill", False))
        stop_after = int(getattr(cfg, "stop_after_empty_pages", DEFAULT_STOP_AFTER_EMPTY_PAGES) or 1)
        budget = float(getattr(cfg, "max_seconds", 0.0) or 0.0)
        if backfill:
            LOG.info("【backfill】历史存量补全模式：不早停，将扫完全部列表页")
            # R-C 处置（2026-10-09）：**待补为 0 时直接短路**——补全模式的唯一目的是补正文，
            # 而"待补集"在**离线**即可由主库判定（known_urls − seen_urls，二者均来自旁路索引/主库）。
            # 实测（批 45）：主库 13178 条**全部已有正文** ⇒ 原实现仍会空扫 629 页（约 25 分钟/次）。
            # 注意：仅在 `known_urls` 非空（即由调用方传入）时短路——否则无从判断，保持原行为。
            _pending_possible = self.known_urls - self.seen_urls if self.known_urls else None
            if self.known_urls and not _pending_possible:
                LOG.info("【backfill】主库 %d 条**均已有正文**（待补 0 条）→ 不翻页直接结束"
                         "（stopped_reason=nothing_to_backfill）", len(self.known_urls))
                self.stats = {
                    "sub_source": SUB_SOURCE, "list_pages_fetched": 0, "list_pages_without_new": 0,
                    "list_items_seen": 0, "new_items": 0, "pending_details": 0,
                    "details_fetched": 0, "details_failed": 0, "details_skipped_master": 0,
                    "elapsed_list_s": 0.0, "elapsed_detail_s": 0.0,
                    "stopped_reason": "nothing_to_backfill", "backfill": True, "budget_s": budget,
                    "list_pages_total": None, "list_pages_planned": None,
                    "master_known": len(self.known_urls), "master_detailed": len(self.seen_urls),
                }
                return []

        first = fetch(self._list_url(0))
        n_pages = total_pages(first)
        if max_pages:
            n_pages = min(n_pages, max_pages)
        LOG.info("zhengceku 栏目：共 %d 页，本次抓取 %d 页", total_pages(first), n_pages)

        seen = self.seen_urls
        known = self.known_urls
        n_listed = 0                  # 列表命中总数（含已跳过）
        n_new = 0                     # 本次列表发现的**新条目**（detail_url 不在主库）
        pages_fetched = 0             # 实际成功解析的列表页数
        pages_without_new = 0         # **无新条目**的列表页数（"空转页"——无效时长的直接来源）
        empty_streak = 0
        pending = []
        stopped_reason = "swept_all" if backfill else "end"
        t_list0 = time.time()
        for i in range(n_pages):
            html = first if i == 0 else fetch(self._list_url(i))
            if not html:
                LOG.warning("列表页 %d 抓取失败，跳过", i)
                continue
            pages_fetched += 1
            items = self._parse_list(html, self._list_url(i))
            n_listed += len(items)
            new_in_page = 0
            for u, t, d in items:
                if u not in known:        # N-190：新条目判定（主库**无**此 detail_url）
                    known.add(u)
                    new_in_page += 1
                    n_new += 1
                if u in seen:             # 主库已有正文 → **不发详情请求**（省一次网络往返）
                    continue
                seen.add(u)
                pending.append((u, t, d))
            if new_in_page:
                empty_streak = 0
            else:
                empty_streak += 1
                pages_without_new += 1
            LOG.info("列表页 %d/%d → 命中 %d 条（新 %d），累计待抓 %d 条",
                     i + 1, n_pages, len(items), new_in_page, len(pending))
            if max_items and len(pending) >= max_items:
                stopped_reason = "max_items"
                break
            if should_stop_paging(empty_streak, stop_after, backfill):
                stopped_reason = "no_new_items"
                LOG.info("连续 %d 页无新条目 → 停止翻页（增量模式，N-190）；"
                         "历史存量补全请用 --backfill（独立排程）", empty_streak)
                break
            if delay and i + 1 < n_pages:
                time.sleep(delay)
        elapsed_list = time.time() - t_list0
        if max_items:
            pending = pending[:max_items]
        LOG.info("列表级完成：翻页 %d/%d 页（无新页 %d），命中 %d 条，本次待抓 %d 条"
                 "（主库既有正文跳过 %d 条）",
                 pages_fetched, n_pages, pages_without_new, n_listed, len(pending),
                 n_listed - len(pending))

        if no_details:
            # 仅列表字段：正文留空会导致 clean 层 core 空值率超阈值 → 显式标记，不参与主库合并由调用方决定
            LOG.warning("--no-details：仅产出列表级记录（无正文），请勿直接并入主库")
            self.stats = {
                "sub_source": SUB_SOURCE, "list_pages_fetched": pages_fetched,
                "list_pages_without_new": pages_without_new, "list_items_seen": n_listed,
                "new_items": n_new, "pending_details": len(pending), "details_fetched": 0,
                "details_failed": 0, "details_skipped_master": n_listed - len(pending),
                "elapsed_list_s": round(elapsed_list, 1), "stopped_reason": "no_details",
                "backfill": backfill, "budget_s": budget, "list_pages_planned": n_pages,
                "list_pages_total": total_pages(first), "elapsed_detail_s": 0.0,
            }
            return [{"title": t, "detail_url": u, "publish_date": d, "pub_date_original": "",
                     "effective_date": "", "issue_organ": "", "document_number": "",
                     "category": CATEGORY, "source": SUB_SOURCE, "full_text": "",
                     "summary": "", "attachments": [], "attachment_text": "",
                     "attachment_count": 0} for u, t, d in pending]

        records = list(carried)
        if carried:
            LOG.info("【续跑】本次在此基础上继续，已计入 %d 条", len(carried))
        details_fetched = 0
        details_failed = 0
        t_det0 = time.time()
        for k, (u, t, d) in enumerate(pending, 1):
            # N-190：**软预算**（--max-seconds）——到时停止取新详情并**正常落盘退出**（rc=0）。
            # 为何要它：撞上层 7200s 硬超时会被强杀，而"未走完的产物"仍会落盘并被下游采用（N-152）。
            if budget and (time.time() - t_det0) >= budget:
                stopped_reason = "budget"
                LOG.warning("已达 --max-seconds=%s 预算 → 停止详情抓取（已抓 %d 条正常落盘，"
                            "剩余 %d 条下次续跑）", budget, len(records), len(pending) - k + 1)
                break
            try:
                rec = self.fetch_one(u, title_hint=t, date_hint=d)
            except Exception as e:  # noqa: BLE001
                details_failed += 1
                LOG.warning("详情页失败 %s：%s", u, e)
                continue
            details_fetched += 1
            records.append(rec)
            if k % 20 == 0 or k == len(pending):
                LOG.info("详情进度 %d/%d（累计 %d 条；最近：%s）",
                         k, len(pending), len(records), rec["title"][:34])
            if ckpt and len(records) % ckpt == 0:
                self._save_partial(records)
                LOG.info("【checkpoint】已落暂存 %d 条", len(records))
            if delay:
                time.sleep(delay)
        elapsed_detail = time.time() - t_det0
        if ckpt and records:
            self._save_partial(records)      # 收尾落盘：保证最后不足一批的增量也持久化
        if _ATTACH_FAST and _skipped[0]:
            LOG.info("【提速模式】本次跳过附件原文抽取 %d 条（正文≥%d 字）；"
                     "如需附件原文，对目标条目用 collectors/gov/zhengceku.py <url> 单条补录",
                     _skipped[0], _FAST_MIN_BODY)
        # —— N-190 采集统计（供上游落 data/run_state/collect_stats/*.json）——
        # 关键指标：pages_without_new / pages_fetched = **空转页占比**（本次优化要压的就是它）；
        #          details_skipped_master = 因主库已有正文而**未发请求**的条目数（省下的往返）。
        self.stats = {
            "sub_source": SUB_SOURCE,
            "list_pages_total": total_pages(first),
            "list_pages_planned": n_pages,
            "list_pages_fetched": pages_fetched,
            "list_pages_without_new": pages_without_new,
            "list_items_seen": n_listed,
            "new_items": n_new,
            "pending_details": len(pending),
            "details_fetched": details_fetched,
            "details_failed": details_failed,
            "details_skipped_master": n_listed - len(pending),
            "elapsed_list_s": round(elapsed_list, 1),
            "elapsed_detail_s": round(elapsed_detail, 1),
            "stopped_reason": stopped_reason,
            "backfill": backfill,
            "budget_s": budget,
        }
        return records

    def fetch_one(self, url: str, title_hint: str = "", date_hint: str = "") -> dict:
        """抓取单条详情（含附件原文），产出 gov 主库契约记录。"""
        html = fetch(url)
        if isinstance(html, bytes):
            html = html.decode("utf-8", "replace")
        d = parse_detail(html, url)
        if not d["title"]:
            d["title"] = title_hint
        if not d["publish_date"]:
            d["publish_date"] = date_hint
        try:
            if _ATTACH_FAST and len(d["full_text"]) >= _FAST_MIN_BODY:
                # 提速/减重模式：正文已完整时不下载、不抽取附件原文。
                # 依据：附件原文（OCR + Word COM）是全量采集的耗时与体积主因——
                # 实测 1600 条暂存达 516 MB（均 322 KB/条），全量 1.22 万条约需 5.5 GB，
                # 内存压力是进程中断的首要嫌疑；且正文已完整的条目无需附件补正文。
                # 仅当正文不足（正文以附件形式发布）时才做完整抽取，保证正文不缺失。
                atts: list = []
                att_text: str = ""
                _skipped[0] += 1
            else:
                atts, att_text = fetch_attachments(html, url, entry_id=url)
        except Exception as e:  # noqa: BLE001
            LOG.warning("附件处理失败 %s：%s", url, e)
            atts, att_text = [], ""
        body = d["full_text"]
        # 正文以附件形式发布（页面正文为空/仅文件名）→ 以附件原文补足
        if len(body) < 200 and len(att_text) > len(body):
            body = att_text
        rec = {
            "title": d["title"],
            "detail_url": url,
            "publish_date": d["publish_date"],
            "pub_date_original": d["pub_date_original"],
            "effective_date": "",
            "issue_organ": d["issue_organ"],
            "document_number": d["document_number"],
            "category": CATEGORY,
            "source": SUB_SOURCE,
            "full_text": body,
            "attachments": atts,
            # 批 51/W-A：记录级聚合副本不再写（见 collector 同注）
            "attachment_count": len(atts),
            "summary": make_summary(body),
        }
        # 批 50：`attachments` 只保留**真实附件**；正文载体（「下载Word/PDF」等）移入 `body_docs`
        from std_lib.scraper_std.attachments import split_record_body_docs
        split_record_body_docs(rec)
        rec.update(_table_top_level(list(rec["attachments"]) + list(rec.get("body_docs") or [])))
        return rec


def build_record(url: str, with_attachments: bool = True) -> dict:
    """独立入口：单条 URL 补录（便于人工核验），返回 gov 主库契约记录。"""
    class _Cfg:
        pass
    return ZhengcekuScraper(_Cfg()).fetch_one(url)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="gov 源「国务院政策文件库·部门文件」子采集器（单条补录入口；批量请用 collectors/gov/collector.py）")
    ap.add_argument("--url", required=True, help="详情页 URL")
    ap.add_argument("--out", default="", help="输出 JSON 路径（可选，默认仅打印）")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    rec = build_record(args.url)
    print(f"[ok] {rec['document_number'] or '(无文号)'} | 正文 {len(rec['full_text'])} 字 | "
          f"附件 {rec['attachment_count']} 个（附件原文 {len(rec['attachment_text'])} 字） | {rec['title'][:50]}")
    if args.out:
        json.dump([rec], open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("[out]", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
