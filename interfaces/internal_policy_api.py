# -*- coding: utf-8 -*-
"""
interfaces/internal_policy_api — 内部制度唯一访问接口（D-03 IPN 独立体系）

P0：占位壳；P6（internal_policy_base indexer/aligner）实现。
- register/query/version_chain：IPN 编号、版本链、废止关系。
- merged_view：制度×监管主题/RFN 对齐视图（D-06 预留 schema，P7 由 drafter 消费）。
"""
from __future__ import annotations

# merged_view schema 版本（D-06：二期 app 读取前冻结）
MERGED_VIEW_SCHEMA_VERSION = "1.0"
# UNALIGNED 桶（R18：内部制度无对应监管主题时保留于此，不进主视图）
UNALIGNED_BUCKET = "UNALIGNED"


class InternalPolicyAPI:
    def register(self, title, version, file_type, status="draft", source_path=""):  # pragma: no cover
        raise NotImplementedError("P6 internal indexer 接入后实现（返回 IPN-hex）")

    def query(self, ipn=None, title=None):  # pragma: no cover
        raise NotImplementedError("P6 接入后实现")

    def version_chain(self, ipn):  # pragma: no cover
        raise NotImplementedError("P6 接入后实现")

    def get_merged_view(self, view="active"):  # pragma: no cover
        raise NotImplementedError("P7 merged_view 生成后实现（view: active|full_historical）")


_api: InternalPolicyAPI | None = None


def get_internal_policy_api() -> InternalPolicyAPI:
    global _api
    if _api is None:
        _api = InternalPolicyAPI()
    return _api
