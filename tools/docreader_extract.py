# -*- coding: utf-8 -*-
"""tools.docreader_extract — **docreader 的入链调用点**（链步骤 `docreader:extract`，N-180）

为什么需要它不是"纸面接线"
--------------------------
`weknora_docreader` 在清单里长期是"未装/未接线"（v2 P2-1）。本轮**源码级部署**完成后，
若只把清单状态改成"可用"而**不给出真实调用点**，就仍是纸面状态（本仓已把这类问题固化为
判据：绑定必须指向**真实调用点**，见 `gen_flow_map.MODEL_BINDINGS` 的 N-177 注释）。
本文件即那个调用点：对语料中的真实文档跑 docreader，产出**分析视图**并量化其相对既有
6 态抽取（`crawler_common.extract_document_text`）的**增量**。

两道纪律
--------
1. **零硬依赖 · 降级可观测**：docreader 不可用（venv/源码/驱动缺失，或子进程失败）→
   **显式 SKIP 并说明原因**，rc=0（增强层缺失是合法状态，不是故障）；
2. **不写事实源**：产物固定落 `reports/docreader/`（路径硬校验）——docreader 只提供
   "版面/定位增量"，**不替换**既有的抽取事实源（替换须先度量，见报告遗留项）。

用法
----
    python -m tools.docreader_extract --limit 20
    python -m tools.docreader_extract --root data/corpus --limit 5 --engine markitdown
"""

from __future__ import annotations

import argparse
import datetime
import io
import json
import os
import sys

# 引导：**必须经 `bootstrap`（唯一引导点）**（`gate_import_bootstrap` 断言 tools/ 层
# 的 `sys.path.insert` **只减不增**）→ 运行方式为 `python -m tools.docreader_extract`。
from bootstrap import bootstrap

bootstrap("all")

import paths
from config.exitcodes import ExitCode

OUT_DIR = os.path.join(paths.ROOT, "reports", "docreader")
EXTS = (".pdf", ".docx", ".xlsx", ".doc", ".xls", ".pptx", ".epub", ".md", ".html")


def _candidates(root: str, limit: int) -> list:
    """枚举待解析文件（**确定性排序** → 结果可复现）。"""
    abs_root = root if os.path.isabs(root) else os.path.join(paths.ROOT, root)
    out: list = []
    if not os.path.isdir(abs_root):
        return out
    for dp, _dn, fn in os.walk(abs_root):
        for f in sorted(fn):
            if f.lower().endswith(EXTS):
                p = os.path.join(dp, f)
                try:
                    sz = os.path.getsize(p)
                except OSError:
                    continue
                if sz > 0:
                    out.append((p, sz))
        if len(out) > limit * 4:
            break
    out.sort(key=lambda x: (x[0].replace(os.sep, "/")))
    return out[:limit]


def _baseline_extract(path: str) -> dict:
    """既有 6 态抽取（**对照组**）：`crawler_common.extract_document_text`。

    它在本仓是**事实源路径**；此处只读调用，用于量化 docreader 的增量（不写任何东西）。
    """
    try:
        from std_lib.scraper_std import crawler_common as cc

        with open(path, "rb") as fh:
            data = fh.read()
        ex = cc.extract_document_text(data, os.path.basename(path), enable_ocr=False)
        txt = (ex.get("text") or "").strip()
        return {"ok": bool(txt), "method": ex.get("method", ""), "chars": len(txt)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "method": f"error:{type(e).__name__}", "chars": 0}


def run(root: str = "data/corpus", limit: int = 20, engine: str = "", timeout: int = 600) -> dict:
    from std_lib.common_lib import docreader_bridge as br

    res: dict = {
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "root": root,
        "limit": limit,
        "engine": engine or "builtin",
        "status": "",
        "note": "",
        "bridge_available": False,
        "self_test": "",
        "files": 0,
        "ok": 0,
        "increment": {},
        "rows": [],
    }
    ok, why = br.available()
    res["bridge_available"] = ok
    if not ok:
        res["status"] = "skipped"
        res["note"] = f"docreader 不可用 → 显式 SKIP（增强层零硬依赖）：{why}"
        _write(res)
        return res
    tok, selfmsg = br.self_test()
    res["self_test"] = selfmsg
    if not tok:
        res["status"] = "skipped"
        res["note"] = f"docreader 自检未过 → 显式 SKIP：{selfmsg}"
        _write(res)
        return res

    cands = _candidates(root, limit)
    res["files"] = len(cands)
    if not cands:
        res["status"] = "skipped"
        res["note"] = f"`{root}` 下未找到可解析文件（扩展名 {EXTS}）"
        _write(res)
        return res

    dc = bc = 0
    blocks_total = 0
    for p, sz in cands:
        got = br.parse_file(p, engine=engine, timeout=timeout)
        base = _baseline_extract(p)
        dc += len(got.get("markdown") or "")
        bc += int(base.get("chars") or 0)
        blocks_total += len(got.get("source_blocks") or [])
        if got.get("ok"):
            res["ok"] += 1
        res["rows"].append(
            {
                "file": os.path.relpath(p, paths.ROOT).replace(os.sep, "/"),
                "size_kb": round(sz / 1024, 1),
                "docreader_ok": bool(got.get("ok")),
                "docreader_chars": len(got.get("markdown") or ""),
                "docreader_blocks": len(got.get("source_blocks") or []),
                "docreader_images": int(got.get("image_count") or 0),
                "docreader_engine": got.get("engine", ""),
                "docreader_elapsed_s": got.get("elapsed_s", 0),
                "baseline_method": base.get("method", ""),
                "baseline_chars": int(base.get("chars") or 0),
                "notice": (got.get("notice") or "")[:200],
            }
        )
    res["status"] = "ok"
    res["increment"] = {
        "docreader_chars_total": dc,
        "baseline_chars_total": bc,
        "chars_ratio": round(dc / bc, 3) if bc else None,
        "source_blocks_total": blocks_total,
        "files_ok": f"{res['ok']}/{res['files']}",
    }
    res["note"] = (
        f"docreader 解析 {res['ok']}/{res['files']} 份；字符量 {dc} vs 既有 6 态 {bc}"
        f"（比值 {res['increment']['chars_ratio']}）；**段落级定位 source_blocks 共 {blocks_total} 条**"
        "（既有 6 态**没有**该能力，是 docreader 的净增量）"
    )
    _write(res)
    return res


def _write(res: dict) -> None:
    """落盘**分析视图**（`reports/docreader/`）—— 路径硬校验：**绝不写事实源**。"""
    os.makedirs(OUT_DIR, exist_ok=True)
    today = datetime.datetime.now().strftime("%Y%m%d")
    jp = os.path.join(OUT_DIR, f"docreader_extract_{today}.json")
    mp = os.path.join(OUT_DIR, f"docreader_extract_{today}.md")
    assert os.path.abspath(jp).startswith(os.path.abspath(os.path.join(paths.ROOT, "reports"))), (
        "docreader 分析视图只能写 reports/ 下"
    )
    with open(jp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=1)
    L = [
        f"# docreader 解析分析视图 · {res['generated_at']}",
        "",
        "> **只读分析视图**：docreader 提供版面/定位增量，**不替换**既有抽取事实源"
        "（替换须先度量，见报告遗留项）。",
        f"> 状态：**{res['status']}** ｜ 文件 {res['files']} ｜ 成功 {res['ok']} ｜ 引擎 `{res['engine']}`",
        "",
        f"- 自检：{res.get('self_test') or '—'}",
        f"- 说明：{res['note']}",
    ]
    if res.get("rows"):
        L += [
            "",
            "| 文件 | 大小KB | docreader 字符 | 段落定位 | 图片 | 引擎 | 耗时s | 既有6态 method | 既有字符 |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for r in res["rows"]:
            L.append(
                f"| `{r['file'].split('/')[-1]}` | {r['size_kb']} | {r['docreader_chars']} | "
                f"{r['docreader_blocks']} | {r['docreader_images']} | {r['docreader_engine']} | "
                f"{r['docreader_elapsed_s']} | `{r['baseline_method']}` | {r['baseline_chars']} |"
            )
    with open(mp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(L) + "\n")
    res["artifacts"] = [os.path.relpath(jp, paths.ROOT), os.path.relpath(mp, paths.ROOT)]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="docreader 文档解析（分析视图；不写事实源）")
    ap.add_argument("--root", default="data/corpus", help="扫描根（默认 data/corpus）")
    ap.add_argument("--limit", type=int, default=20, help="最多解析文件数（默认 20）")
    ap.add_argument("--engine", default="", help="引擎：builtin（默认）/markitdown/opendataloader")
    ap.add_argument("--timeout", type=int, default=600, help="单文件超时（秒）")
    a = ap.parse_args(argv)
    res = run(a.root, a.limit, a.engine, a.timeout)
    print(f"[docreader_extract] status={res['status']}  files={res['files']}  ok={res['ok']}")
    if res.get("note"):
        print(f"  {res['note']}")
    for p in res.get("artifacts") or []:
        print(f"  产物 → {p}")
    return int(ExitCode.OK)


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    raise SystemExit(main())
