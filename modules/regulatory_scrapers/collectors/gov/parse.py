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
import logging
import os
import sys

try:
    import requests  # noqa: F401
    from bs4 import BeautifulSoup  # noqa: F401
    from requests.adapters import HTTPAdapter  # noqa: F401
    from urllib3.util.retry import Retry  # noqa: F401
except ImportError as e:  # pragma: no cover
    sys.stderr.write(
        "缺少依赖，请先执行：\n"
        "  pip install requests beautifulsoup4 lxml\n"
        f"原始错误：{e}\n"
    )
    sys.exit(2)

# 附件下载 + 文本抽取（修复：gov 此前完全不抓附件）
# W4（L1 重组）：import 双形态（包形态相对导入 / 脚本形态裸名）。
try:
    from .attachments import fetch_gov_attachments
except ImportError:  # pragma: no cover
    try:
        from attachments import fetch_gov_attachments  # type: ignore[no-redef]
    except ImportError:
        def fetch_gov_attachments(*a, **k):  # type: ignore[no-redef,misc]
            return [], ""

LOG = logging.getLogger("gov_scraper")

# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #

# 常用桌面浏览器 UA 池（轮换以降低被识别为脚本的概率）
# （共享库对齐审计 阶段 4）实测：本文件原有 UA 池与 ``crawler_common.USER_AGENTS``
# **逐字相同**（md5 一致、n=5），故改为复用共享库，消除重复副本；
# 沿用 nfra 既有「try 导入 + 缺模块回退单 UA」降级样板，避免缺依赖时启动失败。
_STD_LIB_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _STD_LIB_ROOT not in sys.path:
    sys.path.insert(0, _STD_LIB_ROOT)
try:
    from std_lib.scraper_std.crawler_common import USER_AGENTS
except ImportError:  # pragma: no cover
    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ]


# 默认请求头（模拟真实浏览器）
# 通用缓存模块（五源统一抽象层：std_lib/scraper_std/cache_store，2026-09-05）
# gov xzfgk 列表/详情页均为服务端渲染 HTML → 委托 TextResponseCache（存储 HTML 文本）；
# 命中读盘跳过网络、离线缺失抛 OfflineMiss、仅成功响应（非空）落盘，不缓存错误/拦截页。
try:
    from std_lib.scraper_std.cache_store import OfflineMiss, bind_source_cache  # noqa: F401
except ImportError:  # pragma: no cover
    from std_lib.scraper_std.cache_store import OfflineMiss

_RESP_TEXT = None  # TextResponseCache 实例；None 表示未启用缓存
_OfflineMiss = OfflineMiss  # 兼容别名


SOURCES = {
    # 2026-09-04：仅保留 xzfgk（行政法规库）。flk（国家法律法规数据库）正文存站内
    # OBS 外部不可达、仅元数据无正文，已停止抓取并清理（见 docs/gov正文缺失排查报告）。
    "xzfgk": ("https://www.gov.cn/zhengce/xzfgk/", "行政法规"),
    # 2026-09-15：纳入「国务院政策文件库·国务院部门文件」（zhengceku/bmwj，629 页）
    # 为 gov 源第 2 个子源。此前该栏目完全缺采，导致其下规范性文件（如银发〔2019〕316号）
    # 在 gov 源无任何记录；且该栏目正文常以附件(.doc/.pdf)形式发布，须同时抓附件原文。
    # 实现见 collectors/gov/zhengceku.py（ZhengcekuScraper）。
    "zhengceku": ("https://www.gov.cn/zhengce/zhengceku/bmwj/home.htm", "部门文件"),
}
# ⚠️ 本 dict 为 gov 源子源登记表的**唯一事实源**；collectors/gov/collector.py 从此处导入，
# 不在采集器主文件中重复定义（避免两处漂移，见 2026-09-15 收敛）。


_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRAPERS_ROOT = os.path.dirname(os.path.dirname(_HERE))
# 拆分（E2）后本层有兄弟模块（scrape_config / extract / outputs）：脚本形态直调时
# 裸名 import 需要**本目录**在路径上（单文件时代不需要）。
for _p in (_HERE, _SCRAPERS_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)



#: 主库文件名（N-206 / S-A，2026-10-09）：**改为 JSONL**（`gov_laws.jsonl`）。
#: 为何换格式：`.json` 单数组解析 1GB 主库实测峰值 **5.24~6.13GB**（≈6× 体积：JSON 文本→对象膨胀
#: + 内部全量副本）；JSONL 可**逐行流式**读写 ⇒ 峰值与语料体积解耦（实测流式遍历同体量语料仅 37MB）。
#: 兼容：迁移期若 JSONL 缺失而旧 `gov_laws.json` 仍在，读取侧按其**旧格式流式**解析（见 `_iter_master_records`）。


#: 采集统计落点（相对仓库根）；每源**只保留最新一份**（覆盖写）


#: 统计历史保留条数（每子源一份 jsonl；仅紧凑指标，滚动截断）
#: 进入历史文件的**紧凑指标**（与评估"无效时长"直接相关的字段）


# ---- 拆分（2026-10-16，E2；TD-1 夹具就位后执行）--------------------------------
# 迁出：纯抽取层（8 个函数）→ `extract.py`；落盘层（13 个函数 + MasterView +
#       load_resume + 5 个常量）→ `outputs.py`；配置类型与列契约 → `scrape_config.py`。
# 依赖方向单向（scrape_config ← outputs ← parse），两块依赖闭包经 AST 分析**无交集**。
# **re-export 保持外部调用面不变**（沿用 2026-09-13 P3 既有约定）：`collector.py`
# 仍从本模块取这些符号，无需改动调用点。
# 顺带清一处**死定义**：CONTENT_SELECTORS / TITLE_SELECTORS 在本文件定义但代码零引用
# （仅 docstring 提及），实际使用方是 `collector.py`（其 L391 遍历 CONTENT_SELECTORS）
# ⇒ 删本文件副本，消除同仓重复定义。
try:  # 包形态
    from . import extract as _extract
    from . import outputs as _outputs
    from . import scrape_config as _config
except ImportError:  # 脚本形态
    import extract as _extract  # type: ignore[no-redef]
    import outputs as _outputs  # type: ignore[no-redef]
    import scrape_config as _config  # type: ignore[no-redef]

# re-export（显式赋值，规避 F401/F405 与 mypy no-redef；外部调用面不变）
CSV_COLUMNS = _config.CSV_COLUMNS
DEFAULT_HEADERS = _config.DEFAULT_HEADERS
ScrapeConfig = _config.ScrapeConfig
clean_text = _extract.clean_text
decode_html = _extract.decode_html
extract_date = _extract.extract_date
extract_doc_number = _extract.extract_doc_number
extract_issue_organ = _extract.extract_issue_organ
make_summary = _extract.make_summary
_longest_text_block = _extract._longest_text_block
_is_https_scheme_upgrade = _extract._is_https_scheme_upgrade
MASTER_NAME = _outputs.MASTER_NAME
MASTER_NAME_LEGACY = _outputs.MASTER_NAME_LEGACY
MasterView = _outputs.MasterView
load_resume = _outputs.load_resume
master_path = _outputs.master_path
master_index_path = _outputs.master_index_path
write_outputs = _outputs.write_outputs
write_master_index = _outputs.write_master_index
build_master_index_streaming = _outputs.build_master_index_streaming
merge_and_write = _outputs.merge_and_write
merge_with_master = _outputs.merge_with_master
collect_stats_path = _outputs.collect_stats_path
write_collect_stats = _outputs.write_collect_stats


def build_config(args) -> ScrapeConfig:
    base, category = SOURCES[args.source]
    return ScrapeConfig(
        source=args.source,
        base_url=base,
        category=category,
        out_dir=args.out_dir,
        max_pages=args.max_pages,
        max_items=args.max_items,
        fetch_details=not args.no_details,
        details_limit=args.details_limit,
        resume=args.resume,
        delay_min=args.delay_min,
        delay_max=args.delay_max,
        timeout=args.timeout,
        retries=args.retries,
        summary_len=args.summary_len,
        checkpoint_every=getattr(args, "checkpoint_every", 200),
        # N-190：增量治理参数（老调用方无这些属性时退回默认，保持向后兼容）
        stop_after_empty_pages=int(getattr(args, "stop_after_empty_pages", 1) or 1),
        backfill=bool(getattr(args, "backfill", False)),
        max_seconds=float(getattr(args, "max_seconds", 0.0) or 0.0),
    )


