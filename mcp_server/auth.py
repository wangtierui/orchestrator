# -*- coding: utf-8 -*-
"""mcp_server.auth — token 读取/校验（§4.4，复用本仓 `.pkulaw_token` 既有模式）。

纪律：
  · token **只从文件读**（默认 `<ROOT>/.mcp_token`，权限 0600），可用
    `REG_ORCH_MCP_TOKEN_FILE` 覆盖路径——**禁止入库、禁止硬编码**（`gate_secret_scan` 守护）；
  · 无 token 文件 → `load_token()` 返回 None，传输层据此**拒绝启动并给出可执行指引**
    （不是「无认证裸奔」，也不是晦涩异常）；
  · 校验用 `secrets.compare_digest`（常数时间，避免计时侧信道）。
"""
from __future__ import annotations

import os
import secrets
import stat

DEFAULT_NAME = ".mcp_token"
ENV_PATH = "REG_ORCH_MCP_TOKEN_FILE"


def token_path() -> str:
    """token 文件路径（env 覆盖 → 默认仓根 `.mcp_token`）。"""
    import paths

    return os.environ.get(ENV_PATH) or os.path.join(paths.ROOT, DEFAULT_NAME)


def load_token() -> str:
    """读 token（不存在/空 → 空串）。"""
    p = token_path()
    try:
        with open(p, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def verify(supplied: str) -> bool:
    """常数时间校验；空 token 视为不通过。"""
    want = load_token()
    if not want or not supplied:
        return False
    return secrets.compare_digest(want, supplied.strip())


def init_token(value: str = "") -> str:
    """生成/写入 token（`python -m mcp_server.auth --init`）；权限收紧到 0600。"""
    import pathlib

    p = pathlib.Path(token_path())
    tok = value.strip() or secrets.token_urlsafe(32)
    p.write_text(tok + "\n", encoding="utf-8")
    try:
        os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)  # 0600（Windows 上为 no-op 尽力）
    except OSError:
        pass  # 平台不支持 chmod 不阻断（仍提示用户文件位置）
    return tok


if __name__ == "__main__":
    import sys

    if "--init" in sys.argv:
        tok = init_token()
        print(f"[mcp] token 已写入 {token_path()}（权限 0600）：{tok[:8]}…")
    else:
        print(f"[mcp] token 路径：{token_path()}；已存在：{bool(load_token())}")
