#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
补全"政策法规"完整列表缓存：分页抓取 927(法律法规) 与 928(政策规章规范性文件) 的
全部列表页，写入 nfra_collector.py 的缓存目录（与 --cache-dir 共用命名）。
仅抓列表（含 docId/标题/日期/链接），不含详情；详情由主脚本按需抓取。
WAF 弹性：cookie 预热 + 重试 + 退避 + 间隔。
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

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import nfra_collector as m

CACHE = os.path.join(HERE, "cache", "nfra")  # 五源统一缓存根
m.set_cache_dir(CACHE)

API = m.API
LIST = API + "/DocInfo/SelectDocByItemIdAndChild"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
REF = (API.rsplit("/cbircweb", 1)[0] +
       "/cn/view/pages/ItemList.html?itemPId=923&itemId=926"
       "&itemUrl=ItemListRightMore.html&itemName=%E6%94%BF%E7%AD%96%E6%B3%95%E8%A7%84")
CK = os.path.join(HERE, "config", "cookies.txt")

def _warmup():
    try:
        subprocess.run(["curl", "-s", "-L", "-A", UA, "-c", CK, "--max-time", "30",
                        API.rsplit("/cbircweb", 1)[0] + "/cn/view/pages/index/index.html"],
                       capture_output=True, timeout=40)
    except Exception:
        pass
    time.sleep(1.0)

def fetch_list_page(item_id, page, delay=1.5):
    params = {"itemId": item_id, "pageSize": 18, "pageIndex": page}
    cp = m._cache_path(LIST, params)
    if os.path.exists(cp):
        return json.load(open(cp, encoding="utf-8"))
    full = LIST + "?" + "&".join("%s=%s" % (k, v) for k, v in params.items())
    for attempt in range(1, 5):
        try:
            r = subprocess.run(["curl", "-s", "-L", "-A", UA, "-H", "Referer: " + REF,
                                "-b", CK, "-c", CK, "--max-time", "30", full],
                               capture_output=True, timeout=40)
            out = r.stdout
            if r.returncode == 0 and out.strip().startswith(b"{"):
                d = json.loads(out)
                if d.get("rptCode") == 200:
                    with open(cp, "w", encoding="utf-8") as f:
                        json.dump(d, f, ensure_ascii=False)
                    time.sleep(delay)
                    return d
            print("  [retry %d] 928 p%d first=%r" % (attempt, page, out[:40]))
        except Exception as e:
            print("  [err] 928 p%d %s" % (page, e))
        time.sleep(2 ** attempt)
    print("  [FAIL] 928 p%d" % page)
    return None

def get_total(item_id, max_tries=15):
    """稳健获取某栏目总量：对第 1 页反复重试，直到 WAF 冷却返回成功。"""
    for t in range(max_tries):
        d = fetch_list_page(item_id, 1, delay=2.0)
        if d:
            return d["data"]["total"]
        print("  [total-retry %d] 冷却中…" % t)
        time.sleep(6)
    return None

def main():
    _warmup()
    all_ids = []
    for iid, name in [(927, "法律法规"), (928, "政策规章规范性文件")]:
        total = get_total(iid)
        if total is None:
            print("  !! 无法获取 %s 总量，跳过" % name)
            continue
        npages = (total + 17) // 18
        got = 0
        for p in range(1, npages + 1):
            d = fetch_list_page(iid, p)
            if d:
                rows = d["data"].get("rows") or []
                for r in rows:
                    did = r.get("docId")
                    if did is not None and did not in all_ids:
                        all_ids.append(did)
                got += len(rows)
            else:
                print("  [miss] %s p%d" % (name, p))
        print("  %s 完成: total=%d 页数=%d 唯一docId=%d" % (name, total, npages, got))
    print("\n列表抓取完成：唯一 docId = %d" % len(all_ids))

if __name__ == "__main__":
    main()
