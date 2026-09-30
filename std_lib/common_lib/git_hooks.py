# -*- coding: utf-8 -*-
"""std_lib.common_lib.git_hooks — **版本化 git 钩子**的就绪度核验（N-164，2026-09-30）

问题背景
--------
本仓约定"每次提交后自动同步到远程"（用户约定 2026-09-08）。但 `.git/hooks/**` **不入库**
（git 的安全设计：钩子可执行任意代码，不能随 clone 自动生效），于是出现两类后果：

  · **易漏**：每台机器 / 每次重新克隆都要手工复制钩子 → 漏了则"自动提交"**根本不跑**；
  · **易漂移**：钩子升级后各副本版本不一致（仓库里改了，本机仍跑旧的）。

实测事故（N-163）：钩子用 `>/dev/null 2>&1` 吞掉推送失败 → **第二十六批起 8 批提交从未
推送成功**，直至人工排查才发现。

本仓解法（**版本化 + 工具化 + 机器核验**）
------------------------------------------
  · 钩子**入库**于 `.githooks/`（内容随 `git pull` 自动更新）；
  · `core.hooksPath=.githooks`（**一次性** config，由 `tools/install_git_hooks.py` 设置）；
  · **本模块**提供 `check()`（只读核验），供 CLI 与 `tools/audit_health.py` 复用 ——
    使"钩子没装 / 装了但 CRLF / 丢了自动推送块"**可被机器发现**，而非靠人记得。

为什么放在 `std_lib/common_lib`（而非 tools/ 内）
--------------------------------------------------
因为它有**两个消费方**（CLI 工具 + 健康审计）。若把实现留在 `tools/install_git_hooks.py`
再由审计 `from tools.install_git_hooks import check` 导入，mypy 会把同一文件解析为
**两个模块名**（`install_git_hooks` 与 `tools.install_git_hooks`）并报
`Source file found twice under different module names`（实测踩中）。
能力下沉到共享库后：**工具只做 CLI 薄壳，审计从库导入**，单名解析。
"""

from __future__ import annotations

import os
import stat
import subprocess

import paths

#: 版本化钩子目录（入库；随 clone 生效，只需设一次 `core.hooksPath`）
HOOKS_DIR_REL = ".githooks"

#: 各钩子**必须包含**的特征串（缺一即视为未就绪 —— 例如只剩 graphify 块而丢了自动推送块）
REQUIRED: dict = {
    "post-commit": ("git push origin main", "graphify-sync"),
    "post-checkout": ("graphify-sync",),
}


def hooks_path() -> str:
    """`git config core.hooksPath` 的值（空串 = 未设置）。"""
    try:
        r = subprocess.run(
            ["git", "config", "--get", "core.hooksPath"],
            cwd=paths.ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return (r.stdout or "").strip()
    except Exception:  # noqa: BLE001  git 不可用 → 视为未设置
        return ""


def check() -> list:
    """→ 未就绪原因列表（**空 = 就绪**）。**只读**，供 CLI 与审计复用。"""
    problems: list = []
    hp = hooks_path()
    if hp != HOOKS_DIR_REL:
        problems.append(
            f"`core.hooksPath` = {hp or '（未设置）'} ≠ `{HOOKS_DIR_REL}` → 版本化钩子**不会执行**"
            "（自动推送与图同步均失效）"
        )
    for name, needles in REQUIRED.items():
        p = os.path.join(paths.ROOT, HOOKS_DIR_REL, name)
        if not os.path.isfile(p):
            problems.append(f"缺少钩子文件 `{HOOKS_DIR_REL}/{name}`")
            continue
        raw = open(p, "rb").read()
        if b"\r\n" in raw:
            # 关键：sh 钩子在 CRLF 下会在 Git Bash 报 `$'\r': command not found` → **静默失效**
            problems.append(
                f"`{HOOKS_DIR_REL}/{name}` 含 **CRLF**（sh 钩子须为 LF，否则 Git Bash 下静默失效；"
                "由 `.gitattributes` 的 `.githooks/* text eol=lf` 保证）"
            )
        text = raw.decode("utf-8", "replace")
        for needle in needles:
            if needle not in text:
                problems.append(f"`{HOOKS_DIR_REL}/{name}` 缺少必需片段 `{needle}`")
        if os.name != "nt" and not (os.stat(p).st_mode & stat.S_IXUSR):
            problems.append(f"`{HOOKS_DIR_REL}/{name}` 无可执行位（chmod +x）")
    return problems


def install() -> int:
    """设置 `core.hooksPath` + 置可执行位（幂等）→ 未就绪原因条数（0 = 成功）。"""
    if not os.path.isdir(os.path.join(paths.ROOT, HOOKS_DIR_REL)):
        return -1
    subprocess.run(
        ["git", "config", "core.hooksPath", HOOKS_DIR_REL], cwd=paths.ROOT, check=False, timeout=10
    )
    if os.name != "nt":
        for name in REQUIRED:
            fp = os.path.join(paths.ROOT, HOOKS_DIR_REL, name)
            if os.path.isfile(fp):
                os.chmod(fp, 0o755)
    return len(check())
