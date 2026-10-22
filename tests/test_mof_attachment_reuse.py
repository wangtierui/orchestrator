# -*- coding: utf-8 -*-
"""tests/test_mof_attachment_reuse.py — 附件**抽取复用**守卫（第一百零七批 T4）。

根因（2026-10-22 实测定位）
--------------------------
`collect:mof` 一轮 **13113 s（3.64 h）**、峰值内存 **6.26 GB**。逐环节核对后确认：
瓶颈**不是网络**，而是 `download_attachment` 的"本地已有副本"分支——

```python
# 原实现（注释写"仅本地重抽"）
if os.path.exists(dest) and os.path.getsize(dest) > 0:
    return _finalize_attachment(att, open(dest, "rb").read(), ...)   # ← 每次都重抽
```

即：**记录一变（哪怕只是生效日期），它的全部附件都会被重新解析**。
一轮 182 条变更 × 约 5 个附件 ≈ **900 次 PDF/表格解析**
（`extract_document_text` + `structured_table_fields` + `rich_object_fields`）
⇒ 3.64 h；大表格 PDF 的解析峰值内存即 6.26 GB（同一根因，TD-17）。

修法
----
本地副本**未变**（磁盘大小 == 历史 `size_bytes`）且 `extract_version` 一致时，
**整段复用**历史抽取结果，跳过解析。安全条件三条缺一不可。

守卫五组
--------
A 条件全满足 ⇒ 复用，且 `extract_document_text` **不被调用**
B 文件大小变了 ⇒ **必须重抽**（不能拿旧文本冒充新文件）
C `extract_version` 不匹配 ⇒ **必须重抽**（抽取逻辑改过，旧产物不可信）
D 无历史记录 ⇒ 正常重抽（首轮行为不变）
E 接线：`collect_attachments` 与 collector 必须真的把历史附件传下去
"""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.contract

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COLL = os.path.join(ROOT, "modules", "regulatory_scrapers", "collectors", "mof",
                    "collector.py")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """附件目录指到 tmp，并拦截三个抽取函数（计数 + 假结果）。"""
    from modules.regulatory_scrapers.collectors.mof import attachments as A

    calls = {"n": 0}

    def _fake_extract(data, fname):
        calls["n"] += 1
        return {"text": f"EXTRACTED-{len(data)}", "extracted": True,
                "extract_status": "ok", "kind": "pdf", "sha256": "deadbeef"}

    monkeypatch.setattr(A, "extract_document_text", _fake_extract)
    monkeypatch.setattr(A, "structured_table_fields", lambda d, f: {})
    monkeypatch.setattr(A, "rich_object_fields", lambda d, f, **kw: {})
    monkeypatch.setattr(A, "ATTACHMENTS_DIR", str(tmp_path))
    monkeypatch.setattr(A, "REPO_ROOT", str(tmp_path.parent))
    A.REUSE_STATS["reused"] = 0
    A.REUSE_STATS["extracted"] = 0
    return A, calls, tmp_path


def _att(**kw):
    base = {"file_name": "a.pdf", "file_type": "0", "extension": ".pdf",
            "file_url": "http://example.invalid/a.pdf"}
    base.update(kw)
    return base


def _seed_prev(A, tmp, law_id: int = 42, data: bytes = b"%PDF-1.4 seed"):
    """模拟**上一轮已落盘**：直接调 `_finalize_attachment`（它会写文件 + 盖章）。"""
    d = os.path.join(str(tmp), str(law_id))
    os.makedirs(d, exist_ok=True)
    fname = A.safe_filename("a.pdf", ".pdf", f"{law_id}_0")
    dest = os.path.join(d, fname)
    rec = A._finalize_attachment(_att(), data, fname, law_id, dest)
    return rec, dest


class TestReuseHit:
    """A. 三条安全条件全满足 ⇒ 复用且不解析。"""

    def test_reuse_skips_extraction(self, env) -> None:
        A, calls, tmp = env
        prev, _dest = _seed_prev(A, tmp)
        assert calls["n"] == 1, "首轮应解析一次"
        assert prev["extract_version"] == A.EXTRACT_VERSION
        _n_before = calls["n"]

        r = A.download_attachment(_att(), 42, str(tmp), rate=None, prev_att=prev)
        assert r is not None
        assert r["extract_reused"] is True, "未复用（会退回每轮重解析）"
        assert calls["n"] == _n_before, f"复用路径仍解析了（{calls['n']}）"
        assert r["text"] == prev["text"], "复用未带回历史抽取文本"
        assert A.REUSE_STATS["reused"] == 1

    def test_reuse_preserves_table_and_rich_fields(self, env) -> None:
        A, _calls, tmp = env
        prev, _ = _seed_prev(A, tmp, law_id=7)
        prev["table_structured"] = [{"k": 1}]
        prev["table_recovery_method"] = "excel_classified_v1"
        prev["role"] = "attachment"
        r = A.download_attachment(_att(), 7, str(tmp), rate=None, prev_att=prev)
        assert r["table_structured"] == [{"k": 1}]
        assert r["table_recovery_method"] == "excel_classified_v1"
        assert r["role"] == "attachment"


class TestMustReExtract:
    """B/C/D. 任一条件不满足都必须重抽。"""

    def test_size_changed_reextracts(self, env) -> None:
        A, calls, tmp = env
        prev, dest = _seed_prev(A, tmp, law_id=9)
        with open(dest, "ab") as f:      # 篡改磁盘文件 ⇒ 大小变
            f.write(b"<extra>")
        n0 = calls["n"]
        r = A.download_attachment(_att(), 9, str(tmp), rate=None, prev_att=prev)
        assert r["extract_reused"] is False, "文件已变却仍复用（会保留旧正文）"
        assert calls["n"] == n0 + 1

    def test_version_mismatch_reextracts(self, env) -> None:
        A, calls, tmp = env
        prev, _ = _seed_prev(A, tmp, law_id=11)
        prev["extract_version"] = "v0-old"
        n0 = calls["n"]
        r = A.download_attachment(_att(), 11, str(tmp), rate=None, prev_att=prev)
        assert r["extract_reused"] is False, (
            "抽取版本不同却复用 ⇒ 抽取逻辑升级后仍用旧口径产物（静默劣化）")
        assert calls["n"] == n0 + 1

    def test_no_prev_reextracts(self, env) -> None:
        A, calls, tmp = env
        _seed_prev(A, tmp, law_id=13)
        n0 = calls["n"]
        r = A.download_attachment(_att(), 13, str(tmp), rate=None, prev_att=None)
        assert r["extract_reused"] is False
        assert calls["n"] == n0 + 1


class TestWiring:
    """E. 接线：必须真的把历史附件传下去，否则复用永不命中。"""

    def test_collect_passes_prev_atts(self) -> None:
        src = open(os.path.join(
            ROOT, "modules/regulatory_scrapers/collectors/mof/attachments.py"),
            encoding="utf-8").read()
        assert "prev_atts" in src, "collect_attachments 未接收历史附件"
        assert "_att_key(" in src, "未按身份键匹配历史附件"
        coll = open(COLL, encoding="utf-8").read()
        assert 'prev_atts=(prev_entry or {}).get("attachments")' in coll, (
            "collector 未传历史 attachments ⇒ 复用永不生效")

    def test_stats_exposed_in_status(self) -> None:
        coll = open(COLL, encoding="utf-8").read()
        assert "attachments_reused" in coll and "attachments_extracted" in coll, (
            "status 未披露复用/重抽计数（优化不可辨）")

    def test_extract_version_is_stamped(self) -> None:
        src = open(os.path.join(
            ROOT, "modules/regulatory_scrapers/collectors/mof/attachments.py"),
            encoding="utf-8").read()
        assert 'att["extract_version"] = EXTRACT_VERSION' in src, (
            "抽取结果未盖版本号 ⇒ 复用判据恒失效（等于没优化）")
