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
import html
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


def save_html_report(entries, path):
    """生成可读的离线 HTML 报告（自包含，无外部依赖）。"""
    items_html = []
    for i, e in enumerate(entries, 1):
        att_cell = ""
        for a in (e.get("attachments") or []):
            lp = a.get("local_path")
            fn = html.escape(str(a.get("file_name", "")))
            att_cell += (f'<a href="{html.escape(lp)}" target="_blank">{fn}</a><br>'
                         if lp else fn + "<br>")
        if not att_cell:
            att_cell = "—"
        items_html.append(f"""
        <tr>
          <td>{i}</td>
          <td><a href="{e.get('detail_link','')}" target="_blank">{html.escape(str(e.get('title','')))}</a></td>
          <td>{html.escape(str(e.get('category_name','')))}</td>
          <td>{html.escape(str(e.get('publish_date','')))}</td>
          <td>{html.escape(str(e.get('effective_date','')))}</td>
          <td>{html.escape(str(e.get('issue_org','')))}</td>
          <td>{html.escape(str(e.get('doc_no','')))}</td>
          <td>{html.escape(str(e.get('summary',''))[:160])}</td>
          <td>{html.escape(str(e.get('content_length','')))}</td>
          <td>{att_cell}</td>
          <td>{'有效' if e.get('active') else '已移除'}</td>
          <td>{html.escape(str(e.get('change','')))}</td>
        </tr>""")
    doc = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>财政部法规数据库抓取结果</title>
<style>
 body{{font-family:-apple-system,"Microsoft YaHei",sans-serif;margin:24px;color:#222}}
 h1{{font-size:20px}} table{{border-collapse:collapse;width:100%;font-size:13px}}
 th,td{{border:1px solid #ddd;padding:6px 8px;text-align:left;vertical-align:top}}
 th{{background:#f5f7fa}} tr:nth-child(even){{background:#fafafa}}
 a{{color:#1E6FD9;text-decoration:none}}
 .meta{{color:#888;font-size:12px;margin-bottom:12px}}
</style></head><body>
<h1>财政部法规数据库抓取结果</h1>
<div class="meta">来源：{HOST} ｜ 条目数：{len(entries)} ｜ 生成时间：{datetime.now().isoformat(timespec='seconds')}</div>
<table>
<thead><tr><th>#</th><th>标题</th><th>类别</th><th>公布日期</th><th>生效日期</th>
<th>发文机关</th><th>发文字号</th><th>内容摘要</th><th>正文字数</th><th>附件</th><th>状态</th><th>变更</th></tr></thead>
<tbody>{''.join(items_html)}</tbody>
</table></body></html>"""
    fs_lock.atomic_write_text(path, doc)


__all__ = ['save_csv', 'save_html_report', 'save_json', 'write_status']
