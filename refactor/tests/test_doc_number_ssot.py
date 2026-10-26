# -*- coding: utf-8 -*-
"""doc_number 单一事实源（P0 回归锁）。

锁定 2026-10-13 收敛动作，防止再度漂移：
  - SSOT：clean.core.doc_number / collectors.lib.doc_number 不得各自实现，
    必须再导出 common.doc_number（三者 normalize_doc_number 同一对象）。
  - canonical.canonicalize 落库时即规范化 document_number（不再留未规范化原文）。
  - 等价链：raw → canonicalize → clean.schema._resolve_doc_number 得到的
    document_number 必须一致（P0 真问题：规范记录未规范化 / 清洗才规范化 的分裂）。

不触网、不落盘，纯函数级断言。
"""
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))  # 包根 refactor/

# 强制 file 后端；若本环境 REFACTOR_STORAGE=postgres 会强制 sqlalchemy，桩入占位。
os.environ["REFACTOR_STORAGE"] = "file"
if "collectors.storage.sql_backend" not in sys.modules:
    _stub = types.ModuleType("collectors.storage.sql_backend")
    _stub.SqlStore = object
    sys.modules["collectors.storage.sql_backend"] = _stub

from collectors.lib.doc_number import normalize_doc_number as norm_collectors
from collectors.storage.canonical import canonicalize
from clean.core.doc_number import normalize_doc_number as norm_clean
from clean.schema import _resolve_doc_number
from common.doc_number import normalize_doc_number as norm_common


def test_ssot_single_implementation():
    """三处 doc_number 必须指向同一函数对象（无 near-identical 副本漂移）。"""
    assert norm_clean is norm_common, "clean.core.doc_number 未收敛到 common"
    assert norm_collectors is norm_common, "collectors.lib.doc_number 未收敛到 common"
    print("test_ssot_single_implementation OK")


def test_canonicalize_normalizes_document_number():
    """规范记录 落库即规范化：半角 [ ] → 全角〔〕，且去头部残渣。"""
    cases = [
        ("金规[2026]4号", "金规〔2026〕4号"),
        ("银发[1999]407号", "银发〔1999〕407号"),
        ("金金金…财会[2026]12号", "财会〔2026〕12号"),
    ]
    for raw_dn, expect in cases:
        env = canonicalize("pbc", {"document_number": raw_dn, "title": "t", "content": "c"})
        assert env["document_number"] == expect, (raw_dn, env["document_number"])
    # document_number 字段优先级：document_number 优先于 doc_no/doc_no
    env = canonicalize("pbc", {"document_number": "国办发[2013]87号", "doc_no": "X"})
    assert env["document_number"] == "国办发〔2013〕87号"
    print("test_canonicalize_normalizes_document_number OK")


def test_raw_canonicalize_resolve_equivalence():
    """P0 核心锁：raw → canonicalize → _resolve_doc_number 等价（不再分裂）。"""
    cases = [
        {"document_number": "金规[2026]4号"},
        {"document_number": "银发[1999]407号"},
        {"document_number": "金金金…财会[2026]12号"},
        {"document_number": "国办发[2013]87号", "doc_no": "IGNORED"},
    ]
    for raw in cases:
        rec = dict(raw)
        rec.setdefault("title", "标题")
        rec.setdefault("content", "正文")
        env = canonicalize("pbc", rec)
        resolved = _resolve_doc_number(env)
        assert env["document_number"] == resolved, (
            f"规范记录与清洗侧 document_number 不一致: "
            f"规范记录={env['document_number']!r} vs resolve={resolved!r} (raw={raw})"
        )
    print("test_raw_canonicalize_resolve_equivalence OK")


def test_normalize_idempotent_and_safe():
    """normalize_doc_number 幂等、空值安全、已规范化串不变。"""
    assert norm_common("金规[2026]4号") == norm_common(norm_common("金规[2026]4号"))
    assert norm_common("") == ""
    assert norm_common("国办发〔2013〕87号") == "国办发〔2013〕87号"
    print("test_normalize_idempotent_and_safe OK")


if __name__ == "__main__":
    test_ssot_single_implementation()
    test_canonicalize_normalizes_document_number()
    test_raw_canonicalize_resolve_equivalence()
    test_normalize_idempotent_and_safe()
    print("ALL DOC_NUMBER SSOT TESTS PASSED")
