# -*- coding: utf-8 -*-
"""std_lib.common_lib.docreader_bridge — **WeKnora docreader 调用桥**（N-180，2026-09-30）

职责：在**主环境（3.13）**里调用**独立 venv（3.11）**中的 docreader 做文档解析，并把结果
归一为稳定结构。跨解释器边界**不引入 gRPC 常驻服务**：改用**一次性子进程**（驱动
`tools/docreader_parse_driver.py`），因为——
  · 无端口占用、无长驻进程要守护；
  · 依赖隔离彻底（docreader 的 numpy/pandas pin 不会渗进主环境）；
  · 异常即非零退出 + 结果 JSON 落地 → **降级可观测**（本仓「增强层零硬依赖」纪律）。

为什么放到 `std_lib/`（而不是 `tools/`）
---------------------------------------
它会有**两个消费方**（链步骤 `tools/docreader_extract.py` + 将来的抽取层封装）。`tools/` 内
"被其它模块导入的实现"会触发 mypy 双名解析（`Source file found twice under different module
names`）并阻断 CI —— 本仓已实测踩中一次（见 `std_lib/common_lib/git_hooks.py` 的同款注释）。

路径来源：**唯一事实源** = `config/schema/semantic_tools.json` 的 `tools.weknora_docreader.bridge`
（本模块只解析，不重复声明路径）。
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time

import paths

#: 单文件解析默认超时（秒）。大扫描件渲染较慢，但**必须有上限**（否则子进程可无限挂起）。
DEFAULT_TIMEOUT = 600


def _spec() -> dict:
    """取清单里的 `bridge` 段（不存在 → 空 dict，由调用方判"未登记"）。"""
    from std_lib.common_lib import semantic_tools as st

    spec = (st.load_manifest().get("tools") or {}).get("weknora_docreader") or {}
    return spec.get("bridge") or {}


def _abs(rel: str) -> str:
    return rel if os.path.isabs(rel) else os.path.join(paths.ROOT, rel)


def available() -> tuple[bool, str]:
    """→ `(可用?, 说明)`。判据：venv 解释器 + 上游源码根 + 驱动脚本**三者齐备**。

    刻意**不**在此处 import docreader（跨解释器，主环境根本 import 不到）——"可用性"由
    **文件事实**判定（与本仓 `source_tree` 探测同理）；真正的运行期能力由 `self_test()` 证明。
    """
    sp = _spec()
    if not sp:
        return False, "清单未登记 `tools.weknora_docreader.bridge`（路径唯一事实源缺失）"
    need = {
        "venv_python": "隔离 venv 解释器（docreader 的依赖与主环境冲突，必须隔离）",
        "source_root": "上游源码根（`PYTHONPATH` 指向它才能 `import docreader`）",
        "driver": "解析驱动脚本（跨解释器边界的执行体）",
    }
    missing = [f"{k}（{why}）：{sp.get(k)}" for k, why in need.items() if not os.path.exists(_abs(str(sp.get(k) or "")))]
    if missing:
        return False, "缺 " + "；".join(missing[:3])
    return True, "venv + 上游源码 + 驱动齐备"


def self_test(timeout: int = 180) -> tuple[bool, str]:
    """**真实**自检：用 venv 解释器验证 `import docreader.parser` 与 grpcio 版本硬约束。

    为何必须真跑：`grpcio` 的下限是本仓**修正过的**（上游 pyproject 写 `>=1.78.0`，而**已提交的
    stub 硬编码 `GRPC_GENERATED_VERSION='1.80.0'`**）→ 版本不足会在 import 期 RuntimeError。
    只查文件存在**不足以**发现这类问题。
    """
    ok, why = available()
    if not ok:
        return False, why
    sp = _spec()
    code = (
        "import json,grpc;"
        "from docreader.parser import Parser;"
        "print(json.dumps({'grpc':grpc.__version__,'ok':True}))"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = _abs(str(sp["source_root"]))
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        r = subprocess.run(
            [_abs(str(sp["venv_python"])), "-c", code],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
            cwd=_abs(str(sp["source_root"])),
        )
    except subprocess.TimeoutExpired:
        return False, f"自检超时（{timeout}s）"
    except Exception as e:  # noqa: BLE001
        return False, f"自检无法执行：{type(e).__name__}: {e}"
    tail = (r.stdout or "").strip().splitlines()
    if r.returncode == 0 and tail:
        try:
            ver = json.loads(tail[-1]).get("grpc", "?")
        except Exception:  # noqa: BLE001
            ver = "?"
        return True, f"`import docreader.parser` 成功；grpcio={ver}（须 ≥1.80.0）"
    err = ((r.stderr or "") + (r.stdout or "")).strip().splitlines()
    return False, f"rc={r.returncode}：{err[-1][:200] if err else '无输出'}"


def parse_file(path: str, *, engine: str = "", timeout: int = DEFAULT_TIMEOUT) -> dict:
    """解析**单个本地文件** → 稳定结构。

    → `{"ok", "engine", "markdown", "source_blocks", "image_count", "metadata", "notice", "elapsed_s"}`

    **不抛异常**：任何失败都转成 `ok=False` + `notice`（调用方据此回退既有抽取，且**必须披露**）。
    """
    out = {
        "ok": False,
        "engine": engine or "builtin",
        "markdown": "",
        "source_blocks": [],
        "image_count": 0,
        "metadata": {},
        "notice": "",
        "elapsed_s": 0.0,
        "source": path,
    }
    ok, why = available()
    if not ok:
        out["notice"] = f"docreader 不可用：{why}"
        return out
    if not os.path.exists(path):
        out["notice"] = f"输入不存在：{path}"
        return out
    sp = _spec()
    t0 = time.time()
    tmpdir = tempfile.mkdtemp(prefix="docreader_")
    res_json = os.path.join(tmpdir, "result.json")
    cmd = [
        _abs(str(sp["venv_python"])),
        _abs(str(sp["driver"])),
        "--file",
        os.path.abspath(path),
        "--out",
        res_json,
    ]
    if engine:
        cmd += ["--engine", engine]
    env = dict(os.environ)
    env["PYTHONPATH"] = _abs(str(sp["source_root"]))
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
            cwd=_abs(str(sp["source_root"])),
        )
        if os.path.exists(res_json):
            with open(res_json, encoding="utf-8") as fh:
                got = json.load(fh)
            out.update({k: got.get(k, out.get(k)) for k in out if k != "source"})
            out["source"] = path
            if not got.get("ok"):
                out["ok"] = False
                out["notice"] = got.get("error") or f"驱动返回失败（rc={r.returncode}）"
        else:
            err = ((r.stderr or "") + (r.stdout or "")).strip().splitlines()
            out["notice"] = f"驱动未产出结果（rc={r.returncode}）：{err[-1][:200] if err else '无输出'}"
    except subprocess.TimeoutExpired:
        out["notice"] = f"解析超时（{timeout}s）→ 已终止子进程"
    except Exception as e:  # noqa: BLE001
        out["notice"] = f"调用失败：{type(e).__name__}: {e}"
    out["elapsed_s"] = round(time.time() - t0, 2)
    return out
