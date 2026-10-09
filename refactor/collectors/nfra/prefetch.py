#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""预取脚本（refactor 迁入版；原 nfra_prefetch.py 逐函数等价）。

用稳定的 curl 通道把目标站点的真实 JSON 数据拉取到 cache/ 目录；命名与 collector._cache_path 一致，
主脚本在 --cache-dir 下可直接离线解析。
"""
# ---- 仓库引导（使 std_lib/config 可导入）----
import os as _os
import sys as _sys

_GUIDE_ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "..", ".."))
if _GUIDE_ROOT not in _sys.path:
    _sys.path.insert(0, _GUIDE_ROOT)
del _GUIDE_ROOT, _os, _sys
import json
import os
import subprocess
import time

# R-2（2026-09-30）拾取节流/冷却**具名常量**（数值不变，零行为变更）。
_WARMUP_COOLDOWN_S = 1.0

import urllib.parse

# 复用主采集器 + 共用件（包内绝对导入，脚本/模块双兼容）
from refactor.collectors.nfra import collector as m
from refactor.collectors.nfra.common import warmup
from std_lib.scraper_std.cache_store import source_cache_root

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = source_cache_root("nfra")  # 单一物理缓存根
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
        except Exception as e:  # noqa: BLE001
            print("  [err] %s %s" % (label, e))
        time.sleep(2 ** attempt)
    print("  [FAIL] %s" % label)
    return False

def main():
    warmup(COOKIE_JAR)
    fetch_curl(CHILD, {"itemId": 926, "pageSize": 50}, "child(926)", delay=1.0)
    ch = json.load(open(m._cache_path(CHILD, {"itemId": 926, "pageSize": 50}),
                        encoding="utf-8"))["data"]
    items = [(it["itemId"], it["itemName"]) for it in ch] + [(926, "政策法规(本级)")]

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

    for did in doc_ids:
        fetch_curl(DETAIL, {"docId": did}, "detail %s" % did, delay=1.8)

    print("\n预取完成：%d 个文档缓存于 %s" % (len(doc_ids), CACHE))

if __name__ == "__main__":
    main()
