# -*- coding: utf-8 -*-
"""std_lib.common_lib.governance_store.feedback — 确认台留言板（第一百一十一批）。

为什么要这张表
--------------
确认台对公司内网开放**只读**之后，"同事用出问题"必须有个**落地入口**：
- 没有它：问题只存在于聊天/口头 ⇒ 无法统计、无法排优先级、无法证明"已修"；
- 有了它：每条问题带**页面/分类/严重度/记录者/服务端时间**，处置后能回归验证，
  进而把"数据质量与功能缺失"变成可度量的改进输入（见 §T5 方案文档）。

四条不可妥协的纪律
------------------
1. **只追加**：处置只改 `status/handled_*`，**不删行** —— 删了就失去"问题曾经存在"的证据。
2. **来源不可混淆**：`account` 是**填写者自报**（`account_src='self'`）；将来接可信代理/
   Windows 集成认证后由可信头注入（`account_src='header'`）。两种来源必须可分辨。
3. **不可伪造的字段由服务端写**：`ip`/`user_agent`/`host_user`/`created_at` 全部服务端取，
   前端传什么都不采信（否则"谁在什么时候报的"就不可审计）。
4. **不阻塞写入**（旁路纪律）：治理库未启用/写入异常 ⇒ 返回 `None`，调用方据实提示失败，
   但**绝不因此影响确认台其他功能**。

写入频率限制放在 `interfaces/feedback_api`（业务规则），本模块只负责存取。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from ._db import LOG, _ensure, connect, enabled, now_iso

#: 分类（受控值；前端下拉与此一致）
KINDS: tuple[str, ...] = ("bug", "data", "feature", "other")
#: 严重度（受控值）
SEVERITIES: tuple[str, ...] = ("low", "normal", "high", "blocker")
#: 处置状态（受控值）
STATUSES: tuple[str, ...] = ("open", "ack", "closed")


def _row(r: sqlite3.Row) -> dict:
    return dict(r)


def new_fid(now: datetime | None = None) -> str:
    """生成可读且基本不碰撞的编号：`FB-<yyyymmddHHMMSS>-<4位序号>`。

    为什么不用自增 id：留言会被人**口头引用**（"FB-20261023… 那个"），
    带时间戳可读性远好于纯数字；碰撞概率由"同一秒内序号"兜住（见 `feedback_add`）。
    """
    import secrets

    ts = (now or datetime.now()).strftime("%Y%m%d%H%M%S")
    return f"FB-{ts}-{secrets.token_hex(2).upper()}"


def feedback_add(
    *,
    kind: str,
    title: str,
    detail: str = "",
    page: str = "",
    severity: str = "normal",
    account: str = "",
    account_src: str = "self",
    ip: str = "",
    user_agent: str = "",
    host_user: str = "",
    fid: str = "",
) -> str | None:
    """追加一条留言 → 返回 `fid`；治理库未启用/异常 → `None`（调用方不得中断）。

    只做**存储**：参数合法性与频率限制在 `interfaces.feedback_api`（业务规则集中一处）。
    """
    if not enabled():
        return None
    if not (title or "").strip():
        return None
    _fid = fid or new_fid()
    conn = _ensure()
    try:
        conn.execute(
            "INSERT INTO feedback(fid,kind,severity,page,title,detail,account,"
            "account_src,ip,user_agent,host_user,created_at,status) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'open')",
            (_fid, kind, severity, page, title.strip(), detail, account,
             account_src, ip, user_agent, host_user, now_iso()),
        )
        conn.commit()
        LOG.info("[feedback] 收到留言 %s（%s/%s）", _fid, kind, severity)
        return _fid
    except sqlite3.Error as e:
        LOG.warning("[feedback] WARN 写入失败（不影响确认台其他功能）：%s", e)
        return None


def feedback_list(*, status: str = "", kind: str = "", limit: int = 200) -> list[dict]:
    """按时间倒序列出留言（`status`/`kind` 为空 = 不过滤）。"""
    if not enabled():
        return []
    where: list[str] = []
    args: list[object] = []
    if status:
        where.append("status = ?")
        args.append(status)
    if kind:
        where.append("kind = ?")
        args.append(kind)
    sql = "SELECT * FROM feedback"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC, fid DESC LIMIT ?"
    args.append(max(1, int(limit)))
    with connect(readonly=True) as c:
        return [_row(r) for r in c.execute(sql, args)]


def feedback_stats() -> dict:
    """计数（总数 / 按状态 / 按分类 / 按严重度）——供页签头部与方案度量。"""
    if not enabled():
        return {"available": False}
    with connect(readonly=True) as c:
        total = c.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
        by_status = {r["status"]: r["n"] for r in c.execute(
            "SELECT status, COUNT(*) n FROM feedback GROUP BY status")}
        by_kind = {r["kind"]: r["n"] for r in c.execute(
            "SELECT kind, COUNT(*) n FROM feedback GROUP BY kind")}
        by_sev = {r["severity"]: r["n"] for r in c.execute(
            "SELECT severity, COUNT(*) n FROM feedback GROUP BY severity")}
        first = c.execute("SELECT MIN(created_at) FROM feedback").fetchone()[0] or ""
    return {"available": True, "total": total, "by_status": by_status,
            "by_kind": by_kind, "by_severity": by_sev, "first_at": first}


def feedback_resolve(fid: str, *, status: str = "closed", by: str = "",
                     handling: str = "") -> bool:
    """处置一条留言（改状态 + 记录处置人与说明）。**不删行**。

    `status` 只允许 `ack`（已受理）/`closed`（已闭环）——把"关闭"和"受理"分开，
    是因为很多问题的真实状态是"已知、在排期"，混成一个值会让看板失真。
    """
    if not enabled() or not fid:
        return False
    if status not in ("ack", "closed"):
        return False
    conn = _ensure()
    try:
        cur = conn.execute(
            "UPDATE feedback SET status=?, handled_by=?, handled_at=?, handling=? "
            "WHERE fid=?",
            (status, by, now_iso(), handling, fid),
        )
        conn.commit()
        return bool(cur.rowcount)
    except sqlite3.Error as e:
        LOG.warning("[feedback] WARN 处置失败：%s", e)
        return False
