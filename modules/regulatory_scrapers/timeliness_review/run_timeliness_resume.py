# -*- coding: utf-8 -*-
"""run_timeliness_resume.py —— 时效验证「按源分批续跑」编排（2026-09-17）

需求（用户 2026-09-17 指令）
--------------------------
对五源中尚未完成时效验证的数据执行续跑，规则：
  1. 先筛选出未完成时效验证的记录（交由 verify_missing.py 的「效力缺失」判据）；
  2. **按数据源分批处理**；
  3. **gov 数据源因数据量过大，优先级设为最低 —— 必须等待其余四源全部完成时效验证后再启动**；
  4. 记录各源处理进度、成功/失败数量及失败原因；
  5. 支持断点续跑，避免重复验证。

与既有工具的分工
----------------
- `verify_missing.py`：**单源**的效力缺失核验驱动；自身已有 per-source checkpoint
  （`pkulaw_{src}_missing_checkpoint.jsonl`）与变更台账，**"避免重复验证"由它保证**。
- 本脚本：**编排层**——决定源的**处理顺序**、执行 gov 门禁、汇总进度与失败原因、
  提供跨源的断点（已 success 的源不重跑）。

进度台账
--------
`timeliness_review/续跑编排进度_{date}.json`：每源一条记录，含
status / missing / queried / ok / nomatch / fail / degraded / changed /
elapsed_s / exit_code / reason（失败原因原样保存，便于事后归因）。

用法
----
    python run_timeliness_resume.py --dry-run           # 只看顺序与现状，不执行
    python run_timeliness_resume.py                     # 执行（非 gov 组 → gov 门禁 → gov）
    python run_timeliness_resume.py --only mof          # 只跑指定源（调试）
    python run_timeliness_resume.py --force-gov         # 忽略门禁强制跑 gov（不建议）

⛔ 风控（沿用 verify_missing.py）：workers≤2、间隔≥0.2s、连续 15 次认证失败自动停止、
   分批 ≤500；**北大法宝积分用尽（90001）会自动停止**，此时本脚本记为 blocked 并可续跑。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))          # 仓库根
SCRAPERS = os.path.dirname(HERE)                        # modules/regulatory_scrapers
VERIFY = os.path.join(HERE, "verify_missing.py")

# 优先级：非 gov 四源先跑，gov 最低（用户明确要求）
PRIORITY = ["supp", "pbc", "nfra", "mof"]
LOWEST = "gov"
ALL_SOURCES = PRIORITY + [LOWEST]

# 单源输出行：`  supp  success      missing=   0 ok=   0 degraded=   0 changed=  0`
_STAT = re.compile(
    r"^\s*(?P<src>[a-z]+)\s+(?P<status>\w+)\s+missing=\s*(?P<missing>\d+)\s+"
    r"ok=\s*(?P<ok>\d+)\s+degraded=\s*(?P<degraded>\d+)\s+changed=\s*(?P<changed>\d+)", re.M)
_QUERIED = re.compile(r"queried=\s*(?P<queried>\d+)")
_NOMATCH = re.compile(r"nomatch=\s*(?P<nomatch>\d+)")
_FAIL = re.compile(r"fail=\s*(?P<fail>\d+)")
# 已知的「非错误但需人工介入」信号
_BLOCK_HINTS = {
    "90001": "北大法宝积分用尽（配额限制）——待配额恢复后续跑",
    "认证": "连续认证失败自动停止",
    "token": "北大法宝 token 缺失或失效",
}


def _is_done(rec: dict) -> bool:
    """该源是否算「已完成」（可放行 gov 门禁）。

    ⚠️ 不能只认 `success`：`verify_missing.py` 在**查到结果但无同名命中**时会报
    `overall=partial`（如 pbc：missing=2 全部 nomatch → degraded=2）。
    `nomatch` 是**正常结论**（该文件在北大法宝无同名记录），并非核验失败，
    若按 strict `success` 判门禁，gov 将**永远无法启动**。
    故判据为：状态 ∈ {success, partial} 且 `fail == 0`（无非预期失败）；
    阻断类状态（unavailable / blocked / failed）一律不算完成。
    """
    st = rec.get("status")
    if st not in ("success", "partial"):
        return False
    return int(rec.get("fail") or 0) == 0


def load_progress(p: str) -> dict:
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except ValueError:
            pass
    return {"sources": {}, "history": []}


def save_progress(p: str, d: dict) -> None:
    tmp = p + ".tmp"
    json.dump(d, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def classify_reason(out: str, err: str, code: int) -> str:
    """失败/阻断原因归因（按已知信号优先，其次取 stderr 尾行）。"""
    blob = (out or "") + "\n" + (err or "")
    for k, v in _BLOCK_HINTS.items():
        if k in blob:
            return v
    if code != 0:
        tail = [l for l in (err or "").strip().split("\n") if l.strip()]
        return f"exit={code}" + (f"；{tail[-1][:200]}" if tail else "")
    return ""


def run_source(src: str, extra: list, py: str) -> dict:
    cmd = [py, VERIFY, "--source", src] + extra
    t0 = time.time()
    print(f"\n{'=' * 78}\n[编排] 开始核验源 {src}  ← {' '.join(cmd[2:])}\n{'=' * 78}", flush=True)
    try:
        p = subprocess.run(cmd, cwd=SCRAPERS, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=None)
        out, err, code = p.stdout or "", p.stderr or "", p.returncode
    except KeyboardInterrupt:
        raise
    except Exception as e:                                   # noqa: BLE001
        out, err, code = "", f"{type(e).__name__}: {e}", -1
    el = round(time.time() - t0, 1)

    # 打印原样输出（保留完整证据），并解析统计
    print(out[-4000:] if out else "(无 stdout)")
    if err.strip():
        print("[stderr]", err[-1500:], file=sys.stderr)

    rec = {"source": src, "elapsed_s": el, "exit_code": code, "extra_args": extra}
    m = _STAT.search(out or "")
    if m:
        rec.update(status=m.group("status"), missing=int(m.group("missing")),
                   ok=int(m.group("ok")), degraded=int(m.group("degraded")),
                   changed=int(m.group("changed")))
        for name, rx in (("queried", _QUERIED), ("nomatch", _NOMATCH), ("fail", _FAIL)):
            mm = rx.search(out or "")
            rec[name] = int(mm.group(name)) if mm else 0
    else:
        rec.update(status="failed" if code != 0 else "unknown", missing=None,
                   ok=0, degraded=0, changed=0, queried=0, nomatch=0, fail=0)
    reason = classify_reason(out, err, code)
    if reason:
        rec["reason"] = reason
    if rec.get("status") == "success" and (rec.get("fail") or 0) > 0:
        rec["status"] = "partial"
        rec.setdefault("reason", f"部分条目核验失败 fail={rec['fail']}")
    return rec


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="时效验证按源分批续跑编排（gov 最低优先级）")
    ap.add_argument("--py", default=sys.executable, help="Python 解释器（默认当前解释器）")
    ap.add_argument("--only", default="", help="只跑指定源（逗号分隔，调试用）")
    ap.add_argument("--force-gov", action="store_true", help="忽略 gov 门禁强制跑 gov")
    ap.add_argument("--dry-run", action="store_true", help="只报告现状与执行顺序，不真正核验")
    ap.add_argument("--probe", type=int, default=0, help="透传给 verify_missing 的冒烟条数")
    ap.add_argument("--progress", default="", help="进度台账路径（默认 timeliness_review/续跑编排进度_{date}.json）")
    args = ap.parse_args(argv)

    date = time.strftime("%Y%m%d")
    prog_p = args.progress or os.path.join(HERE, f"续跑编排进度_{date}.json")
    prog = load_progress(prog_p)
    extra = ["--probe", str(args.probe)] if args.probe else []
    only = [s.strip() for s in args.only.split(",") if s.strip()]

    order = only or ALL_SOURCES
    print(f"[编排] 优先级顺序：{' → '.join(PRIORITY)} → **{LOWEST}（最低，须待前四源全部完成）**")
    print(f"[编排] 本次执行顺序：{' → '.join(order)}")
    print(f"[编排] 进度台账：{os.path.relpath(prog_p, ROOT)}")
    print(f"[编排] 历史结果：" + (", ".join(
        f"{s}={v.get('status')}" for s, v in prog.get("sources", {}).items()) or "（无）"))

    # 既有进度：已 success 的源跳过（断点：避免重复验证）
    todo = []
    for s in order:
        prev = (prog.get("sources") or {}).get(s) or {}
        if _is_done(prev) and not extra:
            print(f"[断点] {s} 上次已完成（status={prev.get('status')} fail={prev.get('fail', 0)}），"
                  f"跳过以避免重复验证（如需重跑请 --only {s}）")
            continue
        todo.append(s)

    # gov 门禁：前四源必须全部 success
    if LOWEST in todo and not args.force_gov and not only:
        done = {s: ((prog.get("sources") or {}).get(s) or {}).get("status") for s in PRIORITY}
        not_done = [s for s in PRIORITY
                    if not _is_done((prog.get("sources") or {}).get(s) or {})]
        print(f"[门禁] gov 前四源状态：{ {s: done.get(s) for s in PRIORITY} }")
        if not_done or any(s in todo for s in PRIORITY):
            pending = sorted(set(not_done) | {s for s in todo if s in PRIORITY})
            print(f"[门禁] !! 前四源尚未全部完成（未完成：{pending}）"
                  f" -> 本次不启动 gov（符合「gov 优先级最低」要求；续跑本脚本即可）")
            todo = [s for s in todo if s != LOWEST]
        else:
            print(f"[门禁] 前四源全部完成 -> 允许启动 {LOWEST}")

    if args.dry_run:
        print(f"\n[编排] dry-run：待执行 = {todo or '（无）'}")
        for s in todo:
            prev = (prog.get("sources") or {}).get(s) or {}
            print(f"   · {s}: 上次 missing={prev.get('missing')} status={prev.get('status')}")
        return 0

    for s in todo:
        rec = run_source(s, extra, args.py)
        prog.setdefault("sources", {})[s] = rec
        prog.setdefault("history", []).append({"at": time.strftime("%Y-%m-%d %H:%M:%S"), **rec})
        prog["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        save_progress(prog_p, prog)
        print(f"[编排] {s} → status={rec.get('status')} missing={rec.get('missing')} "
              f"ok={rec.get('ok')} fail={rec.get('fail')} 耗时={rec.get('elapsed_s')}s"
              + (f"  原因：{rec['reason']}" if rec.get("reason") else ""))
        # 配额/认证类阻断：停下并保留续跑点（继续跑下去只会浪费配额）
        if rec.get("status") in ("blocked", "failed") and rec.get("reason") and \
                any(h in rec["reason"] for h in _BLOCK_HINTS.values()):
            print(f"[编排] !! 检测到阻断信号（{rec['reason']}）-> 停止本次编排，进度已保存，可续跑")
            break

    # 收尾汇总
    print(f"\n{'=' * 78}\n[编排] 汇总（进度台账：{os.path.relpath(prog_p, ROOT)}）")
    print(f"  {'源':<6} {'status':<10} {'missing':>8} {'ok':>6} {'fail':>5} {'changed':>8}  原因")
    for s in ALL_SOURCES:
        v = (prog.get("sources") or {}).get(s) or {}
        print(f"  {s:<6} {str(v.get('status','—')):<10} {str(v.get('missing','—')):>8} "
              f"{str(v.get('ok','—')):>6} {str(v.get('fail','—')):>5} "
              f"{str(v.get('changed','—')):>8}  {v.get('reason','')[:60]}")
    ok4 = all(_is_done((prog.get("sources") or {}).get(s) or {}) for s in PRIORITY)
    g = (prog.get("sources") or {}).get(LOWEST) or {}
    print(f"\n[门禁] 前四源是否全部完成（success/partial 且 fail=0）："
          f"{'是 -> 可启动 gov 续跑' if ok4 else '否 -> gov 仍不启动（务必先完成前四源）'}")
    if g:
        print(f"[门禁] {LOWEST}: status={g.get('status')} missing={g.get('missing')} "
              f"ok={g.get('ok')} fail={g.get('fail')} 耗时={g.get('elapsed_s')}s"
              + (f"  原因：{g['reason']}" if g.get("reason") else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
