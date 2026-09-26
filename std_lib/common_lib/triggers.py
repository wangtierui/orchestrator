# -*- coding: utf-8 -*-
"""
common_lib.triggers — 条件触发链（TriggerRunner，v2 §3.5，P2-1）

定位
----
`config/triggers.yaml` 是**条件触发的唯一事实源**（链外节点的触发条件、步骤序列、
失败/跳过语义）。本模块是它的**唯一执行器与决策器**：把"这一步该不该跑"从
人的记忆变成**给定判据的机器判定**，并把判定结果（含**未启用原因**）显式化了。

纪律（v2 §3.5）
--------------
① 步骤以 **argv 列表**表达，`subprocess.run(argv, ...)` **不经 shell**；
② `enabled_when` 的每个条件都必须在 `CONDITION_IMPLS` 有实现；缺实现 →
   **enabled=False**（绝不静默按 True 执行，这是"条件触发"最容易骗人的地方）；
③ 条件判定返回 `(bool, 证据字符串)` —— 证据进决策表与 `run_log`，可追责；
④ `on_fail: manual_breakpoint` 的触发项失败时**必须**写 `worklist` 队列
   （否则又回到"需要人、但系统不告诉人"的原病）。

用法
----
    from common_lib.triggers import decide, run_all, run_trigger
    table = decide(ctx)                 # 决策表（含 enabled/原因）
    rep = run_trigger("timeliness_verify", ctx)
"""
from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys

import paths

TRIGGERS_YAML = os.path.join(paths.CONFIG_DIR, "triggers.yaml")

# 触发项失败语义（受控值）
ON_FAIL = frozenset({"stop", "stop_on_quota", "manual_breakpoint", "skip_and_warn"})
# 时效核验状态文件（`days_since` 条件的默认计时锚点）
_TIMELINESS_STATE = os.path.join(paths.MODULES_DIR, "regulatory_scrapers", "timeliness_review",
                                 "verification_state.json")
# 发布件清单（`publish_manifest_changed` 条件的观察对象）
_PUBLISH_MANIFEST = os.path.join(paths.MODULES_DIR, "base_publish", "published",
                                 "publish_manifest.json")
_PKULAW_TOKEN = os.path.join(paths.ROOT, ".pkulaw_token")


def load_triggers() -> dict:
    """读 `config/triggers.yaml`（无 PyYAML 或文件缺失 → 抛错，不静默兜底）。"""
    import yaml  # noqa: PLC0415

    with open(TRIGGERS_YAML, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


# --------------------------------------------------------------------------- #
# 条件实现（每个条件返回 (bool, 证据)；缺实现 → 视为 False + 原因，绝不默认 True）
# --------------------------------------------------------------------------- #
def _c_token_exists(want: bool, _ctx: dict) -> tuple[bool, str]:
    ok = os.path.exists(_PKULAW_TOKEN) and bool(
        open(_PKULAW_TOKEN, encoding="utf-8", errors="replace").read().strip()
        if os.path.exists(_PKULAW_TOKEN) else "")
    return (ok == bool(want)), f"token 存在={ok}（{os.path.basename(_PKULAW_TOKEN)}）"


def _c_days_since(days: int, _ctx: dict) -> tuple[bool, str]:
    if not os.path.exists(_TIMELINESS_STATE):
        return True, "核验状态文件不存在（视为从未核验 → 满足）"
    mt = datetime.datetime.fromtimestamp(os.path.getmtime(_TIMELINESS_STATE))
    delta = (datetime.datetime.now() - mt).days
    return delta >= int(days), f"距上次核验 {delta} 天（阈值 {days}）"


def _c_unindexed_originals(want: bool, _ctx: dict) -> tuple[bool, str]:
    """原件库是否存在未索引文件（v2 §3.5 修正 T3：不用"目录非空"，而用"未索引"）。

    实现取廉价且可判定的口径：原件库文件数 > 索引记录数 → 有未索引（差额）。
    无法读取索引时返回 False + 原因（不猜）。
    """
    originals = os.path.join(paths.MODULES_DIR, "internal_policy_base", "data", "originals")
    idx = os.path.join(paths.MODULES_DIR, "internal_policy_base", "data",
                       "internal_policy_index.json")
    if not os.path.isdir(originals):
        return False, f"原件库不存在（{os.path.relpath(originals, paths.ROOT)}）"
    n_files = sum(1 for _ in os.scandir(originals) if _.is_file())
    if not os.path.exists(idx):
        return bool(want), f"原件 {n_files} 份；索引缺失（视为未索引 → 满足）"
    try:
        recs = json.load(open(idx, encoding="utf-8")).get("records", [])
    except (OSError, ValueError) as e:
        return False, f"索引不可读（{type(e).__name__}）→ 不判定"
    gap = n_files - len(recs)
    return (gap > 0) == bool(want), f"原件 {n_files} 份 / 索引 {len(recs)} 条（未索引 {max(0, gap)}）"


def _c_explicit_arg(flag: str, ctx: dict) -> tuple[bool, str]:
    argv = list(ctx.get("argv") or [])
    hit = flag in argv
    return hit, f"命令行{'含' if hit else '不含'} {flag}"


def _c_publish_manifest_changed(want: bool, _ctx: dict) -> tuple[bool, str]:
    """发布件清单自上次 wiki 同步后是否变化（以治理库登记的 `version` 为基线）。"""
    if not os.path.exists(_PUBLISH_MANIFEST):
        return False, "publish_manifest.json 不存在 → 不判定"
    try:
        from common_lib import governance_store as gs  # noqa: PLC0415
        if not gs.enabled():
            return False, "治理库未启用 → 无基线可比（不判定）"
        cur = gs.version_of_file(_PUBLISH_MANIFEST)
        prev = gs.get_watermark("wiki:sync") or {}
        base_v = (prev.get("version") or "") if isinstance(prev, dict) else ""
        if not base_v:
            return bool(want), "无 wiki:sync 水位基线（视为变化 → 满足）"
        return (cur != base_v) == bool(want), f"清单版本 {cur[:12]} vs 基线 {base_v[:12]}"
    except Exception as e:  # noqa: BLE001  条件判定失败不得中断主链
        return False, f"判定异常（{type(e).__name__}）→ 不判定"


def _c_inbox_has_new(want: bool, _ctx: dict) -> tuple[bool, str]:
    """投放区是否有**新内容**（v2 §3.12.6）：与 `_inbox_manifest.json` 的 (文件, size, mtime)
    逐项比对；无清单时"有文件即视为新"。清单缺失/不可读 → 不判定（返回 False + 原因）。"""
    manifest = os.path.join(paths.INBOX_DIR, "_inbox_manifest.json")
    current: dict[str, tuple] = {}
    for dom in ("internal", "regulatory_stats"):
        base = os.path.join(paths.INBOX_DIR, dom)
        if not os.path.isdir(base):
            continue
        for dirpath, _dirs, files in os.walk(base):
            for fn in files:
                if fn.startswith(".") or fn == "_registry.yaml":
                    continue
                fp = os.path.join(dirpath, fn)
                try:
                    st = os.stat(fp)
                except OSError:
                    continue
                rel = os.path.relpath(fp, paths.ROOT).replace(os.sep, "/")
                current[rel] = (st.st_size, int(st.st_mtime))
    if not os.path.exists(manifest):
        return bool(current) == bool(want), f"无投递清单；投放区文件 {len(current)} 个"
    try:
        rows = json.load(open(manifest, encoding="utf-8")).get("rows") or []
    except (OSError, ValueError) as e:
        return False, f"投放清单不可读（{type(e).__name__}）→ 不判定"
    seen = {r.get("file"): (r.get("size"), r.get("mtime")) for r in rows}
    new = [f for f, sig in current.items() if seen.get(f) != sig]
    return (bool(new)) == bool(want), f"新增/变更 {len(new)} 个（既有 {len(seen)} 条清单）"


CONDITION_IMPLS: dict[str, object] = {
    "token_exists": _c_token_exists,
    "days_since": _c_days_since,
    "unindexed_originals": _c_unindexed_originals,
    "explicit_arg": _c_explicit_arg,
    "publish_manifest_changed": _c_publish_manifest_changed,
    "inbox_has_new": _c_inbox_has_new,
}


# --------------------------------------------------------------------------- #
# 决策表
# --------------------------------------------------------------------------- #
def decide(ctx: dict | None = None, *, only: str = "") -> list[dict]:
    """逐触发项判定 → 决策表 [{id, stage, enabled, reasons[], steps, on_fail, on_skip}]。"""
    ctx = dict(ctx or {})
    data = load_triggers()
    out: list[dict] = []
    for t in data.get("triggers") or []:
        tid = t.get("id", "")
        if only and tid != only:
            continue
        reasons: list[str] = []
        enabled = True
        cond = t.get("enabled_when") or {}
        if not cond:
            enabled = False
            reasons.append("未声明 enabled_when（拒绝无判据执行）")
        for name, want in cond.items():
            fn = CONDITION_IMPLS.get(name)
            if fn is None:
                enabled = False
                reasons.append(f"{name}: **无实现** → 不判定（须在 CONDITION_IMPLS 登记）")
                continue
            try:
                ok, ev = fn(want, ctx)  # type: ignore[operator]
            except Exception as e:  # noqa: BLE001  单个条件异常不拖垮决策表
                ok, ev = False, f"条件异常 {type(e).__name__}: {e}"
            reasons.append(f"{name}={want} → {'满足' if ok else '不满足'}（{ev}）")
            enabled = enabled and bool(ok)
        out.append({"id": tid, "stage": t.get("stage", ""), "enabled": enabled,
                    "reasons": reasons, "steps": t.get("steps") or [],
                    "on_fail": t.get("on_fail", "stop"),
                    "on_skip": t.get("on_skip", ""), "desc": t.get("desc", ""),
                    "resume": t.get("resume", "")})
    return out


# --------------------------------------------------------------------------- #
# 执行
# --------------------------------------------------------------------------- #
def _expand_args(argv: list[str], ctx: dict) -> list[str]:
    """`{arg}` / `{vault}` 占位符展开（来自 ctx；缺值则原样保留 → 目标校验会报错）。"""
    out = []
    for a in argv:
        for k, v in (ctx.get("vars") or {}).items():
            a = a.replace("{" + k + "}", str(v))
        out.append(a)
    return out


def _argv_abs(argv: list[str]) -> list[str]:
    """把 argv[0] 为仓内脚本（cli.py / 相对路径）的形态补成绝对路径（不经 shell）。"""
    if not argv:
        return argv
    if argv[0] in ("python", "python.exe", "py", "python3"):
        return [sys.executable] + [_abs(a) for a in argv[1:]]
    return [_abs(argv[0])] + list(argv[1:])


def _abs(p: str) -> str:
    return p if os.path.isabs(p) else os.path.join(paths.ROOT, p)


def run_trigger(tid: str, ctx: dict | None = None, *, dry_run: bool = False,
                worklist=True) -> dict:
    """执行一个触发项。返回 {id, status, steps[], note}（status ∈ ran/partial/failed/skipped）。"""
    ctx = dict(ctx or {})
    ctx.setdefault("argv", [])
    rows = decide(ctx, only=tid)
    if not rows:
        return {"id": tid, "status": "unknown", "steps": [], "note": "触发项未登记"}
    row = rows[0]
    if not row["enabled"]:
        return {"id": tid, "status": "skipped", "steps": [], "on_skip": row["on_skip"],
                "note": "；".join(row["reasons"])}
    if dry_run:
        return {"id": tid, "status": "dry-run",
                "steps": [{"argv": _argv_abs(_expand_args(s.get("argv") or [], ctx)),
                           "timeout": s.get("timeout")} for s in row["steps"]],
                "note": "；".join(row["reasons"])}

    results: list[dict] = []
    failed = False
    for s in row["steps"]:
        argv = _argv_abs(_expand_args(s.get("argv") or [], ctx))
        timeout = s.get("timeout")
        rec = {"argv": argv, "rc": -1, "timeout": timeout}
        try:
            r = subprocess.run(argv, cwd=paths.ROOT, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=timeout)
            rec["rc"] = r.returncode
            rec["tail"] = "\n".join((r.stderr or r.stdout or "").strip().splitlines()[-3:])
        except subprocess.TimeoutExpired:
            rec["rc"], rec["tail"] = 124, f"timeout {timeout}s"
        except OSError as e:
            rec["rc"], rec["tail"] = 127, f"{type(e).__name__}: {e}"
        results.append(rec)
        if rec["rc"] != 0:
            failed = True
            break
    status = "ran" if not failed else "failed"
    note = "；".join(row["reasons"])
    if failed and row["on_fail"] == "manual_breakpoint" and worklist:
        _worklist(tid, results, note)
    return {"id": tid, "status": status, "steps": results, "on_fail": row["on_fail"], "note": note}


def _worklist(tid: str, results: list[dict], note: str) -> None:
    """`on_fail: manual_breakpoint` → 失败必须进 worklist（v2 §3.5 纪律④）。"""
    try:
        from common_lib import governance_store as gs  # noqa: PLC0415
        bad = next((r for r in results if r.get("rc") != 0), {})
        gs.worklist_add(
            "trigger_manual_breakpoint", tid, stage="", artifact_key=f"trigger:{tid}",
            payload={"trigger": tid, "argv": bad.get("argv"), "rc": bad.get("rc"),
                     "tail": bad.get("tail", ""), "reason": note},
            suggestion="人工处置触发链失败（修复后重跑该触发项）")
    except Exception:  # noqa: BLE001  旁路设施：登记失败不得中断触发链
        pass


def run_all(ctx: dict | None = None, *, dry_run: bool = False) -> dict:
    """按 yaml 声明顺序执行全部触发项 → {ran, skipped, failed, details[]}。"""
    ctx = dict(ctx or {})
    ctx.setdefault("argv", [])
    details = [run_trigger(r["id"], ctx, dry_run=dry_run) for r in decide(ctx)]
    return {"ran": sum(1 for d in details if d["status"] == "ran"),
            "skipped": sum(1 for d in details if d["status"] == "skipped"),
            "failed": sum(1 for d in details if d["status"] == "failed"),
            "details": details}


def decision_table_text(ctx: dict | None = None) -> str:
    """人类可读决策表（供 `cli.py triggers` 与 `run --dry-run`）。"""
    lines = []
    for r in decide(ctx):
        mark = "[将执行]" if r["enabled"] else "[跳过]  "
        lines.append(f"{mark} {r['id']:<20} stage={r['stage']:<5} {(r['desc'] or '')}")
        for reason in r["reasons"]:
            lines.append(f"          └ {reason}")
        if not r["enabled"] and r["on_skip"]:
            lines.append(f"          └ on_skip: {r['on_skip']}")
    return "\n".join(lines)


if __name__ == "__main__":  # 库自检：打印决策表（不执行任何步骤）
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    print(decision_table_text({"argv": sys.argv[1:]}))
