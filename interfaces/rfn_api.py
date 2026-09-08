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
    def register_doc(self, theme, title, docno=None, pub_date="", source="",
                     fingerprint="", source_mark=""):
        from rfn.registry import register_doc  # noqa: PLC0415
        return register_doc(theme=theme, title=title, docno=docno, pub_date=pub_date,
                            source=source, fingerprint=fingerprint, source_mark=source_mark)

    def query_or_register(self, theme, title, docno=None, pub_date="",
                          source="", fingerprint="", force_register=False):
        from rfn.query_or_register import query_or_register  # noqa: PLC0415
        return query_or_register(theme=theme, title=title, docno=docno, pub_date=pub_date,
                                 source=source, fingerprint=fingerprint,
                                 force_register=force_register)

    def rebuild_index(self):
        from rfn.registry import rebuild_index  # noqa: PLC0415
        return rebuild_index()

    # ---- 溯源桥（Q1=A / R7：写者=reconcile 后处理；读取口=本接口）----
    def bridge_upsert(self, rfn, source, source_url, dedup_key, title, docno):
        """按 RFN 幂等 upsert 桥记录（保留既有锚；供 reconcile 后处理调用）。"""
        from rfn.bridge import upsert  # noqa: PLC0415
        row = {"监管文件编号": rfn, "文件来源": source, "source_url": source_url,
               "dedup_key": dedup_key, "登记时标题": title, "登记时文号": docno}
        return upsert(row)

    def bridge_lookup(self, source_url=None, dedup_key=None):
        from rfn.bridge import lookup  # noqa: PLC0415
        return lookup(source_url=source_url, dedup_key=dedup_key)


_api: RFNAPI | None = None


def get_rfn_api() -> RFNAPI:
    global _api
    if _api is None:
        _api = RFNAPI()
    return _api
