#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
预取脚本：用稳定的 curl 通道把目标站点的真实 JSON 数据拉取到 cache/ 目录。
命名规则与 nfra_collector.py 的 _cache_path 完全一致，
因此主脚本在 --cache-dir cache 下可直接离线解析，无需再次联网。

适用场景：
  * 当前运行环境对 Python urllib 多连接有网关限制时，用 curl 绕过；
  * 任何需要"先抓原始数据、后离线处理"的 reproducible 流程。
"""

# ---- P3b 仓库引导（migrate_collectors_p3b.py 注入）：使 std_lib/config 可导入 ----
import os as _os
import sys as _sys

_GUIDE_ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "..", ".."))
if _GUIDE_ROOT not in _sys.path:
    _sys.path.insert(0, _GUIDE_ROOT)
del _GUIDE_ROOT, _os, _sys
import json
import os
import subprocess
import sys
import time
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import nfra_collector as m  # 复用 _cache_path / 缓存命名

from std_lib.scraper_std.cache_store import source_cache_root  # noqa: E402

CACHE = source_cache_root("nfra")  # 单一物理缓存根（modules/regulatory_scrapers/cache/nfra）
m.set_cache_dir(CACHE)

BASE = "https://www.nfra.gov.cn"
API = BASE + "/cbircweb"
CHILD = API + "/DocInfo/SelectItemAndDocByItemPId"
LIST = API + "/DocInfo/SelectDocByItemIdAndChild"
DETAIL = API + "/DocInfo/SelectByDocId"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
REF = (BASE + "/cn/view/pages/ItemList.html?itemPId=923&itemId=926"
       "&itemUrl=ItemListRightMore.html&itemName=%E6%94%BF%E7%AD%96%E6%B3%95%E8%A7%84")
COOKIE_JAR = os.path.join(HERE, "config", "cookies.txt")

def _warmup():
    """首页预热：拿到会话 cookie，模拟真实浏览器，降低被 WAF 限流概率。"""
    try:
        subprocess.run(["curl", "-s", "-L", "-A", UA, "-c", COOKIE_JAR,
                        "--max-time", "30", BASE + "/cn/view/pages/index/index.html"],
                       capture_output=True, timeout=40)
    except Exception:  # noqa: BLE001  采集容错（字段/附件缺失不阻断采集）
        pass
    time.sleep(1.0)

def fetch_curl(url, params, label, delay=2.0):
    full = url + "?" + urllib.parse.urlencode(params)
    cp = m._cache_path(url, params)
    if os.path.exists(cp):
        print("  [cache] %s" % label)
        return True
    for attempt in range(1, 5):
        try:
            r = subprocess.run(
                ["curl", "-s", "-L", "-A", UA, "-H", "Referer: " + REF,
                 "-b", COOKIE_JAR, "-c", COOKIE_JAR,
                 "--max-time", "30", full],
                capture_output=True, timeout=40)
            out = r.stdout
            if r.returncode == 0 and out.strip().startswith(b"{"):
                with open(cp, "wb") as f:
                    f.write(out)
                print("  [ok] %s (%d bytes)" % (label, len(out)))
                time.sleep(delay)
                return True
            print("  [retry %d] %s rc=%s first=%r" % (attempt, label, r.returncode,
                                                      out[:40] if out else out))
        except Exception as e:
            print("  [err] %s %s" % (label, e))
        time.sleep(2 ** attempt)
    print("  [FAIL] %s" % label)
    return False

def main():
    _warmup()
    # 1) 栏目结构
    fetch_curl(CHILD, {"itemId": 926, "pageSize": 50}, "child(926)", delay=1.0)
    ch = json.load(open(m._cache_path(CHILD, {"itemId": 926, "pageSize": 50}),
                        encoding="utf-8"))["data"]
    items = [(it["itemId"], it["itemName"]) for it in ch] + [(926, "政策法规(本级)")]

    # 2) 各栏目列表（分页）
    doc_ids = []
    for iid, name in items:
        p, total, got = 1, None, 0
        while True:
            fetch_curl(LIST, {"itemId": iid, "pageSize": 18, "pageIndex": p},
                       "list %s p%d" % (name, p))
            data = json.load(open(m._cache_path(LIST, {"itemId": iid, "pageSize": 18,
                                                       "pageIndex": p}), encoding="utf-8"))["data"]
            if total is None:
                total = data.get("total", 0)
            rows = data.get("rows") or []
            for r in rows:
                did = r.get("docId")
                if did is not None and did not in doc_ids:
                    doc_ids.append(did)
                got += 1
            if not rows or got >= total:
                break
            p += 1

    # 3) 逐篇详情
    for did in doc_ids:
        fetch_curl(DETAIL, {"docId": did}, "detail %s" % did, delay=1.8)

    print("\n预取完成：%d 个文档缓存于 %s" % (len(doc_ids), CACHE))

if __name__ == "__main__":
    main()
