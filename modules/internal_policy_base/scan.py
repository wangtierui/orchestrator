# -*- coding: utf-8 -*-
"""
internal_policy_base.scan — 内部制度源目录扫描与文件名解构（P6 scanner）

输入：制度源目录（默认可经 INTERNAL_POLICY_ROOT 或 --source-dir 指定，不硬编码路径）。
输出：文件级元数据清单（不解读正文）：
      {
        "ipn": "IPN-<16hex>",            # D-03：独立编号，md5(文号|标题)[:16]
        "file_name": "...",               # 原文件名
        "extension": "pdf|doc|docx|xlsx|xls",
        "docno": "阳光人寿办发〔2023〕58号",  # 文件名解构；无 → ""
        "title": "...",                   # 文件名解构（去文号/扩展名/前导_）；无 → 去扩展名
        "source_path": "绝对路径(源目录)",
        "size_bytes": 123,
        "sha256": "hex",                  # 内容指纹（摄取去重）
        "scanned_at": "YYYY-MM-DD HH:MM:SS",
      }

文件名形态（实测 107 文件）：
  1) 有文号：`阳光人寿办发〔2023〕58号_个险客经渠道团队套利处置管理办法.pdf`
  2) 无文号：`_个险中心城市渠道营销员考勤管理办法（2023版）.pdf`（前导 _）
  3) 无文号无下划线：`某某管理办法.pdf`
"""
from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime

# 文号形态：机关代字〔年〕序号 号（含 全半角括号、纯数字年、可无代字）
_DOCNO_RE = re.compile(
    r"(?P<docno>[\u4e00-\u9fa5]{1,20}(?:〔|\()\d{4}(?:〕|\))\s*第?\s*\d{1,4}\s*号)"
)
# 年份括注（如 （2023版）/（2023年））不属文号
_EXT_RE = re.compile(r"\.(pdf|doc|docx|xlsx|xls|xlsm|csv|txt|wps|rtf)$", re.I)
_SUPPORTED = {".pdf", ".doc", ".docx", ".xlsx", ".xls", ".xlsm"}


def parse_filename(name: str) -> dict:
    """文件名 → {docno, title}（解构锚点，非正文权威）。
    有文号：docno=文号, title=下划线后段（去扩展名）；
    无文号：title=去前导'_'/'-' 后整名（去扩展名）。
    """
    base = _EXT_RE.sub("", name.strip())
    m = _DOCNO_RE.search(name)
    docno = m.group("docno").strip() if m else ""
    if docno:
        # 文号后通常是 _ 分隔标题
        after = name[m.end():].lstrip("_")
        title = _EXT_RE.sub("", after).strip() if after else _EXT_RE.sub("", base)
    else:
        title = _EXT_RE.sub("", base).lstrip("_").lstrip("-").strip()
    return {"docno": docno, "title": title}


def ipn_of(docno: str, title: str, extension: str = "") -> str:
    """内部制度编号派生（D-03，独立 RFN 空间）：md5(文号|标题|介质) 前 16 位。

    键 = 文号 + '|' + 标题 + (可选 '|' + 扩展名)（2026-09-08 修复）：
      - 同一 OA 文号常整批发文（如「阳光人寿发〔2025〕293号」下发 17 份档案表单），
        若只用文号会全部冲突；
      - 同一制度常见双介质（同名 pdf + xlsx 并存），扩展名参与消歧；
    无文号文件回退标题。与 rfn.unique_key 同构但前缀分离（IPN-）。
    """
    docno = (docno or "").strip()
    title = (title or "").strip()
    ext = (extension or "").strip().lower().lstrip(".")
    key = f"{docno}|{title}|{ext}" if docno else f"{title}|{ext}"
    return "IPN-" + hashlib.md5(key.encode("utf-8")).hexdigest()[:16]


def scan_directory(root: str, *, supported: set[str] | None = None) -> list[dict]:
    """递归扫描目录下受支持文件 → 元数据清单（按文件名排序，确定性）。"""
    supported = supported or _SUPPORTED
    out = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if not os.path.isdir(root):
        raise FileNotFoundError(f"制度源目录不存在: {root}")
    for dirpath, _dirs, files in os.walk(root):
        for fn in sorted(files):
            ext = os.path.splitext(fn)[1].lower()
            if ext not in supported:
                continue
            p = os.path.join(dirpath, fn)
            sha = hashlib.sha256(open(p, "rb").read()).hexdigest()
            parsed = parse_filename(fn)
            out.append({
                "ipn": ipn_of(parsed["docno"], parsed["title"], extension=ext.lstrip(".")),
                "file_name": fn,
                "relative_path": os.path.relpath(p, root),
                "extension": ext.lstrip("."),
                "docno": parsed["docno"],
                "title": parsed["title"],
                "source_path": p,
                "size_bytes": os.path.getsize(p),
                "sha256": sha,
                "scanned_at": now,
            })
    return out


if __name__ == "__main__":  # 自检
    import json
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    samples = [
        "阳光人寿办发〔2023〕58号_个险客经渠道团队套利处置管理办法.pdf",
        "_个险中心城市渠道营销员考勤管理办法（2023版）.pdf",
        "阳光人寿客户敏感信息查询权限授权管理办法.doc",
    ]
    for s in samples:
        print(json.dumps(parse_filename(s), ensure_ascii=False))
