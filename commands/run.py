# -*- coding: utf-8 -*-
"""
commands/run — **唯一主链入口**（v2 §3.13.2/§3.13.3，P2-6）

动机（v2 §3.13.1 M4）："下一步该跑什么、有几个待办、哪些阶段上次失败了"此前只存在于
**人脑**与 `reports/_tmp/生产刷新汇总_*.json`。统一入口把"跑主链"与"环境前置、续跑、
试跑、条件触发"收进一条命令，且**不新开子进程**（避免"编排器的编排器"）。

    python cli.py run [--no-scrape] [--collect <spec>] [--supp-batch <backlog.json>]
                      [--resume] [--from <step>] [--only <step,step>]
                      [--stop-on-error] [--json-logs] [--dry-run] [--list-steps]
                      [--triggers] [--skip-doctor]

纪律：
- 参数语义**完全对齐** `tools/run_production_refresh.py`（不削减），并转调其 `main()`；
- `run` 启动前自动跑 `doctor --quick`（§3.13.4），环境 FAIL 直接拒绝执行（rc=3）；
- `--resume` 的跳过判据与**水位同源**（v2 R13），只跳过「上次 rc=0 且全库水位无 stale」的步骤。
"""

from __future__ import annotations

import argparse
import sys

from bootstrap import bootstrap
from config.exitcodes import ExitCode  # noqa: E402  (R3：退出码语义化)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="cli.py run", description="生产主链唯一入口（v2 §3.13）")
    ap.add_argument("--no-scrape", action="store_true", help="跳过网络抓取（仅清洗+全链）")
    ap.add_argument("--collect", default="all", help="抓取源（逗号分隔或 all）")
    ap.add_argument("--supp-batch", default="", help="supp 批量摄取 backlog JSON")
    ap.add_argument("--resume", action="store_true", help="从上次失败/未执行步骤续跑")
    ap.add_argument("--from", dest="from_step", default="", help="从指定步骤名开始")
    ap.add_argument("--only", default="", help="只执行指定步骤名（逗号分隔）")
    ap.add_argument("--stop-on-error", action="store_true", help="任一步失败即中止")
    ap.add_argument("--json-logs", action="store_true", help="结构化日志（JSON lines）")
    ap.add_argument("--dry-run", action="store_true", help="只打印将执行的 argv，不执行")
    ap.add_argument("--list-steps", action="store_true", help="列出步骤名后退出")
    ap.add_argument(
        "--triggers",
        action="store_true",
        help="主链成功后追加执行**已启用**的条件触发项（默认不执行：避免与主链"
        "既有阶段重复；决策表见 `cli.py triggers`）",
    )
    ap.add_argument("--skip-doctor", action="store_true", help="跳过前置环境自检（排障用）")
    return ap


def _argv_for_chain(args) -> list[str]:
    """把本命令的参数转成 `run_production_refresh.main(argv)` 的 argv（单一来源）。"""
    out: list[str] = []
    for flag, val in (
        ("--no-scrape", args.no_scrape),
        ("--resume", args.resume),
        ("--stop-on-error", args.stop_on_error),
        ("--json-logs", args.json_logs),
        ("--dry-run", args.dry_run),
        ("--list-steps", args.list_steps),
    ):
        if val:
            out.append(flag)
    for flag, val in (
        ("--collect", args.collect),
        ("--supp-batch", args.supp_batch),
        ("--from", args.from_step),
        ("--only", args.only),
    ):
        if val and not (flag == "--collect" and val == "all"):
            out += [flag, val]
    return out


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    if not args.skip_doctor and not args.list_steps:
        from commands import doctor as _doctor  # noqa: PLC0415

        res = _doctor.run_checks(quick=True)
        _doctor.write_report(res)
        if res["fail"]:
            print(
                f"[run] 前置环境自检未通过（fail={res['failed_ids']}）→ 拒绝执行主链"
                f"（先跑 `python cli.py doctor`；排障可加 --skip-doctor）"
            )
            return ExitCode.ENV
        print(f"[run] 前置自检通过（{res['checked']} 项，warn={res['warn']}）")

    bootstrap("all", include_tools=True)
    from run_production_refresh import main as _chain_main  # noqa: PLC0415

    rc = _chain_main(_argv_for_chain(args))

    if args.triggers and rc == 0 and not args.dry_run:
        from std_lib.common_lib import triggers as trg  # noqa: PLC0415

        rep = trg.run_all({"argv": list(argv or [])})
        print(f"[run] 条件触发：执行 {rep['ran']} / 跳过 {rep['skipped']} / 失败 {rep['failed']}")
        if rep["failed"]:
            return ExitCode.DATA
    return rc


def run(argv=None) -> int:
    return main(argv)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        pass
    raise SystemExit(main())
