#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gov_regulations_scraper.py
===========================

自动抓取行政法规库（xzfg.moj.gov.cn）的行政法规条目，并结构化输出（含正文全文）：
  - xzfgk —— 行政法规库（司法部 xzfg.moj.gov.cn）
             列表与详情页均为服务端渲染（HTML），可用 requests 直接抓取；
             详情页含正文全文/发文字号/生效日期/发文机关。

2026-09-04：仅保留 xzfgk 数据源。flk（国家法律法规数据库，flk.npc.gov.cn）
正文原文存储于站内 OBS 外部不可达，仅元数据无正文，已停止抓取并清理相关代码。

通用能力：
  - 会话复用 + 连接池；请求头 / User-Agent 轮换；
  - 随机延时 + 指数退避重试，规避反爬、控制请求频率；
  - 自动遍历分页（?PageIndex= 或 点击"下一页"）；
  - 对每条目二次请求详情页，抽取正文全文、发文字号、生效日期、发文机关；
  - 输出结构化 JSON（含全文）与 CSV 表格（标题/类别/发布日期/正文链接/摘要）；
  - 完善的异常捕获与运行日志，单条失败不影响整体。

用法示例：
  # 完整抓取行政法规库（含全部详情正文），输出 gov_laws.jsonl/.csv（覆盖更新；N-206/S-A 起为 JSONL）
  python scraper.py

  # 快速验证（前 2 页、5 条详情）
  python scraper.py --max-pages 2 --max-items 5

依赖：
  pip install requests beautifulsoup4 lxml
"""

from __future__ import annotations

# ---- P3b 仓库引导（migrate_collectors_p3b.py 注入）：使 std_lib/config 可导入 ----
import os as _os
import sys as _sys

_GUIDE_ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "..", "..", ".."))
if _GUIDE_ROOT not in _sys.path:
    _sys.path.insert(0, _GUIDE_ROOT)
del _GUIDE_ROOT, _os, _sys
# W4（§7.10 问题 5）：logs 落点改引 paths.LOGS_DIR（原为 CWD 相对 "logs" 自拼，
# 链上 cwd=仓根时写仓根 logs/、standalone 时写 CWD/logs——两种语义隐式分叉）。
# import paths 置于**仓根引导之后**（引导前 import 必 ModuleNotFoundError——
# modules standalone 脚本的同款教训）；isort 将其归入 first-party 段（stdlib 之后）。
import argparse
import logging
import os
import sys
import time
from typing import Any

import paths

# ---- 缓存会话层：**已下沉到 `gov_session.py`**（2026-10-20）----
# `_RESP_TEXT` 是**可变全局**，必须与 `RobustSession`/`_init_cache` 同处（见该模块
# docstring）。此处 re-export 只为保持外部导入点不变——注意 `_RESP_TEXT` 取到的
# 是导入时的**引用快照**，真实状态在 `gov_session`；`main()` 只用 `_init_cache()`，
# 不直接读 `_RESP_TEXT`（AST 已确认），故无陈旧读取。
try:
    from .gov_session import (
        _RESP_TEXT,
        RobustSession,
        _init_cache,
        _OfflineMiss,
        set_cache_dir,
        set_offline,
    )
except ImportError:  # pragma: no cover  脚本形态
    from gov_session import (  # type: ignore[no-redef]  # noqa: F401
        _RESP_TEXT,
        RobustSession,
        _init_cache,
        _OfflineMiss,
        set_cache_dir,
        set_offline,
    )


# ---- 常量与依赖别名：**单一出处已下沉到 `gov_constants.py`**（2026-10-20）----
# 本文件仍 re-export 全部符号（`collector` 是兄弟模块/外部脚本的共同入口）。
# ⚠️ **双形态**：包形态 / **脚本形态**（生产 `COLLECT_CMD` 形态）。后者无包上下文，
#    相对导入会抛 `attempted relative import with no known parent package` ⇒ 必须兜底。
try:
    from .gov_constants import (
        _NOISE_CLASS_HINTS,
        _ORGAN_KW,
        _SCHEME_UPGRADE_RE,
        _SCRAPERS_ROOT,
        _STD_LIB_ROOT,
        CONTENT_SELECTORS,
        CSV_COLUMNS,
        DEFAULT_HEADERS,
        LOG,
        SOURCES,
        TITLE_SELECTORS,
        USER_AGENTS,
        BeautifulSoup,
        HTTPAdapter,
        MasterView,
        OfflineMiss,
        Retry,
        ScrapeConfig,
        _cache_ep,
        _is_https_scheme_upgrade,
        _longest_text_block,
        _unified_doc_number,
        bind_source_cache,
        build_config,
        clean_text,
        decode_html,
        extract_date,
        extract_doc_number,
        extract_issue_organ,
        fetch_gov_attachments,
        load_resume,
        make_summary,
        merge_and_write,
        merge_with_master,
        requests,
        write_collect_stats,
        write_outputs,
    )
except ImportError:  # pragma: no cover  脚本形态
    from gov_constants import (  # type: ignore[no-redef]  # noqa: F401
        _NOISE_CLASS_HINTS,
        _ORGAN_KW,
        _SCHEME_UPGRADE_RE,
        _SCRAPERS_ROOT,
        _STD_LIB_ROOT,
        CONTENT_SELECTORS,
        CSV_COLUMNS,
        DEFAULT_HEADERS,
        LOG,
        SOURCES,
        TITLE_SELECTORS,
        USER_AGENTS,
        BeautifulSoup,
        HTTPAdapter,
        MasterView,
        OfflineMiss,
        Retry,
        ScrapeConfig,
        _cache_ep,
        _is_https_scheme_upgrade,
        _longest_text_block,
        _unified_doc_number,
        bind_source_cache,
        build_config,
        clean_text,
        decode_html,
        extract_date,
        extract_doc_number,
        extract_issue_organ,
        fetch_gov_attachments,
        load_resume,
        make_summary,
        merge_and_write,
        merge_with_master,
        requests,
        write_collect_stats,
        write_outputs,
    )










# --------------------------------------------------------------------------- #
# 健壮的 HTTP 客户端
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# 工具函数
# --------------------------------------------------------------------------- #









# --------------------------------------------------------------------------- #
# 数据源 1：行政法规库（xzfgk，服务端渲染，requests 可抓）
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #



# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #

# 子源登记表 SOURCES 由 parse 导入（唯一事实源，见上方 import 块）。


# —— 运行锁统一实现（N-8）：判定逻辑收敛到 regulatory_scrapers/fs_lock.py，四源共用 ——
import atexit

from config.exitcodes import ExitCode
from std_lib.common_lib import fs_lock

# ---- 抓取器：**已下沉到 `gov_scraper.py`**（2026-10-20）----
# 它不读模块级可变状态（会话实例经构造参数传入）⇒ 与 gov_session 无共享状态。
try:
    from .gov_scraper import XzfgkScraper
except ImportError:  # pragma: no cover  脚本形态
    from gov_scraper import XzfgkScraper  # type: ignore[no-redef]

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="抓取 gov.cn 行政法规库（xzfg.moj.gov.cn）行政法规条目（含正文全文）")
    parser.add_argument("--source", choices=["xzfgk", "zhengceku", "all"], default="all",
                        help="数据源：xzfgk=行政法规库；zhengceku=国务院政策文件库·部门文件；"
                             "all=两者依次抓取（默认，gov 源常规采集范围）")
    parser.add_argument("--out-dir",
                        default=os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "raw"),
                        help="输出目录（5b 收敛：默认统一 data/raw/，即 regulatory_scrapers/data/raw）")
    parser.add_argument("--max-pages", type=int, default=0,
                        help="最大翻页数（0=不限制）")
    parser.add_argument("--max-items", type=int, default=0,
                        help="最大条目数（0=不限制，指列表条目）")
    parser.add_argument("--no-details", action="store_true",
                        help="不抓取详情页（仅列表字段，适合全量列表级抓取）")
    parser.add_argument("--details-limit", type=int, default=0,
                        help="仅对前 N 条抓取详情正文（0=全部；调试用采样）")
    parser.add_argument("--resume", action="store_true",
                        help="增量续抓：基于 out_dir 最新同源输出跳过已抓条目，"
                             "避免重复并支持中断后恢复（默认即增量，见 --full）")
    parser.add_argument("--full", action="store_true",
                        help="全量重抓：忽略旧主库、重抓全部列表条目详情（默认增量：跳过已抓条目并合并历史主库）")
    parser.add_argument("--delay-min", type=float, default=1.5,
                        help="请求最小延时（秒）")
    parser.add_argument("--delay-max", type=float, default=3.5,
                        help="请求最大延时（秒）")
    parser.add_argument("--timeout", type=int, default=30, help="请求超时（秒）")
    parser.add_argument("--retries", type=int, default=4, help="重试次数")
    parser.add_argument("--summary-len", type=int, default=200,
                        help="内容摘要字数")
    parser.add_argument("--cache-dir", default="",
                        help="请求缓存目录（显式覆盖）：缺省由 cache_store.source_cache_root(gov) 统一解析"
                             "→ modules/regulatory_scrapers/cache/gov（单物理根）")
    parser.add_argument("--offline", action="store_true",
                        help="纯离线模式：仅读取 --cache-dir 缓存，缓存缺失即跳过（不联网）")
    parser.add_argument("--csv", action="store_true",
                        help="额外输出 CSV 表格视图（默认仅写 JSON 主库，2026-09-09 规范）")
    parser.add_argument("--stop-on-error", action="store_true",
                        help="多子源抓取时任一子源失败即中止（默认跳过失败子源、继续其余并汇总）")
    parser.add_argument("--checkpoint-every", type=int, default=200,
                        help="详情阶段断点落盘间隔（条；0=不落暂存）。zhengceku 全量约 30 小时，"
                             "主库仅在全部结束后写一次，中断必须靠暂存文件续跑（默认 200）")
    # ---- N-190 / R-C（2026-10-08）：增量治理与**子源拆分**参数 ----
    parser.add_argument("--stop-after-empty-pages", type=int, default=1,
                        help="（仅 zhengceku）连续 N 页无**新条目**即停止翻页（默认 1）。"
                             "列表按发布日期倒序 ⇒ 其后各页只会更旧，继续翻页是纯空转（N-190）")
    parser.add_argument("--backfill", action="store_true",
                        help="（仅 zhengceku）历史存量补全：**不早停**、扫完全部列表页，"
                             "补齐\"在主库但尚无正文\"的历史条目（月度作业用，不进周度增量）")
    parser.add_argument("--max-seconds", type=float, default=0.0,
                        help="详情阶段**软预算**（秒；0=不限）。到时停止取新详情并**正常落盘退出**"
                             "（rc=0，stopped_reason=budget）——避免撞上层硬超时被强杀（N-152 教训）")
    parser.add_argument("--env-source", default="",
                        help="覆盖写主库信封 source（子源拆分后保持 gov 信封稳定，见 R-C）")
    parser.add_argument("--env-category", default="",
                        help="覆盖写主库信封 category（同上）")
    parser.add_argument("--log-file", default="",
                        help="日志文件路径（默认输出到控制台与 out-dir/scraper.log）")
    args = parser.parse_args(argv)
    # 增量默认（2026-09-08 周调度增量改造）：resume = 非 --full。显式 --full 才全量重抓。
    args.resume = not args.full

    # 通用缓存（五源统一抽象层）：统一根绑定 + 离线开关
    _init_cache(args.cache_dir or None, args.offline)

    os.makedirs(args.out_dir, exist_ok=True)
    os.makedirs(paths.LOGS_DIR, exist_ok=True)
    log_file = args.log_file or os.path.join(paths.LOGS_DIR, "scraper.log")
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

    # —— 多子源分发（2026-09-15 纳入 zhengceku）——
    # 原实现硬编码 XzfgkScraper；现按 cfg.source 分发，支持 --source all 依次抓取
    # 两个子源并**合并进同一 gov 主库** gov_laws.json（按 detail_url 去重，新优先）。
    # W4（L1 重组）：import 双形态（包形态相对导入 / 脚本形态裸名）。
    try:
        from .zhengceku import ZhengcekuScraper
    except ImportError:  # pragma: no cover - 脚本形态
        from zhengceku import ZhengcekuScraper  # type: ignore[no-redef]

    if args.source == "all":
        want = list(SOURCES)
    else:
        want = [args.source]

    # P0 修复（2026-09-22 周调度发现）：`SOURCES` 中并无 "all" 键，原先在子源分发前
    # 直接 `build_config(args)` 会 KeyError('all') → `--source all`（**即默认值**）与
    # 无参调用必然崩溃（2026-09-15 纳入 zhengceku 时引入的回归）。
    # 现按**首个子源**构建公共配置：RobustSession 的超时/重试/延时以及 resume/out_dir
    # 均与子源无关；子源专属 cfg 仍在下方循环内按 `scfg = build_config(sub)` 重建。
    _cfg_args = argparse.Namespace(**vars(args))
    _cfg_args.source = want[0]
    cfg = build_config(_cfg_args)
    client = RobustSession(cfg)

    # resume：只跳过「已有正文」的 detail_url（无正文的历史记录仍需重抓补全）
    seen_urls = set()
    # N-190：主库**全部** detail_url —— 增量早停的判据（"新条目" = 不在主库者）。
    known_urls = set()
    master_view: MasterView | None = None
    if cfg.resume:
        # N-200：主库**只读视图**（旁路索引优先；需要正文时才全量解析，且**全进程只解析一次**）
        master_view = MasterView(cfg.out_dir)
        _seen, detailed = master_view.keys()
        seen_urls = set(detailed)
        known_urls = set(k[1] for k in _seen if k[1])
        LOG.info("【增量判据】主库 %d 条（已有正文 %d 条）：不在主库者=**新条目**（驱动翻页），"
                 "在主库而无正文者=**待补详情**（下次续跑补齐）；来源=%s",
                 len(known_urls), len(seen_urls),
                 "旁路索引（未解析正文）" if master_view.from_index else "全量解析")

    records: list[dict[str, Any]] = []
    failed: list[str] = []
    sub_stats: dict[str, dict] = {}
    scrapers: dict[str, Any] = {}
    for s in want:
        sub = argparse.Namespace(**vars(args))
        sub.source = s
        scfg = build_config(sub)
        LOG.info("开始抓取子源：%s（%s）", scfg.source, scfg.category)
        # 显式 `Any`：两个子源采集器类型不同（XzfgkScraper / ZhengcekuScraper），
        # 且仅在 try 内构造 ⇒ 用 Any 让 mypy 可推断下游 `recs`/`records`（否则 var-annotate 报错）
        scraper: Any = None
        try:
            if s == "xzfgk":
                scraper = XzfgkScraper(scfg, client, master_view)
            else:
                scraper = ZhengcekuScraper(scfg, client, seen_urls, known_urls)
            recs = scraper.run()
        except Exception as e:  # noqa: BLE001
            # 意图声明（本仓规范）：**单个子源失败不得拖垮整条 gov 采集**
            # ——其余子源仍应产出，失败者记入 `failed` 并在退出码/统计里体现。
            # 故此处是「按设计隔离」的宽泛捕获，不是忘记收窄。
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

    # —— N-190：采集统计落盘（**每子源一份**；旁路观测，失败不影响采集）——
    # ⚠️ 必须在**所有返回路径**上都执行：本批实测发现"无新条目"是最常见情形（周度空跑），
    #    而它恰是评估"无效时长"最需要的样本 —— 若只在写库后统计，就会**永远看不到**这类样本。
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
                    "new_records": None,       # 由各子源 stats.new_items 表达（口径不重复）
                    # R-F 处置（2026-10-09）：把"怎么读这个指标"写进数据本身，避免误读。
                    "metric_note": ("空转占比 = list_pages_without_new / list_pages_fetched；"
                                    "list_pages_fetched 很小时（如 1 页）该比例无独立意义，"
                                    "须结合**绝对量**（页数 × 单页耗时）评估无效时长"),
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

    # 信封 source/category：单子源沿用其自身标识；all 时标为 gov 汇总。
    # R-C/N-190：`--env-source/--env-category` 可**显式覆盖** —— 子源拆分后"单子源单跑"成为常态，
    #   若不做覆盖，最后一次子源的标识会写进主库信封（下游按 source 判别时不一致）。
    env_source = args.env_source or (cfg.source if len(want) == 1 else "gov")
    env_cat = args.env_category or (cfg.category if len(want) == 1 else "行政法规+部门文件")
    cfg.source, cfg.category = env_source, env_cat

    merge_stat: dict | None = None
    if cfg.resume and master_view is not None:
        # 增量续抓：本次 records 仅含新发现条目 → 与现主库合并后落盘，防丢历史。
        # S-B（N-206，2026-10-09）：改走 **`merge_and_write` 流式合并** —— 旧主库**逐条读**、
        #   命中同键用新记录覆盖、**逐条写** `.tmp`（峰值＝新记录+键集，与主库体积解耦）。
        #   原路径 `merge_with_master(...)` 需把整表（1GB 级）读进内存 ⇒ 与 clean 同源的
        #   6× 瞬时峰值（实测 clean 侧 5.24~6.13GB）。
        merge_stat = merge_and_write(records, cfg, source_label=env_source, write_csv=args.csv)
        out_paths = {"json": merge_stat["json"], "csv": merge_stat["csv"]}
    else:
        out_paths = write_outputs(records, cfg, source_label=env_source, write_csv=args.csv)
    # N-192（2026-10-08）：主库**已原子落盘** → 清理子源的续跑暂存（其记录已并入主库）。
    # 不清的代价（实测）：每轮增量白付"载入 899.7MB / 12573 条 + 全量合并 ~10 分钟"且产出零变化。
    # 反例保护：若上一步写库失败/进程被杀，此处不会执行 ⇒ 暂存保留，下次仍可续跑。
    for _sc in scrapers.values():
        if hasattr(_sc, "clear_partial"):
            _sc.clear_partial()
    _emit_stats()
    LOG.info("成功抓取 %d 条（子源：%s；失败：%s）。文件：%s",
             len(records), ",".join(want), ",".join(failed) or "无", out_paths)
    return ExitCode.OK

if __name__ == "__main__":
    sys.exit(main())
