# -*- coding: utf-8 -*-
"""commands.gates — orchestrator 命令：gates（自 cli.py 迁移，2026-09-13 审查 P3）。"""
from __future__ import annotations


def run(argv):
    from gates import GatesRunner
    ok, results = GatesRunner().run()
    for r in results:
        flag = "[OK] " if r["passed"] else "[FAIL]"
        print(f"  {flag}  {r['desc']}  {r.get('detail')}")
    print("====================")
    print("PASS: 全部门禁通过" if ok else "FAIL: 存在未通过门禁")
    return 0 if ok else 1
