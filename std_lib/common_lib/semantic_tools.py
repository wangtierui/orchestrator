# -*- coding: utf-8 -*-
"""std_lib.common_lib.semantic_tools — P1 语义增强工具的**能力探测与指纹**（N-102）

定位（为什么需要它）
--------------------
优化方案 v2 为 P1 语义增强定了三条硬纪律：**零硬依赖 / 降级链 / 指纹**。但"零硬依赖"若只写在
文档里，落地时会退化成两种坏形态：
  ① 某模块顶层 `import hanlp` → 未安装的克隆**导入即崩**（可移植性破坏）；
  ② `try: import x except: pass` 吞掉一切 → 增强能力**静默失效**（无人知道跑的是回退路径）。
本模块把"探测"变成**唯一、显式、可查询**的动作：

```
semantic_tools.probe("hanlp")        # → {"available": False, "version": "", "detail": "..."}
semantic_tools.available("hanlp")    # → bool（调用方据此走增强/回退分支）
semantic_tools.fallback_notice("hanlp", layer="sentence_split_ml")
semantic_tools.fingerprint()         # → 稳定指纹 dict（写入产物 provenance）
```

单一事实源
----------
工具清单（来源仓库 / 用途 / pip 包 / 探测名 / 对应 v2 项 / 体积与离线性 / 许可待核）登记在
`config/schema/semantic_tools.json`；本模块只**读取**它，不重复定义任何工具信息。
消费者：本模块、`gates/gate_config_integrity` 判据 U（清单自洽）、
`tools/gen_benchmark.py` §6.6（每轮全链刷新"当前环境具备哪些增强能力"）。

指纹与可复现性
--------------
ML 输出受 **模型版本 / 权重 / 依赖版本** 影响 → 同一输入可能产出不同产物，使 `input_sha`
断点与 `gate_watermark` 水位判据**失真**。故凡使用本清单任一工具产出的产物，**必须**把
`fingerprint()` 写入其 provenance（既有先例：`relations` 的 `extractor_version`）。
`fingerprint()` 键序稳定（sorted）且只含可比较标量，便于落盘后逐键 diff。
"""

from __future__ import annotations

import importlib.util
import json
import os
from functools import lru_cache


def _manifest_path() -> str:
    """清单路径（经 `paths` 解析，不写相对路径/盘符）。"""
    import paths

    return os.path.join(paths.CONFIG_DIR, "schema", "semantic_tools.json")


@lru_cache(maxsize=1)
def load_manifest() -> dict:
    """读取工具清单（唯一事实源）。

    文件缺失/不可解析/无 `tools` 段 → 抛 `RuntimeError`（**不静默返回空**：空清单会让探测
    把一切判为不可用，从而**掩盖**真正的配置损坏）。
    """
    p = _manifest_path()
    if not os.path.exists(p):
        raise RuntimeError(f"语义工具清单缺失：{p}")
    try:
        with open(p, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as e:
        raise RuntimeError(f"语义工具清单不可解析（{p}）：{type(e).__name__}: {e}") from e
    if not isinstance(data.get("tools"), dict) or not data["tools"]:
        raise RuntimeError(f"语义工具清单无 tools 段：{p}")
    return data


def tool_names() -> list[str]:
    """清单内全部工具名（**稳定排序**）。"""
    return sorted(load_manifest()["tools"])


def policy() -> dict:
    """三条硬纪律（零硬依赖 / 降级链 / 指纹）与违规口径。"""
    return dict(load_manifest().get("policy") or {})


def _dist_version(pypi: str) -> str:
    """pip 发行版版本（无发行包/无元数据 → ""）。"""
    if not pypi:
        return ""
    try:
        from importlib.metadata import PackageNotFoundError, version

        return version(pypi)
    except PackageNotFoundError:
        return ""
    except Exception:  # noqa: BLE001  元数据损坏不应阻断探测（版本降级为空）
        return ""


def _module_version(import_name: str) -> str:
    """模块自述版本（`__version__`；无则 ""）。**惰性导入**，失败即空。"""
    if not import_name:
        return ""
    try:
        mod = __import__(import_name)
    except Exception:  # noqa: BLE001  导入失败 = 未安装/不可用（由 available 统一表达）
        return ""
    return str(getattr(mod, "__version__", "") or "")


def probe(name: str) -> dict:
    """探测单个工具 → 稳定结构（**不抛异常**，清单缺失除外）。

    键：`name / kind / available / version / detail / extra / pipeline_ref / endpoint`。
    `kind` 取自清单：`module`（pip 包）/ `service`（独立服务：客户端库 + 端点）/ `manual`（人工获取）。
    """
    tools = load_manifest()["tools"]
    if name not in tools:
        return {
            "name": name,
            "kind": "unknown",
            "available": False,
            "version": "",
            "detail": f"工具未登记于 semantic_tools.json（已有：{tool_names()}）",
            "extra": "",
            "pipeline_ref": "",
            "endpoint": "",
        }
    spec = tools[name]
    pspec = spec.get("probe") or {}
    kind = str(pspec.get("kind") or "module")
    import_name = str(pspec.get("import_name") or "")
    pypi = str(spec.get("pypi") or "")

    found = False
    detail = ""
    if import_name:
        try:
            found = importlib.util.find_spec(import_name) is not None
        except (ImportError, ValueError) as e:  # 名称异常 / 父包缺失 → 视作未安装
            found = False
            detail = f"find_spec({import_name}) 失败：{type(e).__name__}"
    else:
        detail = "清单未声明探测名"

    ver = (_dist_version(pypi) or _module_version(import_name)) if found else ""
    endpoint = ""
    if kind == "service":
        env = str(pspec.get("endpoint_env") or "")
        endpoint = (
            os.environ.get(env, str(pspec.get("default_endpoint") or "")) if env else ""
        )
        if found and not endpoint:
            detail = f"客户端可用但未配置端点（env {env}）"
    if kind == "manual" and not found:
        detail = "论文/代码库，须人工获取（无发行包）"
    if not found and not detail:
        detail = f"{'pip 包' if pypi else '模块'} {import_name or '?'} 未安装"

    return {
        "name": name,
        "kind": kind,
        "available": bool(found),
        "version": ver,
        "detail": detail,
        "extra": str(spec.get("extra") or ""),
        "pipeline_ref": str(spec.get("pipeline_ref") or ""),
        "endpoint": endpoint,
    }


def probe_all() -> dict:
    """全部工具探测结果（键序稳定）。"""
    return {n: probe(n) for n in tool_names()}


def available(name: str) -> bool:
    """该工具在当前环境是否可用（调用方据此选增强/回退分支）。"""
    return bool(probe(name)["available"])


def available_any(*names: str) -> bool:
    """任一可用（同一增强层的多后端择优，如 hanlp / ltp 二选一）。"""
    return any(available(n) for n in names)


def fallback_notice(name: str, *, layer: str = "") -> str:
    """**可观测的**回退说明（调用方在走正则回退时应当记录它）。

    纪律：回退**不得静默** —— 否则"增强层已上线但从未生效"会长期无人发现
    （同类历史缺陷：链外产物 N-46 滞后 19 天）。
    """
    p = probe(name)
    where = f"[{layer}] " if layer else ""
    return (
        f"{where}语义增强 {name!r} 不可用（{p['detail']}）→ 回退既有正则实现；"
        f"如需启用：pip install -e .[{p['extra']}]" if p["extra"] else
        f"{where}语义增强 {name!r} 不可用（{p['detail']}）→ 回退既有正则实现"
    )


def fingerprint(names: list[str] | tuple[str, ...] | None = None) -> dict:
    """语义能力**指纹**（供产物 provenance 落盘）。

    → `{"available": [...], "unavailable": [...], "versions": {名: 版本}, "schema_version": ...}`
    **键序稳定**（sorted）、只含标量 → 可逐键 diff。**不含**模型权重 sha（须由增强层在加载
    实际模型后补充 `model_id`/`weights_sha` 至 `versions` 之外的同级字段，见清单 policy）。
    """
    picked = list(names) if names else tool_names()
    vers: dict = {}
    avail: list = []
    unavail: list = []
    for n in sorted(picked):
        p = probe(n)
        if p["available"]:
            avail.append(n)
            vers[n] = p["version"]
        else:
            unavail.append(n)
    return {
        "available": avail,
        "unavailable": unavail,
        "versions": dict(sorted(vers.items())),
        "schema_version": str(load_manifest().get("schema_version") or ""),
    }


def summary_line() -> str:
    """单行摘要（供日志/基准表引用）。"""
    ps = probe_all()
    ok = [n for n, p in ps.items() if p["available"]]
    return (
        f"语义增强能力：{len(ok)}/{len(ps)} 可用"
        + (f"（{', '.join(ok)}）" if ok else "（全部未安装 → 全链走既有正则路径）")
    )


def _main(argv: list[str]) -> int:
    from config.exitcodes import ExitCode

    cmd = argv[1] if len(argv) > 1 else "--probe"
    if cmd == "--probe":
        print(summary_line())
        for n, p in probe_all().items():
            flag = "可用" if p["available"] else "未装"
            ver = f" v{p['version']}" if p["version"] else ""
            print(f"  [{flag}] {n:20}{ver:14} {p['pipeline_ref']}")
            if not p["available"]:
                print(f"          {p['detail']}")
        return int(ExitCode.OK)
    if cmd == "--fingerprint":
        print(json.dumps(fingerprint(), ensure_ascii=False, indent=1, sort_keys=True))
        return int(ExitCode.OK)
    print(f"用法：{os.path.basename(argv[0])} [--probe|--fingerprint]")
    return int(ExitCode.USAGE)


if __name__ == "__main__":  # 离线自检：探测层面**无外部依赖**，未安装亦应正常返回
    import sys as _sys

    _ps = probe_all()
    assert _ps and all({"name", "available", "detail"} <= set(p) for p in _ps.values()), _ps
    _fp = fingerprint()
    assert set(_fp) == {"available", "unavailable", "versions", "schema_version"}, _fp
    assert sorted(_fp["available"] + _fp["unavailable"]) == tool_names(), "指纹须覆盖全部工具"
    raise SystemExit(_main(_sys.argv))
