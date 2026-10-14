# -*- coding: utf-8 -*-
"""commands.implaudit — `cli.py implaudit ...`（W3，方案 §5.3；落地执行检视）。"""
from __future__ import annotations


def run(argv):
    """implaudit ingest <台账目录> | gaps [--limit N] | report | stats。"""
    import json

    from config.exitcodes import ExitCode

    action = argv[0] if argv else "stats"
    from interfaces import implementation_audit_api as api

    if action == "ingest":
        if len(argv) < 2:
            print("用法：orchestrator implaudit ingest <台账目录>")
            return ExitCode.USAGE
        print(json.dumps(api.ingest_dir(argv[1]), ensure_ascii=False, indent=1))
        return ExitCode.OK
    if action == "gaps":
        kw = {"limit": int(argv[1])} if len(argv) > 1 and argv[1].isdigit() else {}
        print(json.dumps(api.run_gaps(**kw), ensure_ascii=False, indent=1))
        return ExitCode.OK
    if action == "report":
        print(api.render_markdown())
        return ExitCode.OK
    if action == "stats":
        print(json.dumps(api.stats(), ensure_ascii=False, indent=1))
        return ExitCode.OK
    print("用法：orchestrator implaudit {ingest <dir>|gaps|report|stats}")
    return ExitCode.USAGE
