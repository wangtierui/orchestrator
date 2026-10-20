# -*- coding: utf-8 -*-
"""mof/mof_constants — mof 采集的**依赖引导 + 常量 + 可变缓存状态**（拆分的基础层）。

为什么单独成文件（2026-10-20 第一百零五批）
------------------------------------------
`collector.py` 原 800 行里，约 130 行是「pipeline/附件/cache 的导入与 re-export +
常量 + 缓存状态」。它们必须先下沉，否则后续要拆的 `mof_io` / `mof_lock` / `mof_sig`
需 `from .collector import HOST/logger`，而 `collector` 又要 `from .mof_io import …`
⇒ **循环导入**。常量层是"零依赖叶子"，上层才能单向依赖。

**必须同处的四件**（与 `gov_session` 同理，不可再拆）：
`_RESP`（可变全局）+ `_init_cache`（`global _RESP` 写入）+ `set_cache_dir` /
`set_offline`（读它）—— 任一搬走都会造成"两处状态、缓存开关静默失效"
（不报错，只是永远不命中），本仓反复踩过这一类的坑。

**引导与标准库**：本文件自带一份（`if not in sys.path` 幂等），与 `collector.py`
中那份**重复但必要**——两者各自被独立 import，互不继承 sys.path 状态。

**逐字提取**：正文由脚本从 `collector.py` 按行区间搬运（零手抄——实测手抄会把常量
值写错）。等价性由 `tests/baselines/mof_collector_baseline.json` 锁定。
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

from std_lib.scraper_std.pipeline import (  # 批 49/T1：raw 统一 JSONL 读写助手
    read_raw_records,
    resolve_raw_path,
    write_raw_jsonl,
)

# B7（W4，2026-10）：原子写 SSOT = std_lib.common_lib.fs_lock（本文件下方「运行锁」段
# 已有 `from std_lib.common_lib import fs_lock` 模块级 import；原本地 `_atomic_write`
# 私造已删，状态/报告写入一律 `fs_lock.atomic_write_text`——差异仅是 mkstemp+fsync
# 加固（更安全，语义不变：同目录临时文件 + os.replace 原子替换 + 目录自动创建）。

# 共享加固工具层（UA 池、文档文本抽取、魔数纠正命名、表格结构化）
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
    def rich_object_fields(data, name="", *, image_dir=None, rec_key=""):  # type: ignore[misc]
        return {}

# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #
# 通用缓存模块（五源统一抽象层：std_lib/scraper_std/cache_store，2026-09-05）
# mof 接口均返回 JSON → 委托 ResponseCache；命中读盘跳过网络、离线缺失抛 OfflineMiss、
# 仅成功响应（code==200）落盘，不缓存错误/拦截页。
try:
    from std_lib.scraper_std.cache_store import OfflineMiss, bind_source_cache
except ImportError:  # pragma: no cover
    from std_lib.scraper_std.cache_store import OfflineMiss, bind_source_cache

_RESP = None  # ResponseCache 实例；None 表示未启用缓存
_OfflineMiss = OfflineMiss  # 兼容别名


# ---- 拆分（2026-09-13，P3）：下列符号迁 mof/attachments，re-export 保持外部调用兼容 ----
# W4（L1 重组）：import 双形态（包形态相对导入 / 脚本形态裸名）。经**模块对象**取符号
# ⇒ 名单只此一份（两形态各写一遍名单必然漂移）。
try:
    from . import attachments as _att
except ImportError:  # pragma: no cover - 脚本形态
    import attachments as _att  # type: ignore[no-redef]

# re-export（显式赋值，规避 F401/F405；外部调用面不变）
_finalize_attachment = _att._finalize_attachment
_mof_cache_key = _att._mof_cache_key
_request = _att._request
build_entry = _att.build_entry
collect_attachments = _att.collect_attachments
download_attachment = _att.download_attachment
fetch_attachments = _att.fetch_attachments
fetch_category_records = _att.fetch_category_records
fetch_list_page = _att.fetch_list_page
html_to_text = _att.html_to_text
normalize_date = _att.normalize_date
safe_filename = _att.safe_filename


def _init_cache(path=None, offline=False):
    """统一缓存根绑定（缺省 cache_store.source_cache_root("mof")，单物理根）；path 显式可覆盖。"""
    global _RESP
    _RESP = bind_source_cache("mof", "json", root=path)
    if offline and _RESP is not None:
        _RESP.set_offline(True)


def set_cache_dir(path):
    """兼容旧调用（同目录脚本）：仅设根，沿用当前离线态。"""
    _init_cache(path)


def set_offline(flag):
    if _RESP is not None:
        _RESP.set_offline(flag)

HOST = "http://fgk.mof.gov.cn"
BASE = "/dev"  # 接口基础路径：/dev/lawFile/list 等

# 产物目录统一（Plan B 阶段 2b + 5b 收敛 + 2026-09-08 docs_root 统一根）
SRC_DIR = os.path.dirname(os.path.abspath(__file__))
# ⚠️ 2026-10-20 修（实测）：原 `REPO_ROOT = dirname(SRC_DIR)` 只上溯**一级**
# ⇒ 实际是 `.../collectors`（注释却写 "regulatory_scrapers"）⇒ `--outdir` 默认值
# 落到 `.../collectors/data/raw`，而**主库在 `.../regulatory_scrapers/data/raw`**
# ⇒ 裸跑会静默写到错位置、白跑，且不报错（实测踩到）。
# **注意区分两个基准**（不能共用一个常量）：
#   · `outdir`（本文件）⇒ 主库 = `regulatory_scrapers/data/raw`（上溯两级）
#   · `local_path` 基准（attachments.py 的 REPO_ROOT）⇒ **仓根**（历史数据实测如此）
SCRAPERS_ROOT = os.path.dirname(os.path.dirname(SRC_DIR))  # modules/regulatory_scrapers
from std_lib.scraper_std.cache_store import docs_root

ATTACHMENTS_DIR = docs_root("mof", "attachments")

# 两个目标 URL 对应的类别（lfgccId -> 展示名称）
DEFAULT_CATEGORIES = {
    "1000000000000300000": "财政法律法规（财政部规章）",
    "1000000000000400000": "财政部规范性文件",
}

# 浏览器化请求头，降低被反爬拦截概率
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

logger = logging.getLogger("mof_scraper")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

__all__ = [
    "ATTACHMENTS_DIR", "BASE", "CC_USER_AGENTS", "DEFAULT_CATEGORIES",
    "HEADERS", "HOST", "OfflineMiss", "SCRAPERS_ROOT",
    "SRC_DIR", "_OfflineMiss", "_RESP", "_finalize_attachment",
    "_init_cache", "_mof_cache_key", "_request", "bind_source_cache",
    "build_entry", "collect_attachments", "docs_root", "download_attachment",
    "extract_document_text", "fetch_attachments", "fetch_category_records", "fetch_list_page",
    "html_to_text", "logger", "normalize_date", "read_raw_records",
    "resolve_raw_path", "rich_object_fields", "safe_filename", "set_cache_dir",
    "set_offline", "structured_table_fields", "write_raw_jsonl",
]
