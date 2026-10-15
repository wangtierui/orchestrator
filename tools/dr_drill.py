"""DR 演练执行器（方案 §13.4）：从备份恢复到服务验证的**全程计时**，记录落台账。

演练范围（每次一类，季度轮转）：
  · `governance_db`：把 `data/governance.db` 复制到演练目录 → 用只读副本跑
    `governance_api.watermarks()` 与门禁侧读取 → 验证水位/台账可读（对应 RTO 4h 的
    「水位/台账可自愈重登记」属性）。
  · `raw`：取一条 raw 产物的备份 → 校验可解析 + 记录大小/哈希（对应 RTO 8h 的
    「断点续跑兜底」前提）。

**只读演练**：不覆盖生产文件、不删任何数据；全部产物落 `reports/_dr/`。
计时口径：从「开始复制」到「验证通过」，与 RTO 对照（演练环境本地 ⇒ 记录的是
**恢复路径可行性**与实操耗时，不是灾难现场的 RTO 达标证明——这一点写进台账 honesty 字段）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import time
from datetime import datetime, timedelta, timezone

# 引导：**唯一引导点 `bootstrap`** —— 不自写 `sys.path.insert`（门禁
# `gate_import_bootstrap` 判据：tools/ 注入数只减不增）。运行方式：python -m tools.dr_drill
from bootstrap import bootstrap

bootstrap("all")

import paths

ROOT = paths.ROOT
TZ8 = timezone(timedelta(hours=8))
DR_DIR = os.path.join(paths.REPORTS_DIR, "_dr")
# 台账落点：**`reports/dr/`（入库）**，而非 `data/run_state/`——后者整体被 .gitignore
# 忽略（派生状态），而演练记录是**审计证据**（方案 §13.13 出口判据要"有记录"）。
# 与 §7.10 的 reports 分区裁定一致：机器台账类留 reports/。
LOG = os.path.join(paths.REPORTS_DIR, "dr", "dr_drill_log.jsonl")
# RTO 目标（小时），来自 ADR-0012 §三
RTO_HOURS = {"governance_db": 4, "raw": 8}


def _sha256(p: str, limit: int = 64 * 1024 * 1024) -> str:
    h = hashlib.sha256()
    read = 0
    with open(p, "rb") as fh:
        while read < limit:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
            read += len(chunk)
    return h.hexdigest()


def drill_governance_db() -> dict:
    """治理库恢复演练：复制 → 只读打开 → 关键表可读 + 水位可查。"""
    src = os.path.join(paths.DATA_DIR, "governance.db")
    t0 = time.time()
    rec: dict = {"target": "governance_db", "source": os.path.relpath(src, ROOT)}
    if not os.path.exists(src):
        rec.update(ok=False, error="源库不存在")
        return rec
    os.makedirs(DR_DIR, exist_ok=True)
    dst = os.path.join(DR_DIR, "governance_restore.db")
    shutil.copy2(src, dst)
    rec["restored_bytes"] = os.path.getsize(dst)
    rec["sha256_prefix"] = _sha256(dst)[:16]
    import sqlite3

    conn = sqlite3.connect(f"file:{dst}?mode=ro", uri=True)
    try:
        tables = [r[0] for r in conn.execute(
            "select name from sqlite_master where type='table' order by name")]
        counts = {}
        for t in ("watermark", "run_log", "run_step", "gate_result", "document"):
            if t in tables:
                counts[t] = conn.execute(f"select count(*) from {t}").fetchone()[0]
        rec["tables"] = len(tables)
        rec["row_counts"] = counts
        rec["watermarks_readable"] = "watermark" in tables
    finally:
        conn.close()
    # 服务级验证：**强制指向恢复副本**（`REG_ORCH_GOVERNANCE_DB`，SSOT 支持覆盖）。
    # ⚠️ 若不覆盖，list_watermarks() 读的是**生产库** ⇒ 演练只证明"生产库可读"，
    # 完全没验证恢复副本——那是最容易自我欺骗的一种通过。
    try:
        os.environ["REG_ORCH_GOVERNANCE_DB"] = dst
        import importlib

        from std_lib.common_lib import governance_store as gs

        importlib.reload(gs)  # 让 db_path() 重新读 env（模块级常量缓存）
        wm = gs.list_watermarks()          # SSOT 导出的水位读取口（非自定义名）
        rec["api_watermarks"] = len(wm)
        rec["api_ok"] = True
        rec["api_db"] = gs.db_path()
        rec["api_is_restore_copy"] = os.path.abspath(gs.db_path()) == os.path.abspath(dst)
    except Exception as e:  # noqa: BLE001
        rec["api_ok"] = False
        rec["api_error"] = f"{type(e).__name__}: {str(e)[:120]}"
    finally:
        os.environ.pop("REG_ORCH_GOVERNANCE_DB", None)
    rec["elapsed_s"] = round(time.time() - t0, 2)
    rec["ok"] = bool(rec.get("api_ok")) and rec.get("watermarks_readable")
    return rec


def drill_raw() -> dict:
    """raw 恢复演练：**原地只读**校验 + 哈希/记录数（不复制大文件）。

    ⚠️ 首版把 480MB 的 gov_laws.jsonl 复制进 `reports/_dr/` ⇒ 演练自身成了仓库负担
    （且极可能被误提交）。故改为：
      · 校验对象 = **备份源优先**（`backups/` 内最近产物，无则用工作副本并显式标注
        `source_kind`，绝不谎称验过备份）；
      · 解析**原地只读**（流式逐行，不整体载入）；hash 只取前 64MB（够标识用）。
    """
    raw_dir = os.path.join(paths.MODULES_DIR, "regulatory_scrapers", "data", "raw")
    t0 = time.time()
    rec: dict = {"target": "raw", "source": os.path.relpath(raw_dir, ROOT)}
    if not os.path.isdir(raw_dir):
        rec.update(ok=False, error="raw 目录不存在")
        return rec
    cands = [f for f in sorted(os.listdir(raw_dir)) if f.endswith((".jsonl", ".json"))]
    if not cands:
        rec.update(ok=False, error="raw 目录无产物")
        return rec
    # 优先取**记录文件**（.jsonl 优先、体量最大者）：索引类小文件解析成功不构成
    # 恢复能力证明（演练若只验 index 会得到虚假的绿灯）。
    jsonls = [f for f in cands if f.endswith(".jsonl")]
    pool = jsonls or cands
    name = max(pool, key=lambda f: os.path.getsize(os.path.join(raw_dir, f)))
    src = os.path.join(raw_dir, name)
    rec["artifact"] = name
    rec["source_kind"] = "working_copy"     # 无 backups/ 备份可用时的诚实标注
    bdir = os.path.join(paths.ROOT, "backups")
    if os.path.isdir(bdir):
        bcands = [f for f in os.listdir(bdir) if f.endswith(".jsonl")]
        if bcands:
            bname = max(bcands, key=lambda f: os.path.getsize(os.path.join(bdir, f)))
            src = os.path.join(bdir, bname)
            rec["artifact"] = f"backups/{bname}"
            rec["source_kind"] = "backup"
    rec["bytes"] = os.path.getsize(src)
    rec["sha256_prefix"] = _sha256(src)[:16]
    n = 0
    try:
        if src.endswith(".jsonl"):
            with open(src, encoding="utf-8") as fh:
                for i, ln in enumerate(fh):
                    if i == 0:
                        json.loads(ln)  # 首行 _meta 信封
                        continue
                    if ln.strip():
                        json.loads(ln)
                        n += 1
        else:
            json.load(open(src, encoding="utf-8"))
            n = -1  # JSON 数组：不逐条计数
        rec["records"] = n
        rec["parse_ok"] = True
    except (OSError, ValueError) as e:
        rec["parse_ok"] = False
        rec["parse_error"] = f"{type(e).__name__}: {str(e)[:120]}"
    rec["elapsed_s"] = round(time.time() - t0, 2)
    rec["ok"] = bool(rec.get("parse_ok"))
    return rec


def append_log(rec: dict) -> None:
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    rec = {"drill_at": datetime.now(TZ8).replace(microsecond=0).isoformat(),
           "rto_target_h": RTO_HOURS.get(rec.get("target", "")),
           "honesty": ("本地演练：验证**恢复路径可行**与实操耗时，"
                       "不等价于灾难现场的 RTO 达标证明"),
           **rec}
    with open(LOG, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="DR 恢复演练（方案 §13.4；记录落 dr_drill_log.jsonl）")
    ap.add_argument("target", choices=["governance_db", "raw", "all"], nargs="?", default="all")
    a = ap.parse_args(argv)
    todo = ["governance_db", "raw"] if a.target == "all" else [a.target]
    rc = 0
    for t in todo:
        rec = drill_governance_db() if t == "governance_db" else drill_raw()
        append_log(rec)
        flag = "OK " if rec.get("ok") else "FAIL"
        print(f"[dr {flag}] {t} elapsed={rec.get('elapsed_s')}s "
              f"rto_target={RTO_HOURS.get(t)}h")
        for k in ("bytes", "restored_bytes", "records", "tables", "api_watermarks",
                  "sha256_prefix", "artifact", "error", "parse_error", "api_error"):
            if k in rec:
                print(f"    {k}={rec[k]}")
        if not rec.get("ok"):
            rc = 1
    print(f"[dr] 台账：{os.path.relpath(LOG, ROOT)}（产物：{os.path.relpath(DR_DIR, ROOT)}）")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
