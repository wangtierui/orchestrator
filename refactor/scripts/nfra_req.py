#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""nfra 探测请求记录器。

每次调用发 1 个请求，并追加一行 JSON 到同目录的 nfra_probe_log.jsonl，
记录：时间戳、请求类型(list/detail)、id、参数、retries、结果(rptCode/error)、耗时。
用于事后分析 nfra 的限流/额度规律（时间 + 请求 + 结果 三件套）。

用法：
  python scripts/nfra_req.py --kind list   --id 928 --retries 1
  python scripts/nfra_req.py --kind detail --id 344358 --retries 0 --delay 2
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collectors.lib.http import http_get_json

H = {
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://www.nfra.gov.cn/cn/view/pages/ItemList.html?itemPId=923&itemId=926&itemUrl=ItemListRightMore.html&itemName=%E6%94%BF%E7%AD%96%E6%B3%95%E8%A7%84",
    "Accept": "application/json, text/javascript, */*; q=0.01",
}
LIST = "https://www.nfra.gov.cn/cbircweb/DocInfo/SelectDocByItemIdAndChild"
DET = "https://www.nfra.gov.cn/cbircweb/DocInfo/SelectByDocId"

LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nfra_probe_log.jsonl")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", choices=["list", "detail"], required=True)
    ap.add_argument("--id", type=str, required=True,
                    help="列表用 itemId；详情用 docId")
    ap.add_argument("--retries", type=int, default=1)
    ap.add_argument("--page", type=int, default=1)
    ap.add_argument("--delay", type=float, default=0.0,
                    help="发请求前的预休眠秒数（模拟间隔）")
    ap.add_argument("--after-success-min", type=float, default=0.0,
                    help="发请求前，先睡到『日志里上次成功时间 + N 分钟』之后"
                         "（用于验证限流计时器是否只从成功算起）")
    ap.add_argument("--note", type=str, default="")
    args = ap.parse_args()

    if args.after_success_min > 0:
        last_ok = None
        if os.path.exists(LOG):
            with open(LOG, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = json.loads(line)
                    except Exception:
                        continue
                    if r.get("ok") is True and r.get("rptCode") == 200:
                        last_ok = r["ts"]
        if not last_ok:
            print("[WARN] 日志里找不到成功记录，无法计算 --after-success-min，直接发")
        else:
            t_ok = time.mktime(time.strptime(last_ok, "%Y-%m-%dT%H:%M:%S"))
            target = t_ok + args.after_success_min * 60
            now = time.time()
            if target > now:
                print("[*] 上次成功 %s，睡到 %s 再发（约 %.0f 秒）"
                      % (last_ok, time.strftime("%H:%M:%S", time.localtime(target)),
                         target - now), flush=True)
                remain = target - now
                while remain > 0:
                    s = min(30.0, remain)
                    time.sleep(s)
                    remain -= s
                    print("[*] 心跳：还剩 %.0f 秒" % remain, flush=True)
            else:
                print("[WARN] 目标时间 %s 已过，直接发（结果可能无判别意义）"
                      % time.strftime("%H:%M:%S", time.localtime(target)), flush=True)

    if args.kind == "list":
        url, params = LIST, {"itemId": args.id, "pageSize": 18, "pageIndex": args.page}
    else:
        url, params = DET, {"docId": args.id}

    if args.delay > 0:
        time.sleep(args.delay)

    t0 = time.time()
    rec = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t0)),
        "kind": args.kind,
        "id": args.id,
        "params": params,
        "retries": args.retries,
        "note": args.note,
    }
    try:
        d = http_get_json(url, params=params, extra_headers=H, retries=args.retries)
        rec["rptCode"] = d.get("rptCode")
        rec["ok"] = (rec["rptCode"] == 200)
        if args.kind == "list":
            rows = (d.get("data") or {}).get("rows") or []
            rec["rows"] = len(rows)
    except Exception as e:
        rec["rptCode"] = None
        rec["ok"] = False
        rec["error"] = "%s: %s" % (type(e).__name__, str(e)[:80])
    rec["dur_s"] = round(time.time() - t0, 2)

    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(json.dumps(rec, ensure_ascii=False))


if __name__ == "__main__":
    main()
