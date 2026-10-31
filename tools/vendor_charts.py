# -*- coding: utf-8 -*-
"""tools/vendor_charts.py — 把图表库**全量提取到本地**（离线可用、版本可复算）。

为什么不用 CDN：工作台是内网/离线可用的本地台，外链脚本一旦断网整页图表全白；
且 CDN 版本会漂移，审计时无法复算。

产物：
    web/assets/vendor/echarts/echarts.min.js                    （Apache-2.0）
    web/assets/vendor/echarts/echarts-gl.min.js                 （MIT）
    web/assets/vendor/echarts/echarts-wordcloud.min.js          （ISC）
    web/assets/vendor/echarts/echarts-liquidfill.min.js         （MIT）
    web/assets/vendor/echarts/echarts-graph-modularity.min.js   （ISC）
    web/assets/vendor/echarts/ecStat.min.js                     （BSD）
    web/assets/vendor/echarts/LICENSE-<pkg>.txt                 （逐包许可证）
    web/assets/vendor/echarts/MANIFEST.json                     （版本/体积/sha256/来源）

用法：
    python -m tools.vendor_charts            # 下载并写清单（已存在且校验通过则跳过）
    python -m tools.vendor_charts --check    # 只校验本地文件与清单是否一致（不联网）
    python -m tools.vendor_charts --force    # 强制重新下载
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "web", "assets", "vendor", "echarts")

# 版本**固定**（不要用 latest：审计要能复算）
PACKAGES = [
    {"pkg": "echarts", "version": "5.5.1", "path": "dist/echarts.min.js",
     "file": "echarts.min.js", "license": "Apache-2.0"},
    {"pkg": "echarts-gl", "version": "2.0.9", "path": "dist/echarts-gl.min.js",
     "file": "echarts-gl.min.js", "license": "MIT"},
    {"pkg": "echarts-wordcloud", "version": "2.1.0", "path": "dist/echarts-wordcloud.min.js",
     "file": "echarts-wordcloud.min.js", "license": "ISC"},
    {"pkg": "echarts-liquidfill", "version": "3.1.0", "path": "dist/echarts-liquidfill.min.js",
     "file": "echarts-liquidfill.min.js", "license": "MIT"},
    {"pkg": "echarts-graph-modularity", "version": "2.1.0",
     "path": "dist/echarts-graph-modularity.min.js",
     "file": "echarts-graph-modularity.min.js", "license": "ISC"},
    {"pkg": "echarts-stat", "version": "1.2.0", "path": "dist/ecStat.min.js",
     "file": "ecStat.min.js", "license": "BSD"},
]

CDN = "https://cdn.jsdelivr.net/npm/{pkg}@{ver}/{path}"
MANIFEST = os.path.join(OUT, "MANIFEST.json")


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _get(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "vendor_charts/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _load_manifest() -> dict:
    if os.path.exists(MANIFEST):
        with open(MANIFEST, encoding="utf-8") as fh:
            return json.load(fh)
    return {"packages": []}


def check() -> int:
    """只校验：文件在不在、sha256 与清单是否一致。"""
    man = _load_manifest()
    if not man.get("packages"):
        print("[vendor] 未找到 MANIFEST.json，先运行 python -m tools.vendor_charts")
        return 2
    bad = []
    for p in man["packages"]:
        path = os.path.join(OUT, p["file"])
        if not os.path.exists(path):
            bad.append(f"缺失 {p['file']}")
        elif _sha256(path) != p["sha256"]:
            bad.append(f"sha256 不符 {p['file']}")
    for b in bad:
        print("  ✗", b)
    print(f"[vendor] 校验 {len(man['packages'])} 个文件，问题 {len(bad)} 个")
    return 0 if not bad else 1


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if "--check" in args:
        return check()
    force = "--force" in args
    os.makedirs(OUT, exist_ok=True)
    man = _load_manifest()
    by_name = {p["file"]: p for p in man.get("packages", [])}

    for spec in PACKAGES:
        dest = os.path.join(OUT, spec["file"])
        url = CDN.format(pkg=spec["pkg"], ver=spec["version"], path=spec["path"])
        if os.path.exists(dest) and not force:
            sha = _sha256(dest)
            old = by_name.get(spec["file"])
            if old and old.get("sha256") == sha:
                print(f"  = {spec['file']}（已存在，校验通过）")
                spec.update({"sha256": sha, "bytes": os.path.getsize(dest), "url": url,
                             "downloaded_at": old.get("downloaded_at", "")})
                by_name[spec["file"]] = spec
                continue
        print(f"  ↓ {spec['file']} ← {url}")
        blob = _get(url)
        with open(dest, "wb") as fh:
            fh.write(blob)
        spec.update({"sha256": hashlib.sha256(blob).hexdigest(), "bytes": len(blob),
                     "url": url, "downloaded_at": time.strftime("%Y-%m-%dT%H:%M:%S")})
        by_name[spec["file"]] = spec
        print(f"    {len(blob):,} bytes")

        # 许可证（个别包没有 LICENSE 文件则跳过，清单里已记 license 字段）
        for cand in ("LICENSE", "LICENSE.txt", "LICENSE.md"):
            lurl = CDN.format(pkg=spec["pkg"], ver=spec["version"], path=cand)
            try:
                txt = _get(lurl)
            except Exception:  # noqa: BLE001
                continue
            with open(os.path.join(OUT, f"LICENSE-{spec['pkg']}.txt"), "wb") as fh:
                fh.write(txt)
            print(f"    + LICENSE-{spec['pkg']}.txt")
            break

    pkgs = [by_name[s["file"]] for s in PACKAGES if s["file"] in by_name]
    total = sum(p.get("bytes", 0) for p in pkgs)
    man = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "note": "图表库本地副本；版本固定，sha256 可复算（python -m tools.vendor_charts --check）",
        "total_bytes": total,
        "packages": pkgs,
    }
    with open(MANIFEST, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(man, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print(f"[vendor] 完成：{len(pkgs)} 个库，共 {total / 1024 / 1024:.2f} MB → {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
