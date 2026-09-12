# -*- coding: utf-8 -*-
"""
config/loader.py — 配置加载器（R24：程序可读配置唯一读取口）

- sources.yaml：源目录唯一事实源 → source registry（含 enabled 过滤 / 别名 / collector 模块名）。
- ocr.yaml：OCR 引擎配置；${VAR} 占位符展开（REG_ORCH_ROOT→paths.ROOT；同名环境变量覆盖）。
- 所有模块不得直接 open 本目录 yaml；一律 from config.loader import load_sources, load_ocr。
"""
from __future__ import annotations

import os
import re
from typing import Any

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None

import paths  # noqa: E402  (根 paths 唯一入口，R4)

# --------------------------------------------------------------------------- #
# 占位符展开：${NAME} 与 ${NAME:-default}（shell 风格默认值）
# 2026-09-12 修复：default 支持**一层嵌套**（如 ${OCR_TESSERACT_BIN:-${REG_ORCH_ROOT}/external/...}）
# ——原正则 [^}]* 遇嵌套提前截断，展开残留 "${REG_ORCH_ROOT/external/tesseract/..."；
# 现 default 允许含 ${...}，并以迭代展开消化嵌套层级。
# --------------------------------------------------------------------------- #
_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-((?:[^{}]|\{[^{}]*\})*))?\}")


def _expand(value: str) -> str:
    def _rep(m: re.Match) -> str:
        name, default = m.group(1), m.group(2)
        env = os.environ.get(name, "")
        if env:
            return env
        if name == "REG_ORCH_ROOT":
            return paths.ROOT
        return default if default is not None else ""
    out = value
    for _ in range(5):  # 迭代展开（嵌套 ${A:-${B}}：内层随下一轮次消化）
        prev = out
        out = _PLACEHOLDER.sub(_rep, out)
        if out == prev:
            break
    return out


def _expand_deep(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _expand_deep(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand_deep(v) for v in obj]
    if isinstance(obj, str):
        return _expand(obj)
    return obj


# --------------------------------------------------------------------------- #
# sources.yaml → source registry
# --------------------------------------------------------------------------- #
_ENABLED_SOURCES: dict[str, dict] | None = None


def load_sources(refresh: bool = False) -> dict[str, dict]:
    """返回 {source_id: cfg}，含 enabled=false 的条目（带 disabled_reasons）。

    顶层 external_sources 的 id 支持 'gov.flk' 表示子源；仅登记 doc 用，不进入 SOURCE_SET。
    """
    global _ENABLED_SOURCES
    if _ENABLED_SOURCES is not None and not refresh:
        return _ENABLED_SOURCES
    if yaml is None:
        raise RuntimeError("PyYAML 未安装：pip install PyYAML")
    with open(paths.SOURCES_YAML, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    out: dict[str, dict] = {}
    for src in data.get("external_sources", []):
        out[src["id"]] = src
    if data.get("internal_source"):
        out["internal"] = data["internal_source"]
    _ENABLED_SOURCES = out
    return out


def active_source_ids(refresh: bool = False) -> list[str]:
    """外部启用源标识（enabled=true 且不含 '.' 的子源、非 internal）——由 sources.yaml 派生，
    与 config.enums.SOURCE_SET 交叉一致由 gate_sources_config 校验（R15：无集合字面量）。"""
    srcs = load_sources(refresh)
    return sorted(
        sid for sid, cfg in srcs.items()
        if cfg.get("enabled") and "." not in sid and sid != "internal"
    )


# --------------------------------------------------------------------------- #
# collector 路由（R15 补全）：sources.yaml「collector」字段消费
# --------------------------------------------------------------------------- #
def collector_module(source_id: str, refresh: bool = False) -> str:
    """源 collector 模块名（collectors.gov_collector → gov_collector；无点原样返回）。"""
    cfg = load_sources(refresh).get(source_id) or {}
    v = (cfg.get("collector") or "").strip()
    return v.rsplit(".", 1)[-1] if "." in v else v


def collector_path(source_id: str, refresh: bool = False) -> str:
    """解析源 collector 脚本绝对路径；collectors 目录缺失对应模块返回空串（供新增源 checklist 判空）。"""
    mod = collector_module(source_id, refresh)
    if not mod:
        return ""
    p = os.path.join(paths.ROOT, "modules", "regulatory_scrapers", "collectors", mod + ".py")
    return p if os.path.exists(p) else ""


def collector_source_map(refresh: bool = False) -> dict[str, str]:
    """{source_id: collector 脚本绝对路径}——仅 enabled 外部源，供编排/调用路由（R15/R11）。"""
    out = {}
    for sid in active_source_ids(refresh):
        p = collector_path(sid, refresh)
        if p:
            out[sid] = p
    return out


# --------------------------------------------------------------------------- #
# ocr.yaml → ocr 配置
# --------------------------------------------------------------------------- #
_OCR_CFG: dict | None = None


def load_ocr(refresh: bool = False) -> dict:
    global _OCR_CFG
    if _OCR_CFG is not None and not refresh:
        return _OCR_CFG
    if yaml is None:
        raise RuntimeError("PyYAML 未安装")
    with open(paths.OCR_YAML, encoding="utf-8") as fh:
        _OCR_CFG = _expand_deep(yaml.safe_load(fh))
    return _OCR_CFG


if __name__ == "__main__":  # 离线自检
    print("active sources:", active_source_ids())
    ocr = load_ocr()
    print("ocr engine:", ocr.get("ocr", {}).get("engine"))
    print("tesseract_bin:", ocr.get("ocr", {}).get("tesseract_bin"))
