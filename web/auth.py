# -*- coding: utf-8 -*-
"""web.auth — 确认台的**认证与会话**（2026-10-23，第一百一十二批）。

为什么必须做
============
第一百一十一批把确认台对公司内网开放（只读 + 分级），但"分级"只回答
「**哪些动作**能做」，没回答「**谁在看**」。任何能连上端口的人都能读全部数据
（16607 份监管文件、861 份制度、留言板），这在合规项目里不可接受。

三种模式（`--auth`）
===================
| 模式 | 适用 | 凭据 | 说明 |
|---|---|---|---|
| `off` | 仅回环 | 无 | **默认**；`--lan` 时自动升级为 `token`（不允许"对外且无认证"） |
| `token` | 内网共享口令 | 启动时生成的一次性口令 | 一次输入 → 换 HMAC 会话 cookie；口令落 `data/state/console_token.txt` |
| `basic` | 每人一账号 | `config/console_users.yaml`（PBKDF2-SHA256） | 支持"审计到人"；**明文 HTTP 下会暴露口令**，务必配 `--tls-*` 或仅在可信网段用 |

另支持**可信代理认证**（`--trust-proxy` + `--trusted-proxies`）：
请求来自受信代理且带 `X-Remote-User` 时，直接视为已认证（`src=proxy`）。
这是"集成认证"的落点 —— 也是留言板**去掉自报域账号**的前提（见 `T5`）。

安全设计（逐条都有理由）
------------------------
1. **会话是无状态 HMAC 签名 cookie**（`wb_sess`）：服务重启不掉线，也不需要
   在内存里存 session 表（多线程/多进程都安全）。签名密钥落 `data/state/console_secret.key`。
2. **CSRF 双重提交**：登录时同时下发 `wb_csrf`，所有 **POST** 必须带
   `X-CSRF` 头与之相等 —— 光靠 `SameSite=Strict` 挡不住"同站子域"场景。
3. **登录失败限频**：同 IP 10 分钟 10 次，超过直接 429（防口令爆破）。
4. **常数时间比较**（`hmac.compare_digest`）：口令/签名比较不得用 `==`（时序侧信道）。
5. **口令绝不明文落盘**：`basic` 模式只存 `pbkdf2_sha256$<iter>$<salt>$<hash>`。
6. **审计到人**：登录成功/失败/登出都写 `audit_log`（旁路设施，失败不影响请求）。

非目标（明确不做）
------------------
- 不做注册/找回密码（内网工具，账号由管理员 `python -m web.auth adduser` 维护）；
- 不自研 TLS（用标准库 `ssl` 包装，见 `web/server.py --tls-cert/--tls-key`）；
- 不把认证信息放进 URL/query（会进浏览器历史与代理日志）。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time

import paths
from config.exitcodes import ExitCode

#: 会话有效期（秒）：8 小时（一个工作日，过长等于长期免密）
SESSION_TTL_S = 8 * 3600
#: 登录失败限频：窗口 600s / 上限 10 次（按 IP）
FAIL_WINDOW_S = 600.0
FAIL_MAX = 10

#: 每人一枚访问令牌（YAML：users: {name: {hash, note, created_at}}）。
#: 存**哈希**不存明文：文件在 data/state/ 下、可能被打包带走，明文即泄露。
TOKENS_PATH = os.path.join(paths.DATA_DIR, "state", "console_tokens.yaml")

COOKIE_SESS = "wb_sess"
COOKIE_CSRF = "wb_csrf"

STATE_DIR = os.path.join(paths.DATA_DIR, "state")
SECRET_PATH = os.path.join(STATE_DIR, "console_secret.key")
TOKEN_PATH = os.path.join(STATE_DIR, "console_token.txt")
USERS_PATH = os.path.join(paths.ROOT, "config", "console_users.yaml")

_LOCK = threading.Lock()
_FAILS: dict[str, list[float]] = {}


# --------------------------------------------------------------------------- #
# 密钥与口令
# --------------------------------------------------------------------------- #
def _ensure_dir() -> None:
    os.makedirs(STATE_DIR, exist_ok=True)


def secret() -> bytes:
    """会话签名密钥（首次自动生成，32 字节随机）。

    为什么落文件而不是进程内随机：**服务由计划任务每次登录拉起**，
    进程内随机会让所有会话在重启后失效（同事正在看的页面突然 401）。
    """
    _ensure_dir()
    try:
        with open(SECRET_PATH, "rb") as fh:
            b = fh.read().strip()
        if len(b) >= 32:
            return b
    except OSError:
        pass
    b = secrets.token_hex(32).encode()
    tmp = SECRET_PATH + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(b)
    os.replace(tmp, SECRET_PATH)
    return b


def access_token(rotate: bool = False) -> str:
    """共享口令（`token` 模式）。存在即复用；`rotate=True` 时重新生成。

    口令是**人可抄写**的短串（4 组 4 位），不是 UUID —— 它要贴在群里/白板上，
    太长会被抄错。熵仍有 16^8 ≈ 4.3e9，配合失败限频足够内网使用。
    """
    _ensure_dir()
    if not rotate:
        try:
            with open(TOKEN_PATH, encoding="utf-8") as fh:
                t = fh.read().strip()
            if t:
                return t
        except OSError:
            pass
    t = "-".join(secrets.token_hex(2).upper() for _ in range(4))
    tmp = TOKEN_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(t)
    os.replace(tmp, TOKEN_PATH)
    return t


def _token_hash(token: str) -> str:
    """令牌哈希（sha256 足够：令牌本身 16 位随机数，不需要慢哈希）。"""
    import hashlib

    return hashlib.sha256((token or "").strip().upper().encode("utf-8")).hexdigest()


def load_user_tokens() -> dict[str, dict]:
    """读令牌注册表（文件不存在/损坏 → 空表，不抛）。"""
    if not os.path.exists(TOKENS_PATH):
        return {}
    try:
        import yaml

        with open(TOKENS_PATH, encoding="utf-8") as fh:
            d = yaml.safe_load(fh) or {}
        users = d.get("users") or {}
        return {str(k): dict(v or {}) for k, v in users.items()} if isinstance(users, dict) else {}
    except Exception:  # noqa: BLE001  注册表坏了不该让服务起不来
        return {}


def save_user_tokens(users: dict[str, dict]) -> None:
    """写令牌注册表（原子替换；仅存哈希）。"""
    import yaml

    _ensure_dir()
    tmp = TOKENS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        yaml.safe_dump({"users": users}, fh, allow_unicode=True, sort_keys=True)
    os.replace(tmp, TOKENS_PATH)


def issue_user_token(user: str, *, note: str = "") -> str:
    """为 `user` 颁发（或轮换）令牌，**返回明文一次**（之后只有哈希）。"""
    import datetime
    import secrets

    name = (user or "").strip()
    if not name or len(name) > 64:
        raise ValueError("用户名非法（1-64 字符）")
    token = "-".join(secrets.token_hex(2).upper() for _ in range(4))
    users = load_user_tokens()
    users[name] = {
        "hash": _token_hash(token),
        "note": (note or "").strip(),
        "created_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    save_user_tokens(users)
    return token


def revoke_user_token(user: str) -> bool:
    users = load_user_tokens()
    if name_ok := (user or "").strip():
        if name_ok in users:
            del users[name_ok]
            save_user_tokens(users)
            return True
    return False


def identify_token(token: str) -> tuple[str, str]:
    """令牌 → `(user, level)`。

    `level`：`user`（个人令牌，可审计到人）/ `shared`（共享口令）/ `""`（不匹配）。
    """
    tk = (token or "").strip()
    if not tk:
        return "", ""
    h = _token_hash(tk)
    for name, rec in load_user_tokens().items():
        if hmac_compare(str(rec.get("hash") or ""), h):
            return name, "user"
    if hmac_compare(tk, access_token()):
        return "内网共享口令用户", "shared"
    return "", ""


def hmac_compare(a: str, b: str) -> bool:
    """常数时间比较（口令与令牌统一走这里，避免两处实现时效/长度泄露）。"""
    import hmac

    return hmac.compare_digest((a or "").encode("utf-8"), (b or "").encode("utf-8"))


def hash_password(pw: str, *, iterations: int = 260000) -> str:
    """PBKDF2-SHA256 → `pbkdf2_sha256$<iter>$<salt_hex>$<hash_hex>`。"""
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${dk.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    """常数时间校验。格式非法一律 False（不抛：坏配置不该让服务 500）。"""
    try:
        algo, it_s, salt_h, hash_h = str(stored).split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", str(pw).encode("utf-8"),
                                 bytes.fromhex(salt_h), int(it_s))
        return hmac.compare_digest(dk.hex(), hash_h)
    except (ValueError, TypeError):
        return False


def load_users() -> dict[str, dict]:
    """读 `config/console_users.yaml` → `{user: {"password": "...", "role": "..."}}`。

    文件缺失/未装 yaml ⇒ 空字典（`basic` 模式下等同于"没有人能登录"，
    比"放行所有人"安全）。**每次读取**（不缓存）：改账号不必重启服务。
    """
    try:
        import yaml
    except Exception:  # noqa: BLE001
        return {}
    try:
        with open(USERS_PATH, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except OSError:
        return {}
    users = data.get("users") if isinstance(data, dict) else None
    return users if isinstance(users, dict) else {}


# --------------------------------------------------------------------------- #
# 会话 cookie
# --------------------------------------------------------------------------- #
def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def issue_session(user: str, src: str = "session", *,
                  level: str = "user") -> tuple[str, str, int]:
    """签发会话 → `(cookie_value, csrf_token, expires_ts)`。

    `level`（审计级别）写进**签名载荷**：`user` = 能定位到人；`shared` = 共享口令。
    放载荷里而不是查表：会话是无状态的，服务重启/多进程下都能还原这个事实。
    """
    exp = int(time.time()) + SESSION_TTL_S
    nonce = secrets.token_hex(8)
    payload = json.dumps({"u": user, "s": src, "e": exp, "n": nonce,
                          "l": level}, separators=(",", ":"), ensure_ascii=False)
    raw = _b64e(payload.encode("utf-8"))
    sig = hmac.new(secret(), raw.encode(), hashlib.sha256).hexdigest()[:32]
    csrf = secrets.token_hex(16)
    return f"{raw}.{sig}", csrf, exp


def parse_session(cookie_value: str) -> dict | None:
    """校验签名与有效期 → `{"user","src","exp"}`；失败返回 None。"""
    if not cookie_value or "." not in cookie_value:
        return None
    raw, _, sig = cookie_value.rpartition(".")
    want = hmac.new(secret(), raw.encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(sig, want):
        return None
    try:
        d = json.loads(_b64d(raw).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if int(d.get("e") or 0) < int(time.time()):
        return None
    return {"user": str(d.get("u") or ""), "src": str(d.get("s") or "session"),
            "exp": int(d.get("e") or 0),
            # 审计级别（2026-10-26）：`user` 可定位到人 / `shared` 共享口令。
            # 旧会话没有 `l` 字段 —— 按 `user` 处理（那一版只有 basic/代理会签发会话）。
            "level": str(d.get("l") or "user")}


# --------------------------------------------------------------------------- #
# 失败限频
# --------------------------------------------------------------------------- #
def note_failure(ip: str) -> tuple[bool, int]:
    """记一次失败 → `(是否仍允许重试, 需等待秒数)`。"""
    now = time.time()
    with _LOCK:
        arr = [t for t in _FAILS.get(ip, []) if now - t < FAIL_WINDOW_S]
        arr.append(now)
        _FAILS[ip] = arr
        if len(_FAILS) > 4096:
            for k in [k for k, v in _FAILS.items() if not v or now - v[-1] > FAIL_WINDOW_S]:
                _FAILS.pop(k, None)
        if len(arr) > FAIL_MAX:
            return False, max(1, int(FAIL_WINDOW_S - (now - arr[0])) + 1)
        return True, 0


def clear_failures(ip: str) -> None:
    with _LOCK:
        _FAILS.pop(ip, None)


def throttled(ip: str) -> int:
    """该 IP 是否已被限频 → 需等待秒数（0 = 未限频）。"""
    now = time.time()
    with _LOCK:
        arr = [t for t in _FAILS.get(ip, []) if now - t < FAIL_WINDOW_S]
        _FAILS[ip] = arr
        if len(arr) > FAIL_MAX:
            return max(1, int(FAIL_WINDOW_S - (now - arr[0])) + 1)
    return 0


# --------------------------------------------------------------------------- #
# 统一入口
# --------------------------------------------------------------------------- #
def local_user() -> str:
    """本机操作系统登录账号（回环自动认证的身份来源）。

    为什么这是**可信**身份而不是"自报"：回环请求只能由本机的进程发起，而本机的
    用户边界就是 OS 账号边界 —— 能连 127.0.0.1 的人本来就能读同一个数据目录。
    因此回环免口令不降低安全强度，只省掉一次粘贴口令（原先内网开放后连本机
    都要登录，用户实际报障："两个地址都显示未认证，什么都看不到"）。
    """
    for key in ("USERNAME", "USER", "LOGNAME"):
        v = os.environ.get(key)
        if v:
            return v.strip()[:64]
    return "local"


def _is_loopback(ip: str) -> bool:
    try:
        import ipaddress

        s = (ip or "").strip()
        if s.lower().startswith("::ffff:"):
            s = s[7:]
        return ipaddress.ip_address(s).is_loopback
    except ValueError:
        return False


def _looks_proxied(h: dict) -> bool:
    """请求是否**看起来经过代理**（用于阻止"同机反向代理"白拿回环身份）。

    只判"这些头是否存在"，不采信其内容 —— 内容一律不信（可伪造），
    存在的意义只是"这个连接不是浏览器直连"。
    """
    return any(k in h for k in ("x-forwarded-for", "x-real-ip", "forwarded"))


def identity(*, cookies: dict, client_ip: str, headers: dict,
             mode: str, trusted_proxies: tuple[str, ...] = (),
             lan: bool = False) -> dict:
    """解析请求身份 → `{"user","src","authenticated","mode"}`。

    `src` 取值：`proxy`（可信代理注入）/ `session`（口令或账号登录）/
    `local`（回环自动身份）/ `none`。

    优先级：可信代理 > 会话 cookie > **回环自动身份**（仅 `mode=auto`）。

    ⚠️ 回环自动身份有**一个必须防的坑**：若同机跑反向代理并转发到 127.0.0.1，
    外部用户也会被看成回环。故要求"请求里没有任何代理痕迹头"才自动认证；
    真要用同机代理，请显式 `--auth token|basic`（README §6.9）。
    """
    h = {str(k).lower(): v for k, v in (headers or {}).items()}
    # ① 可信代理：只有**来自受信 IP** 时才认 `X-Remote-User`
    if trusted_proxies and client_ip in trusted_proxies:
        u = (h.get("x-remote-user") or h.get("remote-user") or "").strip()
        if u:
            return {"user": u[:64], "src": "proxy", "authenticated": True,
                    "mode": mode, "level": "user"}
    # ② 会话 cookie
    ses = parse_session((cookies or {}).get(COOKIE_SESS, ""))
    if ses:
        return {"user": ses["user"], "src": ses["src"], "authenticated": True,
                "mode": mode, "level": ses.get("level", "user")}
    # ③ 回环自动身份（mode=auto）：本机 == 已用 OS 账号登录
    if mode == "auto" and _is_loopback(client_ip) and not _looks_proxied(h):
        return {"user": local_user(), "src": "local", "authenticated": True,
                "mode": mode, "level": "user"}
    return {"user": "", "src": "none", "authenticated": False, "mode": mode,
            "level": ""}


def cookie_header(name: str, value: str, *, max_age: int,
                  secure: bool = False, http_only: bool = True) -> str:
    """构造 `Set-Cookie`（默认 **HttpOnly + SameSite=Strict**）。

    ⚠️ CSRF cookie **必须** `http_only=False`：double-submit 的整套机制依赖
    「页面脚本能读到该 cookie 并把它放进 `X-CSRF` 头」。设成 HttpOnly 会让前端
    永远拿不到 token ⇒ 所有写请求 403（实测踩到，表现为"登录成功但一提交就 403"）。
    会话 cookie 则相反：**只能** HttpOnly（JS 读不到才不会被 XSS 偷走）。
    """
    parts = [f"{name}={value}", "Path=/", f"Max-Age={int(max_age)}",
             "SameSite=Strict"]
    if http_only:
        parts.append("HttpOnly")
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


def parse_cookies(raw: str) -> dict:
    out: dict[str, str] = {}
    for part in (raw or "").split(";"):
        if "=" in part:
            k, _, v = part.partition("=")
            out[k.strip()] = v.strip()
    return out


def audit(event: str, *, user: str = "", ip: str = "", note: str = "") -> None:
    """写治理库审计（旁路设施：任何失败都不得影响请求）。"""
    try:
        from std_lib.common_lib import governance_store as gs

        gs.log_audit(event, "console_auth", target_key=user or ip,
                     field="identity", old="", new=f"{user}@{ip}",
                     basis=note[:200], actor="web.auth")
    except Exception:  # noqa: BLE001
        pass


def main(argv: list[str] | None = None) -> int:
    """`python -m web.auth {token,adduser,list,revoke}`。

    `token` 子命令（2026-10-26 扩）：
        token                    # 显示/生成**共享**口令
        token --rotate           # 轮换共享口令
        token --for zhangsan     # 为某人颁发**个人令牌**（可审计到人）
        token --list             # 列出已颁发令牌（只有用户名/时间，没有明文）
        token --revoke zhangsan  # 吊销某人令牌
    """
    import argparse

    ap = argparse.ArgumentParser(prog="web.auth", description="确认台凭据管理")
    sub = ap.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("token", help="共享口令 / 个人令牌")
    t.add_argument("--rotate", action="store_true", help="轮换共享口令")
    t.add_argument("--for", dest="for_user", default="", help="为某人颁发个人令牌")
    t.add_argument("--list", action="store_true", help="列出已颁发的个人令牌")
    t.add_argument("--revoke", default="", help="吊销某人的个人令牌")
    t.add_argument("--note", default="", help="备注（部门/用途）")

    u = sub.add_parser("adduser", help="basic 模式：新增账号")
    u.add_argument("--user", required=True)
    u.add_argument("--password", default="", help="留空则交互输入")

    a = ap.parse_args(argv)
    if a.cmd == "token":
        if a.list:
            users = load_user_tokens()
            if not users:
                print("（尚未颁发任何个人令牌；用 token --for <user> 颁发）")
                return ExitCode.OK
            print(f"{'用户名':20s} {'颁发时间':20s} 备注")
            for name in sorted(users):
                rec = users[name]
                print(f"{name:20s} {str(rec.get('created_at') or ''):20s} "
                      f"{rec.get('note') or ''}")
            return ExitCode.OK
        if a.revoke:
            ok = revoke_user_token(a.revoke)
            print("已吊销" if ok else "未找到该用户", a.revoke)
            return ExitCode.OK if ok else ExitCode.FAIL
        if a.for_user:
            token = issue_user_token(a.for_user, note=a.note)
            print(f"已为 {a.for_user} 颁发个人令牌（仅显示一次，请转交本人）：")
            print(f"  {token}")
            print(f"（注册表 {TOKENS_PATH}，只存哈希；吊销：python -m web.auth "
                  f"token --revoke {a.for_user}）")
            audit("token_issue", user=a.for_user, note=a.note or "个人令牌")
            return ExitCode.OK
        print(f"共享口令：{access_token(rotate=a.rotate)}")
        print(f"（文件 {TOKEN_PATH}；--rotate 可更换）")
        print("提示：共享口令**无法审计到人**；需要到人请用 "
              "`token --for <user>` 颁发个人令牌。")
        return ExitCode.OK
    # 走到这里 = 没有匹配到任何子命令分支（argparse 之外是手工分发）。
    # 显式返回失败码：签名是 `-> int`，落到函数末尾会返回 None（mypy: Missing return）。
    return ExitCode.FAIL

if __name__ == "__main__":
    raise SystemExit(main())
