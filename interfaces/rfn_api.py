# -*- coding: utf-8 -*-
"""
interfaces/rfn_api — RFN/归属表唯一访问接口（v1 SSOT 表 / Q1 / R2）

P0：占位壳，声明签名；P4（classifier rfn 迁入）后实现体委托 modules/regulatory_classifier/rfn。
覆盖：注册（register_doc）/三级查询（query_or_register）/索引重建（rebuild_index）/
主题归属写接口（theme_api 关联）与 RFN↔clean 桥表读写（R7 写者=reconcile）。
"""
from __future__ import annotations


class RFNAPI:
    """监管文件编号唯一事实源访问层（契约先行）。"""

    # ---- 注册 / 查询（委托 rfn.registry / rfn.query_or_register）----
    def register_doc(self, theme, title, docno=None, pub_date="", source="",
                     fingerprint="", source_mark=""):  # pragma: no cover
        raise NotImplementedError("P4 接入 rfn.registry 后实现")

    def query_or_register(self, theme, title, docno=None, pub_date="",
                          source="", fingerprint="", force_register=False):  # pragma: no cover
        raise NotImplementedError("P4 接入 rfn.query_or_register 后实现")

    def rebuild_index(self):  # pragma: no cover
        raise NotImplementedError("P4 接入 rfn.registry 后实现")

    # ---- 溯源桥（Q1=A / R7：写者=reconcile 后处理）----
    def bridge_upsert(self, rfn, source, source_url, dedup_key, title, docno):  # pragma: no cover
        raise NotImplementedError("P5 reconcile_clean_drift 接入后实现")

    def bridge_lookup(self, source_url=None, dedup_key=None):  # pragma: no cover
        raise NotImplementedError("P5 接入后实现")


_api: RFNAPI | None = None


def get_rfn_api() -> RFNAPI:
    global _api
    if _api is None:
        _api = RFNAPI()
    return _api
