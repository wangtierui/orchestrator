#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""nfra 限流间隔测量器（自动化）。

目标：测出 nfra「一次成功之后，隔多久才能再成功一次」的具体间隔 I，
并判断失败是否会刷新（重置）计时器。

流程：
  A. 轮询直到出现一次成功（长封锁解除），记为 S。
  B. 从 S 起阶梯探测 60/120/.../3600s 发单请求，第一个成功的位置给出 I 的区间，再二分细化。
  C. 重置测试：在 B 的成功点 T0 立刻补打一次失败 F（落在冷却内必败），
     再验证「S+2I 能否成功」（无重置）与「F+I 能否成功」（重置）。
所有请求都追加到 nfra_probe_log.jsonl（与单请求脚本同一文件）。

用法（后台跑，解锁后自动测量）：
  nohup python scripts/nfra_measure_interval.py > scripts/measure.out 2>&1 &
"""
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
LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nfra_probe_log.jsonl")
ID = "928"
PROBE_INTERVAL_S = 120  # 阶段 A 轮询间隔


def log(kind, note, **extra):
    rec = {"kind": kind, "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
           "id": ID, "note": note}
    rec.update(extra)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def fire(retries=0, note=""):
    t0 = time.time()
    try:
        d = http_get_json(LIST, params={"itemId": ID, "pageSize": 18, "pageIndex": 1},
                          extra_headers=H, retries=retries)
        rc = d.get("rptCode")
    except Exception:
        rc = None
    ok = (rc == 200)
    log("list", note, rptCode=rc, ok=ok, retries=retries, dur_s=round(time.time() - t0, 2))
    return ok


def heartbeat(remain):
    print("[*] 心跳：还剩 %.0f 秒" % remain, flush=True)


def sleep_until(target):
    remain = target - time.time()
    while remain > 0:
        s = min(30.0, remain)
        time.sleep(s)
        remain -= s
        heartbeat(remain)


def main():
    # ---- 阶段 A：等封锁解除 ----
    print("[A] 轮询等待长封锁解除（每 %ds 一发）..." % PROBE_INTERVAL_S, flush=True)
    S = None
    while S is None:
        if fire(retries=0, note="phaseA-poll"):
            S = time.time()
            print("[A] 封锁解除，成功锚点 S=%s" % time.strftime("%H:%M:%S", time.localtime(S)),
                  flush=True)
        else:
            sleep_until(time.time() + PROBE_INTERVAL_S)

    # ---- 阶段 B：阶梯测间隔 ----
    print("[B] 从 S 起阶梯测量间隔...", flush=True)
    candidates = [60, 120, 180, 240, 300, 360, 480, 600, 900, 1200, 1800, 2700, 3600]
    prev = 0
    lo = hi = None
    for c in candidates:
        sleep_until(S + c)
        if fire(retries=0, note="phaseB-stair-%d" % c):
            lo, hi = prev, c
            print("[B] 首个成功候选=%ds，区间 (%d,%d]" % (c, prev, c), flush=True)
            break
        prev = c
    if hi is None:
        print("[B] 3600s 内仍未成功，间隔>=1h，停止。", flush=True)
        return

    # 二分细化（最多 3 步，约 ±15s）
    for _ in range(3):
        if hi - lo <= 15:
            break
        mid = (lo + hi) // 2
        sleep_until(S + mid)
        if fire(retries=0, note="phaseB-bisect-%d" % mid):
            hi = mid
        else:
            lo = mid
    I_est = hi
    print("[B] 测得间隔 I≈%d s（区间 [%d,%d]）" % (I_est, lo, hi), flush=True)

    # ---- 阶段 C：重置测试 ----
    # 当前成功点 T0 ≈ S+I_est。先落一个保证失败的 F（冷却内），δ=30s。
    print("[C] 重置测试：在成功点补打失败 F...", flush=True)
    sleep_until(S + I_est + 30)
    fire(retries=0, note="phaseC-fail-probe")  # F，预期失败
    # 无重置视角：下一个成功应在 S+2I；重置视角：应在 F+I = S+2I+30
    sleep_until(S + 2 * I_est + 15)            # 落在两者中间
    ok_mid = fire(retries=0, note="phaseC-from-S+2I-mid")
    if not ok_mid:
        sleep_until(S + 2 * I_est + 45)        # 偏重置侧
        ok_late = fire(retries=0, note="phaseC-from-F+I-late")
    else:
        ok_late = None
    log("measure", "phaseC-result", I_est=I_est, interval_lo=lo, interval_hi=hi,
        from_S_ok=ok_mid, from_F_ok=(ok_late if ok_late is not None else "n/a"))
    print("[C] 完成。I≈%ds；from_success_ok=%s；from_failure_ok=%s"
          % (I_est, ok_mid, ok_late), flush=True)


if __name__ == "__main__":
    main()
