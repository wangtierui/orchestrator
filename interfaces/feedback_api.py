# -*- coding: utf-8 -*-
"""interfaces.feedback_api — 留言板的**业务规则层**（校验 / 限频 / 形状统一）。

为什么不把这些塞进 `web/api` 或 `governance_store`
--------------------------------------------------
- `governance_store` 只管"存取"，它不知道"什么算合法留言"，也不该懂 HTTP 上下文；
- `web/api` 是路由表，规则写进去会随路由膨胀，且**无法被 CLI/脚本复用**
  （将来 `cli.py feedback add` 要同一套校验）；
- 这里集中：**字段长度/受控值/域账号格式/写入频率**，并统一返回
  `{ok:True,fid}` 或 `{ok:False,reason}`（失败原因必须可读，不能只给 400）。

关于"域账号"的诚实说明（重要）
------------------------------
服务端**无法**从一次普通 HTTP 请求里得知访问者的 Windows 域账号 —— 那需要
Windows 集成认证（Negotiate/NTLM）或反向代理注入可信头，本仓未部署。

因此：
- 默认由**填写者自报**（`account_src="self"`），格式校验 + **IP/UA/服务端时间**交叉核对；
- 若将来接入反向代理/NTLM，请求会带 `X-Remote-User`（或 `Remote-User`），此时
  记为 `account_src="header"` 并在 UI 标注"由网关认证"。
- **绝不**把"自报"伪装成"已认证"：两种来源在库里可分辨，报告里也如实区分。
"""

from __future__ import annotations

import os
import re
import threading
import time

#: 字段上限（与前端 maxlength 一致；服务端**必须**再校验一次）
MAX_TITLE = 120
MAX_DETAIL = 4000
MAX_PAGE = 60
MAX_ACCOUNT = 64

#: 域账号允许：`user` / `DOMAIN\\user` / `user@corp.example`
_ACCOUNT_RE = re.compile(r"^(?:[A-Za-z0-9][A-Za-z0-9._\-]{0,31}\\)?"
                         r"[A-Za-z0-9][A-Za-z0-9._\-]{0,31}"
                         r"(?:@[A-Za-z0-9][A-Za-z0-9.\-]{1,62})?$")

#: 写入频率（内存计数，重启归零 —— 够用且零依赖；真要防刷应上网关）
_RATE_IP = (6, 600.0)       # 每 IP：10 分钟 6 条
_RATE_ACCOUNT = (12, 3600.0)  # 每账号：1 小时 12 条
_LOCK = threading.Lock()
_HITS: dict[str, list[float]] = {}


def _hit(key: str, limit: int, window: float) -> tuple[bool, int]:
    """记一次并判是否超限 → `(放行?, 建议等待秒数)`。"""
    now = time.time()
    with _LOCK:
        arr = [t for t in _HITS.get(key, []) if now - t < window]
        if len(arr) >= limit:
            _HITS[key] = arr
            return False, max(1, int(window - (now - arr[0])) + 1)
        arr.append(now)
        _HITS[key] = arr
        # 简单防膨胀：键太多时清掉已过期的（留言场景量级很小，无需更复杂）
        if len(_HITS) > 4096:
            for k in [k for k, v in _HITS.items() if not v or now - v[-1] > 3600]:
                _HITS.pop(k, None)
        return True, 0


def _clean(s: object, *, limit: int) -> str:
    """去控制字符 + 限长。**不做 HTML 转义**（前端 `esc()` 负责），只保证不夹带 `\\x00`。"""
    txt = str(s if s is not None else "")
    txt = "".join(ch for ch in txt if ch >= " " or ch == "\n")
    txt = txt.replace("\r\n", "\n").strip()
    return txt[:limit]


def host_user() -> str:
    """服务进程所在机器的登录账号（**交叉核对**用，不是访问者账号）。"""
    for key in ("USERNAME", "USER"):
        v = os.environ.get(key)
        if v:
            return v[:MAX_ACCOUNT]
    return ""


def header_user(headers: dict | None) -> str:
    """可信代理注入的访问者账号（未部署则为空）。

    只认 `X-Remote-User` / `Remote-User` 两个头；**不认** `X-Forwarded-User` 之类的
    自定义变体 —— 名字越少越不容易被误配成"谁都能设"的头。
    """
    h = {str(k).lower(): v for k, v in (headers or {}).items()}
    for k in ("x-remote-user", "remote-user"):
        v = (h.get(k) or "").strip()
        if v:
            return _clean(v, limit=MAX_ACCOUNT)
    return ""


def add(payload: dict, *, ip: str = "", user_agent: str = "",
        header_account: str = "", identity_user: str = "",
        identity_src: str = "none") -> dict:
    """校验 + 落库。返回 `{ok, fid}` 或 `{ok:False, reason}`（**不抛异常**）。

    身份来源优先级（2026-10-23，接入认证后）：
      ① `identity_user`（**已认证**：登录会话或可信代理）→ 直接采信，
         `account_src` 记为 `session` / `proxy`，**不再要求填写域账号**；
      ② `header_account`（未开会话但走代理）→ `header`；
      ③ 都没有 → 回落到**自报**（`self`）并做格式校验（保留兼容与离线场景）。
    """
    from std_lib.common_lib import governance_store as gs

    p = payload or {}
    title = _clean(p.get("title"), limit=MAX_TITLE)
    detail = _clean(p.get("detail"), limit=MAX_DETAIL)
    page = _clean(p.get("page"), limit=MAX_PAGE)
    kind = _clean(p.get("kind"), limit=16) or "bug"
    severity = _clean(p.get("severity"), limit=16) or "normal"
    account = _clean(p.get("account"), limit=MAX_ACCOUNT)

    if len(title) < 4:
        return {"ok": False, "reason": "标题至少 4 个字（便于归档与检索）"}
    if kind not in gs.feedback.KINDS:
        return {"ok": False, "reason": f"分类非法：{kind!r}（可选 {list(gs.feedback.KINDS)}）"}
    if severity not in gs.feedback.SEVERITIES:
        return {"ok": False,
                "reason": f"严重度非法：{severity!r}（可选 {list(gs.feedback.SEVERITIES)}）"}

    # 域账号：**已认证身份 > 可信头 > 自报**
    if identity_user:
        account = _clean(identity_user, limit=MAX_ACCOUNT)
        account_src = "proxy" if str(identity_src) == "proxy" else "session"
    elif header_account:
        account, account_src = _clean(header_account, limit=MAX_ACCOUNT), "header"
    else:
        account_src = "self"
        if not account:
            return {"ok": False, "reason": "请填写域账号（本机账号名即可，便于回访）"}
        if not _ACCOUNT_RE.match(account):
            return {"ok": False,
                    "reason": "域账号格式不合法（允许 `user` / `DOMAIN\\user` / "
                              "`user@corp.com`，仅字母数字与 . _ -）"}

    ok, wait = _hit(f"ip:{ip}", *_RATE_IP)
    if not ok:
        return {"ok": False, "reason": f"提交过于频繁（同一 IP 10 分钟内最多 "
                                       f"{_RATE_IP[0]} 条），请 {wait} 秒后再试"}
    ok, wait = _hit(f"acc:{account.lower()}", *_RATE_ACCOUNT)
    if not ok:
        return {"ok": False, "reason": f"该账号提交过于频繁（1 小时最多 "
                                       f"{_RATE_ACCOUNT[0]} 条），请 {wait} 秒后再试"}

    fid = gs.feedback_add(kind=kind, title=title, detail=detail, page=page,
                          severity=severity, account=account,
                          account_src=account_src, ip=ip,
                          user_agent=_clean(user_agent, limit=300),
                          host_user=host_user())
    if not fid:
        return {"ok": False, "reason": "留言未写入（治理库未启用或写入失败），请稍后重试"}
    return {"ok": True, "fid": fid, "account_src": account_src}


def items(*, status: str = "", kind: str = "", limit: int = 200) -> dict:
    """列出留言（附计数），形状与其它 provider 一致（`available` + `items`）。"""
    from std_lib.common_lib import governance_store as gs

    try:
        rows = gs.feedback_list(status=status, kind=kind, limit=limit)
        st = gs.feedback_stats()
    except Exception as e:  # noqa: BLE001  旁路：读失败降级，不白屏
        return {"available": False, "reason": f"{type(e).__name__}: {str(e)[:200]}",
                "items": [], "source": "governance_store.feedback_list"}
    KINDS = {"bug": "缺陷", "data": "数据质量", "feature": "功能缺失", "other": "其他"}
    SEV = {"low": "轻微", "normal": "一般", "high": "严重", "blocker": "阻塞"}
    STATUS = {"open": "待处置", "ack": "已受理", "closed": "已闭环"}
    for r in rows:
        r["kind_label"] = KINDS.get(r.get("kind", ""), r.get("kind", ""))
        r["severity_label"] = SEV.get(r.get("severity", ""), r.get("severity", ""))
        r["status_label"] = STATUS.get(r.get("status", ""), r.get("status", ""))
        r["account_src_label"] = {
            "proxy": "网关认证",        # 可信代理注入的 X-Remote-User
            "session": "登录身份",      # 口令/账号登录后的会话身份
            "header": "网关认证",       # 兼容旧数据（曾用 header 表示代理）
            "self": "填写者自报",       # 未接认证时的回落
        }.get(str(r.get("account_src") or ""), "填写者自报")
    return {"available": True, "items": rows, "total": len(rows),
            "stats": st, "kinds": list(gs.feedback.KINDS),
            "severities": list(gs.feedback.SEVERITIES),
            "source": "governance_store.feedback_list"}


def resolve(fid: str, *, handling: str = "", by: str = "",
            status: str = "closed") -> dict:
    """处置留言（仅本机可调，见 `web/policy.LOCAL_ONLY_ROUTES`）。"""
    from std_lib.common_lib import governance_store as gs

    fid = _clean(fid, limit=40)
    handling = _clean(handling, limit=1000)
    by = _clean(by, limit=MAX_ACCOUNT) or host_user() or "local"
    if not fid:
        return {"ok": False, "reason": "缺 fid"}
    if status not in ("ack", "closed"):
        return {"ok": False, "reason": f"status 非法：{status!r}（ack|closed）"}
    if status == "closed" and len(handling) < 4:
        return {"ok": False, "reason": "闭环必须填写处置说明（审计要求，至少 4 个字）"}
    ok = gs.feedback_resolve(fid, status=status, by=by, handling=handling)
    return {"ok": ok, "fid": fid, "status": status} if ok else {
        "ok": False, "reason": "未找到该留言或写入失败"}
