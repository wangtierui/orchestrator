# -*- coding: utf-8 -*-
"""mof/att_extract — mof 采集的**纯变换层**（无网络、无模块级可变状态）。

为什么单独成文件（2026-10-20，D-遗留拆分第三批）
------------------------------------------------
`attachments.py` 原本把「常量 + 纯变换 + 网络 + 下载 + 熔断 + 编排」混在 668 行里。
纯变换部分是**最容易被独立测试**的一块（输入→输出，无 IO），抽出来后：

  · 可用纯函数测试覆盖（不必造 HTTP/文件环境）；
  · `html_to_text` / `normalize_date` / `safe_filename` 这类**语义敏感**函数
    单独一处，改动时影响面清楚；
  · 主文件只剩网络/下载/编排，读起来是一条线。

**逐字搬运**：本文件的 5 个函数与其 docstring 全部**原样**搬自
`attachments.py`（未改一行逻辑），仅新增 import。
"""
from __future__ import annotations

import html
import json
import os
import re
import sys
import urllib.parse
from datetime import UTC, datetime

# ⚠️ **双形态导入**（生产必踩）：本模块被 `attachments.py` 导入，而 `attachments.py`
# 自身有两条进入路径：
#   · 包形态：`collector.py` 里 `from . import attachments as _att` ⇒ 相对导入可用；
#   · **脚本形态**：`python collectors/mof/collector.py`（生产 `COLLECT_CMD` 就是
#     这样调的），`collector.py` 兜底走 `import attachments as _att` ⇒ 此时
#     `attachments` 是**顶层模块**、没有包上下文 ⇒ 本文件的相对导入会抛
#     `attempted relative import with no known parent package`。
# 故与 `attachments.py` 的 `try/except` 惯例保持一致。
try:
    from .att_constants import _STYLE_RE, _TAG_RE, _WS_RE, BASE, HOST
except ImportError:  # pragma: no cover  脚本形态
    from att_constants import _STYLE_RE, _TAG_RE, _WS_RE, BASE, HOST

__all__ = [
    "_mof_cache_key", "build_entry", "html_to_text", "normalize_date",
    "safe_filename",
]


def _mof_cache_key(method, path, payload):
    """为 _request 生成确定性缓存键 (endpoint, params)。

    - POST：endpoint = HOST+path，params = payload（嵌套 dict/list 值用 JSON 序列化，
      保证 urlencode 安全且确定性；mof 列表 payload 含 queryMap 嵌套，必须此处理）。
    - GET：若 path 含查询串则拆出 query 作为 params；否则 params={}。
    键交给 ResponseCache.path()（与 nfra 命名算法逐字一致，仅扩展名 .json）。
    """
    full_url = HOST + path
    if method == "GET" and "?" in path:
        ep, q = path.split("?", 1)
        params = dict(urllib.parse.parse_qsl(q))
        endpoint = HOST + ep
    elif method == "POST":
        endpoint = full_url
        params = dict(payload or {})
    else:
        endpoint = full_url
        params = {}
    # 嵌套值（dict/list）JSON 序列化，标量保持原样（urlencode 安全 + 确定性）
    flat = {}
    for k, v in params.items():
        flat[k] = (json.dumps(v, sort_keys=True, ensure_ascii=False)
                   if isinstance(v, (dict, list)) else v)
    return endpoint, flat


def html_to_text(s):
    """将正文 HTML 转为纯文本。"""
    if not s or not isinstance(s, str):
        return ""
    s = _STYLE_RE.sub(" ", s)
    s = _TAG_RE.sub(" ", s)
    s = html.unescape(s)
    s = _WS_RE.sub(" ", s)
    return s.strip()


# norm-specialization: 字段规范化型日期（B3/W4 登记，norm.py 日期分层第三类）——
# 与解析层 cleaner.normalize_date 的真实语义差异：入参为**字段值**（list [y,m,d] /
# datetime 字符串 / None），输出 None 语义（缺省 vs 空串分化）；抽取型（crawler_common）
# 入参为长文本。三类各有契约，不合并。
def normalize_date(v):
    """规范化日期字段（兼容 '2024-11-03 00:00:00' / [2024,11,3,0,0] / 'None'）。"""
    if v is None:
        return None
    if isinstance(v, list) and len(v) >= 3:
        try:
            return f"{int(v[0]):04d}-{int(v[1]):02d}-{int(v[2]):02d}"
        except Exception:  # noqa: BLE001
            return None
    if isinstance(v, str):
        v = v.strip()
        if v in ("", "None", "null", "[]"):
            return None
        # 截取日期部分（去时间）
        m = re.match(r"(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})", v)
        if m:
            return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        return v.split(" ")[0] if " " in v else v
    return str(v)


def safe_filename(name, ext, fallback):
    """生成安全的本地文件名（去除非法字符、补扩展名、限长）。

    （共享库对齐审计 阶段 1）核心逻辑（去非法字符 / 限长）委托
    ``crawler_common.safe_filename``；本函数保留 mof 原有语义：
    **无 URL 参与哈希**、第三参 ``fallback`` 兜底、末补扩展名（共享库版不补扩展名）。
    """
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    from std_lib.scraper_std.crawler_common import safe_filename as _cc_safe_filename

    name = _cc_safe_filename(name or fallback, "", 120)
    if ext and not name.lower().endswith("." + ext.lower()):
        name = f"{name}.{ext}"
    return name


def build_entry(rec, detail, category_id, category_name, fetch_detail_enabled,
                attachments=None):
    """将列表记录 + 详情合并为结构化条目。"""
    # 正文优先用详情接口（列表里通常也已包含），兜底用列表字段
    content_html = ""
    if fetch_detail_enabled and detail:
        content_html = detail.get("lcontent") or ""
    if not content_html:
        content_html = rec.get("lcontent") or ""
    content_text = html_to_text(content_html)

    # 摘要：题注优先，否则取正文前若干字符
    summary = ((detail or rec).get("lcaption") if fetch_detail_enabled
               else rec.get("lcaption"))
    if not summary:
        summary = content_text[:200]

    law_id = rec.get("id") or (detail or {}).get("id")

    rec_out = {
        "id": law_id,
        "title": rec.get("title"),
        "category_id": category_id,
        "category_name": category_name or rec.get("ltflbName"),
        "publish_date": normalize_date((detail or rec).get("lBuDate")
                                       if fetch_detail_enabled else rec.get("lBuDate")),
        "effective_date": normalize_date((detail or rec).get("lSsDate")
                                         if fetch_detail_enabled else rec.get("lSsDate")),
        "issue_org": (detail or rec).get("lbbdw") if fetch_detail_enabled else rec.get("lbbdw"),
        "doc_no": (detail or rec).get("lwh") if fetch_detail_enabled else rec.get("lwh"),
        "summary": summary,
        "content_text": content_text,
        "content_length": len(content_text),
        "status": (detail or rec).get("lstate") if fetch_detail_enabled else rec.get("lstate"),
        "create_dept": rec.get("createDeptName"),
        "file_count": rec.get("fileCount"),
        "expire_date": normalize_date((detail or rec).get("expireDate")
                                      if fetch_detail_enabled else rec.get("expireDate")),
        "abolish_date": normalize_date((detail or rec).get("abolishDate")
                                       if fetch_detail_enabled else rec.get("abolishDate")),
        "detail_link": f"{HOST}/ui/src/views/law_html/{law_id}.html" if law_id else None,
        "api_link": f"{BASE}/lawFile/get/{law_id}" if law_id else None,
        "source": "fgk.mof.gov.cn",
        "fetch_time": datetime.now(UTC).isoformat(),
        "attachments": attachments or [],
        "attachment_count": len(attachments or []),
        # 批 51/W-A：记录级聚合副本不再写（attachments[].text 为唯一权威；cleaned 侧派生）
        "table_structured": [t for a in (attachments or [])
                             for t in (a.get("table_structured") or [])] or [],
        "table_raw_text": "\n\n".join(
            a.get("table_raw_text") for a in (attachments or []) if a.get("table_raw_text")
        ),
        "table_recovery_method": "structured" if any(
            a.get("table_structured") for a in (attachments or [])) else "",
        "rich_structured": [o for a in (attachments or [])
                            for o in (a.get("rich_structured") or [])] or [],
        "rich_text": "\n".join(
            a.get("rich_text") for a in (attachments or []) if a.get("rich_text")),
        "rich_count": sum((a.get("rich_count") or 0) for a in (attachments or [])),
        "attachment_names": "; ".join(
            (a.get("file_name") or "") for a in (attachments or [])
        ),
    }
    # 批 50：`attachments` 只保留**真实附件**；正文载体（fileType=90「下载文字版」/ 站点生成的
    # 「HTML 冒充 .doc」文字版）移入 `body_docs`；其文本若未被正文覆盖则**并入正文字段**（不丢内容）
    from std_lib.scraper_std.attachments import split_record_body_docs

    split_record_body_docs(rec_out)
    _all_atts = (list(rec_out.get("attachments") or [])
                 + list(rec_out.get("body_docs") or []))
    rec_out["table_structured"] = [t for a in _all_atts
                                   for t in (a.get("table_structured") or [])] or []
    rec_out["table_raw_text"] = "\n\n".join(
        a.get("table_raw_text") for a in _all_atts if a.get("table_raw_text"))
    rec_out["table_recovery_method"] = "structured" if any(
        a.get("table_structured") for a in _all_atts) else ""
    rec_out["rich_structured"] = [o for a in _all_atts
                                  for o in (a.get("rich_structured") or [])] or []
    return rec_out
