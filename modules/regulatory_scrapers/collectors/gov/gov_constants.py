# -*- coding: utf-8 -*-
"""gov/gov_constants — gov 采集的**依赖引导 + 纯常量**（无业务逻辑、无可变状态）。

内容由脚本从 `collector.py` **按行区间逐字提取**（2026-10-20 第一百零四批），
不是手抄——实测手抄会把 4 个常量的值全写错（`_ORGAN_KW` / `_NOISE_CLASS_HINTS` /
`_SCHEME_UPGRADE_RE` / `CSV_COLUMNS`）。等价性由 `tests/collector_baseline.py --source gov`
的结构快照（模块名 / 常量摘要 / 方法签名 / CLI 选项）锁定。

为什么拆：`collector.py` 841 行里前 204 行几乎全是被 `RobustSession`(99) /
`XzfgkScraper`(245) / `main`(227) 共用的「引导 + try-import 兜底 + 常量」，且本文件
有 **6 处 try-import 别名**（`requests`/`BeautifulSoup`/`HTTPAdapter`/`Retry`/
`fetch_gov_attachments`/`USER_AGENTS`/`_unified_doc_number`/`OfflineMiss`）——
赋值在 `try` 内，**AST 依赖闭包捕获不到**，任何拆分都必须显式搬运。

**刻意不搬**（留在 `collector.py`）：
  · `_RESP_TEXT` —— 跨调用可变全局（`_init_cache` 用 `global` 改它）；
  · `_init_cache`/`set_cache_dir`/`set_offline` —— 与 `_RESP_TEXT` 必须同处；
  · **仓库引导**（`_GUIDE_ROOT` + `sys.path.insert`）—— 它是每个文件的**运行前提**
    而非常量：`collector.py` 要继续 `import paths`（v2 实测：删掉后立刻
    `ModuleNotFoundError: No module named 'paths'`），本文件也要 import 仓内模块
    ⇒ 两份并存，`if not in sys.path` 幂等，**不是重复缺陷**。

**双形态**：被包形态与**脚本形态**（`python collectors/gov/collector.py`）触达。
"""
from __future__ import annotations

import logging
import os

# ---- 仓库引导（原 collector.py L36-43，逐字搬运；与 collector 中的那份并存且幂等）----
# ---- P3b 仓库引导（migrate_collectors_p3b.py 注入）：使 std_lib/config 可导入 ----
import os as _os
import re
import sys
import sys as _sys

_GUIDE_ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "..", "..", ".."))
if _GUIDE_ROOT not in _sys.path:
    _sys.path.insert(0, _GUIDE_ROOT)
del _GUIDE_ROOT, _os, _sys

# ---- A2 try-import + LOG + USER_AGENTS + doc_number + cache_store（原 collector.py L61-120，逐字搬运）----
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
# W4（L1 重组）：兄弟模块 import 双形态——包形态（相对导入）/ 脚本形态
# （`python collectors/gov/collector.py` 直调时本目录为 sys.path[0]）。
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

# 统一文号提取器（2026-09-05 五源共享：doc_number 模块，15+ 优先级正则 + 规范化）
try:
    from std_lib.scraper_std.doc_number import extract_doc_number as _unified_doc_number
except ImportError:  # pragma: no cover
    _unified_doc_number = None  # type: ignore[assignment]

# 默认请求头（模拟真实浏览器）
# 通用缓存模块（五源统一抽象层：std_lib/scraper_std/cache_store，2026-09-05）
# gov xzfgk 列表/详情页均为服务端渲染 HTML → 委托 TextResponseCache（存储 HTML 文本）；
# 命中读盘跳过网络、离线缺失抛 OfflineMiss、仅成功响应（非空）落盘，不缓存错误/拦截页。
try:
    # `OfflineMiss` 在此**为 re-export 而导入**（`collector.py` 从本模块取它做
    # `_OfflineMiss` 兼容别名）⇒ 加 noqa 是正当用途，不是死导入。
    from std_lib.scraper_std.cache_store import OfflineMiss, bind_source_cache
except ImportError:  # pragma: no cover
    from std_lib.scraper_std.cache_store import OfflineMiss, bind_source_cache  # noqa: F401

# ---- A3 parse re-export（原 collector.py L126-152，逐字搬运）----
# ---- 拆分（2026-09-13，P3）：下列符号迁 gov/parse，re-export 保持外部调用兼容 ----
# W4（L1 重组）：兄弟模块 import 双形态——包形态（相对导入）/ 脚本形态（裸名，
# 此时 collectors/gov/ 为 sys.path[0]）。经**模块对象**再取符号 ⇒ 名单只此一份
# （双份名单 = 双份维护点，双形态各写一遍 import 名单必然漂移）。
try:
    from . import parse as _parse
except ImportError:  # pragma: no cover - 脚本形态
    import parse as _parse  # type: ignore[no-redef]

# re-export（显式赋值，规避 F401/F405；外部调用面不变）
SOURCES = _parse.SOURCES  # 子源登记表唯一事实源在 gov/parse（2026-09-15 收敛，勿在别处重复定义）
MasterView = _parse.MasterView
ScrapeConfig = _parse.ScrapeConfig
_is_https_scheme_upgrade = _parse._is_https_scheme_upgrade
_longest_text_block = _parse._longest_text_block
build_config = _parse.build_config
clean_text = _parse.clean_text
decode_html = _parse.decode_html
extract_date = _parse.extract_date
extract_doc_number = _parse.extract_doc_number
extract_issue_organ = _parse.extract_issue_organ
load_resume = _parse.load_resume
make_summary = _parse.make_summary
merge_and_write = _parse.merge_and_write
merge_with_master = _parse.merge_with_master
write_collect_stats = _parse.write_collect_stats
write_outputs = _parse.write_outputs

# ---- A4 _cache_ep + HEADERS + SELECTORS（原 collector.py L172-204，逐字搬运）----
# N-167：`_cache_ep` 的**唯一实现**在 `std_lib/scraper_std/cache_store.py`
# （原 gov/pbc 两源各写一份逐字相同的实现 → 改一漏一即缓存键算法分叉，且**不会报错**）。
# 别名导入保持全部调用点不变。
from std_lib.scraper_std.cache_store import url_endpoint_key as _cache_ep

DEFAULT_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
    "application/json;q=0.8,*/*;q=0.7",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    # 注意：不要包含 br（brotli）。本机 urllib3 未安装 brotli 解码器，
    # 一旦服务器回 brotli 压缩，resp.content 会是未解压乱码字节，
    # decode_html 解出乱码 → 选择器全部落空 → 详情正文=0（海量 gov 子站因此被误判为空）。
    # 去掉 br 后服务器回退 gzip，urllib3 可正常解压。
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

# 详情页正文候选容器选择器（按优先级尝试）
CONTENT_SELECTORS = [
    ".law-chapter",        # 司法部行政法规详情正文
    "#content",
    "#UCAP-CONTENT",
    ".content-text",
    ".pages_content",
    ".article",
    "#zoom",
    "#article-content",
    ".TRS_Editor",
    "div.content",
]

TITLE_SELECTORS = ["h1", ".article-title", "#title", ".title", "h2"]

# ---- A5 _ORGAN_KW + _NOISE_CLASS_HINTS（原 collector.py L317-322，逐字搬运）----

# 发文机关关键词（用于从『X令第N号』中定位机关名）
_ORGAN_KW = r"(?:国务院|部|委员会|局|人民政府|政府|厅|署|中央军委)"

# 详情页需剔除的噪声元素（下载条 / 历史沿革 / 按钮等）
_NOISE_CLASS_HINTS = ["download", "fold", "historical", "tip", "btn", "share", "qr", "vconsole"]

# ---- A6 _SCHEME_UPGRADE_RE（原 collector.py L326-330，逐字搬运）----


# http 站点正文页仅返回「JS 协议升级」脚本（window.location.href="https:"+...），
# requests 不执行 JS → 拿不到正文。检测到该模式后改用 https 重抓。
_SCHEME_UPGRADE_RE = re.compile(r'targetProtocol\s*=\s*["\']https:', re.I)

# ---- A7 CSV_COLUMNS（原 collector.py L587-592，逐字搬运）----
# --------------------------------------------------------------------------- #
# CSV 表格列（2026-09-04：随 FlkScraper 段删除时被误删，已从备份恢复）
CSV_COLUMNS = [
    "title", "category", "publish_date", "pub_date_original", "effective_date",
    "issue_organ", "document_number", "detail_url", "summary", "source",
]

# ---- A8 _SCRAPERS_ROOT（原 collector.py L604-607，逐字搬运）----

_SCRAPERS_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _SCRAPERS_ROOT not in sys.path:
    sys.path.insert(0, _SCRAPERS_ROOT)

__all__ = [
    "CSV_COLUMNS", "CONTENT_SELECTORS", "DEFAULT_HEADERS", "LOG", "MasterView",
    "SOURCES", "ScrapeConfig", "TITLE_SELECTORS", "_NOISE_CLASS_HINTS",
    "_ORGAN_KW", "_SCHEME_UPGRADE_RE", "_cache_ep", "_is_https_scheme_upgrade",
    "_longest_text_block", "_unified_doc_number", "USER_AGENTS",
    "bind_source_cache", "build_config", "clean_text", "decode_html",
    "extract_date", "extract_doc_number", "extract_issue_organ",
    "fetch_gov_attachments", "load_resume", "make_summary", "merge_and_write",
    "merge_with_master", "write_collect_stats", "write_outputs",
]
