# -*- coding: utf-8 -*-
"""mof/att_constants — mof 采集的**纯常量与依赖别名**（无逻辑、无可变状态）。

为什么单独成文件（2026-10-20，D-遗留拆分第三批）
------------------------------------------------
`attachments.py` 原本 668 行，其中**前 185 行几乎全是常量与 try-import 兜底**
（19 个常量 + 8 处别名），真正有逻辑的只有 12 个函数。常量与函数混在一处的问题
不是"行数"，而是：

  · **改一个 URL/阈值要翻到 600 行外去找它有没有被别处重复定义**；
  · `split_precheck` 实测本文件有 **8 处 try-import 别名**
    （`CC_USER_AGENTS`/`extract_document_text`/`rich_object_fields`/
    `structured_table_fields`/`OfflineMiss`/`bind_source_cache`/`LEGACY_KINDS`）
    ——这类赋值在 `try` 内，**AST 依赖闭包捕获不到**，任何拆分都必须**显式搬运**，
    否则脚本形态下运行时才 NameError。集中到本文件后，只需在这里兜底一次。

**刻意不搬的东西**（放在 `attachments.py`）
------------------------------------------
`_ATT_502_STREAK` / `_ATT_CIRCUIT_OPEN` 是**跨调用可变全局**（`download_attachment`
用 `global` 递增/置位），`_RESP` / `_LOCKS` 同理。搬走会让 `global` 语义跨模块、
变成两处状态（熔断失效且难查），收益远小于风险 ⇒ **留在使用处**。
`_MAX_ATT_502_STREAK` 是**纯阈值**（不可变）⇒ 本文件。

**双形态导入**：本文件可能被包形态（`from .att_constants import …`）与
**脚本形态**（`python collectors/mof/collector.py`）两种方式触达，故保留
仓内惯例的仓根引导（与 `attachments.py` 一致）。
"""
from __future__ import annotations

# ---- P3b 仓库引导（与 attachments.py 同款；脚本形态下使 std_lib/paths 可导入）----
import os as _os
import sys as _sys

_GUIDE_ROOT = _os.path.abspath(
    _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "..", "..", ".."))
if _GUIDE_ROOT not in _sys.path:
    _sys.path.insert(0, _GUIDE_ROOT)
del _GUIDE_ROOT, _os, _sys

import logging
import os
import re

# 共享加固工具层（UA 池、文档文本抽取、魔数纠正命名、表格结构化）
# ⚠️ 这些别名在 `try` 内赋值 ⇒ **AST 依赖闭包捕获不到**（split_precheck 实测）。
#    集中在此处兜底一次，下游模块直接 `from .att_constants import …` 即可。
try:
    from std_lib.scraper_std.crawler_common import USER_AGENTS as CC_USER_AGENTS
    from std_lib.scraper_std.crawler_common import extract_document_text
    from std_lib.scraper_std.rich_object import rich_object_fields
    from std_lib.scraper_std.table_recovery import structured_table_fields
except ImportError:  # pragma: no cover
    CC_USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ]

    def extract_document_text(data, name="", **kw):  # type: ignore[misc]
        return {"text": "", "kind": "unknown", "extracted": False,
                "extract_status": "library_missing", "sha256": "",
                "needs_ocr": False, "garble_ratio": 0.0, "size_bytes": len(data)}

    def structured_table_fields(data, name="", *, kind=None):  # type: ignore[misc]
        return {}

    def rich_object_fields(data, name="", *, image_dir=None, rec_key="") -> dict:  # type: ignore[misc]
        return {}


# 通用缓存模块（五源统一抽象层：std_lib/scraper_std/cache_store）
try:
    from std_lib.scraper_std.cache_store import OfflineMiss, bind_source_cache
except ImportError:  # pragma: no cover
    from std_lib.scraper_std.cache_store import OfflineMiss

#: 兼容别名（`attachments.py` 内的离线缺失异常类型以此为准）
OfflineMissAlias = OfflineMiss

#: 旧二进制 kind 集合（**单一事实源在共享库**；此处按需导入，避免重复定义）
try:
    from std_lib.scraper_std.crawler_common import LEGACY_KINDS
except Exception:  # noqa: BLE001  采集容错：常量缺失时退化为"不跳过"
    LEGACY_KINDS = frozenset()

from paths import ROOT as _WS_ROOT
from std_lib.scraper_std.cache_store import docs_root

# --------------------------------------------------------------------------- #
# 站点与接口
# --------------------------------------------------------------------------- #
HOST = "http://fgk.mof.gov.cn"
BASE = "/dev"                                   # 接口基础路径：/dev/lawFile/list 等
ATTACH_PATH = "/file/f/get"                     # GET ?infoId=<id>&fileType=0/90

#: 附件类型 → 语义类别
_ATTACH_KIND = {"0": "attachment", "90": "text_version"}

# --------------------------------------------------------------------------- #
# 路径
# --------------------------------------------------------------------------- #
SRC_DIR = os.path.dirname(os.path.abspath(__file__))

#: `local_path` 的**基准路径**（`os.path.relpath(dest, REPO_ROOT)`，下游按它拼真实文件）。
#:
#: ⚠️ 2026-10-20 修（实测）：原实现 `dirname(SRC_DIR)` 只上溯**一级** ⇒ 得到
#: `.../collectors`（注释却写 "regulatory_scrapers"，两者不符）⇒ 新写入的
#: `local_path` 会变成 `../../data/docs/…` 这种**带 `..` 的脏路径**，换 cwd 解析就错。
#: 正确基准是**仓根**：实测现存 246 条 `local_path`（形如
#: `modules/regulatory_scrapers/data/docs/…`）以 `paths.ROOT` 拼接后**文件真实存在**
#: ⇒ 历史数据本就是仓根基准，本修正是**回到既有口径**，不是改口径。
REPO_ROOT = _WS_ROOT

#: 附件落盘根（`data/docs/mof_regulations_scraper/attachments`）
ATTACHMENTS_DIR = docs_root("mof", "attachments")

# --------------------------------------------------------------------------- #
# 采集配置
# --------------------------------------------------------------------------- #
DEFAULT_CATEGORIES = {
    "1000000000000300000": "财政法律法规（财政部规章）",
    "1000000000000400000": "财政部规范性文件",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": HOST,
    "Referer": HOST + "/ui/start/",
}

#: 批 55/F1：旧二进制格式（OLE2 系）是否执行表格/富内容轨 —— **默认 False，与 gov 对齐**
#: （LibreOffice 转换约 6.5s/附件、多数产出为空 ⇒ 采集步耗时主因之一）；置 True 恢复完整能力。
ENRICH_LEGACY = False

# --------------------------------------------------------------------------- #
# HTML 清洗正则（`html_to_text` 用）
# --------------------------------------------------------------------------- #
_TAG_RE = re.compile(r"<[^>]+>")
_STYLE_RE = re.compile(r"<(style|script)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_WS_RE = re.compile(r"\s+")

# --------------------------------------------------------------------------- #
# 附件主机熔断阈值（**纯阈值**，不可变；连续计数状态见 attachments.py）
# --------------------------------------------------------------------------- #
_MAX_ATT_502_STREAK = 8

# --------------------------------------------------------------------------- #
# 日志
# --------------------------------------------------------------------------- #
logger = logging.getLogger("mof_scraper")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

__all__ = [
    "ATTACHMENTS_DIR", "ATTACH_PATH", "BASE", "CC_USER_AGENTS",
    "DEFAULT_CATEGORIES", "ENRICH_LEGACY", "HEADERS", "HOST", "LEGACY_KINDS",
    "OfflineMiss", "OfflineMissAlias", "REPO_ROOT", "SRC_DIR",
    "bind_source_cache", "docs_root", "extract_document_text", "logger",
    "rich_object_fields", "structured_table_fields",
]
