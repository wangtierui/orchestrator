# -*- coding: utf-8 -*-
"""std_lib.common_lib.mirrors — **镜像源就绪度核验**（N-175，2026-09-30）

职责：把「镜像配置是否声明 / 是否已施加到环境 / 变量值是否一致」做成**可复用**的只读核验，
供 `commands/doctor.py`（环境自检）与 `tools/check_mirrors.py`（含实测级核验）**共用**。

为什么放在 `std_lib/`（而非 tools/ 内）
---------------------------------------
与 `git_hooks.py` 同因：它有**两个消费方**（doctor + CLI 工具）。若实现留在 `tools/check_mirrors.py`
再由 doctor `from tools.check_mirrors import ...` 导入，mypy 会把同一文件解析为两个模块名
（`check_mirrors` 与 `tools.check_mirrors`）并**阻断 CI** —— 实测命中过一次
（`tools/audit_health.check_layering()` 会在**提交前**抓到，见该判据）。
纪律：**可复用实现下沉 `std_lib/`，`tools/` 只放 CLI 薄壳。**
"""

from __future__ import annotations

import paths
from config.loader import load_mirrors, mirror_env


def check_env() -> list[str]:
    """离线级核验：配置完整性 + 变量施加速 + 与配置一致性 → 问题列表（空 = 就绪）。

    为何只做"离线级"：本函数会被 `doctor` 每次调用，**不得联网**；实测级（真拉包/真取权重文件）
    由 `tools/check_mirrors.py --probe` 承担。
    """
    import os as _os

    f: list[str] = []
    cfg = load_mirrors()
    if not cfg:
        return [f"镜像配置为空或不可读：{paths.ROOT}/config/mirrors.yaml"]
    want = mirror_env()
    if not want:
        f.append("`mirrors.yaml:apply.env_map` 未解析出任何变量（配置形态不对）")
    for k, v in want.items():
        got = _os.environ.get(k, "")
        if not got:
            f.append(
                f"环境变量 `{k}` **未设置**（应为 `{v}`）→ 权重/依赖会走官方源"
                "（`huggingface.co` 本机**不可达**，首次调用即失败）"
            )
        elif got != v:
            f.append(f"环境变量 `{k}` = `{got}` ≠ 配置声明的 `{v}`（配置与环境不一致）")
    return f


def summary() -> str:
    """一行摘要（供 doctor 展示）。"""
    cfg = load_mirrors()
    env = mirror_env()
    return (
        f"HF_ENDPOINT={env.get('HF_ENDPOINT', '?')}；"
        f"PIP_INDEX_URL={env.get('PIP_INDEX_URL', '?')}（验证于 {cfg.get('verified_at', '?')}）"
    )
