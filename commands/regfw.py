# -*- coding: utf-8 -*-
"""commands.regfw — `cli.py regfw ...`（W3，方案 §5.2；多监管数据体系）。"""
from __future__ import annotations


def run(argv):
    """regfw register | extract | map | stats。"""
    import json

    from config.exitcodes import ExitCode

    action = argv[0] if argv else "stats"
    from interfaces import reg_frameworks_api as api

    if action == "register":
        if len(argv) < 5:
            print("用法：orchestrator regfw register <fid> <name> <regulator> <cadence> [deadline_day]")
            return ExitCode.USAGE
        day = int(argv[5]) if len(argv) > 5 else None
        print(json.dumps(api.register(argv[1], argv[2], argv[3], argv[4], day),
                         ensure_ascii=False))
        return ExitCode.OK
    if action == "extract":
        if len(argv) < 3:
            print("用法：orchestrator regfw extract <fid> <素材目录> [--theme T] [--owner D]")
            return ExitCode.USAGE
        kw = {}
        if "--theme" in argv:
            kw["theme"] = argv[argv.index("--theme") + 1]
        if "--owner" in argv:
            kw["owner_dept"] = argv[argv.index("--owner") + 1]
        print(json.dumps(api.extract_dir(argv[1], argv[2], **kw), ensure_ascii=False, indent=1))
        return ExitCode.OK
    if action == "map":
        kw = {"limit": int(argv[1])} if len(argv) > 1 and argv[1].isdigit() else {}
        print(json.dumps(api.map_clauses(**kw), ensure_ascii=False, indent=1))
        return ExitCode.OK
    if action == "stats":
        print(json.dumps(api.stats(), ensure_ascii=False, indent=1))
        return ExitCode.OK
    print("用法：orchestrator regfw {register|extract|map|stats}")
    return ExitCode.USAGE
