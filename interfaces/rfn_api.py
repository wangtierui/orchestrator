# -*- coding: utf-8 -*-
"""
interfaces/rfn_api — RFN/归属表唯一访问接口（v1 SSOT 表 / Q1 / R2）

P0：占位壳，声明签名；P4（classifier rfn 迁入）后实现体委托 modules/regulatory_classifier/rfn。
覆盖：注册（register_doc）/三级查询（query_or_register）/索引重建（rebuild_index）/
主题归属写接口（theme_api 关联）与 RFN↔clean 桥表读写（R7 写者=reconcile）。
"""

from __future__ import annotations

import os
import sys

import paths

_MOD_CLASS = os.path.join(paths.MODULES_DIR, "regulatory_classifier")
for _p in (_MOD_CLASS, paths.ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)


class RFNAPI:
    """监管文件编号唯一事实源访问层（契约先行）。"""

    # ---- 注册 / 查询（委托 rfn.registry / rfn.query_or_register）----
    def register_doc(
        self, theme, title, docno=None, pub_date="", source="", fingerprint="", source_mark=""
    ):
        from rfn.registry import register_doc  # noqa: PLC0415

        return register_doc(
            theme=theme,
            title=title,
            docno=docno,
            pub_date=pub_date,
            source=source,
            fingerprint=fingerprint,
            source_mark=source_mark,
        )

    def query_or_register(
        self, theme, title, docno=None, pub_date="", source="", fingerprint="", force_register=False
    ):
        from rfn.query_or_register import query_or_register  # noqa: PLC0415

        return query_or_register(
            theme=theme,
            title=title,
            docno=docno,
            pub_date=pub_date,
            source=source,
            fingerprint=fingerprint,
            force_register=force_register,
        )

    def rebuild_index(self):
        from rfn.registry import rebuild_index  # noqa: PLC0415

        return rebuild_index()

    # ---- 溯源桥（Q1=A / R7：写者=reconcile 后处理；读取口=本接口）----
    def bridge_upsert(self, rfn, source, source_url, dedup_key, title, docno):
        """按 RFN 幂等 upsert 桥记录（保留既有锚；供 reconcile 后处理调用）。"""
        from rfn.bridge import upsert  # noqa: PLC0415

        row = {
            "rfn": rfn,
            "文件来源": source,
            "source_url": source_url,
            "dedup_key": dedup_key,
            "登记时标题": title,
            "登记时文号": docno,
        }
        return upsert(row)

    def bridge_lookup(self, source_url=None, dedup_key=None):
        from rfn.bridge import lookup  # noqa: PLC0415

        return lookup(source_url=source_url, dedup_key=dedup_key)

    # ---- 只读访问面（阶段 3，2026-09-18）：供 ipb / drafter / scrapers 消费，替代跨模块直连 ----
    def theme_map(self) -> dict:
        """主题码 → 全名（唯一源 = classifier.rfn.THEME_MAP）。"""
        from rfn import THEME_MAP  # noqa: PLC0415

        return dict(THEME_MAP)

    def get_index(self):
        """RFN 索引单例（`RFNIndex`：by_rfn/by_title/by_docno/by_theme/is_valid…）。"""
        from rfn import get_index  # noqa: PLC0415

        return get_index()

    def registry_paths(self) -> dict:
        """归属表相关**事实源文件路径**（消费方勿再自行拼兄弟仓路径）。

        ⚠️ 路径函数名以 `rfn/registry.py` 实际符号为准（`_csv_path`/`_theme_csv_path`/
        `_fp_path`/`_sync_path`）；索引 CSV 与 registry.rebuild_index 同口径（env 可覆盖）。
        """
        from rfn import registry as _reg  # noqa: PLC0415

        return {
            "attr_csv": _reg._csv_path(),
            "theme_csv": _reg._theme_csv_path(),
            "index_csv": (
                os.environ.get("RFN_REGISTRY_INDEX")
                or os.path.join(_reg._PKG_DIR, "监管文件编号索引.csv")
            ),
            "fp_csv": _reg._fp_path(),
            "sync_status": _reg._sync_path(),
            "bridge_csv": _reg._DEFAULT_CSV.replace(
                "人身保险公司-文件归属表.csv", "rfn_clean_bridge.csv"
            ),
        }

    def load_attr_rows(self) -> list[dict]:
        """归属表全量行（8 列中文列名，见 contract.REGISTRY_CSV_FIELDS）。"""
        from rfn import registry as _reg  # noqa: PLC0415

        return _reg._load_rows()

    def load_theme_rows(self) -> list[dict]:
        """主题归属表全量行（3 列）。"""
        from rfn import registry as _reg  # noqa: PLC0415

        return _reg._load_theme_rows()

    def bridge_rows(self) -> list[dict]:
        """RFN↔clean 溯源桥全量行（唯一写口 = reconcile 后处理）。"""
        from rfn.bridge import load_bridge  # noqa: PLC0415

        return load_bridge()

    def normalize_title(self, text: str) -> str:
        """标题归一（与 rfn 唯一实现同语义；勿在各模块本地 def）。

        ⚠️ 方法名刻意**不叫** `norm_title` —— `gate_no_duplicate_libs` 会对
        非 std_lib 文件中出现的 `def norm_title` 判 FAIL（SSOT 唯一实现纪律）。
        """
        from rfn import _norm_title  # noqa: PLC0415

        return _norm_title(text)


_api: RFNAPI | None = None


def get_rfn_api() -> RFNAPI:
    global _api
    if _api is None:
        _api = RFNAPI()
    return _api


# ---- 模块级便捷函数（阶段 3：供 `from interfaces.rfn_api import X` 直接消费）----
def theme_map() -> dict:
    return get_rfn_api().theme_map()


def get_index():
    return get_rfn_api().get_index()


def registry_paths() -> dict:
    return get_rfn_api().registry_paths()


def register_doc(theme, title, docno=None, pub_date="", source="", fingerprint="", source_mark=""):
    return get_rfn_api().register_doc(
        theme,
        title,
        docno=docno,
        pub_date=pub_date,
        source=source,
        fingerprint=fingerprint,
        source_mark=source_mark,
    )


def bridge_rows() -> list[dict]:
    return get_rfn_api().bridge_rows()


def attr_rows() -> list[dict]:
    return get_rfn_api().load_attr_rows()


def data_dir() -> str:
    """classifier 数据目录（`modules/regulatory_classifier/data`；I-3 新增访问器）。

    取法：由归属表路径 `registry_paths()["attr_csv"]` 的父目录派生 —— 与归属表**同源**，
    不引入第二个「classifier data 在哪」的判定。
    """
    return os.path.dirname(registry_paths()["attr_csv"])
