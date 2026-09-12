# -*- coding: utf-8 -*-
"""gate_secret_scan —— 密钥/敏感值硬编码扫描（第 14 道门禁，2026-09-12 审查 P1-4）。

背景：仓内早有 `std_lib/scraper_std/secret_scan.py`（内置 7 类规则：口令/API Key/密钥令牌/
代理明文口令/连接串/私钥/会话密钥），但此前未接入 gates 与提交路径——本次收口为门禁。

范围：仓库源码/配置文件（.py/.yaml/.yml/.json/.toml/.ini/.cfg/.env 等）；
排除：reports/（文档可含示例）、data//published//corpus//logs/、backups/、
      secret_scan.py 自身（其自检夹具为动态拼接，不应豁免整体——仅跳过该文件以避误报）。

通过标准：无 CRITICAL/HIGH 命中（placeholder/环境变量注入形态不报）。
"""
from __future__ import annotations

import os
import sys


def run() -> tuple[bool, dict]:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for p in (root, os.path.join(root, "std_lib")):
        if p not in sys.path:
            sys.path.insert(0, p)

    from scraper_std.secret_scan import scan_directory  # noqa: PLC0415

    findings = scan_directory(root, include_ext=[".py", ".yaml", ".yml", ".json",
                                                 ".toml", ".ini", ".cfg", ".env"])
    skip_frag = (os.sep + "reports" + os.sep, os.sep + "data" + os.sep,
                 os.sep + "published" + os.sep, os.sep + "corpus" + os.sep,
                 os.sep + "backups" + os.sep, os.sep + "_tmp" + os.sep,
                 "secret_scan.py",        # 扫描器自身（自检夹具动态拼接）
                 "gate_secret_scan.py")   # 本门禁（描述文本含规则名，防自命中）
    kept = [f for f in findings
            if not any(s in (f.file or "") for s in skip_frag)]
    crit = [f for f in kept if f.level == "CRITICAL"]
    high = [f for f in kept if f.level == "HIGH"]
    problems = [f"{f.file}:{f.line} [{f.level}] {f.hint} → {f.matched[:80]}"
                for f in (crit + high)]
    passed = not problems
    detail = {
        "checked_root": root,
        "findings_total": len(findings),
        "in_scope": len(kept),
        "critical": len(crit),
        "high": len(high),
        "problems": problems[:20],
        "note": "命中须改环境变量注入（token 经 env/文件注入为合规形态）",
    }
    return passed, detail
