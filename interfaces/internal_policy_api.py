# -*- coding: utf-8 -*-
"""
interfaces/internal_policy_api — 内部制度唯一访问接口（P6 实装；D-03 IPN 独立体系）

委托 modules/internal_policy_base（indexer/align/scan 产物），只读索引与对齐视图。
merged_view（制度×监管主题/RFN 对齐视图，D-06 预留）由 P7 aligner/drafter 生成后回填。
"""
from __future__ import annotations

import json
import os

import paths

_MOD_BASE = os.path.join(paths.MODULES_DIR, "internal_policy_base")
_DATA = os.path.join(_MOD_BASE, "data")
_INDEX_PATH = os.path.join(_DATA, "internal_policy_index.json")
_ALIGN_PATH = os.path.join(_DATA, "align_result.json")

# merged_view schema 版本（D-06：二期 app 读取前冻结）
MERGED_VIEW_SCHEMA_VERSION = "1.0"
# UNALIGNED 桶（R18：内部制度无对应监管主题时保留于此，不进主视图）
UNALIGNED_BUCKET = "UNALIGNED"


def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


class InternalPolicyAPI:
    # ---- 注册 / 版本链（indexer 摄取即注册；D-03 IPN-hex）----
    def register(self, title, version, file_type, status="draft", source_path=""):
        from internal_policy_base.scan import ipn_of, parse_filename  # noqa: PLC0415
        parsed = parse_filename(os.path.basename(source_path)) if source_path else {"docno": "", "title": title}
        ipn = ipn_of(parsed.get("docno", ""), parsed.get("title") or title)
        return {"ipn": ipn, "status": status, "note": "以 indexer.ingest 为准（本接口幂等返回编号）"}

    def query(self, ipn=None, title=None):
        idx = _load(_INDEX_PATH)
        for r in idx.get("records", []):
            if ipn and r.get("ipn") == ipn:
                return r
            if title and r.get("title") == title:
                return r
        return None

    def version_chain(self, ipn):
        rec = self.query(ipn=ipn)
        return {"ipn": ipn, "chain": [{"ipn": ipn, "title": rec.get("title", "") if rec else "",
                                       "status": rec.get("status", "") if rec else ""}]}

    def get_merged_view(self, view="active"):
        if os.path.exists(_ALIGN_PATH):
            a = _load(_ALIGN_PATH)
            return {"schema_version": MERGED_VIEW_SCHEMA_VERSION, "view": view,
                    "source": "align_result.json", "count": a.get("count", 0),
                    "theme_stat": a.get("theme_stat", {})}
        return {"schema_version": MERGED_VIEW_SCHEMA_VERSION, "view": view,
                "note": "待运行 internal_policy_base/align.py 生成对齐视图"}


_api: InternalPolicyAPI | None = None


def get_internal_policy_api() -> InternalPolicyAPI:
    global _api
    if _api is None:
        _api = InternalPolicyAPI()
    return _api
