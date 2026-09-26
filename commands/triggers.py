# -*- coding: utf-8 -*-
"""
commands/triggers — 条件触发链的决策表与执行（v2 §3.5，P2-1）

    python cli.py triggers                 # 决策表（含**未启用原因**与证据）
    python cli.py triggers --json          # 机器可读决策表
    python cli.py triggers run [--id X] [--dry-run] [--arg <k=v>]
                                          # 执行已启用的触发项（`--dry-run` 只列 argv）

定位说明（与 `cli.py run` 的分工）：`run` 是主链唯一入口，默认**不**重复执行触发链
（主链既有阶段已覆盖多数链外节点，重复执行会双写）；本命令是触发链的**显式驱动面**，
`run --triggers` 为"主链成功后追加执行"的开关。
"""

from __future__ import annotations

import argparse
import json
import sys

from bootstrap import bootstrap
from config.exitcodes import ExitCode  # noqa: E402  (R3：退出码语义化)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="cli.py triggers", description="条件触发链（v2 §3.5）")
    ap.add_argument(
        "action",
        nargs="?",
        default="table",
        choices=["table", "list", "run"],
        help="table 决策表（默认）| list 列出触发项 | run 执行",
    )
    ap.add_argument("--id", default="", help="只处理指定触发项")
    ap.add_argument("--dry-run", action="store_true", help="只列 argv，不执行")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument(
        "--arg",
        action="append",
        default=[],
        help="上下文变量 k=v（供 {arg}/{vault} 占位符；可多次）",
    )
    return ap


def _ctx(args) -> dict:
    vars_: dict[str, str] = {}
    vals: list[str] = []
    for kv in args.arg:
        if "=" in kv:
            k, v = kv.split("=", 1)
            vars_[k.strip()] = v.strip()
        else:
            vals.append(kv)
    return {"argv": list(sys.argv[1:]) + vals, "vars": vars_}


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    bootstrap("all")
    from std_lib.common_lib import triggers as trg  # noqa: PLC0415

    ctx = _ctx(args)

    if args.action == "list":
        rows = trg.decide(ctx)
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=1))
        else:
            for r in rows:
                print(f"{r['id']:<20} stage={r['stage']:<6} steps={len(r['steps'])}  {r['desc']}")
        return ExitCode.OK

    if args.action == "table":
        rows = trg.decide(ctx, only=args.id)
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=1))
        else:
            print(
                trg.decision_table_text(ctx)
                if not args.id
                else "\n".join(
                    f"[{'将执行' if r['enabled'] else '跳过  '}] {r['id']}\n"
                    + "\n".join(f"          └ {x}" for x in r["reasons"])
                    for r in rows
                )
            )
        return ExitCode.OK

    # ---- run ----
    if args.dry_run:
        rep = trg.run_all(ctx, dry_run=True)
        if args.json:
            print(json.dumps(rep, ensure_ascii=False, indent=1))
        else:
            for d in rep["details"]:
                print(f"[{d['status']}] {d['id']}")
                for s in d.get("steps") or []:
                    print(f"    argv={s.get('argv')} timeout={s.get('timeout')}")
                if d.get("note"):
                    print(f"    依据: {d['note']}")
        return ExitCode.OK
    if args.id:
        rep = trg.run_trigger(args.id, ctx)
        print(f"[triggers] {rep['id']}: {rep['status']} {rep.get('note', '')}")
        return ExitCode.OK if rep["status"] in ("ran", "skipped") else ExitCode.DATA
    rep = trg.run_all(ctx)
    print(f"[triggers] 执行 {rep['ran']} / 跳过 {rep['skipped']} / 失败 {rep['failed']}")
    return ExitCode.OK if not rep["failed"] else ExitCode.DATA


def run(argv=None) -> int:
    return main(argv)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        pass
    raise SystemExit(main())
