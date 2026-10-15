"""J12. 确认台（`web/`）纪律守护 —— 方案 §8 的机器守门。

为什么需要判据：确认台是本仓**第一个可从浏览器触达的写路径**（`POST
/api/worklist/resolve`）。若无门禁，"顺手在 web/ 里加个 SQL" 会绕过既有的
`interfaces/` 唯一取数口与 `governance_store` 表级唯一写方纪律 —— 这是**新增攻击面
必须配门禁**的典型。

判据（逐条可复跑）：
  J12.1 **只读取数**：不得出现裸 SQL 的写语句（INSERT/UPDATE/DELETE/DROP/REPLACE）。
  J12.2 **无自写引导**：不得 `sys.path.insert`（唯一引导点 = bootstrap，J11 之外的
        纪律延续）。
  J12.3 **静态资源闭包**：`web/assets/` 不得有外链（`http(s)://` 出现在 src/href 的
        脚本/样式里）——零 CDN 是可用性要求（出口网络有过滤）。
  J12.4 **命令已接线**：`serve` 必须在 `cli.COMMANDS` 里注册（薄壳纪律：确认台有 CLI 入口，
        不是"只能手敲 python -m"的野命令）。
  J12.5 **无密钥硬编码**：响应脱敏词表存在且响应过滤被调用（防 env 注入值随 payload 外泄）。
"""
from __future__ import annotations

import os
import re

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "web")
ASSETS = os.path.join(WEB_DIR, "assets")
SKIP_DIRS = {"__pycache__"}

_SQL_WRITE = re.compile(r"\b(INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM|"
                        r"DROP\s+TABLE|REPLACE\s+INTO)\b", re.I)
#: 外链仅允许出现在注释/文案里；脚本与样式标签上的 src/href 一律判违规
_EXTERNAL_ASSET = re.compile(r"(?:src|href)\s*=\s*[\"']https?://", re.I)


def _py_sources() -> list[str]:
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(WEB_DIR):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in sorted(filenames):
            if fn.endswith(".py"):
                out.append(os.path.join(dirpath, fn))
    return out


def _check_console_discipline(root: str) -> tuple[list[str], dict]:
    problems: list[str] = []
    detail: dict = {}
    if not os.path.isdir(WEB_DIR):
        return problems, detail  # web/ 未启用 ⇒ 本判据不适用（不判 FAIL）

    srcs = _py_sources()
    detail["py_files"] = len(srcs)

    # J12.1 / J12.2 静态扫描
    for p in srcs:
        rel = os.path.relpath(p, root).replace("\\", "/")
        text = open(p, encoding="utf-8", errors="replace").read()
        m = _SQL_WRITE.search(text)
        if m:
            problems.append(f"J12.1: {rel} 出现裸 SQL 写（{m.group(0)}）——写只走 governance_store")
        if "sys.path.insert" in text:
            problems.append(f"J12.2: {rel} 自写 sys.path.insert（唯一引导点 = bootstrap）")

    # J12.3 零 CDN
    for fn in sorted(os.listdir(ASSETS)) if os.path.isdir(ASSETS) else []:
        if not fn.endswith((".html", ".js", ".css")):
            continue
        text = open(os.path.join(ASSETS, fn), encoding="utf-8", errors="replace").read()
        m = _EXTERNAL_ASSET.search(text)
        if m:
            problems.append(
                f"J12.3: web/assets/{fn} 含外链资源（{m.group(0)[:40]}）——零 CDN 纪律"
                "（出口网络有过滤，外链即不可用风险）")

    # J12.4 serve 已注册
    try:
        import cli
        from commands import serve as _serve  # noqa: F401  薄壳可导入

        registered = "serve" in cli.COMMANDS
        detail["serve_registered"] = registered
        if not registered:
            problems.append("J12.4: serve 未在 cli.COMMANDS 注册（确认台缺 CLI 入口）")
    except Exception as e:  # noqa: BLE001
        problems.append(f"J12.4: serve 命令不可导入（{type(e).__name__}: {str(e)[:120]}）")

    # J12.5 脱敏存在且被调用
    try:
        from web.api import _SECRET_HINTS, handle_request  # noqa: F401

        detail["secret_hints"] = len(_SECRET_HINTS)
        text_api = open(os.path.join(WEB_DIR, "api.py"), encoding="utf-8",
                           errors="replace").read()
        if "_scrub(" not in text_api:
            problems.append("J12.5: web/api.py 未调用 _scrub（响应未脱敏）")
    except Exception as e:  # noqa: BLE001
        problems.append(f"J12.5: web.api 不可导入（{type(e).__name__}）")

    return problems, detail
