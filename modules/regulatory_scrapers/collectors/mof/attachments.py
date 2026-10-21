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
import json
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request

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
    from std_lib.scraper_std.cache_store import OfflineMiss

_RESP = None  # ResponseCache 实例；None 表示未启用缓存
_OfflineMiss = OfflineMiss  # 兼容别名



# --------------------------------------------------------------------------- #
# 常量与依赖别名：**单一出处已下沉到 `att_constants.py`**
# --------------------------------------------------------------------------- #
# 2026-10-20（D-遗留拆分第三批）：本文件原有 19 个常量 + 8 处 try-import 别名，
# 与 12 个函数混在 668 行里。常量/别名下沉后在本文件**仍需可达**
# （`mof/attachments.py` 是兄弟模块与外部脚本的共同入口）⇒ 显式 re-export。
#
# ⚠️ **双形态导入**：`collector.py` 有两条进入本模块的路径——包形态
# （`from . import attachments`）与**脚本形态**（`import attachments`，生产
# `COLLECT_CMD` 就是这样调的）。后者没有包上下文，相对导入会抛
# `attempted relative import with no known parent package` ⇒ 必须兜底。
try:
    from .att_constants import (
        _ATTACH_KIND,
        _MAX_ATT_502_STREAK,
        _STYLE_RE,
        _TAG_RE,
        _WS_RE,
        ATTACH_PATH,
        ATTACHMENTS_DIR,
        BASE,
        CC_USER_AGENTS,
        DEFAULT_CATEGORIES,
        ENRICH_LEGACY,
        HEADERS,
        HOST,
        LEGACY_KINDS,
        REPO_ROOT,
        SRC_DIR,
        OfflineMiss,
        bind_source_cache,
        docs_root,
        extract_document_text,
        logger,
        rich_object_fields,
        structured_table_fields,
    )
except ImportError:  # pragma: no cover  脚本形态（生产调度）
    from att_constants import (  # type: ignore[no-redef]  # noqa: F401  re-export
        _ATTACH_KIND,
        _MAX_ATT_502_STREAK,
        _STYLE_RE,
        _TAG_RE,
        _WS_RE,
        ATTACH_PATH,
        ATTACHMENTS_DIR,
        BASE,
        CC_USER_AGENTS,
        DEFAULT_CATEGORIES,
        ENRICH_LEGACY,
        HEADERS,
        HOST,
        LEGACY_KINDS,
        REPO_ROOT,
        SRC_DIR,
        OfflineMiss,
        bind_source_cache,
        docs_root,
        extract_document_text,
        logger,
        rich_object_fields,
        structured_table_fields,
    )

# 纯变换层（html→text / 日期归一 / 文件名安全 / 缓存键 / 条目组装）同样下沉，
# 本文件 re-export 以保持既有导入点（`from mof.attachments import build_entry`）不变。
try:
    from .att_extract import (
        _mof_cache_key,
        build_entry,
        html_to_text,
        normalize_date,
        safe_filename,
    )
except ImportError:  # pragma: no cover  脚本形态
    from att_extract import (  # type: ignore[no-redef]
        _mof_cache_key,
        build_entry,
        html_to_text,
        normalize_date,
        safe_filename,
    )

# --------------------------------------------------------------------------- #
# **本文件保留的可变状态**（刻意不下沉：跨调用可变的全局不能搬，否则
# `global` 语义跨模块、变成两处状态 ⇒ 熔断失效且难查）
# --------------------------------------------------------------------------- #
_RESP = None  # ResponseCache 实例；None 表示未启用缓存
_OfflineMiss = OfflineMiss  # 兼容别名

_ATT_502_STREAK = 0
_ATT_CIRCUIT_OPEN = False

_LOCKS: dict = {}   # lock_path -> fs_lock.ProcessLock（release_lock 需释放同一实例）

def _request(method, path, payload=None, *, rate=None, timeout=30,
             max_retries=4):
    """发起一次 HTTP 请求，返回解析后的 JSON dict。含重试与指数退避。

    2026-09-05：委托通用缓存层 std_lib/scraper_std.cache_store.ResponseCache——
    命中读盘跳过网络；offline 下缺失抛 OfflineMiss；仅成功响应（code==200）落盘。
    """
    endpoint, params = _mof_cache_key(method, path, payload)

    # 1) 缓存命中：续跑 / 离线
    if _RESP is not None:
        cp = _RESP.path(endpoint, params)
        if os.path.exists(cp):
            try:
                with open(cp, encoding="utf-8") as fh:
                    return json.load(fh)
            except Exception:  # noqa: BLE001  采集容错（字段/附件缺失不阻断采集）
                pass  # 缓存损坏则重新请求
        if _RESP.offline:
            raise _OfflineMiss("%s" % endpoint)

    # 2) 实时请求
    url = HOST + path
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = dict(HEADERS)
    if method == "POST":
        headers["Content-Type"] = "application/json;charset=UTF-8"
    # 反爬加固：每次请求轮换 UA（避免单一 UA 被风控聚类）
    headers["User-Agent"] = random.choice(CC_USER_AGENTS)

    if rate is not None:
        rate.wait()

    last_err: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            req = urllib.request.Request(
                url, data=body, headers=headers, method=method
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
            data = json.loads(raw)
            # 写入缓存（仅成功响应：mof 三接口均以 code==200 标识成功）
            if _RESP is not None and isinstance(data, dict) and data.get("code") == 200:
                try:
                    _RESP.put(endpoint, params, data)
                except Exception:  # noqa: BLE001  采集容错（字段/附件缺失不阻断采集）
                    pass
            return data
        except urllib.error.HTTPError as e:
            last_err = e
            # 429/503 等限流类错误：长退避 + 全站降温
            if e.code in (429, 503, 502, 500):
                backoff = min(2 ** attempt * 1.5, 30) + random.uniform(0, 1)
                if rate is not None:
                    rate.penalize(backoff)
                logger.warning(
                    "HTTP %s @ %s (尝试 %d/%d)，%s 秒后重试",
                    e.code, path, attempt, max_retries, round(backoff, 1),
                )
                time.sleep(backoff)
                continue
            # 其它 HTTP 错误直接抛出（如 404 数据不存在）
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last_err = e
            backoff = min(2 ** attempt, 15) + random.uniform(0, 1)
            logger.warning(
                "网络异常 @ %s (尝试 %d/%d)：%s，%s 秒后重试",
                path, attempt, max_retries, e, round(backoff, 1),
            )
            time.sleep(backoff)
            continue
    raise RuntimeError(f"请求失败（已达最大重试）：{path} -> {last_err}")


def fetch_list_page(lfgcc, page, size, rate):
    """抓取某一页列表，返回 (records, total, pages)。"""
    payload = {
        "queryMap": {"lfgcc": lfgcc, "linvalid": "0"},
        "size": size,
        "current": page,
    }
    resp = _request("POST", f"{BASE}/lawFile/list", payload, rate=rate)
    if resp.get("code") != 200 or "data" not in resp:
        raise RuntimeError(f"列表接口返回异常：{resp.get('message')} | {str(resp)[:200]}")
    d = resp["data"]
    # 接口返回的分页字段可能为字符串，统一转 int
    def _to_int(v, default=0):
        try:
            return int(v)
        except (TypeError, ValueError):
            return default
    return d.get("records", []), _to_int(d.get("total")), _to_int(d.get("pages"))


def fetch_attachments(law_id, rate, file_types=("0", "90"), timeout=20):
    """获取某条法规的附件清单（JSON 元数据）。返回 list[dict]。

    接口：GET /file/f/get?infoId=<id>&fileType=0|90
    返回 data 为列表，每项含 id/fileName/fileUrl/serverIp/extension/fileType。
    """
    items = []
    for ft in file_types:
        try:
            url = f"{ATTACH_PATH}?infoId={urllib.parse.quote(str(law_id))}&fileType={ft}"
            resp = _request("GET", url, rate=rate, timeout=timeout)
            if resp.get("code") != 200 or not resp.get("data"):
                continue
            for it in resp["data"]:
                if not isinstance(it, dict):
                    continue
                server = (it.get("serverIp") or "").strip()
                furl = (it.get("fileUrl") or "").strip()
                if not server or not furl:
                    continue
                if not server.lower().startswith("http"):
                    server = "http://" + server
                full = server.rstrip("/") + (furl if furl.startswith("/") else "/" + furl)
                items.append({
                    "file_id": it.get("id"),
                    "file_name": it.get("fileName") or f"{law_id}_{ft}",
                    "file_type": ft,
                    "kind": _ATTACH_KIND.get(str(ft), "other"),
                    "extension": (it.get("extension") or "").lower(),
                    "file_url": full,
                })
        except Exception as e:  # noqa: BLE001
            logger.warning("附件清单获取失败 id=%s fileType=%s：%s", law_id, ft, e)
    return items


def _finalize_attachment(att, data, fname, law_id, dest):
    """附件字节 → 落盘 + 文本/表格/富内容抽取，回填 att（下载与本地复用共用）。"""
    with open(dest, "wb") as f:
        f.write(data)
    att["local_path"] = os.path.relpath(dest, REPO_ROOT).replace("\\", "/")
    att["size_bytes"] = len(data)
    # 批 50：记住 fetch 阶段写入的**角色**（fileType 0=相关附件 / 90=下载文字版）——下方
    # `structured_table_fields`/`rich_object_fields` 会把 kind/file_type 覆盖成文件类型，须还原
    _role_kind = str(att.get("kind") or "")
    _role_ftype = str(att.get("file_type") or "")
    try:
        ext = extract_document_text(data, fname)
        att["text"] = ext.get("text", "")
        att["extracted"] = ext.get("extracted", False)
        att["extract_status"] = ext.get("extract_status", "unsupported")
        att["attachment_kind"] = ext.get("kind", "unknown")
        # 批 55/W-W2：sniff 二次判定证据入库（见 gov 同注）
        if ext.get("sniff"):
            att["attachment_sniff"] = ext["sniff"]
        att["sha256"] = ext.get("sha256", "")
        att["needs_ocr"] = ext.get("needs_ocr", False)
        att["garble_ratio"] = ext.get("garble_ratio", 0.0)
        # 批 55/F1：**对齐 gov 口径** —— 旧二进制格式（.doc/.xls 等 OLE2）默认**跳过**表格/富内容轨
        # （gov 早已如此；mof 原实现未对齐，与 2026-10-10 实测 `collect:mof` 用尽 7200s 超时上限被强杀相关）
        if ENRICH_LEGACY or str(att.get("attachment_kind") or "") not in LEGACY_KINDS:
            try:
                att.update(structured_table_fields(data, fname))
            except Exception:  # noqa: BLE001  采集容错（字段/附件缺失不阻断采集）
                pass
        else:
            att.setdefault("table_recovery_method", "legacy_skipped")
            logger.info("跳过旧格式附件的表格轨（legacy_skipped）：%s", att.get("file_name"))
        try:
            att.update(rich_object_fields(data, fname,
                                          image_dir=docs_root("mof", "diagrams"),
                                          rec_key=str(law_id)))
        except Exception:  # noqa: BLE001  采集容错（字段/附件缺失不阻断采集）
            pass
        # 批 50：还原**角色**语义（表格/富内容字段不得覆盖）⇒ 写入侧据此把正文载体分流到 body_docs
        att["kind"] = _role_kind or "attachment"
        att["file_type"] = _role_ftype
        att["role"] = "text_version" if (_role_ftype == "90" or _role_kind == "text_version") else "attachment"
    except Exception as e:  # noqa: BLE001
        logger.warning("附件文本抽取异常 %s：%s", att.get("file_url"), e)
        att["extracted"] = False
        att["extract_status"] = "extract_error"
    return att


def download_attachment(att, law_id, outdir, rate, timeout=60, max_retries=6):
    """下载单个附件到 data/docs/mof_regulations_scraper/attachments/<law_id>/，返回带本地相对路径与大小的元数据；失败返回 None。

    增量策略（2026-09-10，附件主机 502 环境；2026-10-20 TD-11 修订）：
      - **幂等复用**：本地已存在非空副本 → 直接本地重抽（不再请求，规避重复下载/502 空转）；
      - **主机不可用熔断**：502/503 仅重试 2 次即跳过；**网络异常（超时/连接拒绝/
        DNS 失败）与 502 共用同一连续计数**，连续 8 次即熔断，本次运行后续附件全部
        跳过；**任一次成功即清零**（熔断语义是"连续"不可用，不清零会把健康主机的
        偶发抖动累积成误熔断）。
        ```
        为什么把网络异常也计入（TD-11 实测）：
          原实现只计 502 ⇒ 主机**超时**时永不熔断 ⇒ 每附件走满 6 次退避 ≈150s，
          序列无限延续 ⇒ 全链实跑 14419.7s（4h 硬上限）仍跑不完，mof raw 停滞 10 天。
        ```
      - 429/500 保留指数退避重试；404 等不可恢复直接放弃。
    """
    global _ATT_502_STREAK, _ATT_CIRCUIT_OPEN
    if _ATT_CIRCUIT_OPEN:
        return None
    attach_dir = os.path.join(ATTACHMENTS_DIR, str(law_id))
    os.makedirs(attach_dir, exist_ok=True)
    fname = safe_filename(att.get("file_name"), att.get("extension"),
                          f"{law_id}_{att.get('file_type')}")
    dest = os.path.join(attach_dir, fname)
    # 幂等复用：本地已有非空副本 → 不再下载，仅本地重抽（增量续跑核心）
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        try:
            with open(dest, "rb") as f:
                return _finalize_attachment(att, f.read(), fname, law_id, dest)
        except OSError:
            pass
    last_err: Exception | None = None
    for attempt in range(1, max_retries + 1):
        if rate is not None:
            rate.wait()
        try:
            req = urllib.request.Request(att["file_url"], headers=HEADERS)
            # 反爬加固：每次下载轮换 UA
            req.add_header("User-Agent", random.choice(CC_USER_AGENTS))
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
            # 成功即**清零**连续失败计数：熔断的语义是"**连续**不可用"，
            # 若不清零，一天里的偶发抖动会跨小时累积到 8 次 ⇒ 把健康主机误熔断。
            _ATT_502_STREAK = 0
            return _finalize_attachment(att, data, fname, law_id, dest)
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code in (502, 503):
                # 附件主机限流/不可用：仅重试 2 次，连续达阈值熔断（防数十小时空转）
                _ATT_502_STREAK += 1
                if _ATT_502_STREAK >= _MAX_ATT_502_STREAK:
                    _ATT_CIRCUIT_OPEN = True
                    logger.error("附件主机连续 %d 次 502/503，熔断：本次运行剩余附件跳过。",
                                 _ATT_502_STREAK)
                    return None
                if attempt >= 2:
                    logger.warning("附件下载 HTTP %s @ %s：跳过（熔断计数 %d）",
                                   e.code, att.get("file_url"), _ATT_502_STREAK)
                    return None
                backoff = min(2 ** attempt * 2.0, 20) + random.uniform(0, 1)
                if rate is not None:
                    rate.penalize(backoff)
                time.sleep(backoff)
                continue
            if e.code in (429, 500):
                backoff = min(2 ** attempt * 2.0, 60) + random.uniform(0, 1)
                if rate is not None:
                    rate.penalize(backoff)
                logger.warning(
                    "附件下载 HTTP %s @ %s (尝试 %d/%d)，%ss 后重试",
                    e.code, att.get("file_url"), attempt, max_retries,
                    round(backoff, 1),
                )
                time.sleep(backoff)
                continue
            # 404 等不可恢复错误：直接放弃该附件
            logger.warning("附件下载不可恢复 HTTP %s @ %s：%s",
                           e.code, att.get("file_url"), e)
            return None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last_err = e
            # ⚠️ TD-11（2026-10-20）**这是 4h 硬上限被耗尽的真正主因**：
            # 原实现只把 **502/503** 计入熔断（`_ATT_502_STREAK`），而**网络异常
            # （超时 / 连接拒绝 / DNS 失败）不计入、也永不熔断** ⇒ 附件主机
            # 不可达时，每个附件都走满 `max_retries=6` 次、每次退避 ≤30+1s
            # ≈ **150s/附件**，且序列永不终止（实测全链实跑 14419.7s 仍跑不完，
            # 使 mof raw 停滞 10 天）。
            # 修法：网络异常**与 502 共用同一条熔断计数**——二者都是"主机不可用"
            # 的同义信号，分开计会让任一单独出现时都熔不断。
            _ATT_502_STREAK += 1
            if _ATT_502_STREAK >= _MAX_ATT_502_STREAK:
                _ATT_CIRCUIT_OPEN = True
                logger.error(
                    "附件主机连续 %d 次不可用（含网络异常），熔断：本次运行剩余附件跳过。",
                    _ATT_502_STREAK)
                return None
            backoff = min(2 ** attempt * 1.5, 30) + random.uniform(0, 1)
            logger.warning(
                "附件下载网络异常 (尝试 %d/%d，熔断计数 %d/%d)：%s，%ss 后重试",
                attempt, max_retries, _ATT_502_STREAK, _MAX_ATT_502_STREAK,
                e, round(backoff, 1),
            )
            time.sleep(backoff)
            continue
    logger.warning("附件下载失败 %s：%s", att.get("file_url"), last_err)
    return None


def collect_attachments(law_id, rate, timeout=60, deadline=None):
    """抓取并下载某条法规的全部附件，返回带本地路径的元数据列表。

    timeout 透传至 download_attachment，便于调用方在慢速/限流场景下收紧超时。
    附件主机熔断（连续 502）后不再尝试下载。

    `deadline`（`time.time()` 绝对时刻；None=不限）—— **软耗时上限的可靠落点**。
    为什么必须细到这里（2026-10-21 第一百零六批实测）：`--max-seconds` 原先只在
    **每条记录开始时**检查一次 ⇒ "单条记录很重"时上限完全失效：实测
    `--max-seconds 7200` 实际跑到 **13113s（超 82%）**，最后被父进程 4h 硬超时
    杀掉（`rc=-1 TIMEOUT`）—— 把"可控早停"变成了"被强杀"。一条记录的耗时主要在
    附件（下载 + PDF/表格解析），故检查点必须落到附件循环内。
    """
    if _ATT_CIRCUIT_OPEN:
        return []
    meta = fetch_attachments(law_id, rate, timeout=timeout)
    if not meta:
        return []
    result = []
    for att in meta:
        if deadline is not None and time.time() > deadline:
            logger.warning("附件下载达软耗时上限 ⇒ 本条剩余附件跳过（下次续抓）")
            break
        d = download_attachment(att, law_id, ATTACHMENTS_DIR, rate, timeout=timeout)
        if d:
            result.append(d)
    return result


def fetch_category_records(lfgcc, category_name, *, size=50, rate, max_items=None):
    """分页拉取某类别的列表原始记录（含 lcontent），不抓详情、不做条目级构建。

    返回 (raw_records, expected_total)。增量 diff 与详情抓取在 main 中按 id 进行。
    """
    logger.info("拉取类别 [%s] %s 列表", lfgcc, category_name)
    records = []
    expected_total = 0
    page = 1
    seen_ids = set()
    while True:
        recs, total, pages = fetch_list_page(lfgcc, page, size, rate)
        if page == 1:
            expected_total = total  # 首页总量作为"预期条数"，用于漏抓自检
        logger.info("  第 %d/%d 页，本页 %d 条（全量共 %d 条）",
                    page, max(pages, 1), len(recs), total)
        for rec in recs:
            rid = rec.get("id")
            if rid in seen_ids:
                continue
            seen_ids.add(rid)
            records.append(rec)
            if max_items and len(records) >= max_items:
                logger.info("  已达到 --max-items 上限 %d，停止。", max_items)
                return records, expected_total
        if page >= max(pages, 1) or not recs:
            break
        page += 1
    logger.info("类别 [%s] 列表拉取完成，共 %d 条。", lfgcc, len(records))
    return records, expected_total


__all__ = ["_finalize_attachment", "_mof_cache_key", "_request", "build_entry", "collect_attachments", "download_attachment", "fetch_attachments", "fetch_category_records", "fetch_list_page", "html_to_text", "normalize_date", "safe_filename"]
