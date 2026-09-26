# -*- coding: utf-8 -*-
"""interfaces/clean_index_api — 五源 cleaned 快照索引**唯一访问接口**（阶段 3 实装，2026-09-18）

背景（`reports/数据流转与存储交互优化方案_20260917.md` §6 阶段 3）
----------------------------------------------------------------
此前 `clean_index/index.json` 被 classifier 的 **9 处**调用点各自 `sys.path.insert` +
裸 `from clean_index import ...` 直连（`recall_audit/*`、`scripts/*`、`report_builders/*`），
`interfaces/clean_index_api.py` 本身却是四个 `NotImplementedError` 的空壳——
"跨模块唯一通道"在该链路上完全失效（方案 §2.2 G1）。

本模块现为**唯一引导点**：把 `modules/regulatory_scrapers` 的 `sys.path` 引导收敛到这一处，
调用方一律 `from interfaces.clean_index_api import ...`。这样：
  - 依赖方向显式（`modules/* → interfaces/` 单向），重构时改动面收敛；
  - `gate_no_cross_module_import` 可静态发现新的越权直连。

纪律：本模块**只读**（clean_index 是派生索引，唯一写方 = `build_clean_index`）。
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))            # interfaces/
_ROOT = os.path.dirname(_HERE)                                # orchestrator 根
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import paths  # noqa: E402

_SCRAPERS = os.path.join(paths.MODULES_DIR, "regulatory_scrapers")
if _SCRAPERS not in sys.path:
    sys.path.insert(0, _SCRAPERS)

INDEX_PATH = os.path.join(_SCRAPERS, "clean_index", "index.json")


def _impl():
    """真实实现模块（`modules/regulatory_scrapers/clean_index`）。"""
    import clean_index as _ci  # noqa: PLC0415
    return _ci


def get_clean_index(*, rebuild: bool = False):
    """五源 cleaned 索引单例（缺失/不可用时由实现侧自愈重建）。"""
    return _impl().get_clean_index(rebuild=rebuild)


def rebuild_index(*, hash_files: bool = True):
    """强制重建索引（写方语义；仅供运维/自愈路径调用）。"""
    return _impl().rebuild_index(hash_files=hash_files)


def scan_sources(scraper_root: str = "", *, hash_files: bool = True) -> dict:
    """扫描磁盘生成索引结构（不落盘）。`scraper_root` 省略时取实现侧默认根。"""
    m = _impl()
    return m.scan_sources(scraper_root or m.SCRAPER_ROOT, hash_files=hash_files)


# ---- 目录/文件定位访问器（v2 §3.1.3 I-3，2026-09-26）----
# 补 I-3：发布层（`base_publish/*`）与 classifier 需读 scrapers 的发布件目录与
# clean_index 索引文件，此前各自拼 `os.path.join(_MODULES, "regulatory_scrapers", …)`。
# 判据 D（gate_no_cross_module_import）据此断言「路径必须经 interfaces 或 paths.module_dir」。
def published_dir() -> str:
    """五源发布件目录（`modules/regulatory_scrapers/published`）。"""
    return os.path.join(_SCRAPERS, "published")


def index_path() -> str:
    """clean_index 索引文件（`clean_index/index.json`；快照内容签名与 latest 的事实源）。"""
    return os.path.join(_SCRAPERS, "clean_index", "index.json")


def latest_csv_path(source_id: str) -> str | None:
    return get_clean_index().latest_csv_path(source_id)


def latest_jsonl_path(source_id: str) -> str | None:
    return get_clean_index().latest_jsonl_path(source_id)


def latest(source_id: str) -> dict | None:
    return get_clean_index().latest(source_id)


def latest_date(source_id: str) -> str | None:
    return get_clean_index().latest_date(source_id)


def all_active_csv() -> list[dict]:
    return get_clean_index().all_active_csv()


def all_active_jsonl() -> list[dict]:
    return get_clean_index().all_active_jsonl()


def source_ids() -> list[str]:
    return get_clean_index().source_ids()


def record_count(source_id: str, date: str | None = None):
    return get_clean_index().record_count(source_id, date)


def validate_files() -> dict:
    return get_clean_index().validate_files()


def is_fresh() -> bool:
    return get_clean_index().is_fresh()


class CleanIndexAPI:
    """向后兼容壳：历史调用方 `get_clean_index_api().latest_csv_path(...)` 语义不变。

    v2 §3.1.3 I-4（2026-09-26）：补齐 `interfaces.protocols.CleanIndexProvider` 声明的
    形状（`source_ids` / `latest` / `published_dir` / `index_path`）——协议描述的是
    **provider 对象**（消费方经 `get_clean_index_api()` 取），故类需与协议同形。
    """

    def latest_csv_path(self, source_id: str) -> str | None:
        return latest_csv_path(source_id)

    def latest_jsonl_path(self, source_id: str) -> str | None:
        return latest_jsonl_path(source_id)

    def validate_files(self) -> dict:
        return validate_files()

    def is_fresh(self) -> bool:
        return is_fresh()

    # ---- I-4 协议形状补齐 ----
    def source_ids(self) -> list[str]:
        return source_ids()

    def latest(self, source_id: str) -> dict | None:
        return latest(source_id)

    def published_dir(self) -> str:
        return published_dir()

    def index_path(self) -> str:
        return index_path()


_api: CleanIndexAPI | None = None


def get_clean_index_api() -> CleanIndexAPI:
    global _api
    if _api is None:
        _api = CleanIndexAPI()
    return _api
