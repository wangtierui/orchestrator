#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
中国人民银行条法司 — 法律法规 / 政策规章 / 规范性文件 自动抓取脚本
==================================================================================
功能：
  1. 对四个栏目（国家法律 / 行政法规 / 部门规章 / 规范性文件）自动遍历静态分页；
  2. 解析列表页提取条目名称、详情/附件链接；
  3. 对每条目请求详情页（HTML 正文页）或直接下载附件（.doc/.docx 等），
     提取 标题、发布日期、发文字号、发文机关、生效日期、正文全文、内容摘要；
  4. 以结构化 JSON + CSV 输出（**不再生成报告文件**，见下）。

设计要点（对齐数据治理严谨性要求）：

> ⚠️ **产出边界（N-191，2026-10-08 删除）**：本采集器**只写 JSON/CSV 事实源**，**不再写** `report.md`
> （原实现写到 `os.path.dirname(--out)/reports/report.md` ⇒ `--out` 指向沙箱时会落到**仓外相对位置**，
> 属 R-F 缺陷；按用户决定**改为删除该产出**而非改路径）。统计报告由**编排链汇总**统一产出
> （`reports/_tmp/生产刷新汇总_*.json` + docs/deliverables），采集器侧不再重复造报告。
  - 依赖：标准库 + python-docx / pdfplumber / openpyxl（附件解析）；LibreOffice(headless)
    可选，启用后可将 .doc/.wps/.rtf/.ceb 转 docx 提取正文，否则该格式降级为保真下载；
  - 分页机制自适应：从列表页隐藏字段 `article_paging_list_hidden` 动态提取
    moduleid 与 totalpage，按 `{栏目目录}/{moduleid}-{n}.html` 规律生成各分页 URL，
    不硬编码任何栏目专属参数；
  - 两种条目类型全覆盖：HTML 详情页解析 + 附件（.doc/.docx/.pdf 等）下载，
    不跳过任一类型（满足代码解析完整性约束）；
  - 反爬与稳定性：UA 随机轮换、Referer 携带、 polite 随机延时、指数退避重试、
    超时控制、单条失败不影响整体；
  - 可断点续跑：已存在输出文件时，跳过已成功抓取的详情链接。

用法：
  python collectors/pbc/collector.py                 # 抓取全部四个栏目
  python collectors/pbc/collector.py --category 国家法律   # 仅抓取指定栏目
  python collectors/pbc/collector.py --max-items 50       # 限制条目数（调试用）
  python collectors/pbc/collector.py --delay 1.2 --out ./out
  python collectors/pbc/collector.py --no-attachments     # 不下载附件正文（仅记录链接）
"""


# ---- P3b 仓库引导（migrate_collectors_p3b.py 注入）：使 std_lib/config 可导入 ----
import os as _os
import sys as _sys

_GUIDE_ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "..", "..", ".."))
if _GUIDE_ROOT not in _sys.path:
    _sys.path.insert(0, _GUIDE_ROOT)
del _GUIDE_ROOT, _os, _sys
import argparse
import csv
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from std_lib.scraper_std.pipeline import (  # 批 49/T1：raw 统一 JSONL 读写助手
    read_raw_records,
    resolve_raw_path,
    write_raw_jsonl,
)

# 通用缓存模块（五源统一抽象层：std_lib/scraper_std/cache_store，2026-09-05）
# pbc 列表/详情页均为静态 HTML → 委托 TextResponseCache（存储 HTML 文本）；
# 命中读盘跳过网络、离线缺失抛 OfflineMiss、仅成功响应（status==200）落盘；
# 二进制附件（binary=True）已落盘 ATTACHMENTS_DIR，不经文本缓存。
try:
    from std_lib.scraper_std.cache_store import OfflineMiss, bind_source_cache
    from std_lib.scraper_std.rich_object import rich_object_fields
    from std_lib.scraper_std.table_recovery import structured_table_fields
except ImportError:  # pragma: no cover
    from std_lib.scraper_std.cache_store import OfflineMiss, bind_source_cache

    def structured_table_fields(data, name="", *, kind=None):  # type: ignore[misc]  # pragma: no cover
        return {}

    def rich_object_fields(data, name="", *, image_dir=None, rec_key=""):  # type: ignore[misc]  # pragma: no cover
        return {}

_RESP_TEXT = None  # TextResponseCache 实例；None 表示未启用缓存
_OfflineMiss = OfflineMiss  # 兼容别名


# ---- 拆分（2026-09-13，P3）：下列符号迁 pbc/parse，re-export 保持外部调用兼容 ----
# W4（L1 重组）：import 双形态（包形态相对导入 / 脚本形态裸名）；经**模块对象**取符号
# ⇒ 名单只此一份。
try:
    from . import parse as _parse
except ImportError:  # pragma: no cover - 脚本形态
    import parse as _parse  # type: ignore[no-redef]

# re-export（显式赋值，规避 F401/F405；外部调用面不变）
clean_text = _parse.clean_text
convert_with_libreoffice = _parse.convert_with_libreoffice
detect_libreoffice = _parse.detect_libreoffice
detect_magic = _parse.detect_magic
extract_docx_text = _parse.extract_docx_text
extract_pdf_text = _parse.extract_pdf_text
extract_xls_text = _parse.extract_xls_text
is_attachment = _parse.is_attachment
normalize_digits = _parse.normalize_digits
safe_filename = _parse.safe_filename
safe_url = _parse.safe_url
strip_tags = _parse.strip_tags


# ---- 拆分（2026-10-15，D-遗留第一批；TD-1 夹具就位后执行）--------------------------
# 迁出：列表页解析（parse_listing / listing_page_urls + RE_PAGING / RE_ENTRY）→ `listing.py`；
#       详情页解析（parse_detail / extract_* + 详情页 RE_*）→ `detail.py`；
#       共享常量（BASE / ISSUING_AUTHORITIES / ATTACH_EXT / RE_TAG / RE_WS）→ `constants.py`。
# 依赖方向单向（constants ← listing / detail ← collector），无循环导入。
# **re-export 保持外部调用面不变**（沿用 2026-09-13 P3 既有约定）：外部调用点（含
# `tests/test_collector_replay.py` 的回放用例）仍从本模块取这些符号，无需改动。
try:  # 包形态
    from . import constants as _constants
    from . import detail as _detail
    from . import listing as _listing
except ImportError:  # 脚本形态（python collectors/pbc/collector.py 直调）
    import constants as _constants  # type: ignore[no-redef]
    import detail as _detail  # type: ignore[no-redef]
    import listing as _listing  # type: ignore[no-redef]

# re-export（显式赋值，规避 F401/F405 与 mypy no-redef；外部调用面不变）
ATTACH_EXT = _constants.ATTACH_EXT
BASE = _constants.BASE
ISSUING_AUTHORITIES = _constants.ISSUING_AUTHORITIES
RE_TAG = _constants.RE_TAG
RE_WS = _constants.RE_WS
RE_ARTICLE_TITLE_META = _detail.RE_ARTICLE_TITLE_META
RE_DOC_NUMBER = _detail.RE_DOC_NUMBER
RE_EFFECTIVE = _detail.RE_EFFECTIVE
RE_P = _detail.RE_P
RE_SHIJIAN = _detail.RE_SHIJIAN
RE_TITLE_H2 = _detail.RE_TITLE_H2
RE_ZOOM = _detail.RE_ZOOM
extract_doc_number = _detail.extract_doc_number
extract_effective = _detail.extract_effective
extract_issuing = _detail.extract_issuing
parse_detail = _detail.parse_detail
RE_ENTRY = _listing.RE_ENTRY
RE_PAGING = _listing.RE_PAGING
listing_page_urls = _listing.listing_page_urls
parse_listing = _listing.parse_listing


def _init_cache(path=None, offline=False):
    """统一缓存根绑定（缺省 cache_store.source_cache_root("pbc")，单物理根）；path 显式可覆盖。"""
    global _RESP_TEXT
    _RESP_TEXT = bind_source_cache("pbc", "text", root=path)
    if offline and _RESP_TEXT is not None:
        _RESP_TEXT.set_offline(True)


def set_cache_dir(path):
    """兼容旧调用（同目录脚本）：仅设根，沿用当前离线态。"""
    _init_cache(path)


def set_offline(flag):
    if _RESP_TEXT is not None:
        _RESP_TEXT.set_offline(flag)

# N-167：`_cache_ep` 的**唯一实现**在 `std_lib/scraper_std/cache_store.py`
# （原 gov/pbc 两源各写一份逐字相同的实现 → 改一漏一即缓存键算法分叉，且**不会报错**）。
# 别名导入保持全部调用点不变。
from std_lib.scraper_std.cache_store import url_endpoint_key as _cache_ep

# ----------------------------------------------------------------------------------
# 栏目配置：仅需提供各栏目的列表首页 URL，分页参数运行时自动探测
# ----------------------------------------------------------------------------------

# 产物目录统一（Plan B 阶段 2b + 2026-09-08 docs_root 统一根）
SRC_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SRC_DIR)  # regulatory_scrapers（统一数据根）
from std_lib.scraper_std.cache_store import docs_root

ATTACHMENTS_DIR = docs_root("pbc", "attachments")
CATEGORIES = [
    {"name": "国家法律", "index_url": BASE + "/tiaofasi/144941/144951/index.html"},
    {"name": "行政法规", "index_url": BASE + "/tiaofasi/144941/144953/index.html"},
    {"name": "部门规章", "index_url": BASE + "/tiaofasi/144941/144957/index.html"},
    {"name": "规范性文件", "index_url": BASE + "/tiaofasi/144941/3581332/index.html"},
]

# 常见发文机关关键词（用于从正文启发式识别，按出现优先级排列）
# B1/W4（2026-10）：UA 池单栈——原本地 4 条池（Chrome 120/119 时代）删，改 SSOT
# （crawler_common.USER_AGENTS → http.USER_AGENTS_PC；try-import + 单 UA 兜底降级）。
try:
    from std_lib.scraper_std.crawler_common import USER_AGENTS
except ImportError:  # pragma: no cover
    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ]

# 正则表达式预编译

# ----------------------------------------------------------------------------------
# 附件解析：多格式路由 + LibreOffice 自动探测 + 优雅降级
# ----------------------------------------------------------------------------------


from std_lib.scraper_std.doc_convert import (
    find_libreoffice,
)

LO_PATH = find_libreoffice()
LO_AVAILABLE = LO_PATH is not None







# ----------------------------------------------------------------------------------
# 网络请求层：带重试退避、UA 轮换、超时、频率控制
# ----------------------------------------------------------------------------------
class Fetcher:
    def __init__(self, min_delay=0.8, max_delay=1.6, timeout=30, retries=3):
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.timeout = timeout
        self.retries = retries
        self._last_req = 0.0

    def _throttle(self):
        """礼貌限速：请求间隔随机化，避免固定频率触发风控。"""
        elapsed = time.time() - self._last_req
        gap = random.uniform(self.min_delay, self.max_delay)
        if elapsed < gap:
            time.sleep(gap - elapsed)
        self._last_req = time.time()

    def get(self, url, referer=None, binary=False, timeout=None):
        """
        带指数退避重试的 GET 请求，返回 (status, content)。
        status 为 HTTP 状态码；失败时 status=None，content 为错误说明字符串。

        文本页（binary=False）委托通用缓存层 TextResponseCache：命中读盘跳过网络、
        离线缺失抛 _OfflineMiss、仅成功响应（status==200）落盘；二进制附件
        （binary=True）已落盘 ATTACHMENTS_DIR，不经文本缓存。
        """
        last_err = None
        url = safe_url(url)
        # 1) 缓存命中（仅文本页）：续跑 / 离线
        if not binary and _RESP_TEXT is not None:
            cp = _RESP_TEXT.path(_cache_ep(url), {})
            if os.path.exists(cp):
                try:
                    with open(cp, encoding="utf-8") as fh:
                        return 200, fh.read()
                except Exception:  # noqa: BLE001  采集容错（字段/附件缺失不阻断采集）
                    pass  # 缓存损坏则重新请求
            if _RESP_TEXT.offline:
                raise _OfflineMiss(url)
        for attempt in range(1, self.retries + 1):
            self._throttle()
            try:
                req = urllib.request.Request(url, method="GET")
                req.add_header("User-Agent", random.choice(USER_AGENTS))
                req.add_header("Accept", "*/*")
                req.add_header("Accept-Language", "zh-CN,zh;q=0.9")
                if referer:
                    req.add_header("Referer", referer)
                with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                    status = resp.status
                    data = resp.read()
                    if binary:
                        return status, data
                    # 解码：优先按 HTTP 头，回退 utf-8
                    enc = resp.headers.get_content_charset() or "utf-8"
                    try:
                        text = data.decode(enc, errors="replace")
                    except LookupError:
                        text = data.decode("utf-8", errors="replace")
                    # 写入文本缓存（仅成功响应且非空）
                    if _RESP_TEXT is not None and status == 200 and text:
                        try:
                            _RESP_TEXT.put(_cache_ep(url), {}, text)
                        except Exception:  # noqa: BLE001  采集容错（字段/附件缺失不阻断采集）
                            pass
                    return status, text
            except urllib.error.HTTPError as e:
                last_err = f"HTTP {e.code}"
                # 确定性错误不重试；限流/服务暂不可用则退避后重试
                if e.code in (403, 404, 410):
                    return e.code, None
                if e.code in (429, 503):
                    retry_after = e.headers.get("Retry-After")
                    wait = int(retry_after) if (retry_after and retry_after.isdigit()) else min(2 ** attempt * 2, 16)
                    if attempt < self.retries:
                        time.sleep(wait)
                    continue
            except (urllib.error.URLError, ConnectionError, TimeoutError, Exception) as e:  # noqa: BLE001
                last_err = f"{type(e).__name__}: {e}"
            # 通用退避：2^(attempt) 秒
            if attempt < self.retries:
                time.sleep(min(2 ** attempt, 8))
        return None, last_err

# ----------------------------------------------------------------------------------
# 文本工具
# ----------------------------------------------------------------------------------







# ----------------------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------------------
def scrape_category(cat, fetcher, args, done_urls, existing_map=None):
    name = cat["name"]
    index_url = cat["index_url"]
    records: list = []

    # 1) 抓取首页，探测分页参数
    try:
        status, txt = fetcher.get(index_url, referer=BASE + "/tiaofasi/144941/index.html")
    except _OfflineMiss:
        print(f"  [offline] 栏目《{name}》首页缓存缺失，跳过", file=sys.stderr)
        return records
    if status != 200 or not txt:
        print(f"  [!] 栏目《{name}》首页获取失败 status={status}", file=sys.stderr)
        return records

    moduleid, totalpage, entries = parse_listing(txt, index_url.rsplit("/index.html", 1)[0])
    print(f"  [+] 栏目《{name}》探测到 moduleid={moduleid} totalpage={totalpage} 首页条目={len(entries)}")

    # 2) 遍历所有分页，收集条目
    page_urls = listing_page_urls(index_url, moduleid, totalpage)
    for pu in page_urls[1:]:  # 第 1 页已处理
        try:
            st, ptxt = fetcher.get(pu, referer=index_url)
        except _OfflineMiss:
            print(f"  [offline] 分页缓存缺失 {pu}，停止翻页", file=sys.stderr)
            break
        if st == 200 and ptxt:
            _, _, more = parse_listing(ptxt, index_url.rsplit("/index.html", 1)[0])
            entries.extend(more)
        else:
            print(f"  [!] 分页获取失败 {pu} status={st}", file=sys.stderr)

    # 去重（跨页可能重复）
    uniq = {}
    for t, u in entries:
        uniq[u] = t
    print(f"  [+] 合并后去重条目数：{len(uniq)}")

    # 3) 逐条抓取详情 / 附件
    for i, (url, title) in enumerate(uniq.items(), 1):
        if args.max_items and len(records) >= args.max_items:
            break
        rec = {
            "category": name,
            "title": title,
            "detail_url": url,
            "link_type": "attachment" if is_attachment(url) else "html",
            "file_type": (url.rsplit(".", 1)[-1].lower() if is_attachment(url) else None),
            "local_path": None,
            "publish_date": None,
            "document_number": None,
            "issuing_authority": None,
            "effective_date": None,
            "content": "",
            "summary": "",
            "fetch_status": "pending",
            "error": None,
        }

        if url in done_urls:
            # 断点续跑：复用已成功抓取的完整旧记录（含正文/字段），避免重跑时丢失内容
            old = (existing_map or {}).get(url)
            if old:
                rec = dict(old)
                rec["fetch_status"] = "skipped_existing"
            else:
                rec["fetch_status"] = "skipped_existing"
            records.append(rec)
            continue

        if rec["link_type"] == "html":
            try:
                st, dtxt = fetcher.get(url, referer=index_url)
            except _OfflineMiss:
                rec["fetch_status"] = "fetch_failed"
                rec["error"] = "offline: 详情缓存缺失"
                records.append(rec)
                continue
            if st == 200 and dtxt:
                try:
                    d = parse_detail(dtxt)
                    rec.update({k: d[k] for k in (
                        "title", "publish_date", "document_number",
                        "issuing_authority", "effective_date", "content")})
                    rec["fetch_status"] = "ok"
                except Exception as e:  # noqa: BLE001
                    rec["fetch_status"] = "parse_error"
                    rec["error"] = f"{type(e).__name__}: {e}"
            else:
                rec["fetch_status"] = "fetch_failed"
                rec["error"] = f"status={st}; {dtxt}"
        else:
            # 附件：下载原始文件到本地（供前端显示/下载），并按格式路由解析正文。
            # 覆盖全部附件类型，绝不跳过；解析失败/格式不支持时降级为保真下载并标注。
            if args.no_attachments:
                rec["fetch_status"] = "attachment_skipped"
            else:
                try:
                    st, bdata = fetcher.get(url, referer=index_url, binary=True, timeout=60)
                except _OfflineMiss:
                    rec["fetch_status"] = "fetch_failed"
                    rec["error"] = "offline: 附件缓存缺失"
                    records.append(rec)
                    continue
                if not (st == 200 and bdata):
                    rec["fetch_status"] = "fetch_failed"
                    rec["error"] = f"附件下载失败 status={st}"
                else:
                    # 落盘原始文件（中文名安全编码）
                    fname = safe_filename(rec["title"], rec["file_type"], url)
                    adir = os.path.join(ATTACHMENTS_DIR, name)
                    os.makedirs(adir, exist_ok=True)
                    fpath = os.path.join(adir, fname)
                    with open(fpath, "wb") as fh:
                        fh.write(bdata)
                    rec["local_path"] = os.path.relpath(fpath, REPO_ROOT).replace("\\", "/")
                    # 格式路由解析（异常隔离：单条附件解析异常不中断整体运行）
                    ft = rec["file_type"]
                    parsed = None
                    try:
                        if ft == "docx":
                            parsed = extract_docx_text(bdata)
                        elif ft == "pdf":
                            parsed = extract_pdf_text(bdata)
                        elif ft in ("xls", "xlsx"):
                            parsed = extract_xls_text(bdata)
                        elif ft in ("doc", "wps", "rtf", "ceb"):
                            if LO_AVAILABLE:
                                conv = convert_with_libreoffice(fpath)
                                if conv and os.path.exists(conv):
                                    with open(conv, "rb") as cf:
                                        parsed = extract_docx_text(cf.read())
                        # 表格结构化（2026-09-08 仿 supp 打通）：xlsx/docx 附件表 → rec 表键；
                        # .doc/wps/rtf/ceb 由 helper 内 doc→docx（共享 doc_convert）后取表
                        if ft in ("docx", "xls", "xlsx", "doc", "wps", "rtf", "ceb"):
                            rec.update(structured_table_fields(bdata, fname))
                        # 富内容轨（2026-09-09 rich_object）：docx/xlsx 图形/公式/图片
                        try:
                            rec.update(rich_object_fields(
                                bdata, fname,
                                image_dir=docs_root("pbc", "diagrams"),
                                rec_key=f"{name}_{ft}"))
                        except Exception:  # noqa: BLE001
                            pass
                    except Exception as e:  # noqa: BLE001
                        rec["error"] = ("附件正文解析异常：%s: %s；已保存原始文件供下载"
                                        % (type(e).__name__, e))
                        parsed = None
                    # 结果判定
                    if parsed:
                        rec["content"] = parsed
                        rec["fetch_status"] = "ok"
                    else:
                        rec["fetch_status"] = "attachment_saved"
                        if ft in ("doc", "wps", "rtf", "ceb"):
                            rec["error"] = "正文解析需 LibreOffice 环境（当前不可用），已保存原始文件供下载"
                        elif ft in ("xls", "xlsx"):
                            rec["error"] = "表格正文提取失败，已保存原始文件供下载"
                        elif ft == "pdf":
                            rec["error"] = "PDF 文本提取失败，已保存原始文件供下载"
                        else:
                            rec["error"] = f".{ft} 暂不支持正文解析，已保存原始文件供下载"

        # 内容摘要（前 200 字）
        if rec["content"]:
            rec["summary"] = rec["content"][:200]
        records.append(rec)
        if i % 20 == 0 or i == len(uniq):
            print(f"  [.] 《{name}》进度 {i}/{len(uniq)} 成功={sum(1 for r in records if r['fetch_status']=='ok')}")
    return records

def save_outputs(records, out_dir, write_csv=False):
    os.makedirs(out_dir, exist_ok=True)
    csv_path = os.path.join(out_dir, "pbc_laws.csv")
    # 批 49/T1：raw 统一 JSONL（流式 + 首行 `_meta`；原为**裸数组**整表 json.dump）
    json_path = write_raw_jsonl(os.path.join(out_dir, "pbc_laws.json"), records, source="pbc")
    if write_csv:                                 # 默认仅 JSON 主库（2026-09-09 规范）
        fields = ["category", "title", "detail_url", "link_type", "file_type", "local_path",
                  "publish_date", "document_number", "issuing_authority",
                  "effective_date", "content", "summary", "fetch_status", "error"]
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for r in records:
                w.writerow(r)
    return json_path, (csv_path if write_csv else "")

# —— 运行锁统一实现（N-8）：判定逻辑收敛到 regulatory_scrapers/fs_lock.py，四源共用 ——
import atexit

_SCRAPERS_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _SCRAPERS_ROOT not in sys.path:
    sys.path.insert(0, _SCRAPERS_ROOT)
from std_lib.common_lib import fs_lock


def main():
    ap = argparse.ArgumentParser(description="中国人民银行条法司法规抓取脚本")
    ap.add_argument("--category", help="仅抓取指定栏目名称（如 国家法律）")
    ap.add_argument("--max-items", type=int, default=0, help="限制总条目数（调试用）")
    ap.add_argument("--delay", type=float, default=1.0, help="平均请求间隔（秒）")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "raw"), help="输出目录（默认统一 data/raw/：regulatory_scrapers/data/raw）")
    ap.add_argument("--no-attachments", action="store_true", help="不下载附件正文，仅记录链接")
    ap.add_argument("--csv", action="store_true",
                    help="额外输出 CSV（默认仅写 JSON 主库，2026-09-09 规范）")
    ap.add_argument("--cache-dir", default="",
                    help="请求缓存目录（显式覆盖）：缺省由 cache_store.source_cache_root(pbc) 统一解析"
                         "→ modules/regulatory_scrapers/cache/pbc（单物理根）")
    ap.add_argument("--offline", action="store_true",
                    help="纯离线模式：仅读取 --cache-dir 缓存，缓存缺失即跳过（不联网）")
    args = ap.parse_args()

    # 通用缓存（五源统一抽象层）：统一根绑定 + 离线开关
    _init_cache(args.cache_dir or None, args.offline)

    # —— 调度可靠性：跨进程单实例锁防并发重复（统一 fs_lock 公共库，N-8）——
    _lock = fs_lock.ProcessLock(os.path.join(args.out, "scrape.lock"))
    if not _lock.acquire():
        print("[!] 已有抓取任务在运行（锁存在且 PID 存活），本次跳过以避免重复抓取。",
              file=sys.stderr)
        sys.exit(0)
    atexit.register(_lock.release)

    min_d = max(0.4, args.delay * 0.8)
    max_d = args.delay * 1.6
    fetcher = Fetcher(min_delay=min_d, max_delay=max_d, timeout=30, retries=3)

    # 断点续跑：读取已有 JSON
    done_urls = set()
    existing_map = {}
    out_json = os.path.join(args.out, "pbc_laws.json")
    if os.path.exists(resolve_raw_path(out_json)):
        try:
            existing = read_raw_records(out_json)   # 批 49/T1：双格式（JSONL 优先）
            for r in existing:
                if r.get("fetch_status") == "ok":
                    done_urls.add(r["detail_url"])
                    existing_map[r["detail_url"]] = r
            print(f"[*] 检测到已有结果，跳过 {len(done_urls)} 条已成功条目（断点续跑）")
        except Exception:  # noqa: BLE001  采集容错（字段/附件缺失不阻断采集）
            pass

    cats = [c for c in CATEGORIES if (not args.category or c["name"] == args.category)]
    if not cats:
        print(f"[!] 未找到栏目：{args.category}", file=sys.stderr)
        sys.exit(1)

    all_records = []
    for cat in cats:
        print(f"\n=== 开始抓取栏目：《{cat['name']}》 ===")
        recs = scrape_category(cat, fetcher, args, done_urls, existing_map)
        all_records.extend(recs)
        # 逐栏目增量落盘：即使进程被中断，已完成栏目数据不丢失，下次运行可断点续跑
        save_outputs(all_records, args.out, write_csv=args.csv)
        print(f"  [✓] 《{cat['name']}》已落盘，累计 {len(all_records)} 条")

    if args.max_items:
        all_records = all_records[:args.max_items]

    json_path, csv_path = save_outputs(all_records, args.out, write_csv=args.csv)
    print(f"\n[✓] 完成。JSON: {json_path}\n    CSV : {csv_path}")

if __name__ == "__main__":
    main()
