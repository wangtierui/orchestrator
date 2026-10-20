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
  python collectors/mof/collector.py

  # 只抓列表前若干条做验证（不抓详情，快速）
  python collectors/mof/collector.py --max-items 10 --no-detail

  # 自定义类别与输出目录
  python collectors/mof/collector.py --categories 1000000000000300000 1000000000000400000 \
                            --outdir ./data/raw --delay-min 0.4 --delay-max 0.9
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
REPO_ROOT = os.path.dirname(SRC_DIR)  # regulatory_scrapers（统一数据根）
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
    except Exception as e:  # 单条失败不影响整体  # noqa: BLE001
        logger.warning("详情获取失败 id=%s：%s", law_id, e)
    return None

# --------------------------------------------------------------------------- #
# 附件抓取（前端 JS 异步加载的"相关附件"与"下载文字版"，不在 lcontent 中）
# --------------------------------------------------------------------------- #
ATTACH_PATH = "/file/f/get"  # GET ?infoId=<id>&fileType=0(相关附件)/90(文字版)
_ATTACH_KIND = {"0": "attachment", "90": "text_version"}



# 附件主机 502 熔断（2026-09-10 增量策略）：附件主机 10.1.60.36:8888 曾实测 100% HTTP 502
# 会让全量任务空转数十小时。连续 502 达阈值即熔断，本次运行剩余附件一律跳过（不阻塞主数据）。
#
# ⚠️ N-174（2026-09-30）：**熔断状态的真身不在这里**，在 `collectors/mof/attachments.py:152-156`
# （`_ATT_502_STREAK` / `_ATT_CIRCUIT_OPEN` / `_MAX_ATT_502_STREAK`）—— 下载逻辑
# （`_request` / `download_attachment`）随 P3 拆分迁到了那里（见上方 re-export 块）。
# 此处原有一份**死副本**（三个同名全局，本文件**从不读写**：实测 trio 仅出现在定义处）。
# 它的唯一后果是**让人改错文件**：在这里把阈值 8 改成别的数**不会有任何效果**，而排查者会
# 据此认为"改了却没生效"。故**删除**该副本，并**刻意不做 re-export** ——
# `int`/`bool` 的导入绑定**不会跟随原模块的后续更新**，再导出只会制造"看起来是活的"假象
# （那正是要消除的隐患）。需要观察熔断状态请直接 `import attachments` 后取其同名属性。








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

def _sig_eq(a, b):
    """指纹等价比较：把 None 归一为 '' 后再比。

    背景（2026-09-22 周调度**P0 定位**）：`_sig_current` 对列表侧缺失/不可解析的
    `lSsDate` 走 `normalize_date` → **None**，而主库历史值多为 **''**；`None != ''`
    使 **254 条/周**被误判为"变更" → 触发无谓的详情+附件重抓（附件为本地重抽取，
    单条数秒级）→ 整轮跑不完、主库长期不更新。该字段在列表接口中本就不可得，
    排除其 null 形态差异不损失任何真实可检测的变更能力。
    """
    return (tuple("" if x is None else x for x in a)
            == tuple("" if x is None else x for x in b))

def _canon_law_id(v):
    """法规主键统一到**单一类型**（主库既有约定：纯数字 → int）。

    背景（2026-09-22 周调度**P0 定位**）：`fgk.mof.gov.cn` 列表接口现以**字符串**
    返回 `id`，而主库历史条目为 **int**（同一数值）。原实现 `prev_by_id.get(rid)`
    按原类型取值 → 恒不命中 → **每一条都被判为 "added"**，记录级指纹增量彻底失效，
    表现为每周对全部 ~870 条重抓详情+附件（数小时），主库因此长期停留在旧日期。
    实测：`OVERLAP_raw=0 / OVERLAP_str=90`（100% 同值异型）。
    """
    if v is None:
        return None
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int):
        return v
    s = str(v).strip()
    return int(s) if s.lstrip("-").isdigit() else s

# --------------------------------------------------------------------------- #
# 输出
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# 可靠性增强：原子写、运行锁、状态记录
# --------------------------------------------------------------------------- #
# B7（W4，2026-10）：原 `_atomic_write` 私造已删（与 fs_lock.atomic_write_text 语义重复；
# SSOT 版增加 mkstemp 防并发撞名 + fsync 崩溃安全 + overwrite 参数）。状态/报告写入
# 一律 `fs_lock.atomic_write_text(path, text)`（模块级 import 见下方运行锁段）。

# —— 运行锁统一实现（N-8）：判定逻辑收敛到 regulatory_scrapers/fs_lock.py，四源共用 ——
_SCRAPERS_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _SCRAPERS_ROOT not in sys.path:
    sys.path.insert(0, _SCRAPERS_ROOT)
from std_lib.common_lib import fs_lock

_LOCKS: dict = {}   # lock_path -> fs_lock.ProcessLock（release_lock 需释放同一实例）

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
    fs_lock.atomic_write_text(os.path.join(os.path.dirname(outdir), "state", "status.json"),
                  json.dumps(status, ensure_ascii=False, indent=2))

def save_json(entries, path):
    fs_lock.atomic_write_text(path, json.dumps({
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
    fs_lock.atomic_write_text(path, "\ufeff" + buf.getvalue())

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
    fs_lock.atomic_write_text(path, doc)

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
    ap.add_argument(
        "--max-seconds", type=float, default=0,
        help="**软耗时上限**（秒，默认 0=不限）。到期后**停止继续抓取新条目**，"
             "但会把已抓进度与未处理条目一并落盘（未处理者保留历史记录、标 "
             "`unchanged`），并在 status 里标 `early_stop`。"
             "为什么需要（TD-11，2026-10-20 全链实跑实测）：附件主机不可用时"
             "（超时/502）每附件重试退避可达 150~186s，4h 硬上限被用尽仍跑不完，"
             "导致 mof raw 停滞 10 天。软上限让**主数据先落地**，附件下次续抓。")
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
        # TD-11：本次运行中「新正文疑似被源站截断 ⇒ 沿用历史」的条数。
        # **不静默**：回退是保守决策，必须可查（事后若确认源站真的删减了内容，
        # 据此逐条复核）。未触发时为 0（字段始终存在，下游可无条件读）。
        "content_text_reverted": 0,
        # TD-11（2026-10-20）：软耗时上限到期时的现场（未到期保持 None ⇒ 字段稳定，
        # 下游可无条件读。**不静默**：早停是"本次不完整"的事实，必须可查）。
        "early_stop": None,
        "warning": None,
        "last_error": None,
        "last_error_time": None,
    }
    try:
        rate = RateLimiter(args.delay_min, args.delay_max)
        # TD-11（2026-10-20）：软耗时上限守卫 + 三条"保数据"原语。
        # 逻辑本体在 `mof/limits.py`（纯逻辑、无 IO、可单测）；本文件只做装配。
        # ⚠️ 导入必须**双形态兼容**：本文件既被包内引用，也被调度/CLI 以**脚本形态**
        # 直接跑（`COLLECT_CMD` 形如 `[PY, <绝对路径>, …]`）⇒ 相对导入在脚本形态下
        # 抛 `attempted relative import with no known parent package`。
        # 本仓既有惯例见上方 `attachments` 的 try/except 双导入；`limits` 同理。
        # （实测踩到：只写 `from . import limits` 时，生产 argv 一跑即崩。）
        try:
            from . import limits as _limits
        except ImportError:
            import limits as _limits  # type: ignore[no-redef]

        _stopped_logged = False   # 早停日志只打一次（否则每条 warning 刷屏）
        _guard = _limits.EarlyStopGuard(getattr(args, "max_seconds", 0))
        _CONTENT_SHRINK_RATIO = _limits.CONTENT_SHRINK_RATIO

        def _flush_store() -> str:
            """落盘（正常结束与早停**共用**同一条出口）。

            ⚠️ 为什么必须共用：原实现把落盘内联在 try 末尾，早停若直接 `break`
            就**不会落盘** ⇒ 已抓的进度全丢（等于白跑）。抽成一个出口后，
            早停也能落地。
            """
            p = write_raw_jsonl(store_path, new_store, source=HOST)
            if getattr(args, "csv", False):          # 默认仅 JSON 主库（2026-09-09 规范）
                save_csv(new_store, os.path.join(args.outdir, "mof_laws.csv"))
            save_html_report(
                new_store,
                os.path.join(os.path.dirname(args.outdir), "reports",
                             "mof_laws_report.html"))
            return p
        fetch_detail_enabled = not args.no_detail

        # —— 载入历史存储（mof_laws.json 本身即为持久化存储）——
        store_path = os.path.join(args.outdir, "mof_laws.json")
        prev_items: list = []
        if os.path.exists(resolve_raw_path(store_path)):
            try:
                # 批 49/T1：raw 已统一 JSONL —— `read_raw_records` 双格式（.jsonl 逐行 / 旧 .json 逐条）
                prev_items = read_raw_records(store_path)
            except Exception as e:  # noqa: BLE001
                logger.warning("历史存储读取失败，本次将作为全量新增处理：%s", e)
                prev_items = []
        # 键统一为 str（与 `_canon_law_id` 呼应）：主库为 int、列表接口现返回 str，
        # 若按原类型作键则恒不命中 → 记录级增量退化为全量重抓（见 `_canon_law_id` 注释）。
        prev_by_id = {str(it.get("id")): it for it in prev_items if it.get("id")}

        new_store = []
        current_raw_by_id = {}
        expected_total = 0
        added = updated = unchanged = 0

        for lfgcc in args.categories:
            # 早停后**不再发起列表请求**（连列表分页也省掉——网络已不可用时
            # 每次分页都要等超时）；未抓类别的历史记录由下方"补全"步骤兜住。
            if _guard.stopped:
                logger.warning("已早停 ⇒ 跳过类别 %s 的列表抓取（历史记录保留）", lfgcc)
                continue
            raw_records, exp = fetch_category_records(
                lfgcc, cat_map.get(lfgcc, lfgcc),
                size=args.size, rate=rate, max_items=args.max_items,
            )
            expected_total += exp
            refetch_att = args.refetch_attachments and fetch_detail_enabled
            # 附件主机不可用时可整体跳过附件（--no-attachments），保障主数据全量不被拖垮
            fetch_att = fetch_detail_enabled and not args.no_attachments

            def _atts_for(rid_key, prev_entry, do_fetch: bool):
                """本次要写入的附件元数据。

                **「跳过下载」≠「丢弃历史」**：`build_entry(..., attachments=[])`
                会把 `attachments`/`attachment_count`/`table_structured`/
                `table_raw_text` 四字段整体清空。实测 `--no-attachments` 补抓时
                被重处理的 130 条把主库 63.6MB→36.4MB（差 ≈29.4MB，正是其体量）。
                原语见 `limits.merge_attachments`；`do_fetch` 走形参是为避开
                ruff B023（闭包捕获循环变量 `fetch_att` 的延迟绑定坑）。
                """
                fetched = collect_attachments(rid_key, rate) if do_fetch else []
                return _limits.merge_attachments(
                    fetched, (prev_entry or {}).get("attachments"), do_fetch)
            for rec in raw_records:
                rid = rec.get("id")
                # 网络调用仍用**接口原样 id**；仅比对/记账键做规范化，避免影响请求语义与缓存键
                current_raw_by_id[str(rid)] = rec
                # ── TD-11（2026-10-20）：软耗时上限。到期后**不再发起详情/附件请求**，
                #    但本条仍要进 `current_raw_by_id`（上面已做）——否则它会被
                #    后面的「历史存在但本次列表已无」逻辑误判为已移除。
                if _guard.check() and not _stopped_logged:
                    _stopped_logged = True
                    logger.warning(
                        "达到软耗时上限 %.0fs ⇒ 停止抓取详情/附件；已处理 %d 条，"
                        "剩余条目保留历史记录（标 unchanged）后落盘。",
                        _guard.max_seconds, len(new_store))
                prev = prev_by_id.get(str(rid))
                # ── 早停后：**不再抓详情/附件**，按 `prev` 原样保留 ──
                # 逻辑在 `limits.EarlyStopGuard.keep_prev`（保数据的第一道闸：
                # 跳过而非保留，会让这些 id 被下方 removed 逻辑判成"已移除"）。
                if _guard.stopped:
                    _keep = _guard.keep_prev(prev)
                    if _keep is not None:
                        new_store.append(_keep)
                        unchanged += 1
                    _guard.skipped_in_run += 1
                    continue
                sig_changed = prev is not None and not _sig_eq(_sig_current(rec), _sig_prev(prev))
                if prev is None:
                    # 新增：抓详情以补全字段 + 抓附件
                    detail = fetch_detail(rid, rate) if fetch_detail_enabled else None
                    atts = _atts_for(rid, None, fetch_att)
                    entry = build_entry(rec, detail, lfgcc, cat_map.get(lfgcc, lfgcc),
                                       fetch_detail_enabled, attachments=atts)
                    entry["id"] = _canon_law_id(entry.get("id"))
                    entry["active"] = True
                    entry["change"] = "added"
                    added += 1
                elif sig_changed:
                    # 变更：重新抓详情与附件
                    detail = fetch_detail(rid, rate) if fetch_detail_enabled else None
                    atts = _atts_for(rid, prev, fetch_att)
                    entry = build_entry(rec, detail, lfgcc, cat_map.get(lfgcc, lfgcc),
                                       fetch_detail_enabled, attachments=atts)
                    # ⚠️ TD-11：**「正文疑似被源站截断」时沿用历史**。
                    # 实测（直连接口，非缓存）：源站对长文返回截断内容
                    # （6562 vs 27548 / 14409 vs 50772）⇒ 129 条正文静默缩水 6.0MB。
                    # 判据与权衡见 `limits.content_should_revert`；回退**计数**
                    # （不静默：保守决策须可事后复核）。
                    if prev is not None and _limits.content_should_revert(
                            entry.get("content_text") or "",
                            prev.get("content_text") or "",
                            detail is None):
                        _hist = prev.get("content_text") or ""
                        if len(_hist) > len(entry.get("content_text") or ""):
                            entry["content_text"] = _hist
                            entry["content_length"] = len(_hist)
                            status["content_text_reverted"] = (
                                status.get("content_text_reverted", 0) + 1)
                    entry["id"] = _canon_law_id(entry.get("id"))
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
        # ⚠️ TD-11（2026-10-20）：**早停时禁止执行本段**，二者语义互斥。
        # 实测踩到的严重 bug（已从备份恢复数据）：早停跳过了第 2 个类别（780 条），
        # 那 780 条的 id 自然不在 `current_raw_by_id` ⇒ 被本段判为「已移除」
        # ⇒ 主库 870 条里 **780 条 active 被置 False**。这是**数据破坏**：
        # 「本次列表里没有」有两种完全不同的原因，早停场景下无法区分：
        #   ① 源站真的删了（应当 removed）；
        #   ② 我们根本没抓（绝不能判 removed）。
        removed = 0
        if _guard.stopped:
            logger.warning(
                "早停 ⇒ **跳过「已移除」判定**（未抓取的类别里不能把条目当作已删除；"
                "未进本次结果的 %d 条历史记录将按原样保留）", len(prev_by_id))
        else:
            for rid, prev in prev_by_id.items():
                if rid not in current_raw_by_id:
                    rec = dict(prev)   # N-67：原用 `e`（与 except 变量同名，mypy misc 告警）
                    rec["active"] = False
                    rec["change"] = "removed"
                    rec["removed_at"] = datetime.now(UTC).isoformat()
                    new_store.append(rec)
                    removed += 1

        # ── 早停兜底：把**任何**未进 `new_store` 的历史记录补回来 ──
        # 一个兜底点覆盖三种现场：① 内层已停但被跳过的类别；② 外层直接 continue
        # 的类别；③ `fetch_category_records` 中途异常。三者都会让某些历史 id 缺席，
        # 而不兜底它们就会被下方 removed 逻辑判成已移除。逻辑在 `limits`。
        _restored = _guard.restore_missing(prev_by_id, new_store)
        if _restored:
            unchanged += _restored
            logger.warning("早停兜底：%d 条历史记录按原样保留（未刷新，不再判定为移除）",
                           _restored)
        if _guard.stopped:
            status["early_stop"] = _guard.summary()

        active_count = sum(1 for e in new_store if e.get("active"))

        # —— 漏抓自检：活跃条数应与当前列表预期总量一致（验证模式除外）——
        if expected_total and not args.max_items and active_count != expected_total:
            if _guard.stopped:
                # 早停导致的不完整是**已知且预期**的 ⇒ 不能说成"数据变更建议复核"
                # （那会把运维引向错误的排查方向）。要如实说明"是我们没抓完"。
                status["warning"] = (
                    f"早停：活跃 {active_count} 与预期 {expected_total} 不一致——"
                    f"因达到软耗时上限（{_guard.at_s:.0f}s / {_guard.reason}），"
                    "未抓取的类别待下次续跑；未抓条目的历史记录已按原样保留，"
                    "**未判定为已移除**。")
            else:
                status["warning"] = (
                    f"活跃条目 {active_count} 与预期 {expected_total} 不一致，"
                    "可能抓取期间数据发生变更，建议复核。")
            logger.warning(status["warning"])

        # —— 原子写输出（失败时不覆盖上一次成功结果）——
        # 批 49/T1：改**流式 JSONL** 落盘（原 `save_json` 整表 json.dumps：115MB 主库会多一份整表字符串）
        # TD-11：统一走 `_flush_store()`（正常结束与早停共用同一出口，避免"早停不落盘"）。
        json_path = _flush_store()
        csv_path = os.path.join(args.outdir, "mof_laws.csv")
        html_path = os.path.join(os.path.dirname(args.outdir), "reports",
                                 "mof_laws_report.html")

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
