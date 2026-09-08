#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
nfra 政策数据 周级自动刷新编排器（由自动化每周二 01:00 调度）。

设计目标（对应需求：稳定可靠、避免重复/漏抓、保留异常处理能力）：
  0. 单实例锁：避免与残留/并发进程重叠（防止双进程重复抓取、加倍触发 WAF）。
  1. 列表刷新（获取最新数据，自适应顶部窗口，绝不重拉全量→不暴露 WAF）：
       - 仅删除并强制重拉 pageIndex=1（捕获最新发布项 + 取得各栏目 live total）；
       - 依据各栏目 total 增量自适应计算需刷新的顶部页数 K=⌈Δ/18⌉（仅覆盖新增项所在页码）；
         若当周新增较多(K>1)再补拉第 2..K 页；始终只刷顶部窗口，绝不重拉全部 ~98 页；
       - 其余深层旧页命中缓存、不重复抓取；天然不漏抓新增项（API 按时间倒序，新项在顶部）；
       - 校验：刷新后 docId 总数 >= 备份基线；若下降（被 WAF 拦截导致刷新失败），
         自动回滚备份并告警，本周沿用旧列表（不丢数据）。
       - 取舍：深层列表中"被站点下架/删除"的条目不会被主动发现（需重拉全量才有此能力，
         但那会暴露 WAF，按约定不采用）；此类 stale 项保留在结果中，影响极小。
  2. 详情续跑：nfra_fill_details.py --delay 3 --cooldown 150 --max-consecutive-fail 3
       （只补缺失、可续跑、cache 落盘；命中 WAF 自动冷却，无 --round-limit 尽量多抓）。
  3. 离线重建：nfra_collector.py --cache-dir cache --offline --delay-min 0 --delay-max 0 --out-dir data/raw
  4. 写运行摘要 weekly_refresh.last.json，并打印结论。

异常状态处理：
  - 列表刷新降级 -> 回滚备份并告警，继续用旧列表（不丢数据）。
  - fill 命中 WAF -> 自带冷却退避；若超出安全时长被终止，cache 已落盘，下一周自动续跑。
  - 进程被沙箱中断 -> cache 已落盘，下一轮调度自动续跑。
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
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
from std_lib.scraper_std.cache_store import source_cache_root  # noqa: E402

CACHE = source_cache_root("nfra")  # 单一物理缓存根（modules/regulatory_scrapers/cache/nfra）
LOCK = os.path.join(HERE, "weekly_refresh.lock")
BACKUP_ROOT = os.path.join(CACHE, "_list_backup")
PY = sys.executable

LIST_GLOB = os.path.join(CACHE, "SelectDocByItemIdAndChild*.json")
PAGE1_GLOB = os.path.join(CACHE, "SelectDocByItemIdAndChild*_pageIndex_1_*.json")
STATE_FILE = os.path.join(HERE, "state", "fill_details.state.json")
LAST_FILE = os.path.join(HERE, "state", "weekly_refresh.last.json")

LOCK_MAX_AGE = 6 * 3600  # 锁超过 6h 视为残留，可回收

def _count_docids():
    ids = set()
    for f in glob.glob(LIST_GLOB):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        rows = (d.get("data", {}) or {}).get("rows") or []
        for r in rows:
            did = r.get("docId")
            if did is not None:
                ids.add(did)
    return ids

# —— 运行锁统一实现（N-8）：判定逻辑收敛到 regulatory_scrapers/fs_lock.py，四源共用 ——
_SCRAPERS_ROOT = os.path.dirname(HERE)
if _SCRAPERS_ROOT not in sys.path:
    sys.path.insert(0, _SCRAPERS_ROOT)
from std_lib.common_lib import fs_lock

_LOCK = None   # 当前持有的 fs_lock.ProcessLock 实例

def _acquire_lock():
    """获取单实例运行锁（实现统一委托 fs_lock.ProcessLock，N-8）。

    原实现仅按锁文件 mtime 判陈旧（无 PID 存活检测）；统一后为
    「PID 存活 + max_age 陈旧兜底」双判定，且 LOCK_MAX_AGE 阈值保持不变。
    """
    global _LOCK
    lk = fs_lock.ProcessLock(LOCK, max_age_sec=LOCK_MAX_AGE)
    if lk.acquire():
        _LOCK = lk          # 仅成功时登记，避免失败实例覆盖导致 _release_lock 失效
        return True
    return False

def _release_lock():
    """释放单实例运行锁（幂等；未持有则无操作）。"""
    global _LOCK
    if _LOCK is not None:
        _LOCK.release()
        _LOCK = None

def _cleanup_backups(keep=3):
    try:
        dirs = sorted(
            [d for d in glob.glob(os.path.join(BACKUP_ROOT, "*")) if os.path.isdir(d)],
            key=os.path.getmtime,
            reverse=True,
        )
        for old in dirs[keep:]:
            shutil.rmtree(old, ignore_errors=True)
    except Exception:
        pass

MAX_K = 50  # 自适应窗口上限，防止极端批量发布导致请求失控

def _read_totals_from_page1():
    """从 page1 列表缓存读取各栏目 live total（用于计算需要刷新的顶部窗口页数）。"""
    totals = {}
    for f in glob.glob(PAGE1_GLOB):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        data = d.get("data") or {}
        m = re.search(r"itemId_(\d+)", os.path.basename(f))
        if m and "total" in data:
            totals[int(m.group(1))] = data["total"]
    return totals

def _backup_and_delete(pages, bk):
    for f in pages:
        try:
            shutil.copy2(f, os.path.join(bk, os.path.basename(f)))
            os.remove(f)
        except Exception:
            pass

def refresh_list():
    """自适应顶部窗口刷新：仅重拉"可能含新项的顶部 K 页"，绝不重拉全部页（避免 WAF 暴露）。

    返回 (ok, info)。ok=False 表示刷新降级并已回滚。
    """
    info = {"step": "refresh_list"}
    page1 = sorted(glob.glob(PAGE1_GLOB))
    baseline_ids = _count_docids()
    baseline_totals = _read_totals_from_page1()
    info["baseline_docids"] = len(baseline_ids)
    info["baseline_totals"] = baseline_totals
    if not page1:
        info["note"] = "无 pageIndex=1 列表缓存，跳过刷新（保留原列表）"
        return True, info

    ts = time.strftime("%Y%m%d_%H%M%S")
    bk = os.path.join(BACKUP_ROOT, ts)
    os.makedirs(bk, exist_ok=True)

    # 阶段1：刷新 page1（捕获最新发布项 + 取得各栏目 live total）
    _backup_and_delete(page1, bk)
    try:
        subprocess.run([PY, "nfra_fetch_lists.py"], cwd=HERE, check=False,
                       stdout=sys.stdout, stderr=sys.stderr, timeout=1200)
    except Exception as e:
        info["fetch_err"] = str(e)

    new_totals = _read_totals_from_page1()
    info["new_totals"] = new_totals

    # 自适应计算需刷新的顶部页数 K：仅覆盖"新增项"所在页码，绝不全量
    K = 1
    for iid in set(list(baseline_totals) + list(new_totals)):
        b = baseline_totals.get(iid, 0)
        n = new_totals.get(iid, 0)
        delta = max(n - b, 0)            # 该栏目新增条目数（仅新增，删除不计入）
        kk = max(1, (delta + 17) // 18)  # 每页 18 条，向上取整
        K = max(K, kk)
    K = min(K, MAX_K)
    info["window_pages_K"] = K

    # 阶段2：若新增较多（K>1），再刷新第 2..K 页（仍只顶部窗口，非全量）
    if K > 1:
        extra = []
        for k in range(2, K + 1):
            extra += glob.glob(os.path.join(
                CACHE, "SelectDocByItemIdAndChild*_pageIndex_%d_*.json" % k))
        if extra:
            _backup_and_delete(extra, bk)
            try:
                subprocess.run([PY, "nfra_fetch_lists.py"], cwd=HERE, check=False,
                               stdout=sys.stdout, stderr=sys.stderr, timeout=1200)
            except Exception as e:
                info.setdefault("fetch_err", str(e))

    new_ids = _count_docids()
    info["new_docids"] = len(new_ids)
    if len(new_ids) < len(baseline_ids):
        # 刷新降级：回滚全部备份，避免丢失 docId
        for f in glob.glob(os.path.join(bk, "*.json")):
            shutil.copy2(f, CACHE)
        info["degraded"] = True
        info["restored"] = True
        _cleanup_backups()
        return False, info
    info["degraded"] = False
    _cleanup_backups()
    return True, info

def run_fill(timeout=None):
    return subprocess.run(
        [PY, "nfra_fill_details.py", "--delay", "3", "--cooldown", "150",
         "--max-consecutive-fail", "3"],
        cwd=HERE, check=False, stdout=sys.stdout, stderr=sys.stderr, timeout=timeout,
    )

def run_rebuild():
    return subprocess.run(
        [PY, "nfra_collector.py", "--offline",
         "--delay-min", "0", "--delay-max", "0", "--out-dir",
         os.path.join(os.path.dirname(HERE), "data", "raw")],
        cwd=HERE, check=False, stdout=sys.stdout, stderr=sys.stderr,
    )

def run_attachments(timeout=None):
    """附件下载 + PDF 抽取（高精度）。缓存落盘，超时/异常由调用方兜底，不影响重建。"""
    return subprocess.run(
        [PY, "nfra_fetch_attachments.py"],
        cwd=HERE, check=False, stdout=sys.stdout, stderr=sys.stderr, timeout=timeout,
    )

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-fetch", action="store_true",
                    help="仅校验备份/回滚逻辑，不触发任何网络抓取（自检用）")
    args = ap.parse_args()

    if not _acquire_lock():
        print("[weekly] 已有实例在运行（lock 较新），退出以避免重复抓取。")
        sys.exit(0)
    try:
        summary = {"started_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        if args.no_fetch:
            baseline = _count_docids()
            ts = time.strftime("%Y%m%d_%H%M%S")
            bk = os.path.join(BACKUP_ROOT, "selfcheck_" + ts)
            os.makedirs(bk, exist_ok=True)
            for f in glob.glob(PAGE1_GLOB):
                shutil.copy2(f, os.path.join(bk, os.path.basename(f)))
            for f in glob.glob(PAGE1_GLOB):
                os.remove(f)
            for f in glob.glob(os.path.join(bk, "*.json")):
                shutil.copy2(f, CACHE)
            after = _count_docids()
            summary["selfcheck"] = {
                "baseline_docids": len(baseline),
                "after_restore_docids": len(after),
                "selfcheck_ok": len(after) == len(baseline),
            }
            shutil.rmtree(bk, ignore_errors=True)
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return

        ok, info = refresh_list()
        summary["refresh"] = info
        if not ok:
            print("[weekly][WARN] 列表刷新降级，已回滚备份，本周沿用旧列表。")

        fill_timeout = 40 * 60  # 安全时长 40min，超出则终止（cache 已落盘，下周续跑）
        try:
            run_fill(timeout=fill_timeout)
        except subprocess.TimeoutExpired:
            print("[weekly][WARN] fill 超出安全时长被终止，cache 已落盘，下一周自动续跑。")

        # 附件下载 + PDF 抽取（高精度）：缓存落盘，超时/异常不影响后续离线重建
        att_timeout = 40 * 60
        att_ok = True
        try:
            run_attachments(timeout=att_timeout)
        except subprocess.TimeoutExpired:
            att_ok = False
            print("[weekly][WARN] 附件抽取超出安全时长被终止，cache 已落盘，下一周自动续跑。")
        except Exception as e:
            att_ok = False
            print("[weekly][WARN] 附件抽取异常: %s（不影响重建）" % e)

        run_rebuild()

        st = {}
        try:
            st = json.load(open(STATE_FILE, encoding="utf-8"))
        except Exception:
            pass
        summary["fill_state"] = st
        summary["attachments_ok"] = att_ok
        summary["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        try:
            json.dump(summary, open(LAST_FILE, "w", encoding="utf-8"),
                      ensure_ascii=False, indent=2)
        except Exception:
            pass
        print("\n[weekly] 完成。refresh_ok=%s remaining=%s"
              % (ok, st.get("remaining")))
    finally:
        _release_lock()

if __name__ == "__main__":
    main()
