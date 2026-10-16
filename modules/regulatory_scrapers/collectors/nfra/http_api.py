# -*- coding: utf-8 -*-
"""nfra.http_api — **HTTP 层**（E4 拆分）：会话构造、缓存寻址、JSON 接口取数。

职责边界：**只负责"怎么拿到响应"**（重试 / 缓存 / SSL / UA 轮换），
**不解析、不落盘**（那是 extract 与 collector 的事）。
依赖方向：extract ← http_api ← collector（本层只依赖 extract 的常量 BASE）。
函数体逐字保留，仅改依赖来源。
"""
import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
for _p in (_HERE, _os.path.abspath(_os.path.join(_HERE, "..", "..", "..", ".."))):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)
# 保留 `_HERE`（不删）：常量计算路径需要它
del _p, _os, _sys

import json
import os
import random
import ssl
import time
import urllib

# ⚠️ 导入以**拆分前备份原文**为准（不凭印象写）：`paths` 是**仓根模块**（非
# `std_lib.scraper_std` 的子模块——写错会ImportError 且整层无法加载）；
# `bind_source_cache` / `OfflineMiss` / `docs_root` 来自 `cache_store`
# （`_init_cache` / `_cache_path` 依赖它们）。
from std_lib.scraper_std.cache_store import bind_source_cache

# 层间依赖（HTTP 层 → 解析层的常量；双形态）。
# ⚠️ **必须放在常量区之前**：本层常量 `API = BASE + "/cbircweb"` 在**导入期**求值
# （不是调用期）⇒ 层间 import 放在文件末尾会 NameError。
try:  # 包形态
    from .extract import BASE
except ImportError:  # 脚本形态
    from extract import BASE  # type: ignore[no-redef]

# 端点常量随本层下沉（HTTP 层的输入面）；`BASE` 来自 extract 的常量
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": (
        BASE + "/cn/view/pages/ItemList.html?itemPId=923&itemId=926"
        "&itemUrl=ItemListRightMore.html&itemName=%E6%94%BF%E7%AD%96%E6%B3%95%E8%A7%84"
    ),
}
_SSL_CTX = ssl.create_default_context()
_RESP = None  # ResponseCache 实例；None 表示未启用缓存
API = BASE + "/cbircweb"
PARENT_ITEM_ID = 926                      # 政策法规
LIST_ENDPOINT = API + "/DocInfo/SelectDocByItemIdAndChild"
CHILD_ENDPOINT = API + "/DocInfo/SelectItemAndDocByItemPId"
DETAIL_ENDPOINT = API + "/DocInfo/SelectByDocId"


def make_opener():
    """返回复用的 OpenerDirector（keep-alive 由 http.client 连接池处理）。"""
    return urllib.request.build_opener(urllib.request.HTTPSHandler(context=_SSL_CTX))


def http_get_json(opener, url, params, timeout=25, max_retries=4):
    """带超时与重试退避的 JSON GET。

    非 200、JSON 解析失败、网络异常均触发退避重试；连续失败抛异常由调用方处理。
    若启用缓存（_RESP）且命中，则直接读取本地文件，跳过网络。
    """
    full = url + "?" + urllib.parse.urlencode(params)

    # 1) 缓存命中：离线/续跑
    if _RESP is not None:
        cp = _cache_path(url, params)
        if os.path.exists(cp):
            try:
                with open(cp, encoding="utf-8") as fh:
                    return json.load(fh)
            except Exception:  # noqa: BLE001  采集容错（字段/附件缺失不阻断采集）
                pass  # 缓存损坏则重新请求
        if _RESP.offline:
            # 离线模式：缓存缺失直接跳过，避免无意义的联网重试
            raise _OfflineMiss("%s ? %s" % (url, urllib.parse.urlencode(params)))

    # 2) 实时请求
    last_err = None
    for attempt in range(1, max_retries + 1):
        req = urllib.request.Request(full, headers=DEFAULT_HEADERS, method="GET")
        # 反爬加固：每次请求轮换 UA（避免单一 UA 被风控聚类）
        req.add_header("User-Agent", random.choice(USER_AGENTS))
        try:
            with opener.open(req, timeout=timeout) as resp:
                status = resp.getcode()
                raw = resp.read().decode("utf-8", errors="replace")
            if status != 200:
                # 限流 / 服务端错误 -> 退避后重试
                raise urllib.error.HTTPError(full, status, "HTTP %d" % status, None, None)  # type: ignore[arg-type]
            data = json.loads(raw)
            if data.get("rptCode") != 200:
                raise RuntimeError("接口返回非成功码 rptCode=%s msg=%s"
                                   % (data.get("rptCode"), data.get("msg")))
            # 写入缓存（仅成功响应）
            if _RESP is not None:
                try:
                    _RESP.put(url, params, data)
                except Exception:  # noqa: BLE001  采集容错（字段/附件缺失不阻断采集）
                    pass
            return data
        except (TimeoutError, urllib.error.HTTPError, urllib.error.URLError, ConnectionError, json.JSONDecodeError, RuntimeError) as e:
            last_err = e
            if attempt < max_retries:
                wait = 2 ** attempt + random.uniform(0, 1)
                print("      [retry %d/%d] %s，%.1fs 后重试" % (attempt, max_retries, e, wait),
                      flush=True)
                time.sleep(wait)
    raise RuntimeError("请求失败（已重试 %d 次）: %s" % (max_retries, last_err))


def _cache_path(url, params):
    """根据接口与参数生成确定性缓存文件名（委托通用模块，命名算法保持不变）。"""
    if _RESP is None:
        raise RuntimeError("缓存未初始化：请先调用 set_cache_dir(path)")
    return _RESP.path(url, params)


def _init_cache(path=None, offline=False):
    """统一缓存根绑定（缺省 cache_store.source_cache_root("nfra")，单物理根）；path 显式可覆盖。"""
    global _RESP
    _RESP = bind_source_cache("nfra", "json", root=path)
    if offline and _RESP is not None:
        _RESP.set_offline(True)


def set_cache_dir(path):
    """兼容旧调用（同目录脚本）：仅设根，沿用当前离线态。"""
    _init_cache(path)


def set_offline(flag):
    if _RESP is not None:
        _RESP.set_offline(flag)
