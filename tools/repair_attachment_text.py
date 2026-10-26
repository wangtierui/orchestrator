# -*- coding: utf-8 -*-
"""tools/repair_attachment_text.py — 附件正文**补抽**（只修"采集时没抽出来"的记录）。

为什么需要它（2026-10-26）
--------------------------
`sniff_kind` 曾把 docx 当成 xlsx（在压缩字节里找 `xl/`），抽取随即报
`File contains no valid workbook part`，`extract_status` 落成 `corrupt`、`text` 为空。
修好嗅探之后，**已经落盘的历史记录不会自己变好** —— 事实源里仍是空文本，
页面照旧看不到内容。本工具做的是"用修好的 SSOT 重抽一遍并回写事实源"。

纪律
----
* **单一写入方式**：只改 `attachments[i]` 里的抽取结果字段，不动记录其它字段；
  写入前备份原文件，写入用 tmp + `os.replace`（原子）；
* **单一抽取实现**：一律调 `std_lib.scraper_std.crawler_common.extract_document_text`
  （不复制解析逻辑）；
* **默认 dry-run**，`--apply` 才落盘；`--docno` 可限定范围。

用法
----
    python -m tools.repair_attachment_text --docno "金非银检函〔2025〕82号"
    python -m tools.repair_attachment_text --all            # 全量扫描（只报告）
    python -m tools.repair_attachment_text --docno "…" --apply
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import sys

from bootstrap import bootstrap

bootstrap("all")

from config.exitcodes import ExitCode
from std_lib.scraper_std.crawler_common import extract_document_text

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUPP_RAW = os.path.join(ROOT, "modules", "regulatory_scrapers", "data", "raw",
                        "supplementary_regulations.jsonl")

#: 需要补抽的抽取状态（`ok`/`empty`/`needs_ocr` 都是**有效结论**，不重抽）
BROKEN = ("", "corrupt", "unsupported", "library_missing")


def _is_image(att: dict) -> bool:
    """图片附件（无正文层，抽取结论恒为 unsupported）。"""
    ext = os.path.splitext(str(att.get("local_path") or att.get("name") or ""))[1].lower()
    return ext in (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tif", ".tiff", ".webp") \
        or str(att.get("attachment_kind") or "").lower() in ("jpg", "jpeg", "png")


def _abs(local_path: str) -> str:
    return os.path.join(ROOT, local_path.replace("/", os.sep)) if local_path else ""


def scan(rows: list[dict], docno: str = "", *, only_broken: bool = True) -> list[dict]:
    """找出待补抽的 (记录下标, 附件下标, 路径, 原状态)。"""
    want = (docno or "").strip()
    out: list[dict] = []
    for ri, rec in enumerate(rows):
        if want and str(rec.get("document_number") or "").strip() != want:
            continue
        for ai, att in enumerate(rec.get("attachments") or []):
            st = str(att.get("extract_status") or "")
            if only_broken and st not in BROKEN:
                continue
            # 图片类附件**本来就没有正文**（E-R 图就是一张 jpg）：重抽多少次都是
            # unsupported，列进待补抽只会制造噪声，还会掩盖真正的漏抽。
            if _is_image(att):
                continue
            p = _abs(str(att.get("local_path") or ""))
            if not p or not os.path.exists(p):
                continue
            out.append({"ri": ri, "ai": ai, "path": p, "status": st,
                        "no": str(att.get("attachment_no") or ""),
                        "name": str(att.get("name") or "")[:60],
                        "size": os.path.getsize(p)})
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="repair_attachment_text",
                                 description="附件正文补抽（修 sniff/extract 缺陷遗留）")
    ap.add_argument("--docno", default="", help="只处理该文号")
    ap.add_argument("--all", action="store_true", help="全量（含已成功的也统计，仅影响报表）")
    ap.add_argument("--apply", action="store_true", help="写回事实源（默认 dry-run）")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args(argv)

    if not os.path.exists(SUPP_RAW):
        print(f"[repair] 事实源不存在：{SUPP_RAW}")
        return ExitCode.ENV
    rows = []
    with open(SUPP_RAW, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))

    todo = scan(rows, a.docno, only_broken=not a.all)
    if a.limit:
        todo = todo[: a.limit]
    print(f"[repair] 记录 {len(rows)} 条；待补抽附件 {len(todo)} 个"
          f"{'（--docno ' + a.docno + '）' if a.docno else ''}")
    for t in todo[:10]:
        print(f"   {t['no']:>3s} {t['name']:<44s} {t['size']/1048576:6.2f}MB "
              f"原状态={t['status'] or '(空)'}")
    if len(todo) > 10:
        print(f"   … 其余 {len(todo) - 10} 个")

    fixed = 0
    failed: list[tuple[str, str]] = []
    changed_records: set[int] = set()
    for t in todo:
        with open(t["path"], "rb") as fh:
            data = fh.read()
        r = extract_document_text(data, t["name"])
        att = rows[t["ri"]]["attachments"][t["ai"]]
        if r.get("extracted"):
            att["text"] = r.get("text", "")
            att["extracted"] = True
            att["extract_status"] = r.get("extract_status", "ok")
            att["attachment_kind"] = r.get("kind", "")
            att["needs_ocr"] = bool(r.get("needs_ocr"))
            att["sha256"] = r.get("sha256", "")
            att["re_extract"] = {
                "at": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                "by": "tools.repair_attachment_text",
                "prev_status": t["status"],
                "chars": len(att["text"]),
            }
            changed_records.add(t["ri"])
            fixed += 1
            print(f"   ✓ {t['no'] or '-'} {t['name'][:40]} → {len(att['text'])} 字")
        else:
            failed.append((t["no"] or "-", f"{t['name'][:36]} → {r.get('extract_status')}"))

    print(f"\n[repair] 成功 {fixed} / 失败 {len(failed)}")
    for no, why in failed[:8]:
        print(f"   ✗ {no} {why}")
    if not a.apply:
        print("[repair] dry-run：未写回（加 --apply 落盘）")
        return ExitCode.OK if fixed or not todo else ExitCode.DATA
    if not changed_records:
        print("[repair] 无变化，跳过写入")
        return ExitCode.OK

    bak = SUPP_RAW + ".bak_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    shutil.copy2(SUPP_RAW, bak)
    tmp = SUPP_RAW + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        for rec in rows:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    os.replace(tmp, SUPP_RAW)
    print(f"[repair] 已写回 {len(changed_records)} 条记录（备份 {os.path.basename(bak)}）")
    return ExitCode.OK


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    raise SystemExit(main())
