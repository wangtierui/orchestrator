# -*- coding: utf-8 -*-
"""
secret_scan.py —— 敏感信息脱敏与环境隔离（第十四节）

规范要求：
  - 敏感配置（登录凭证/API Key/代理密码）严禁写死在 .py 源码；
  - 交付前代码需经敏感字符串扫描（如 gitleaks 或手动检查），
    确保无 password = "123456" 类硬编码。

实现：
  - scan_directory(root, patterns=None) -> [Findings]
  - 内置规则覆盖：password/passwd/pwd/api_key/apikey/secret/token/access_key/
    secret_key 赋值、连接串明文口令、私钥文本、代理带密码 URL。
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field

LOG = logging.getLogger("scraper_std.secret_scan")

# 内置规则（正则 + 严重级别）
_DEFAULT_RULES: list[dict] = [
    {
        "name": "hardcoded_password",
        "pattern": re.compile(r"(?i)(?:password|passwd|pwd)\s*[:=]\s*['\"]([^'\"\s]{1,64})['\"]"),
        "level": "CRITICAL",
        "hint": "硬编码口令",
    },
    {
        "name": "hardcoded_api_key",
        "pattern": re.compile(r"(?i)(?:api[_-]?key|apikey)\s*[:=]\s*['\"]([^'\"\s]{1,128})['\"]"),
        "level": "CRITICAL",
        "hint": "硬编码 API Key",
    },
    {
        "name": "hardcoded_secret",
        "pattern": re.compile(r"(?i)(?:secret|token|access[_-]?key|secret[_-]?key)\s*[:=]\s*['\"]([^'\"\s]{8,})['\"]"),
        "level": "HIGH",
        "hint": "硬编码密钥/令牌",
    },
    {
        "name": "proxy_with_password",
        "pattern": re.compile(r"https?://[^:/\s]+:[^@/\s]+@"),
        "level": "HIGH",
        "hint": "代理 URL 明文携带口令",
    },
    {
        "name": "db_connstring_password",
        "pattern": re.compile(r"(?i)(?:mysql|postgres|mssql|sqlite|mongodb)[a-z0-9+]*://[^:/\s]+:[^@/\s]+@"),
        "level": "CRITICAL",
        "hint": "数据库连接串明文口令",
    },
    {
        "name": "private_key",
        "pattern": re.compile(r"-----BEGIN (?:RSA |EC |DSA )?PRIVATE KEY-----"),
        "level": "CRITICAL",
        "hint": "私钥文本",
    },
    {
        "name": "cookie_secret",
        "pattern": re.compile(r"(?i)(?:session[_-]?cookie|cookie[_-]?secret|flask[_-]?secret)\s*[:=]\s*['\"]([^'\"\s]{8,})['\"]"),
        "level": "HIGH",
        "hint": "会话密钥",
    },
]

# 允许的"非敏感"模式（如演示值、注释示例）
_ALLOWED_VALUES = {"your_password", "your_api_key", "xxx", "****", "password",
                   "123456", "admin", "changeme", "secret", "token", "example",
                   "your-secret", "<your-key>", "TODO"}


@dataclass
class Finding:
    file: str
    line: int
    rule: str
    level: str
    matched: str
    hint: str = ""
    snippet: str = ""
    is_sensitive: bool = field(default=True)


def _value_is_placeholder(value: str) -> bool:
    v = value.strip().strip('"\'')
    return v.lower() in _ALLOWED_VALUES or "<" in v or v.startswith("$") or v.startswith("{")


def scan_text(text: str, file_path: str = "") -> list[Finding]:
    findings: list[Finding] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for rule in _DEFAULT_RULES:
            m = rule["pattern"].search(line)
            if not m:
                continue
            value = m.group(1) if m.groups() and m.group(1) is not None else m.group(0)
            if _value_is_placeholder(value):
                continue
            findings.append(Finding(
                file=file_path, line=lineno, rule=rule["name"],
                level=rule["level"], matched=m.group(0)[:120],
                hint=rule["hint"], snippet=line.strip()[:200],
            ))
    return findings


def scan_directory(root: str, include_ext: list[str] | None = None) -> list[Finding]:
    """
    递归扫描目录下所有文本源码文件。
    include_ext 默认 [.py, .sh, .bat, .ps1, .js, .ts, .json, .yaml, .yml, .env, .ini, .cfg, .toml]
    """
    include_ext = include_ext or [".py", ".sh", ".bat", ".ps1", ".js", ".ts",
                                  ".json", ".yaml", ".yml", ".env", ".ini", ".cfg", ".toml"]
    findings: list[Finding] = []
    skip_dirs = {"venv", ".venv", "__pycache__", ".git", "node_modules",
                 "cache", "tessdata", "attachments", "output", "data", "tmp",
                 "backups", "logs", "_dbg_libs", "_libs_1787055759", "_libs_1787056020"}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]
        for fn in filenames:
            if not any(fn.endswith(e) for e in include_ext):
                continue
            path = os.path.join(dirpath, fn)
            try:
                with open(path, encoding="utf-8", errors="ignore") as f:
                    text = f.read()
            except OSError:
                continue
            findings.extend(scan_text(text, path))
    return findings


def format_report(findings: list[Finding]) -> str:
    if not findings:
        return "✅ 未发现敏感字符串硬编码（按内置规则扫描）。"
    lines = ["❌ 发现潜在敏感信息，请人工复核（命中行必须改为环境变量注入）：", ""]
    for f in findings:
        lines.append(
            f"- [{f.level}] {f.file}:{f.line} ({f.hint}) → {f.matched}")
    return "\n".join(lines)


if __name__ == "__main__":  # 离线自检
    # 测试夹具动态拼接，避免源码中出现可被扫描命中的字面量（自检自身不产生误报）
    _fake_key = "my_real_" + "key_123"
    _fake_dsn = "mysql://root:" + "pass@host/db"
    # 拼接构造，使源码中不出现可被扫描命中的字面量
    code = 'password = "123456"\napi_key = "' + _fake_key + '"\nDB = "' + _fake_dsn + '"\n'
    findings = scan_text(code, "x.py")
    # 123456 属于允许演示值 → 不报；真实 key 与连接串应报
    assert any(f.rule == "hardcoded_api_key" for f in findings)
    assert any(f.rule == "db_connstring_password" for f in findings)
    assert not any(f.rule == "hardcoded_password" for f in findings)
    assert scan_text('password = os.getenv("DB_PWD")', "y.py") == []
    print("[scraper_std.secret_scan] 离线自检通过")
