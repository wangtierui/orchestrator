# -*- coding: utf-8 -*-
"""
财政部法规数据库（fgk.mof.gov.cn）自动抓取脚本
================================================

目标站点：http://fgk.mof.gov.cn/ui/start/#/outerHomePage/categoryGuide/lfgccId=...
该站点为 layui 单页应用（SPA），列表与详情数据均由后端 REST 接口返回。
本脚本直接调用其数据接口，避免渲染 SPA，稳定且高效。

接口（经源码反编译确认）：
  列表：POST {HOST}/dev/lawFile/list
         body = {"queryMap":{"lfgcc":<类别ID>,"linvalid":"0"},"size":<每页条数>,"current":<页码>}
         resp = {"code":200,"data":{"records":[...],"total":N,"pages":P,"current":C,"size":S}}
  详情：GET  {HOST}/dev/lawFile/get/{id}
  前端详情页（可点击）：{HOST}/ui/src/views/law_html/{id}.html
  附件（前端 JS 异步加载，不在 lcontent 内）：GET {HOST}/file/f/get?infoId={id}&fileType=0(相关附件)/90(下载文字版)
        返回 data 列表，每项含 fileName/fileUrl/serverIp/extension；文件下载地址 = serverIp + fileUrl。

条目关键字段：
  id            法规ID（详情页/接口主键）
  title         法规名称
  lbbdw         颁布单位（发文机关）
  lwh           发文字号
  lBuDate       公布日期
  lSsDate       施行日期（生效日期）
  lcaption      题注/内容摘要
  lcontent      正文全文（HTML）
  lfgcc         法规层次/类别ID
  ltflbName     条法类别名称
  lstate        状态（4=有效等）

依赖：仅 Python 标准库（urllib / json / csv / re / html / argparse / logging）。

使用：
  # 增量 diff 更新（默认）：载入历史存储，仅对新增/变更条目抓详情，
  # 消失条目标记 removed 而非删除；mof_laws.json 本身即持久化存储
  python mof_collector.py

  # 只抓列表前若干条做验证（不抓详情，快速）
  python mof_collector.py --max-items 10 --no-detail

  # 自定义类别与输出目录
  python mof_collector.py --categories 1000000000000300000 1000000000000400000 \
                            --outdir ./data/raw --delay-min 0.4 --delay-max 0.9
"""


# ---- P3b 仓库引导（migrate_collectors_p3b.py 注入）：使 std_lib/config 可导入 ----
import os as _os
import sys as _sys

_GUIDE_ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "..", ".."))
if _GUIDE_ROOT not in _sys.path:
    _sys.path.insert(0, _GUIDE_ROOT)
del _GUIDE_ROOT, _os, _sys
import argparse
import csv
import html
import io
import json
import logging
import os
import random
import re
import sys
import threading
import time
from datetime import UTC, datetime

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
    def extract_document_text(data, name="", **kw):
        return {"text": "", "kind": "unknown", "extracted": False,
                "extract_status": "library_missing", "sha256": "",
                "needs_ocr": False, "garble_ratio": 0.0, "size_bytes": len(data)}
    def structured_table_fields(data, name="", *, kind=None):
        return {}
    def rich_object_fields(data, name="", *, image_dir=None, rec_key=""):
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


# ---- 拆分（2026-09-13，P3）：下列符号迁 mof_attachments，re-export 保持外部调用兼容 ----
from mof_attachments import (  # noqa: F401  拆分 re-export（显式；规避 F405）
    _finalize_attachment,
    _mof_cache_key,
    _request,
    build_entry,
    collect_attachments,
    download_attachment,
    fetch_attachments,
    fetch_category_records,
    fetch_list_page,
    html_to_text,
    normalize_date,
    safe_filename,
)


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
REPO_ROOT = os.path.dirname(SRC_DIR)  # regulatory_scrapers（统一数据根）
from std_lib.scraper_std.cache_store import docs_root  # noqa: E402

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

# --------------------------------------------------------------------------- #
# 网络层（带重试 / 退避 / 限速）
# --------------------------------------------------------------------------- #
class RateLimiter:
    """请求频率限制器，支持限流触发后的自适应降温（penalize）。

    - 基础间隔 min_delay~max_delay 之间随机，避免固定节奏被识别为脚本。
    - 一旦命中 429/503 等限流，penalize(extra) 追加冷却时长，后续请求整体减速，
      给服务器端限流窗口留出恢复时间；每次 wait 后冷却按半衰减，避免长期过慢。
    """

    def __init__(self, min_delay=0.3, max_delay=0.7, max_cooldown=180.0):
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.max_cooldown = max_cooldown
        self.cooldown = 0.0
        self._last = 0.0
        self._lock = threading.Lock()

    def wait(self):
        with self._lock:
            now = time.time()
            target = random.uniform(self.min_delay, self.max_delay) + self.cooldown
            elapsed = now - self._last
            if elapsed < target:
                time.sleep(target - elapsed)
            self._last = time.time()
            # 降温随每次请求指数衰减一半，避免长期过慢
            self.cooldown = max(0.0, self.cooldown * 0.5)

    def penalize(self, extra):
        """触发限流后追加冷却时长（秒），上限 max_cooldown。"""
        if extra <= 0:
            return
        with self._lock:
            self.cooldown = min(self.max_cooldown, self.cooldown + extra)



# --------------------------------------------------------------------------- #
# 解析工具
# --------------------------------------------------------------------------- #
_TAG_RE = re.compile(r"<[^>]+>")
_STYLE_RE = re.compile(r"<(style|script)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_WS_RE = re.compile(r"\s+")



# --------------------------------------------------------------------------- #
# 抓取逻辑
# --------------------------------------------------------------------------- #

def fetch_detail(law_id, rate):
    """抓取单条详情，返回 dict（失败返回 None）。"""
    try:
        resp = _request("GET", f"{BASE}/lawFile/get/{law_id}", rate=rate)
        if resp.get("code") == 200 and resp.get("data"):
            return resp["data"]
    except Exception as e:  # 单条失败不影响整体
        logger.warning("详情获取失败 id=%s：%s", law_id, e)
    return None

# --------------------------------------------------------------------------- #
# 附件抓取（前端 JS 异步加载的"相关附件"与"下载文字版"，不在 lcontent 中）
# --------------------------------------------------------------------------- #
ATTACH_PATH = "/file/f/get"  # GET ?infoId=<id>&fileType=0(相关附件)/90(文字版)
_ATTACH_KIND = {"0": "attachment", "90": "text_version"}



# 附件主机 502 熔断（2026-09-10 增量策略）：附件主机 10.1.60.36:8888 曾实测 100% HTTP 502
# 会让全量任务空转数十小时。连续 502 达阈值即熔断，本次运行剩余附件一律跳过（不阻塞主数据）。
_ATT_502_STREAK = 0
_ATT_CIRCUIT_OPEN = False
_MAX_ATT_502_STREAK = 8








def _sig_current(rec):
    """当前列表记录的变更指纹（用于增量 diff：标题/正文/日期/文号/机关/题注）。"""
    return (
        rec.get("title"),
        html_to_text(rec.get("lcontent") or ""),
        normalize_date(rec.get("lBuDate")),
        normalize_date(rec.get("lSsDate")),
        rec.get("lwh"),
        rec.get("lbbdw"),
        rec.get("lcaption"),
    )

def _sig_prev(entry):
    """历史存储条目的变更指纹（与 _sig_current 口径对齐）。"""
    return (
        entry.get("title"),
        entry.get("content_text"),
        entry.get("publish_date"),
        entry.get("effective_date"),
        entry.get("doc_no"),
        entry.get("issue_org"),
        entry.get("summary"),
    )

# --------------------------------------------------------------------------- #
# 输出
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# 可靠性增强：原子写、运行锁、状态记录
# --------------------------------------------------------------------------- #
def _atomic_write(path, text):
    """先写临时文件再原子替换，避免写入中途崩溃损坏已有结果文件。

    2026-09-04 修复：目标目录不存在时自动创建。此前 data/reports、data/state
    目录缺失会导致首跑在写报告/状态阶段抛 FileNotFoundError（主库已写入但
    状态未落盘，周报失真）。
    """
    _d = os.path.dirname(path)
    if _d:
        os.makedirs(_d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)

# —— 运行锁统一实现（N-8）：判定逻辑收敛到 regulatory_scrapers/fs_lock.py，四源共用 ——
_SCRAPERS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRAPERS_ROOT not in sys.path:
    sys.path.insert(0, _SCRAPERS_ROOT)
from std_lib.common_lib import fs_lock

_LOCKS = {}   # lock_path -> fs_lock.ProcessLock（release_lock 需释放同一实例）

def _read_lock_pid(lock_path):
    """读取锁文件首段（PID）。格式：PID|ISO|token。失败返回 None。"""
    try:
        with open(lock_path, encoding="utf-8") as f:
            first = f.readline().strip()
    except OSError:
        return None
    try:
        return int(first.split("|")[0])
    except (ValueError, IndexError):
        return None

def _pid_alive(pid):
    """跨平台进程存活检测（Windows/Linux 均可用 os.kill(pid, 0)）。"""
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, OSError):
        return False

def acquire_lock(lock_path, max_age_sec=3 * 3600):
    """基于 PID 存活校验的运行锁（实现统一委托 fs_lock.ProcessLock，N-8）。

    - 判定逻辑（PID 存活 + max_age 陈旧兜底）仅在 fs_lock 公共库一份，四源共用；
    - 锁存在且其中 PID 仍在运行 -> 返回 False（并发重复，调用方跳过本次）；
    - PID 已死 / 锁损坏 / 超龄 -> 覆盖抢占。

    注：保留本函数签名仅为兼容既有调用方与测试；新代码请直接用 fs_lock.ProcessLock。
    """
    lk = fs_lock.ProcessLock(lock_path, max_age_sec=max_age_sec)
    if lk.acquire():
        _LOCKS[lock_path] = lk
        return True
    return False

def release_lock(lock_path):
    """释放由 acquire_lock 获取的锁（幂等；未持有则无操作）。"""
    lk = _LOCKS.pop(lock_path, None)
    if lk is not None:
        lk.release()

def write_status(outdir, status):
    """记录抓取状态（成功 / 异常），供调度与监控读取，保留异常状态处理能力。"""
    _atomic_write(os.path.join(os.path.dirname(outdir), "state", "status.json"),
                  json.dumps(status, ensure_ascii=False, indent=2))

def save_json(entries, path):
    _atomic_write(path, json.dumps({
        "source": HOST,
        "captured_at": datetime.now(UTC).isoformat(),
        "count": len(entries),
        "items": entries,
    }, ensure_ascii=False, indent=2))

def save_csv(entries, path):
    cols = [
        "id", "title", "category_name", "category_id", "publish_date",
        "effective_date", "issue_org", "doc_no", "summary", "content_text",
        "content_length", "status", "create_dept", "file_count",
        "attachment_count", "attachment_names", "attachments",
        "active", "change", "removed_at",
        "detail_link", "api_link", "source", "fetch_time",
    ]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, quoting=csv.QUOTE_ALL, lineterminator="\n")
    w.writeheader()
    for e in entries:
        w.writerow({c: e.get(c, "") for c in cols})
    # 前置 BOM，保证 Excel 正确识别 UTF-8
    _atomic_write(path, "\ufeff" + buf.getvalue())

def save_html_report(entries, path):
    """生成可读的离线 HTML 报告（自包含，无外部依赖）。"""
    items_html = []
    for i, e in enumerate(entries, 1):
        att_cell = ""
        for a in (e.get("attachments") or []):
            lp = a.get("local_path")
            fn = html.escape(str(a.get("file_name", "")))
            att_cell += (f'<a href="{html.escape(lp)}" target="_blank">{fn}</a><br>'
                         if lp else fn + "<br>")
        if not att_cell:
            att_cell = "—"
        items_html.append(f"""
        <tr>
          <td>{i}</td>
          <td><a href="{e.get('detail_link','')}" target="_blank">{html.escape(str(e.get('title','')))}</a></td>
          <td>{html.escape(str(e.get('category_name','')))}</td>
          <td>{html.escape(str(e.get('publish_date','')))}</td>
          <td>{html.escape(str(e.get('effective_date','')))}</td>
          <td>{html.escape(str(e.get('issue_org','')))}</td>
          <td>{html.escape(str(e.get('doc_no','')))}</td>
          <td>{html.escape(str(e.get('summary',''))[:160])}</td>
          <td>{html.escape(str(e.get('content_length','')))}</td>
          <td>{att_cell}</td>
          <td>{'有效' if e.get('active') else '已移除'}</td>
          <td>{html.escape(str(e.get('change','')))}</td>
        </tr>""")
    doc = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>财政部法规数据库抓取结果</title>
<style>
 body{{font-family:-apple-system,"Microsoft YaHei",sans-serif;margin:24px;color:#222}}
 h1{{font-size:20px}} table{{border-collapse:collapse;width:100%;font-size:13px}}
 th,td{{border:1px solid #ddd;padding:6px 8px;text-align:left;vertical-align:top}}
 th{{background:#f5f7fa}} tr:nth-child(even){{background:#fafafa}}
 a{{color:#1E6FD9;text-decoration:none}}
 .meta{{color:#888;font-size:12px;margin-bottom:12px}}
</style></head><body>
<h1>财政部法规数据库抓取结果</h1>
<div class="meta">来源：{HOST} ｜ 条目数：{len(entries)} ｜ 生成时间：{datetime.now().isoformat(timespec='seconds')}</div>
<table>
<thead><tr><th>#</th><th>标题</th><th>类别</th><th>公布日期</th><th>生效日期</th>
<th>发文机关</th><th>发文字号</th><th>内容摘要</th><th>正文字数</th><th>附件</th><th>状态</th><th>变更</th></tr></thead>
<tbody>{''.join(items_html)}</tbody>
</table></body></html>"""
    _atomic_write(path, doc)

# --------------------------------------------------------------------------- #
# 主程序
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="财政部法规数据库自动抓取脚本（增量 diff 更新）")
    ap.add_argument("--categories", nargs="*", default=list(DEFAULT_CATEGORIES.keys()),
                    help="要抓取的类别 lfgccId 列表（默认两个目标 URL 对应的类别）")
    ap.add_argument("--names", nargs="*", default=None,
                    help="与 --categories 对应的展示名称（可选）")
    ap.add_argument("--outdir", default=os.path.join(REPO_ROOT, "data", "raw"),
                    help="输出目录（默认统一 data/raw/：regulatory_scrapers/data/raw）")
    ap.add_argument("--size", type=int, default=50, help="列表每页条数（默认50）")
    ap.add_argument("--delay-min", type=float, default=0.3, help="请求最小间隔(秒)")
    ap.add_argument("--delay-max", type=float, default=0.7, help="请求最大间隔(秒)")
    ap.add_argument("--no-detail", action="store_true", help="仅抓列表，不逐条请求详情页（同时跳过附件下载）")
    ap.add_argument("--no-attachments", action="store_true",
                    help="跳过附件下载（仅主数据全量）。附件主机 10.1.60.36:8888 实测 100%% HTTP 502 "
                         "（195 URL/成功 0），默认重试 6 次退避约 180s/附件，会让全量任务空转数十小时。"
                         "默认 False＝保持原行为（抓取附件）。")
    ap.add_argument("--refetch-attachments", action="store_true",
                    help="强制重新抓取并下载全部条目的附件（用于补抓历史条目附件，默认仅新增/变更条目抓附件）")
    ap.add_argument("--max-items", type=int, default=None, help="每类别最多抓取条目数（验证用）")
    ap.add_argument("--timeout", type=int, default=30, help="单请求超时(秒)")
    ap.add_argument("--cache-dir", default="",
                    help="请求缓存目录（显式覆盖）：缺省由 cache_store.source_cache_root(mof) 统一解析"
                         "→ modules/regulatory_scrapers/cache/mof（单物理根）")
    ap.add_argument("--offline", action="store_true",
                    help="纯离线模式：仅读取 --cache-dir 缓存，缓存缺失即跳过（不联网）")
    ap.add_argument("--csv", action="store_true",
                    help="额外输出 CSV 主库表格（默认仅写 JSON，2026-09-09 规范）")
    args = ap.parse_args()

    # 通用缓存（五源统一抽象层）：统一根绑定 + 离线开关
    _init_cache(args.cache_dir or None, args.offline)

    # 名称映射
    if args.names and len(args.names) == len(args.categories):
        cat_map = dict(zip(args.categories, args.names, strict=False))
    else:
        cat_map = {k: DEFAULT_CATEGORIES.get(k, k) for k in args.categories}

    os.makedirs(args.outdir, exist_ok=True)

    # —— 调度可靠性：运行锁防并发重复 ——
    lock_path = os.path.join(args.outdir, "scrape.lock")
    if not acquire_lock(lock_path):
        logger.error("已有抓取任务在运行（锁文件存在且未超龄），本次跳过以避免重复抓取。")
        sys.exit(0)

    status = {
        "last_run_time": datetime.now(UTC).isoformat(),
        "mode": "incremental",
        "last_success_time": None,
        "last_success_count": None,
        "active_count": None,
        "expected_count": None,
        "added": 0, "updated": 0, "removed": 0, "unchanged": 0,
        "warning": None,
        "last_error": None,
        "last_error_time": None,
    }
    try:
        rate = RateLimiter(args.delay_min, args.delay_max)
        fetch_detail_enabled = not args.no_detail

        # —— 载入历史存储（mof_laws.json 本身即为持久化存储）——
        store_path = os.path.join(args.outdir, "mof_laws.json")
        prev_items = []
        if os.path.exists(store_path):
            try:
                pd = json.load(open(store_path, encoding="utf-8"))
                if isinstance(pd, dict):
                    prev_items = pd.get("items", []) or []
                elif isinstance(pd, list):
                    prev_items = pd
            except Exception as e:
                logger.warning("历史存储读取失败，本次将作为全量新增处理：%s", e)
                prev_items = []
        prev_by_id = {it.get("id"): it for it in prev_items if it.get("id")}

        new_store = []
        current_raw_by_id = {}
        expected_total = 0
        added = updated = unchanged = 0

        for lfgcc in args.categories:
            raw_records, exp = fetch_category_records(
                lfgcc, cat_map.get(lfgcc, lfgcc),
                size=args.size, rate=rate, max_items=args.max_items,
            )
            expected_total += exp
            refetch_att = args.refetch_attachments and fetch_detail_enabled
            # 附件主机不可用时可整体跳过附件（--no-attachments），保障主数据全量不被拖垮
            fetch_att = fetch_detail_enabled and not args.no_attachments
            for rec in raw_records:
                rid = rec.get("id")
                current_raw_by_id[rid] = rec
                prev = prev_by_id.get(rid)
                sig_changed = prev is not None and _sig_current(rec) != _sig_prev(prev)
                if prev is None:
                    # 新增：抓详情以补全字段 + 抓附件
                    detail = fetch_detail(rid, rate) if fetch_detail_enabled else None
                    atts = collect_attachments(rid, rate) if fetch_att else []
                    entry = build_entry(rec, detail, lfgcc, cat_map.get(lfgcc, lfgcc),
                                       fetch_detail_enabled, attachments=atts)
                    entry["active"] = True
                    entry["change"] = "added"
                    added += 1
                elif sig_changed:
                    # 变更：重新抓详情与附件
                    detail = fetch_detail(rid, rate) if fetch_detail_enabled else None
                    atts = collect_attachments(rid, rate) if fetch_att else []
                    entry = build_entry(rec, detail, lfgcc, cat_map.get(lfgcc, lfgcc),
                                       fetch_detail_enabled, attachments=atts)
                    entry["active"] = True
                    entry["change"] = "updated"
                    updated += 1
                else:
                    # 未变：保留历史记录（含历史 attachments），跳过详情与附件抓取
                    entry = dict(prev)
                    if refetch_att and fetch_att:
                        atts = collect_attachments(rid, rate)
                        entry["attachments"] = atts
                        entry["attachment_count"] = len(atts)
                        entry["attachment_names"] = "; ".join(
                            a.get("file_name", "") for a in atts)
                    entry["active"] = True
                    entry["change"] = "unchanged"
                    unchanged += 1
                new_store.append(entry)

        # —— 历史中存在但本次列表已无 -> 视为已移除（保留记录，标记 inactive）——
        removed = 0
        for rid, prev in prev_by_id.items():
            if rid not in current_raw_by_id:
                e = dict(prev)
                e["active"] = False
                e["change"] = "removed"
                e["removed_at"] = datetime.now(UTC).isoformat()
                new_store.append(e)
                removed += 1

        active_count = sum(1 for e in new_store if e.get("active"))

        # —— 漏抓自检：活跃条数应与当前列表预期总量一致（验证模式除外）——
        if expected_total and not args.max_items and active_count != expected_total:
            status["warning"] = (
                f"活跃条目 {active_count} 与预期 {expected_total} 不一致，"
                "可能抓取期间数据发生变更，建议复核。"
            )
            logger.warning(status["warning"])

        # —— 原子写输出（失败时不覆盖上一次成功结果）——
        json_path = store_path
        csv_path = os.path.join(args.outdir, "mof_laws.csv")
        html_path = os.path.join(os.path.dirname(args.outdir), "reports", "mof_laws_report.html")
        save_json(new_store, json_path)
        if getattr(args, "csv", False):            # 默认仅 JSON 主库（2026-09-09 规范）
            save_csv(new_store, csv_path)
        save_html_report(new_store, html_path)

        status.update({
            "last_success_time": datetime.now(UTC).isoformat(),
            "last_success_count": len(new_store),
            "active_count": active_count,
            "expected_count": expected_total,
            "added": added, "updated": updated, "removed": removed, "unchanged": unchanged,
        })
        logger.info("✅ 增量更新完成：新增 %d / 更新 %d / 移除 %d / 未变 %d；活跃 %d 条。",
                    added, updated, removed, unchanged, active_count)
        logger.info("   JSON : %s", json_path)
        logger.info("   CSV  : %s", csv_path)
        logger.info("   HTML : %s", html_path)
    except Exception as e:
        # —— 异常状态处理：记录错误，保留上一次成功结果（不覆盖）——
        status["last_error"] = f"{type(e).__name__}: {e}"
        status["last_error_time"] = datetime.now(UTC).isoformat()
        logger.exception("抓取失败：%s", e)
    finally:
        write_status(args.outdir, status)
        release_lock(lock_path)

    # 异常时以非 0 退出，便于调度系统感知失败
    if status["last_error"]:
        sys.exit(1)

if __name__ == "__main__":
    main()
