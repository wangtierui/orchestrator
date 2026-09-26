#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
分批驱动脚本：规避长进程资源累积 / 沙箱 OOM。
- 每批仅处理 BATCH 条（已验证 3 条安全），每批是独立 python 进程，退出即释放全部内存；
- 已 fetched 的记录由 pbc_backfill_pdfs.py 自动跳过，故可重复运行直至全部完成；
- 自带「连续 3 批无进展」熔断，避免单条坏记录导致无限重试；
- 进度同时输出到 stdout 与 backfill_progress.log，便于后台监控。
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

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
JSON = os.path.join(ROOT, "data", "raw", "pbc_laws.json")
LOG = os.path.join(ROOT, "logs", "backfill_progress.log")
BATCH = 3
MAX_ITERS = 400

def remaining():
    try:
        d = json.load(open(JSON, encoding="utf-8"))
    except Exception:
        return -1
    return sum(1 for r in d
               if r.get("category") == "规范性文件"
               and not r.get("file_type")
               and (r.get("content") or "").endswith(".pdf")
               and r.get("fetch_status") == "skipped_existing")

def log(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:  # noqa: BLE001  采集容错（字段/附件缺失不阻断采集）
        pass

def main():
    log("分批回填启动：BATCH=%d" % BATCH)
    prev = None
    stuck = 0
    for it in range(1, MAX_ITERS + 1):
        rem = remaining()
        if rem == 0:
            log("全部完成，无剩余目标。")
            return
        if rem < 0:
            log("无法读取 JSON，重试。")
            time.sleep(5)
            continue
        this_batch = min(BATCH, rem)
        log(f"第 {it} 批：剩余 {rem} 条，本批处理 {this_batch} 条")
        rc = subprocess.run(
            [PY, "pbc_backfill_pdfs.py", "--limit", str(this_batch),
             "--delay", "0.3", "--save-every", str(this_batch)],
            cwd=ROOT, timeout=1800,   # 审查 P2-5（2026-09-12）：单批超时防挂起
        ).returncode
        if rc != 0:
            log(f"  本批返回码 {rc}（可能异常），下批将重试未处理项。")
        rem2 = remaining()
        if prev is not None and rem2 >= prev:
            stuck += 1
            if stuck >= 3:
                log(f"连续 3 批无进展（剩余 {rem2}），疑似存在无法处理的记录，停止并请人工核查。")
                return
        else:
            stuck = 0
        prev = rem2
        time.sleep(1)
    log("达到最大批次数，停止。")

if __name__ == "__main__":
    main()
