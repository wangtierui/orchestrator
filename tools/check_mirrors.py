# -*- coding: utf-8 -*-
"""tools.check_mirrors — 镜像源（权重/依赖优先访问源）的**核验与实测**

为什么需要它
------------
镜像不可用时的症状**远离配置处**：pip 装到一半失败、HF 首次调用才联网下载（破坏离线性）、
或"主进程能下、子进程不能下"。故把镜像可用性做成**可复跑的两级核验**：

  · **默认（离线级）**：配置是否声明、`bootstrap` 是否已把变量施加到环境、变量值是否与配置一致；
  · **`--probe`（实测级）**：**真实**用 tuna 拉一个小包 + 从 hf-mirror 取一个小文件
    （不是"首页能打开"就算通 —— 那正是最容易骗过人的假阳性）。

用法
----
    python -m tools.check_mirrors            # 离线级核验（快，可入 doctor/CI）
    python -m tools.check_mirrors --probe    # 实测级（联网，需数秒；失败即给出替代端点建议）
"""

from __future__ import annotations

import io
import os
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.request

# 引导：**必须经 `bootstrap`（唯一引导点）** —— `gate_import_bootstrap` 断言 tools/ 层的
# `sys.path.insert` 只减不增；本工具因此以 `python -m tools.check_mirrors` 方式运行。
from bootstrap import bootstrap

bootstrap("all")

from config.exitcodes import ExitCode
from config.loader import load_mirrors, mirror_env

# 离线级核验（`check_env`）的**唯一实现**在 `std_lib/common_lib/mirrors.py` ——
# `commands/doctor.py` 与本工具**共用**它。本文件只做 CLI 薄壳：`tools/` 内**不得**承载
# "被其它模块导入的实现"（否则 mypy 把同一文件解析为两个模块名 → 阻断 CI；见「分层纪律」判据）。
from std_lib.common_lib.mirrors import check_env


def probe_pypi() -> tuple:
    """实测：用 tuna 索引**真实下载**一个小包 → `(ok, 说明)`。"""
    cfg = load_mirrors()
    idx = ((cfg.get("pypi") or {}).get("index_url") or "").strip()
    pkg = ((cfg.get("pypi") or {}).get("verified_package") or "six").strip()
    if not idx:
        return False, "未声明 pypi.index_url"
    with tempfile.TemporaryDirectory() as td:
        t0 = time.time()
        r = subprocess.run(
            [sys.executable, "-m", "pip", "download", pkg, "-d", td, "-i", idx, "--no-deps", "-q"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=240,
        )
        dt = time.time() - t0
        ok = r.returncode == 0 and any(os.scandir(td))
        tail = (r.stderr or r.stdout or "").strip().splitlines()
        return ok, f"{idx} 拉取 {pkg} {'成功' if ok else '失败'}（{dt:.1f}s）" + (
            "" if ok else f" | {tail[-1][:160] if tail else '无输出'}"
        )


def probe_hf() -> tuple:
    """实测：从 hf-mirror **真实取一个小文件** → `(ok, 说明)`。"""
    cfg = load_mirrors()
    hf = cfg.get("huggingface") or {}
    ep = (hf.get("endpoint") or "").rstrip("/")
    repo = hf.get("probe_repo") or "bert-base-chinese"
    fn = hf.get("probe_file") or "vocab.txt"
    if not ep:
        return False, "未声明 huggingface.endpoint"
    url = f"{ep}/{repo}/resolve/main/{fn}"
    # ⚠️ 镜像站 WAF 对**浏览器 UA**返回 403（实测 tuna）；此处用工具型 UA
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8.4.0"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=30, context=ssl.create_default_context()) as r:
            b = r.read()
        return True, f"{ep} 取 {repo}/{fn} 成功（HTTP {r.status}，{len(b)}B，{time.time() - t0:.1f}s）"
    except Exception as e:  # noqa: BLE001
        fb = (hf.get("fallback_endpoint") or "").rstrip("/")
        return False, f"{ep} 取 {repo}/{fn} 失败：{type(e).__name__}: {str(e)[:120]}（兜底端点：{fb}）"


def main(argv=None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    problems = check_env()
    print("[check_mirrors] 离线级核验：")
    if problems:
        for p in problems:
            print(f"  ✗ {p}")
    else:
        cfg = load_mirrors()
        env = mirror_env()
        print(f"  ✓ 配置就绪（verified_at={cfg.get('verified_at')}）")
        for k, v in env.items():
            print(f"    {k} = {v}")
    if "--probe" in argv:
        print("\n[check_mirrors] 实测级核验（联网）：")
        ok1, m1 = probe_pypi()
        print(f"  {'✓' if ok1 else '✗'} PyPI 镜像：{m1}")
        ok2, m2 = probe_hf()
        print(f"  {'✓' if ok2 else '✗'} HF 镜像：{m2}")
        problems = problems if (ok1 and ok2) else (problems + ([] if ok1 else [m1]) + ([] if ok2 else [m2]))
    if problems:
        print("\n[check_mirrors] FAIL：镜像未就绪（处置见 config/mirrors.yaml 头部）")
        return int(ExitCode.FAIL)
    print("\n[check_mirrors] PASS：镜像就绪")
    return int(ExitCode.OK)


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    raise SystemExit(main())
