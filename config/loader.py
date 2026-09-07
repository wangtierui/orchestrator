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
# 占位符展开：${NAME} 与 ${NAME:-default}（shell 风格默认值，简单实现）
# --------------------------------------------------------------------------- #
_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def _expand(value: str) -> str:
    def _rep(m: re.Match) -> str:
        name, default = m.group(1), m.group(2)
        env = os.environ.get(name, "")
        if env:
            return env
        if name == "REG_ORCH_ROOT":
            return paths.ROOT
        return default if default is not None else ""
    return _PLACEHOLDER.sub(_rep, value)


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


def active_source_ids() -> list[str]:
    """仅返回 enabled=true 且不含 '.'（排除子源）的五源标识（与 config.enums.SOURCE_SET 一致）。"""
    srcs = load_sources()
    return sorted(
        sid for sid, cfg in srcs.items()
        if cfg.get("enabled") and "." not in sid and sid in {"gov", "mof", "nfra", "pbc", "supp"}
    )


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
