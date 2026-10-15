"""重建 `tests/cassettes/manifest.json`（夹具事实源清单：名称/来源/交互数/文件摘要）。

manifest 是**审计面**（J13 判据据此发现「夹具被手改却未更新摘要」「漏登记」「夹带密钥」），
因此由工具统一生成，不手改。命令：
    python -m tools.record_cassettes --rebuild-manifest
"""
from __future__ import annotations

import json
import os
import sys
import time

from bootstrap import bootstrap

bootstrap("all")

import paths
from config.exitcodes import ExitCode
from tests import http_cassette as HC

MANIFEST_NAME = "manifest.json"


def rebuild_manifest() -> str:
    """扫描夹具目录 → 写 manifest（稳定排序 + LF）。"""
    d = os.path.join(os.path.dirname(HC.__file__), HC.CASSETTES_DIRNAME)
    rows = []
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".json") or fn == MANIFEST_NAME:
            continue
        name = fn[:-5]
        doc = HC.load_cassette(name)  # 结构校验（含逐条摘要）
        rows.append({
            "name": name,
            "source": doc.get("source"),
            "origin": doc.get("origin"),
            "interactions": len(doc.get("interactions") or []),
            "note": doc.get("note", ""),
            "sha256": HC.cassette_file_sha256(name),
        })
    out = {
        "schema_version": HC.SCHEMA_VERSION,
        "generated_by": "tools/record_cassettes.py --rebuild-manifest",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "cassettes": rows,
    }
    p = os.path.join(d, MANIFEST_NAME)
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    return p


def main(argv: list[str] | None = None) -> int:
    # `python -m tools.record_cassettes …` 时 main() 无参 ⇒ 显式回落 sys.argv[1:]
    # （既支持 `main(["--rebuild-manifest"])` 的程序内调用，也支持命令行）
    argv = list(argv) if argv is not None else sys.argv[1:]
    if "--rebuild-manifest" in argv:
        p = rebuild_manifest()
        print(f"[ok] manifest 已重建：{os.path.relpath(p, paths.ROOT)}")
        return ExitCode.OK
    print(__doc__)
    print("用法：python -m tools.record_cassettes --rebuild-manifest")
    return ExitCode.USAGE


if __name__ == "__main__":
    raise SystemExit(main())
