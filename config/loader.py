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
except Exception:  # pragma: no cover  # noqa: BLE001
    yaml = None

import paths

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
        sid
        for sid, cfg in srcs.items()
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
# --------------------------------------------------------------------------- #
# N-175（2026-09-30）：镜像源（**权重与依赖下载的优先访问源**）
# --------------------------------------------------------------------------- #
MIRRORS_YAML = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mirrors.yaml")

_MIRRORS_CFG: dict | None = None


def load_mirrors(refresh: bool = False) -> dict:
    """镜像配置（唯一事实源 = `config/mirrors.yaml`）。见该文件头部：为什么集中 + 实测证据。"""
    global _MIRRORS_CFG
    if _MIRRORS_CFG is not None and not refresh:
        return _MIRRORS_CFG
    with open(MIRRORS_YAML, encoding="utf-8") as fh:
        _MIRRORS_CFG = yaml.safe_load(fh) or {}
    return _MIRRORS_CFG


def mirror_env(refresh: bool = False) -> dict:
    """→ 应施加到环境的镜像变量（`{ENV_NAME: 值}`），按 `mirrors.yaml:apply.env_map` 解析。

    **不做副作用**（纯函数），便于：① 单测；② 传 `subprocess` 的 `env=`；③ doctor 核验。
    """
    cfg = load_mirrors(refresh)
    out: dict = {}
    for env_name, dotted in ((cfg.get("apply") or {}).get("env_map") or {}).items():
        node: Any = cfg
        for part in str(dotted).split("."):
            node = (node or {}).get(part) if isinstance(node, dict) else None
        if node:
            out[str(env_name)] = str(node)
    return out


def apply_mirror_env(refresh: bool = False) -> list:
    """把镜像变量**施加到本进程环境**（`setdefault`：显式 env 优先，不被覆盖）→ 实际设置的项。

    为何在 `bootstrap()` 里单点调用：镜像变量必须**早于任何 HF/pip 相关导入生效**，
    且要被**子进程继承**（链路各步都是子进程）—— 散在各脚本里必然漏设。
    """
    applied: list = []
    for k, v in mirror_env(refresh).items():
        if not os.environ.get(k):
            os.environ[k] = v
            applied.append(k)
    return applied


# --------------------------------------------------------------------------- #
# N-166（2026-09-30）：门禁 `gate_config_integrity` 的**路径与策略**单源化
# --------------------------------------------------------------------------- #
# 为何放在 loader：该门禁原**自定路径常量**（`_SCHED_YAML` / `_TRIGGERS_YAML`）并直接
# `yaml.safe_load(open(...))` —— 那是**第二条读取路径**（本模块是 R24 声明的"程序可读配置
# 唯一读取口"）。路径若变更，loader 与门禁会各自漂移 → 门禁校验的可能是**旧文件**。
# 现统一由本模块导出路径常量 + 策略加载函数。
SCHEDULE_YAML = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schedule.yaml")
TRIGGERS_YAML = os.path.join(os.path.dirname(os.path.abspath(__file__)), "triggers.yaml")
INTEGRITY_POLICY_YAML = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "integrity_policy.yaml"
)

_POLICY_CFG: dict | None = None


def load_integrity_policy(refresh: bool = False) -> dict:
    """门禁 `gate_config_integrity` 的**策略清单**（唯一事实源 = `config/integrity_policy.yaml`）。

    纪律：调用方**必须**处理键缺失 —— 本函数不吞缺失（原实现把策略写死在代码里，缺失无从发生；
    外置后若静默取默认值，就会把"配置漏写"变成"判据悄悄放宽"）。
    """
    global _POLICY_CFG
    if _POLICY_CFG is not None and not refresh:
        return _POLICY_CFG
    with open(INTEGRITY_POLICY_YAML, encoding="utf-8") as fh:
        _POLICY_CFG = yaml.safe_load(fh) or {}
    return _POLICY_CFG


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
