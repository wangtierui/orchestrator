# -*- coding: utf-8 -*-
"""mof/mof_io — **产物写出层**（状态 / JSON / CSV / HTML 报告）。

为什么单独成文件（2026-10-20 第一百零五批）
------------------------------------------
四个函数都是"把内存结构落成文件"，与抓取逻辑无关；且都依赖
`fs_lock.atomic_write_text`（原子写 SSOT）。抽出来后：
  · 「写盘必须原子」这条约束集中在**一个文件**里，改一处即全局生效；
  · `main` 只剩编排，读起来是一条线。
"""
from __future__ import annotations

# ---- P3b 仓库引导（migrate_collectors_p3b.py 注入）：使 std_lib/config 可导入 ----
import os as _os
import sys as _sys

_GUIDE_ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "..", "..", ".."))
if _GUIDE_ROOT not in _sys.path:
    _sys.path.insert(0, _GUIDE_ROOT)
del _GUIDE_ROOT, _os, _sys

import csv
import io
import json
import os
from datetime import UTC, datetime

from std_lib.common_lib import fs_lock

# `HOST` 由常量层提供（`save_json`/`save_html_report` 把它写进产物的 `source` 字段）。
# ⚠️ 双形态：本文件被包形态与**脚本形态**（生产 `COLLECT_CMD`）触达。
try:
    from .mof_constants import HOST
except ImportError:  # pragma: no cover  脚本形态
    from mof_constants import HOST  # type: ignore[no-redef]


def write_status(outdir, status):
    """记录抓取状态（成功 / 异常），供调度与监控读取，保留异常状态处理能力。"""
    fs_lock.atomic_write_text(os.path.join(os.path.dirname(outdir), "state", "status.json"),
                  json.dumps(status, ensure_ascii=False, indent=2))


def save_json(entries, path):
    fs_lock.atomic_write_text(path, json.dumps({
        "source": HOST,
        "captured_at": datetime.now(UTC).isoformat(),
        "count": len(entries),
        "items": entries,
    }, ensure_ascii=False, indent=2))


def save_csv(entries, path):
    cols = [
        "id", "title", "category_name", "category_id", "publish_date",
        "effective_date", "issue_org", "doc_no", "summary", "content_text",
        "content_length", "status", "create_dept", "file_count",
        "attachment_count", "attachment_names", "attachments",
        "active", "change", "removed_at",
        "detail_link", "api_link", "source", "fetch_time",
    ]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, quoting=csv.QUOTE_ALL, lineterminator="\n")
    w.writeheader()
    for e in entries:
        w.writerow({c: e.get(c, "") for c in cols})
    # 前置 BOM，保证 Excel 正确识别 UTF-8
    fs_lock.atomic_write_text(path, "\ufeff" + buf.getvalue())


__all__ = ['save_csv', 'save_json', 'write_status']
