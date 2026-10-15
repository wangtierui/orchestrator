"""D1诊断第 0 波：信号源可用性自检。

五阶段审查发现的最上游问题：**「告警从未真正送达」**——`reports/_alerts` 目录不存在，
即 notify 的file 后端从未落盘过。这意味着任何以告警为输入的诊断能力都建立在
「从未被验证过的通道」上。

本工具做两件事：
  1. **实测每个信号源的可用性**（存在性 + 数据量 + 最后活动时间），产出可比对的表；
  2. **对 notify 落盘面做 dry-run 自检**（写一个探针文件再删掉）——证明通道**现在**可达，
     而不只是"配置看起来对"。

输出 `reports/diagnosis/signal_availability.json`，供门禁与确认台消费。
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone

from bootstrap import bootstrap

bootstrap("all")

import paths
from config.exitcodes import ExitCode

TZ8 = timezone(timedelta(hours=8))


def _now_iso() -> str:
    return datetime.now(TZ8).replace(microsecond=0).isoformat()


def _probe_notify_channel() -> dict:
    """notify 落盘面**可达性**探针：写一个探针文件再删除（不触发真实告警）。

    为什么用探针而不是真发一条告警：真发会污染告警面（外部 watcher 可能消费），
    且受控事件集要求合法事件名——探针能验证「目录可创建/可写」，这正是通道可达的充分条件。
    """
    out = {"checked": "notify_file_channel", "ok": False, "detail": ""}
    try:
        from std_lib.common_lib import notify as N

        d = N.DEFAULT_DIR
        out["dir"] = os.path.relpath(d, paths.ROOT)
        out["exists_before"] = os.path.isdir(d)
        os.makedirs(d, exist_ok=True)
        probe = os.path.join(d, f"_probe_{int(time.time())}.tmp")
        with open(probe, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("probe")
        readable = os.path.getsize(probe) > 0
        os.remove(probe)
        out["ok"] = bool(readable and os.path.isdir(d))
        out["detail"] = "目录可创建且可写；探针已清理"
        if not out["exists_before"]:
            out["note"] = "目录此前**不存在** ⇒ 自notify 落地以来从未落盘过告警"
    except Exception as e:  # noqa: BLE001
        out["detail"] = f"{type(e).__name__}: {str(e)[:160]}"
    return out


def _table_stats() -> list[dict]:
    """治理库各表：行数 + 最后写入时间（信号是否"活着"）。"""
    import sqlite3

    db = os.path.join(paths.DATA_DIR, "governance.db")
    if not os.path.exists(db):
        return [{"table": "(db)", "rows": None, "last_write": "", "note": "治理库不存在"}]
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    out: list[dict] = []
    for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' "
                            "ORDER BY name").fetchall():
        try:
            n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        except sqlite3.Error:
            continue
        last = ""
        cols = {d[1] for d in con.execute(f"PRAGMA table_info({t})")}
        for cand in ("recorded_at", "created_at", "ts", "decided_at", "resolved_at"):
            if cand in cols:
                try:
                    row = con.execute(f"SELECT MAX({cand}) FROM {t}").fetchone()
                    last = str(row[0] or "")
                except sqlite3.Error:
                    last = ""
                break
        out.append({"table": t, "rows": n, "last_write": last})
    con.close()
    return out


def _step_logs_stats() -> dict:
    """步骤日志目录：文件数 + 最后修改时间（取证面是否可用）。"""
    d = os.path.join(paths.REPORTS_DIR, "_tmp", "step_logs")
    if not os.path.isdir(d):
        return {"dir_exists": False, "files": 0, "last_write": "", "note": "目录不存在"}
    files = [f for f in os.listdir(d) if f.endswith(".log")]
    newest = max((os.path.getmtime(os.path.join(d, f)) for f in files), default=0.0)
    return {"dir_exists": True, "files": len(files),
            "last_write": datetime.fromtimestamp(newest, TZ8).isoformat() if newest else ""}


def _alerts_stats() -> dict:
    """告警目录：文件数 + 最近告警（判断「告警是否真的送达过」）。"""
    d = os.path.join(paths.REPORTS_DIR, "_alerts")
    if not os.path.isdir(d):
        return {"dir_exists": False, "files": 0, "note": "目录不存在 ⇒ 从未落盘过告警"}
    fs = sorted(os.listdir(d))
    return {"dir_exists": True, "files": len([f for f in fs if not f.startswith("_probe_")]),
            "latest": fs[-1] if fs else ""}


def collect() -> dict:
    """采集全部信号源可用性（只读 + 一个 notify 探针）。"""
    return {
        "generated_at": _now_iso(),
        "db_tables": _table_stats(),
        "step_logs": _step_logs_stats(),
        "alerts": _alerts_stats(),
        "notify_channel": _probe_notify_channel(),
    }


def main(argv: list[str] | None = None) -> int:
    doc = collect()
    out_dir = os.path.join(paths.REPORTS_DIR, "diagnosis")
    os.makedirs(out_dir, exist_ok=True)
    p = os.path.join(out_dir, "signal_availability.json")
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)

    print(f"[signal] 采集完成 → {os.path.relpath(p, paths.ROOT)}")
    print(f"{'信号源':28s} {'行数/文件':>10s}  最后活动")
    for r in doc["db_tables"]:
        print(f"  table:{r['table']:22s} {str(r['rows']):>10s}  {r['last_write'][:19]}")
    sl = doc["step_logs"]
    print(f"  dir:step_logs{'':11s} {sl['files']:>10d}  {sl.get('last_write', '')[:19]}")
    al = doc["alerts"]
    print(f"  dir:_alerts{'':13s} {al['files']:>10d}  {al.get('latest', '')}")
    nc = doc["notify_channel"]
    print(f"\n[signal] notify 通道探针：ok={nc['ok']} {nc.get('detail', '')}")
    if nc.get("note"):
        print(f"[signal] ⚠ {nc['note']}")
    # 空信号源清单（供人工判断哪些诊断能力还不能依赖它）
    empties = [r["table"] for r in doc["db_tables"] if (r["rows"] or 0) == 0]
    if empties:
        print(f"[signal] 空表（诊断能力不可依赖）：{empties}")
    return ExitCode.OK


if __name__ == "__main__":
    raise SystemExit(main())
