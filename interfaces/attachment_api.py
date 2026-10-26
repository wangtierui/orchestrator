# -*- coding: utf-8 -*-
"""interfaces.attachment_api — **附件清单与正文的唯一读取口**。

为什么要有这一层（2026-10-26，回答"是否重复造轮子"）
----------------------------------------------------
`regulatory_scrapers` 采集时就把每个附件写进了 raw 事实源（`supplementary_regulations.jsonl`
的 `attachments[]`），字段齐全：

    name / local_path / sha256 / bytes / attachment_no / kind /
    extract_status / extracted / text

也就是说"**有哪些附件、原文在哪、正文抽出来没有**"早就有单一事实源了。此前
`tools/build_east_structure.py` 自己去 `data/corpus/...` 目录 glob 文件 —— 那是在
**事实源之外另造一套发现逻辑**，直接后果就是同一批文件在仓里存在多份副本、
改一边漏一边。本模块把它收敛成一条读路径：

* 页面（附件清单 / 正文可查）→ 本模块
* EAST 结构解析（按 `attachment_no` 取 1-3/1-4/1-5…）→ 本模块

字节文件仍只在采集目录（`modules/regulatory_scrapers/data/docs/...`）存**一份**，
`local_path` 指向它；人工归档目录 `data/corpus/regulatory_stats/**` 不再被程序读取。
"""

from __future__ import annotations

import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: supp 采集事实源（相对仓根；随 `paths` 之外的采集器输出约定）
SUPP_RAW = os.path.join(ROOT, "modules", "regulatory_scrapers", "data", "raw",
                        "supplementary_regulations.jsonl")

_CACHE: dict[str, dict] = {}


def _iter_raw(path: str = SUPP_RAW):
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    yield json.loads(line)
                except ValueError:
                    continue


def find_record(docno: str, *, path: str = SUPP_RAW) -> dict | None:
    """按文号在 supp 事实源里找记录（命中即返回整条，含 `attachments`）。"""
    want = (docno or "").strip()
    if not want:
        return None
    for rec in _iter_raw(path):
        got = str(rec.get("document_number") or rec.get("docno") or "").strip()
        if got == want:
            return rec
    return None


def attachments_for_docno(docno: str, *, with_text: bool = False,
                          text_head: int = 0) -> dict:
    """文号 → 附件清单（**唯一读路径**）。

    返回 `{available, docno, title, count, items:[{no,name,kind,bytes,sha256,
    local_path,exists,extract_status,extracted,text_len,text?}]}`。

    `with_text=True` 时附 `text`（默认不带：1 份 .doc 全文可达 300 万字符，
    列表接口带上会把响应撑爆；`text_head>0` 时只取前 N 字符）。
    """
    key = f"{docno}|{with_text}|{text_head}"
    if key in _CACHE:
        return _CACHE[key]
    rec = find_record(docno)
    if not rec:
        return {"available": False, "docno": docno, "count": 0, "items": [],
                "reason": f"supp 事实源未找到文号 {docno}"
                          f"（文件 {os.path.relpath(SUPP_RAW, ROOT)}）"}
    items = []
    for a in rec.get("attachments") or []:
        lp = str(a.get("local_path") or "")
        abs_p = os.path.join(ROOT, lp.replace("/", os.sep)) if lp else ""
        item = {
            "no": str(a.get("attachment_no") or ""),
            "name": str(a.get("name") or ""),
            "kind": str(a.get("attachment_kind") or a.get("kind") or ""),
            "bytes": int(str(a.get("bytes") or 0).split(".")[0] or 0),
            "sha256": str(a.get("sha256") or ""),
            "local_path": lp,
            "exists": bool(abs_p) and os.path.exists(abs_p),
            "extract_status": str(a.get("extract_status") or ""),
            "extracted": str(a.get("extracted") or "").lower() == "true",
            "text_len": len(a.get("text") or ""),
        }
        if with_text:
            txt = a.get("text") or ""
            item["text"] = txt[:text_head] if text_head else txt
        items.append(item)
    # `items` 是 `list[dict[str, object]]`，`x["no"]` 对 mypy 是 `object`；
    # 排序键需要 `len()` 与可比较 —— 显式转 `str`（运行时值本来就是 str）。
    items.sort(key=lambda x: (len(str(x.get("no") or "")), str(x.get("no") or "")))
    out = {
        "available": True,
        "docno": str(rec.get("document_number") or rec.get("docno") or ""),
        "title": str(rec.get("title") or ""),
        "publish_date": str(rec.get("publish_date") or ""),
        "issue_organ": str(rec.get("issue_organ") or ""),
        "source": str(rec.get("source") or ""),
        "count": len(items),
        "items": items,
    }
    _CACHE[key] = out
    return out


def attachment_file(docno: str, no: str) -> tuple[str, dict] | None:
    """(文号, 附件号) → `(绝对路径, 元数据)`；用于解析器按号取文件。"""
    d = attachments_for_docno(docno)
    for it in d.get("items") or []:
        if str(it.get("no")) == str(no) and it.get("exists"):
            abs_p = os.path.join(ROOT, str(it["local_path"]).replace("/", os.sep))
            return abs_p, it
    return None


if __name__ == "__main__":  # 离线自检
    import sys as _sys

    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    d = attachments_for_docno("金非银检函〔2025〕82号")
    print(json.dumps({k: d[k] for k in ("available", "docno", "count")},
                     ensure_ascii=False, indent=2))
    for it in d.get("items") or []:
        print(f"  {it['no']:>3s} {it['name'][:44]:46s} {it['bytes']/1048576:6.2f}MB "
              f"{it['kind']:>5s} {it['extract_status']:>10s} text={it['text_len']}")
