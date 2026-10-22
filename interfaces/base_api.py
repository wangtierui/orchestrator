# -*- coding: utf-8 -*-
"""interfaces/base_api — 底座发布件统一读取契约（Base Contract v1 §5）

**三模块唯一允许的底座访问面**（禁止裸读底座内部目录——根治 E-08/A-03 直引）：

  manifest(base)                  发布清单（sha256/计数/快照日/schema_version）
  query_external(**filters)       外部记录精确过滤（rfn/document_number/source/theme/timeliness_status）
  search_external(text, limit)    外部全文检索（FTS5 trigram，命中 title/document_number/body）
  get_external(record_id)         单条外部记录
  query_internal(theme="")        内部制度过滤（theme/ipn）
  search_internal(text, limit)    内部全文检索（policies + internal_clauses）
  get_policy(ipn)                 单条内部制度

数据源：`modules/regulatory_scrapers/published/`（external）与
`modules/internal_policy_base/published/`（internal）——JSONL 权威 + SQLite 派生索引。
索引缺失时明确报错指引 `orchestrator base publish`，不做兜底裸读。
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))  # interfaces/
_ROOT = os.path.dirname(_HERE)  # orchestrator 根
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_MODULES = os.path.join(_ROOT, "modules")
EXT_DIR = os.path.join(_MODULES, "regulatory_scrapers", "published")
INT_DIR = os.path.join(_MODULES, "internal_policy_base", "published")


class BaseUnavailable(RuntimeError):
    """发布件/索引未就绪（指引先运行 `orchestrator base publish`）。"""


def _db(base: str) -> str:
    if base == "external":
        p = os.path.join(EXT_DIR, "external_index.sqlite")
        hint = "orchestrator base publish --base external"
    else:
        p = os.path.join(INT_DIR, "internal_index.sqlite")
        hint = "orchestrator base publish --base internal"
    if not os.path.exists(p):
        raise BaseUnavailable(f"发布索引不存在: {p}（先运行 {hint}）")
    return p


def _conn(base: str) -> sqlite3.Connection:
    c = sqlite3.connect(_db(base))
    c.row_factory = sqlite3.Row
    return c


def manifest(base: str = "external") -> dict:
    d = EXT_DIR if base == "external" else INT_DIR
    p = os.path.join(d, "publish_manifest.json")
    if not os.path.exists(p):
        raise BaseUnavailable(f"发布清单不存在: {p}（先运行 orchestrator base publish）")
    return json.load(open(p, encoding="utf-8"))


def query_external(
    *,
    rfn: str = "",
    document_number: str = "",
    source: str = "",
    theme: str = "",
    timeliness_status: str = "",
    limit: int = 100,
    with_body: bool = False,
) -> list[dict]:
    """外部记录精确过滤（等值匹配；空参数忽略）。with_body=False 时省略正文（默认轻量）。"""
    cols = (
        "record_id,rfn,title,document_number,issue_organ,publish_date,effective_date,"
        "timeliness_status,verification_source,source,url,body_len,theme"
    )
    if with_body:
        cols += ",body_text"
    where: list[str] = []
    args: list[object] = []   # SQL 参数可为 str/int（如 LIMIT ? 的 int）
    for k, v in (
        ("rfn", rfn),
        ("document_number", document_number),
        ("source", source),
        ("theme", theme),
        ("timeliness_status", timeliness_status),
    ):
        if v:
            where.append(f"{k}=?")
            args.append(v)
    sql = f"SELECT {cols} FROM records"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " LIMIT ?"
    args.append(int(limit))
    with _conn("external") as c:
        return [dict(r) for r in c.execute(sql, args)]


#: trigram 分词器的**最短可检索长度**：只索引 3 字窗口，2 字词 `MATCH` 必然 0 行。
FTS_MIN_TERM = 3


def fts_terms(text: str) -> list[str]:
    """检索词切成**词元**（按空白切；去掉会破坏 FTS 语法的双引号）。"""
    toks = [t.replace('"', "") for t in re.split(r"\s+", (text or "").strip())]
    return [t for t in toks if t]


def fts_capable(text: str) -> bool:
    """该查询能否走 FTS（**每个词元**都 ≥ 3 字）。

    为什么按词元判定而不是按整串长度（2026-10-23 修复的正是这个坑）：
    原实现用 `len(q) < 3` 判断，于是"保险公司 营销"（9 字）被判为可走 FTS，
    但切词后含 2 字词元时 FTS 仍然 0 命中 —— 用户看到的就是"搜了没结果"。
    """
    toks = fts_terms(text)
    return bool(toks) and all(len(t) >= FTS_MIN_TERM for t in toks)


def fts_query(text: str) -> str:
    """词元 → FTS5 查询串：每个词元各自成短语，**词元之间 AND**。

    单短语（原实现 `'"整串"'`）对多关键词是灾难：用户输入"保险公司 营销"时
    原文里没有这个**连续子串** ⇒ 0 命中。改成 AND 后语义就是"两个词都要出现"。
    """
    return " AND ".join(f'"{t}"' for t in fts_terms(text))


def _like_where(q: str, cols: tuple[str, ...]) -> tuple[str, list[object]]:
    """多词 LIKE 的 WHERE 片段（词元 AND；`%`/`_` 先剔除防通配符注入）。"""
    toks = [t for t in str(q or "").replace("%", "").replace("_", "").split() if t]
    if not toks:
        return "", []
    parts: list[str] = []
    args: list[object] = []
    for t in toks:
        parts.append("(" + " OR ".join(f"{c} LIKE ?" for c in cols) + ")")
        args.extend([f"%{t}%"] * len(cols))
    return " AND ".join(parts), args


def _dedupe(rows: list[dict], key_cols: tuple[str, ...], limit: int = 0) -> list[dict]:
    """按 `key_cols` 去重（保留**先出现**的一行），并截到 `limit`。

    为什么要去重（2026-10-23 实测）：同一份文件可能同时来自多个源
    （gov / nfra 重复收录），`records` 里就是两行、两个 RFN。检索按相关性排序后
    它们会**相邻出现**，用户看到的是"同一条结果出现两次"，观感等同于"结果不准"。
    保留先出现的（即 bm25 更高 / 短标题优先的那条），是**排序语义的延续**。
    """
    out: list[dict] = []
    seen: set[tuple] = set()
    for r in rows:
        key = tuple(str(r.get(c) or "") for c in key_cols)
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
        if limit and len(out) >= limit:
            break
    return out

def search_external(text: str, limit: int = 20, kind: str = "records") -> list[dict]:
    """外部全文检索（FTS5 trigram，**按相关性排序**）。kind=records|clauses。

    2026-10-23 修的两处（用户报"检索出现多个无关文件"）：
      ① **缺 `ORDER BY bm25(...)`** —— FTS5 不排序时按 **rowid（入库顺序）** 返回，
         `LIMIT 20` 就成了"最早入库的 20 条含该词的文档"，与相关性无关。
         实测 `保险公司`：不排序返回「快递暂行条例/森林病虫害防治条例」，
         加 bm25 后返回「保险公司股权管理办法/保险公司控股股东管理办法」。
      ② **缺列权重** —— 标题命中应远高于正文随手提及。权重 `(title, docno, body)`
         取 `10 : 4 : 1`（标题最强、正文最弱），依据是本表列顺序。
    """
    q = fts_query(text)
    if not q:
        return []
    with _conn("external") as c:
        if kind == "clauses":
            sql = (
                "SELECT cl.record_id, cl.rfn, cl.title, cl.article_no, cl.article_body "
                "FROM clauses_fts f JOIN clauses cl ON cl.rowid=f.rowid "
                "WHERE clauses_fts MATCH ? "
                # 列顺序 (article_body, article_no, title)：标题权重最高
                "ORDER BY bm25(clauses_fts, 1.0, 3.0, 10.0) LIMIT ?"
            )
            rows = [dict(r) for r in c.execute(sql, (q, int(limit) * 2))]
            return _dedupe(rows, ("rfn", "article_no"), int(limit))
        sql = (
            "SELECT r.record_id, r.rfn, r.title, r.document_number, r.publish_date, "
            "r.timeliness_status, r.source, r.body_len, r.url, r.theme FROM records_fts f "
            "JOIN records r ON r.rowid=f.rowid WHERE records_fts MATCH ? "
            # 列顺序 (title, document_number, body_text)
            "ORDER BY bm25(records_fts, 10.0, 4.0, 1.0) LIMIT ?"
        )
        # 多取一倍再按 (标题, 文号) 去重：同一文件多源入库会重复出现
        rows = [dict(r) for r in c.execute(sql, (q, int(limit) * 2))]
        return _dedupe(rows, ("title", "document_number"), int(limit))


def search_external_like(text: str, limit: int = 20) -> list[dict]:
    """**短词兜底检索**（`LIKE`）：FTS5 trigram 的已知限制。

    为什么需要（2026-10-22 实测）：`records_fts` 用 **trigram** 分词，只索引 3 字窗口，
    因此 `MATCH '"保险"'`（2 字）**必然返回 0 行** —— 实测：
    `保险`→0、`银行`→0、`监管`→0，而 `保险公司`→3、`保险集团`→3。
    中文双字词恰恰是最常见的检索词 ⇒ 页面表现为"搜什么都是空"。

    兜底范围**只限结构化短字段**（title/document_number/rfn），不扫正文：
    正文在 1.6GB 级产物里，`LIKE '%x%'` 全表扫会让首屏卡死（这是**有意的取舍**，
    并在返回里用 `mode:"like"` 让调用方把它标出来，不伪装成全文检索）。
    多词按 **AND** 组合（同 `search_external` 的理由），并按标题长度升序
    —— 短标题通常就是"这个文件本身"，比长标题更可能是用户要找的。
    """
    where, args = _like_where(text, ("title", "document_number", "rfn"))
    if not where:
        return []
    with _conn("external") as c:
        rows = c.execute(
            "SELECT record_id, rfn, title, document_number, publish_date, "
            "timeliness_status, source, body_len, url, theme FROM records "
            f"WHERE {where} ORDER BY LENGTH(title) ASC, publish_date DESC LIMIT ?",
            [*args, int(limit) * 2])
        return _dedupe([dict(r) for r in rows], ("title", "document_number"),
                       int(limit))


def search_internal_like(text: str, limit: int = 20) -> list[dict]:
    """内部底座的短词兜底（同 `search_external_like` 的理由）。"""
    where, args = _like_where(text, ("title", "docno", "ipn"))
    if not where:
        return []
    with _conn("internal") as c:
        rows = c.execute(
            f"SELECT * FROM policies WHERE {where} "
            "ORDER BY LENGTH(title) ASC, docno LIMIT ?", [*args, int(limit) * 2])
        return _dedupe([dict(r) for r in rows], ("title", "docno"), int(limit))


def get_external(record_id: str) -> dict | None:
    with _conn("external") as c:
        row = c.execute("SELECT * FROM records WHERE record_id=?", (record_id,)).fetchone()
        return dict(row) if row else None


def query_internal(*, theme: str = "", ipn: str = "", limit: int = 200) -> list[dict]:
    where: list[str] = []
    args: list[object] = []   # SQL 参数可为 str/int（如 LIMIT ? 的 int）
    if theme:
        where.append("primary_theme=?")
        args.append(theme)
    if ipn:
        where.append("ipn=?")
        args.append(ipn)
    sql = "SELECT * FROM policies"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " LIMIT ?"
    args.append(int(limit))
    with _conn("internal") as c:
        out = []
        for r in c.execute(sql, args):
            d = dict(r)
            for k in ("secondary_themes", "associated_rfns"):
                try:
                    d[k] = json.loads(d.get(k) or "[]")
                except ValueError:
                    d[k] = []
            out.append(d)
        return out


# --------------------------------------------------------------------------- #
# 分页列表（确认台「政策法规」「公司制度」页签的**唯一取数入口**）
#
# 为什么要在这里做而不是在 web/ 里写 SQL：
#   ① 表结构与列语义归 `interfaces` 所有（web 一旦自己拼 SQL，schema 一改就静默错）；
#   ② 排序/筛选**必须白名单**（列名直接进 SQL ⇒ 不白名单就是注入面）；
#   ③ 前端要"总数 + 页码"，而 `query_external(limit=)` 只返回前 N 条、没有总数，
#      「下一页」无从判断 —— 分页是接口层职责，不是页面拼凑。
#
# 关于 `q` 的扫描范围（**有意的取舍**）：只 LIKE `title/document_number/rfn`
# 三个结构化短字段，**不扫 `body_text`** —— 外部底座正文合计 1.6 GB 量级，
# `LIKE '%x%'` 全表扫会让列表页首屏卡死；正文级检索走 `search_external`（FTS）。
# --------------------------------------------------------------------------- #

#: 外部列表允许的排序列（白名单；不在表内一律回退默认列）
EXTERNAL_SORT_COLS: tuple[str, ...] = (
    "publish_date", "title", "document_number", "source",
    "timeliness_status", "theme", "rfn", "body_len",
)
#: 内部列表允许的排序列（白名单）
INTERNAL_SORT_COLS: tuple[str, ...] = (
    "title", "docno", "primary_theme", "ipn", "eff_status", "article_count",
)

#: 筛选器里表示"该字段为空"的哨兵值（前端下拉用它筛"未定主题 / 未核验"）
EMPTY_FILTER = "__none__"

_MAX_PAGE_SIZE = 200


def _clamp(size: int, page: int) -> tuple[int, int]:
    size = max(1, min(int(size or 50), _MAX_PAGE_SIZE))
    page = max(1, int(page or 1))
    return size, page


def _page_result(items: list[dict], total: int, page: int, size: int,
                 sort: str, desc: bool) -> dict:
    pages = max(1, (total + size - 1) // size)
    return {"items": items, "total": int(total), "page": page, "size": size,
            "pages": pages, "sort": sort, "desc": bool(desc)}


def _like_tokens(q: str, cols: tuple[str, ...]) -> tuple[list[str], list[object]]:
    """多词 LIKE：**每个词都要命中**（AND），词内 `%`/`_` 转义。

    为什么按词 AND 而不是整串 LIKE（2026-10-23 实测）：用户输入"保险公司 营销"
    时，整串 `LIKE '%保险公司 营销%'` 命中率≈0（原文里没有这个连续子串）；
    而"每个词都在标题里出现"才符合人的预期。
    """
    toks = [t for t in str(q or "").replace("%", "").replace("_", "").split() if t]
    if not toks:
        return [], []
    where = []
    args: list[object] = []
    for t in toks:
        where.append("(" + " OR ".join(f"{c} LIKE ?" for c in cols) + ")")
        args.extend([f"%{t}%"] * len(cols))
    return where, args


def list_external_page(
    *,
    q: str = "",
    q_title: str = "",
    q_docno: str = "",
    q_rfn: str = "",
    source: str = "",
    theme: str = "",
    timeliness: str = "",
    sort: str = "publish_date",
    desc: bool = True,
    page: int = 1,
    size: int = 50,
) -> dict:
    """外部监管文件**分页列表**（五源统一；逐列筛选 + 排序 + 总数）。

    返回::

        {"items":[…], "total":N, "page":p, "size":s, "pages":M,
         "sort":"publish_date", "desc":True}

    筛选口径
    --------
    · `theme`/`timeliness` 传 `EMPTY_FILTER` 时筛"该字段为空"（未定主题 / 未核验）；
    · `q_title`/`q_docno`/`q_rfn` **逐列**模糊匹配（对应表头三列各自的输入框）；
    · `q` 是**跨列**关键词（任一词命中 title/docno/rfn 之一）。
    两者可叠加（AND）。
    """
    size, page = _clamp(size, page)
    sort = sort if sort in EXTERNAL_SORT_COLS else "publish_date"
    where: list[str] = []
    args: list[object] = []
    for col, val in (("source", source), ("theme", theme),
                     ("timeliness_status", timeliness)):
        if not val:
            continue
        if val == EMPTY_FILTER:
            where.append(f"COALESCE({col}, '') = ''")
        else:
            where.append(f"{col} = ?")
            args.append(val)
    for col, val in (("title", q_title), ("document_number", q_docno),
                     ("rfn", q_rfn)):
        if (val or "").strip():
            where.append(f"{col} LIKE ?")
            args.append(f"%{val.strip().replace('%', '').replace('_', '')}%")
    w_q, a_q = _like_tokens(q, ("title", "document_number", "rfn"))
    where.extend(w_q)
    args.extend(a_q)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    cols = ("record_id,rfn,title,document_number,issue_organ,publish_date,"
            "effective_date,timeliness_status,source,url,body_len,theme")
    with _conn("external") as c:
        total = c.execute(f"SELECT COUNT(*) FROM records{clause}", args).fetchone()[0]
        rows = c.execute(
            f"SELECT {cols} FROM records{clause} ORDER BY {sort} "
            f"{'DESC' if desc else 'ASC'}, rfn LIMIT ? OFFSET ?",
            [*args, size, (page - 1) * size])
        items = [dict(r) for r in rows]
    return _page_result(items, total, page, size, sort, desc)


def external_facets() -> dict:
    """外部列表的筛选候选值（带计数）：源 / 主题 / 时效。

    只做一次 `GROUP BY`（16607 行，毫秒级）；**不用 DISTINCT 全表**是为了让
    下拉框能显示"每类多少条"，用户据此判断该不该筛。
    """
    out: dict = {"sources": [], "themes": [], "timeliness": []}
    with _conn("external") as c:
        out["sources"] = [{"value": r[0] or "", "count": r[1]} for r in c.execute(
            "SELECT source, COUNT(*) FROM records GROUP BY source ORDER BY 2 DESC")]
        out["themes"] = [{"value": r[0] or "", "count": r[1]} for r in c.execute(
            "SELECT theme, COUNT(*) FROM records GROUP BY theme ORDER BY 2 DESC")]
        out["timeliness"] = [{"value": r[0] or "", "count": r[1]} for r in c.execute(
            "SELECT timeliness_status, COUNT(*) FROM records "
            "GROUP BY timeliness_status ORDER BY 2 DESC")]
    return out


def list_internal_page(
    *,
    q: str = "",
    q_title: str = "",
    q_docno: str = "",
    q_ipn: str = "",
    theme: str = "",
    status: str = "",
    sort: str = "title",
    desc: bool = False,
    page: int = 1,
    size: int = 50,
) -> dict:
    """内部制度**分页列表**（861 条；逐列筛选 + 排序 + 总数）。语义同 `list_external_page`。"""
    size, page = _clamp(size, page)
    sort = sort if sort in INTERNAL_SORT_COLS else "title"
    where: list[str] = []
    args: list[object] = []
    for col, val in (("primary_theme", theme), ("eff_status", status)):
        if not val:
            continue
        if val == EMPTY_FILTER:
            where.append(f"COALESCE({col}, '') = ''")
        else:
            where.append(f"{col} = ?")
            args.append(val)
    for col, val in (("title", q_title), ("docno", q_docno), ("ipn", q_ipn)):
        if (val or "").strip():
            where.append(f"{col} LIKE ?")
            args.append(f"%{val.strip().replace('%', '').replace('_', '')}%")
    w_q, a_q = _like_tokens(q, ("title", "docno", "ipn"))
    where.extend(w_q)
    args.extend(a_q)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    cols = ("ipn,title,docno,eff_status,primary_theme,secondary_themes,"
            "associated_rfns,file_type,extension,article_count")
    with _conn("internal") as c:
        total = c.execute(f"SELECT COUNT(*) FROM policies{clause}", args).fetchone()[0]
        rows = c.execute(
            f"SELECT {cols} FROM policies{clause} ORDER BY {sort} "
            f"{'DESC' if desc else 'ASC'}, ipn LIMIT ? OFFSET ?",
            [*args, size, (page - 1) * size])
        items = [dict(r) for r in rows]
    return _page_result(items, total, page, size, sort, desc)


def internal_facets() -> dict:
    """内部列表的筛选候选值（带计数）：主题 / 状态。"""
    out: dict = {"themes": [], "status": []}
    with _conn("internal") as c:
        out["themes"] = [{"value": r[0] or "", "count": r[1]} for r in c.execute(
            "SELECT primary_theme, COUNT(*) FROM policies "
            "GROUP BY primary_theme ORDER BY 2 DESC")]
        out["status"] = [{"value": r[0] or "", "count": r[1]} for r in c.execute(
            "SELECT eff_status, COUNT(*) FROM policies "
            "GROUP BY eff_status ORDER BY 2 DESC")]
    return out


def search_internal(text: str, limit: int = 20, kind: str = "policies") -> list[dict]:
    """内部全文检索（FTS5 trigram，**按相关性排序**）。kind=policies|clauses。

    与 `search_external` 同一处修复：原实现既无 `ORDER BY bm25(...)`（按 rowid 返回，
    等于"入库最早的 N 条"），也无列权重 ⇒ 标题命中的制度会被正文顺带提及的制度挤掉。
    """
    q = fts_query(text)
    if not q:
        return []
    with _conn("internal") as c:
        if kind == "clauses":
            sql = (
                "SELECT ic.ipn, ic.article_no, ic.article_body, p.title "
                "FROM iclauses_fts f JOIN internal_clauses ic ON ic.rowid=f.rowid "
                "LEFT JOIN policies p ON p.ipn=ic.ipn WHERE iclauses_fts MATCH ? "
                # 列顺序 (article_body, article_no)
                "ORDER BY bm25(iclauses_fts, 1.0, 3.0) LIMIT ?"
            )
            rows = [dict(r) for r in c.execute(sql, (q, int(limit) * 2))]
            return _dedupe(rows, ("ipn", "article_no"), int(limit))
        sql = (
            "SELECT p.ipn, p.title, p.docno, p.primary_theme FROM policies_fts f "
            "JOIN policies p ON p.rowid=f.rowid WHERE policies_fts MATCH ? "
            # 列顺序 (title, docno)
            "ORDER BY bm25(policies_fts, 10.0, 5.0) LIMIT ?"
        )
        rows = [dict(r) for r in c.execute(sql, (q, int(limit) * 2))]
        return _dedupe(rows, ("title", "docno"), int(limit))


def get_policy(ipn: str) -> dict | None:
    rows = query_internal(ipn=ipn, limit=1)
    return rows[0] if rows else None


_CHAIN_COLS = (
    "record_id,rfn,title,document_number,publish_date,effective_date,"
    "timeliness_status,source,body_len"
)


def version_chain(docno: str, limit: int = 50) -> list[dict]:
    """同文号（归一）多版本链（按发布日期升序）——F-K08 版本链视图的最小可用实现。

    阶段 3（2026-09-18）：**下推 SQL**。原实现 `SELECT` 全表 records（1.6 万行、含 body_text
    的宽表）后在 Python 端逐行 `norm_docno` 过滤（方案 §2.2 G7）。现依赖发布库新增的
    `docno_norm` 列 + 索引直接命中；**旧库（未重建）自动回退**旧路径，行为不变。
    """
    from std_lib.common_lib.norm import norm_docno

    nd = norm_docno(docno)
    if not nd:
        return []
    with _conn("external") as c:
        cols = {r[1] for r in c.execute("PRAGMA table_info(records)")}
        if "docno_norm" in cols:
            return [
                dict(r)
                for r in c.execute(
                    f"SELECT {_CHAIN_COLS} FROM records WHERE docno_norm=? "
                    "ORDER BY publish_date LIMIT ?",
                    (nd, int(limit)),
                )
            ]
        rows = [dict(r) for r in c.execute(f"SELECT {_CHAIN_COLS} FROM records")]
    out = [r for r in rows if norm_docno(r.get("document_number") or "") == nd]
    out.sort(key=lambda r: r.get("publish_date") or "")
    return out[:limit]


def view_active(limit: int = 200) -> list[dict]:
    """现行有效视图（timeliness_status=valid 的发布记录，轻量列）。"""
    return query_external(timeliness_status="valid", limit=limit)
