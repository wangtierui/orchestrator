# -*- coding: utf-8 -*-
"""
自适应详情补抓脚本（fill-missing + WAF 冷却）：
  * 仅抓取 cache/ 中缺失详情的文件（跳过已缓存，天然可续跑）；
  * 命中 WAF 拦截页(HTML)时指数退避重试，连续失败达阈值则进入"冷却"长睡，
    避免在无意义重试上耗尽配额、也避免被永久封禁；
  * 成功即重置连续失败计数；进度写入 state 文件，便于监控与续跑；
  * 单进程、低速率（默认 3s 间隔），尽量贴合站点反爬节奏。

用法：
  python nfra_fill_details.py            # 默认：缺失即补，自动冷却
  python nfra_fill_details.py --delay 4  # 更保守的间隔
  python nfra_fill_details.py --cooldown 300 --max-consecutive-fail 3
"""

# ---- P3b 仓库引导（migrate_collectors_p3b.py 注入）：使 std_lib/config 可导入 ----
import os as _os
import sys as _sys

_GUIDE_ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "..", ".."))
if _GUIDE_ROOT not in _sys.path:
    _sys.path.insert(0, _GUIDE_ROOT)
del _GUIDE_ROOT, _os, _sys
import argparse
import glob
import json
import os
import subprocess
import sys
import time
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import nfra_collector as m

from std_lib.scraper_std.cache_store import source_cache_root  # noqa: E402

CACHE = source_cache_root("nfra")  # 单一物理缓存根（modules/regulatory_scrapers/cache/nfra）
m.set_cache_dir(CACHE)

BASE = "https://www.nfra.gov.cn"
API = BASE + "/cbircweb"
LIST = API + "/DocInfo/SelectDocByItemIdAndChild"
CHILD = API + "/DocInfo/SelectItemAndDocByItemPId"
DETAIL = API + "/DocInfo/SelectByDocId"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
REF = (BASE + "/cn/view/pages/ItemList.html?itemPId=923&itemId=926"
       "&itemUrl=ItemListRightMore.html&itemName=%E6%94%BF%E7%AD%96%E6%B3%95%E8%A7%84")
COOKIE_JAR = os.path.join(HERE, "config", "cookies.txt")
STATE_FILE = os.path.join(HERE, "state", "fill_details.state.json")

def _warmup():
    try:
        subprocess.run(["curl", "-s", "-L", "-A", UA, "-c", COOKIE_JAR,
                        "--max-time", "30", BASE + "/cn/view/pages/index/index.html"],
                       capture_output=True, timeout=40)
    except Exception:
        pass
    time.sleep(1.0)

def _load_doc_ids():
    """从已缓存的列表页中提取全部去重 doc_id。"""
    ids = []
    seen = set()
    files = sorted(glob.glob(os.path.join(CACHE, "SelectDocByItemIdAndChild*.json")))
    for f in files:
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        data = d.get("data", {})
        if not isinstance(data, dict) or "rows" not in data:
            continue
        for r in (data.get("rows") or []):
            did = r.get("docId")
            if did is not None and did not in seen:
                seen.add(did)
                ids.append(did)
    return ids

def _fetch_one(did, delay):
    """抓取单篇详情。成功返回 True，WAF/失败返回 False（不写盘污染）。"""
    params = {"docId": did}
    full = DETAIL + "?" + urllib.parse.urlencode(params)
    for attempt in range(1, 4):
        try:
            r = subprocess.run(
                ["curl", "-s", "-L", "-A", UA, "-H", "Referer: " + REF,
                 "-b", COOKIE_JAR, "-c", COOKIE_JAR,
                 "--max-time", "30", full],
                capture_output=True, timeout=40)
            out = r.stdout
            if r.returncode == 0 and out.strip().startswith(b"{"):
                with open(m._cache_path(DETAIL, params), "wb") as f:
                    f.write(out)
                time.sleep(delay)
                return True
        except Exception:
            pass
        time.sleep(2 ** attempt)
    return False

def _read_state():
    if os.path.exists(STATE_FILE):
        try:
            return json.load(open(STATE_FILE, encoding="utf-8"))
        except Exception:
            pass
    return {}

def _write_state(st):
    try:
        json.dump(st, open(STATE_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    except Exception:
        pass

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--delay", type=float, default=3.0, help="成功请求间的间隔(秒)")
    ap.add_argument("--cooldown", type=int, default=240, help="连续失败后冷却睡眠(秒)")
    ap.add_argument("--max-consecutive-fail", type=int, default=3,
                    help="连续失败达到该值即进入冷却")
    ap.add_argument("--round-limit", type=int, default=0,
                    help="本轮最多抓取的篇数(0=不限，抓完所有缺失)")
    args = ap.parse_args()

    all_ids = _load_doc_ids()
    cached = set()
    for f in glob.glob(os.path.join(CACHE, "SelectByDocId__docId_*.json")):
        # 文件名形如 SelectByDocId__docId_1199538.json
        base = os.path.basename(f)
        try:
            did = int(base.split("docId_")[1].split(".")[0])
            cached.add(did)
        except Exception:
            pass
    missing = [i for i in all_ids if i not in cached]
    print("[fill] 总文档=%d | 已缓存详情=%d | 待补=%d"
          % (len(all_ids), len(cached), len(missing)))

    if not missing:
        print("[fill] 无缺失，全部详情已就位。")
        st = _read_state()
        st["done"] = True
        st["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        _write_state(st)
        return

    _warmup()
    st = _read_state()
    done_round = 0
    consecutive_fail = 0
    fetched_this_run = 0
    t0 = time.time()
    for idx, did in enumerate(missing, 1):
        if args.round_limit and fetched_this_run >= args.round_limit:
            print("[fill] 达到本轮上限 %d 篇，停止。" % args.round_limit)
            break
        ok = _fetch_one(did, args.delay)
        if ok:
            consecutive_fail = 0
            fetched_this_run += 1
            done_round += 1
            if idx % 10 == 0 or idx == len(missing):
                print("  [进度] %d/%d  本轮新抓=%d  已用=%.0fs"
                      % (idx, len(missing), fetched_this_run, time.time() - t0))
        else:
            consecutive_fail += 1
            print("  [WAF/FAIL] docId=%s 连续失败=%d/%d"
                  % (did, consecutive_fail, args.max_consecutive_fail))
            if consecutive_fail >= args.max_consecutive_fail:
                print("  [冷却] 连续被拦截，睡眠 %ds 以降温..." % args.cooldown)
                time.sleep(args.cooldown)
                _warmup()  # 冷却后重新预热 cookie
                consecutive_fail = 0

    remaining = len(missing) - fetched_this_run
    st["done"] = (remaining == 0)
    st["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    st["total"] = len(all_ids)
    st["cached"] = len(cached) + fetched_this_run
    st["remaining"] = remaining
    st["fetched_this_run"] = fetched_this_run
    _write_state(st)
    print("\n[fill] 本轮新抓=%d | 仍缺失=%d | 进度=%d/%d"
          % (fetched_this_run, remaining, len(cached) + fetched_this_run, len(all_ids)))

if __name__ == "__main__":
    main()
