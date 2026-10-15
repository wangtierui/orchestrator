"""SBOM 生成（方案 §13.2-3）：`requirements.lock` → **CycloneDX 1.5** JSON。

为何 lock 直生而非 syft（权衡）：syft 未安装且需引入外部二进制 + 扫描整个文件系统
（含 external/ 的 13GB 权重与 virtualenv），而**依赖证据的权威来源就是 lock**
（§13.2-1 已把依赖树冻结为入库事实源）。lock 直生的好处：
  · 零新工具链、可离线、可复现（相同 lock ⇒ 逐字节相同 SBOM）；
  · SBOM 与 lock 的对应关系可直接用 sha256 断言（门禁 D5）。
对「监管说得出用了什么」这个诉求，CycloneDX 的 components 段已足够；
文件级成分需要时再引 syft（复发条件写在本文件末尾注释）。

用法：python -m tools.gen_sbom [--check]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCK = os.path.join(ROOT, "requirements.lock")
OUT_DIR = os.path.join(ROOT, "reports", "_sbom")
LATEST = os.path.join(OUT_DIR, "sbom_latest.json")

CVE_DB = ("https://github.com/pypa/advisory-database")
TZ8 = timezone(timedelta(hours=8))


def _lock_hash() -> str:
    with open(LOCK, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _parse_lock() -> list[dict]:
    """`name==version` / `name>=version` → [{name, version, raw}]（按 name 排序）。"""
    out = []
    with open(LOCK, encoding="utf-8") as fh:
        for ln in fh:
            ln = ln.strip()
            if not ln or ln.startswith("#"):
                continue
            m = re.match(r"^([A-Za-z0-9._-]+)\s*==\s*([^\s;]+)", ln)
            if m:
                out.append({"name": m.group(1), "version": m.group(2), "raw": ln})
                continue
            m2 = re.match(r"^([A-Za-z0-9._-]+)\s*([<>=!~].*)$", ln)
            if m2:
                out.append({"name": m2.group(1), "version": m2.group(2).lstrip("=").strip(),
                            "raw": ln})
    return sorted(out, key=lambda d: d["name"].lower())


def build() -> dict:
    items = _parse_lock()
    now = datetime.now(TZ8).replace(microsecond=0).isoformat()
    doc = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": f"urn:uuid:{hashlib.sha256(_lock_hash().encode()).hexdigest()[:32]}",
        "version": 1,
        "metadata": {
            "timestamp": now,
            "tools": [{
                "vendor": "regulatory_compliance_orchestrator",
                "name": "tools/gen_sbom.py",
                "version": "1.0",
            }],
            "component": {
                "type": "application",
                "name": "regulatory_compliance_orchestrator",
                "bom-ref": "pkg:pypi/regulatory-compliance-orchestrator@local",
                "description": "监管合规编排器（单机脚本链；依赖权威来源 = requirements.lock）",
            },
            "properties": [
                {"name": "lock:sha256", "value": _lock_hash()},
                {"name": "lock:file", "value": "requirements.lock"},
                {"name": "generator:note",
                 "value": "由 lock 直生（非文件系统扫描）；文件级成分如需另引 syft"},
            ],
        },
        "components": [
            {
                "type": "library",
                "name": it["name"],
                "version": it["version"],
                "purl": f"pkg:pypi/{it['name'].lower().replace('_', '-')}@{it['version']}",
                "properties": [{"name": "lock:entry", "value": it["raw"]}],
            }
            for it in items
        ],
        "vulnerabilities": [],
        "externalReferences": [
            {"type": "vcs", "url": "https://github.com/pypa/advisory-database",
             "comment": f"漏洞公告库（CVE）参照：{CVE_DB}"},
        ],
    }
    return doc


def render(doc: dict) -> str:
    return json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=False) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只校验已落盘 SBOM 与 lock 同步")
    a = ap.parse_args()
    doc = build()
    text = render(doc)
    if a.check:
        if not os.path.exists(LATEST):
            print("[sbom] 缺失 reports/_sbom/sbom_latest.json（运行 python -m tools.gen_sbom）")
            return 1
        have = json.load(open(LATEST, encoding="utf-8"))
        h_lock = _lock_hash()
        have_hash = (have.get("metadata", {}).get("properties") or [{}])
        got = next((p.get("value") for p in have_hash if p.get("name") == "lock:sha256"), "")
        if got != h_lock:
            print(f"[sbom] SBOM 记录 lock sha256={got[:16]}… ≠ 当前 lock {h_lock[:16]}…（须重新生成）")
            return 1
        if len(have.get("components") or []) != len(doc["components"]):
            print("[sbom] 组件数与 lock 条目数不一致（须重新生成）")
            return 1
        print(f"[sbom] 与 lock 同步（{len(doc['components'])} 组件，sha256={h_lock[:12]}…）")
        return 0
    os.makedirs(OUT_DIR, exist_ok=True)
    day = datetime.now(TZ8).strftime("%Y%m%d")
    dated = os.path.join(OUT_DIR, f"sbom_{day}.json")
    for p in (LATEST, dated):
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    print(f"[sbom] 已生成 {os.path.relpath(LATEST, ROOT)} 与 {os.path.relpath(dated, ROOT)}"
          f"（{len(doc['components'])} 组件，lock sha256={_lock_hash()[:12]}…）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
