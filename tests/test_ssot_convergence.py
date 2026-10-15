# -*- coding: utf-8 -*-
"""test_ssot_convergence.py —— SSOT 收敛专项基线（F-D06/D07/D10，2026-09-13）。

覆盖：
  - F-D10 clause 行契约：CLAUSE_LINE_FIELDS 含 rfn/timeliness_status/publish_date/effective_date；
    clause_index._load_rfn_bridge 桥表只读加载（降级安全）。
  - F-D07 附件契约：attachment_view 别名覆盖五源命名；record_attachment_views 嵌套/扁平(pbc)/空三态。
  - F-D06 归一基线：norm_docno SSOT 语义；日期分层（cleaner 解析层 vs crawler_common 抽取型）兼容。

纪律：只调用只读/纯函数（不触 build/validate/save/rotate——见 test_misc_pure 教训）。
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "std_lib"),
          os.path.join(ROOT, "modules", "regulatory_scrapers")):
    if p not in sys.path:
        sys.path.insert(0, p)

from scraper_std import cleaner, crawler_common

from interfaces import contract


class TestClauseLineContract:
    def test_fd10_dimensions_present(self):
        fields = set(contract.CLAUSE_LINE_FIELDS)
        for k in ("rfn", "timeliness_status", "publish_date", "effective_date"):
            assert k in fields, k

    def test_rfn_bridge_load_readonly(self):
        import clause_index
        by_dk, by_url = clause_index._load_rfn_bridge()
        assert isinstance(by_dk, dict) and isinstance(by_url, dict)


class TestAttachmentContract:
    def test_alias_covers_five_source_namings(self):
        # gov：file_name/kind（attachment_kind 别名）
        gov = contract.attachment_view({"file_name": "a.docx", "attachment_kind": "docx"})
        assert gov["file_name"] == "a.docx" and gov["kind"] == "docx"
        # mof：attachment_name→file_name；file_type→kind；size_bytes→bytes
        mof = contract.attachment_view({"attachment_name": "b.pdf", "file_type": "pdf", "size_bytes": 5})
        assert mof["file_name"] == "b.pdf" and mof["kind"] == "pdf" and mof["bytes"] == 5
        # nfra：hash 名 + char_count→text_len
        nfra = contract.attachment_view({"attachment_name": "c", "char_count": 88, "url": "http://x"})
        assert nfra["file_name"] == "c" and nfra["text_len"] == 88 and nfra["url"] == "http://x"
        # supp：name→file_name；content_ref→local_path
        supp = contract.attachment_view({"name": "d", "kind": "页面内嵌表格",
                                         "content_ref": "data/raw/_sources/F9_attachment.txt"})
        assert supp["file_name"] == "d" and supp["kind"] == "页面内嵌表格"
        assert str(supp["local_path"]).endswith("F9_attachment.txt")

    def test_record_views_nested(self):
        rec = {"attachments": [{"file_name": "x.pdf", "kind": "pdf"}]}
        views = contract.record_attachment_views(rec)
        assert len(views) == 1 and views[0]["file_name"] == "x.pdf"

    def test_record_views_pbc_flat(self):
        rec = {"link_type": "attachment", "title": "附件1", "file_type": "doc",
               "local_path": "data/docs/pbc/a.doc", "detail_url": "http://pbc/x.doc",
               "content": "正文" * 10}
        views = contract.record_attachment_views(rec)
        assert len(views) == 1
        v = views[0]
        assert v["file_name"] == "附件1" and v["kind"] == "doc"
        assert str(v["local_path"]).endswith("a.doc") and v["text_len"] == 20

    def test_record_views_none(self):
        assert contract.record_attachment_views({"title": "普通文件"}) == []
        assert contract.record_attachment_views({}) == []


class TestDateLayeringAndNorm:
    def test_cleaner_parses_four_formats(self):
        for raw in ("2024-01-02", "2024年1月2日", "20240102", "2024/01/02"):
            assert cleaner.normalize_date(raw) == "2024-01-02", raw

    def test_crawler_extracts_from_text(self):
        assert crawler_common.normalize_date("发布时间：2024年1月2日") == "2024-01-02"
        assert crawler_common.normalize_date("2024-01-02") == "2024-01-02"
        assert crawler_common.normalize_date("2024/1/2") == "2024-01-02"
        assert crawler_common.normalize_date("无日期文本") == ""

    def test_norm_docno_ssot_baseline(self):
        from common_lib.norm import norm_docno
        assert norm_docno("银保监办发〔2019〕19号") == "银保监办发201919"
        assert norm_docno("N/A") == ""


class TestW4PrimitiveConvergence:
    """W4-a（2026-10）B2~B7 原语收敛对等测试（防「合并即改行为」静默回归）。"""

    def test_sha256_bytes_matches_legacy_sha256_of(self):
        """B2：SSOT sha256_bytes 与被删的 sha256_of 语义一致（同字节同摘要）。"""
        import hashlib

        from common_lib.io_atomic import sha256_bytes
        for data in (b"", b"hello", "中文附件".encode(), bytes(range(256))):
            assert sha256_bytes(data) == hashlib.sha256(data).hexdigest(), data[:16]

    def test_sha256_file_matches_legacy_stream(self, tmp_path):
        """B2：SSOT sha256_file 与被删的 inbox_scan.sha256_of 语义一致（流式分块）。"""
        import hashlib

        from common_lib.io_atomic import sha256_file
        p = tmp_path / "blob.bin"
        p.write_bytes(bytes(range(256)) * 5000)  # >1MB：跨分块
        assert sha256_file(str(p)) == hashlib.sha256(p.read_bytes()).hexdigest()

    def test_cache_store_compact_json_write(self, tmp_path):
        """B7：cache_store 经 SSOT 薄壳输出**compact**（与原 _atomic_write_json 逐字节一致）。"""
        import json

        from scraper_std.cache_store import _compact_json_write
        p = tmp_path / "sub" / "m.json"  # 目录不存在：SSOT makedirs 兼容
        obj = {"b": [1, 2], "a": "中文", "n": None}
        _compact_json_write(str(p), obj)
        raw = p.read_text(encoding="utf-8")
        assert raw == json.dumps(obj, ensure_ascii=False), "compact 形态漂移"
        assert not (tmp_path / "sub" / "m.json.tmp").exists(), "临时文件残留"

    def test_mof_collector_status_write_via_ssot(self):
        """B7：mof_collector 状态/JSON/CSV/报告写入口全部解析到 fs_lock.atomic_write_text。

        读文件而非 import 模块——mof_collector 加载有 sys.path 引导副作用，
        本断言只关静态形态（无 `_atomic_write(` 私有调用残留）。
        """
        p = os.path.join(
            ROOT, "modules", "regulatory_scrapers", "collectors", "mof", "collector.py"
        )
        src = open(p, encoding="utf-8").read()
        residue = src.replace("fs_lock.atomic_write_text(", "")
        assert "_atomic_write(" not in residue, "仍存在 _atomic_write 私有调用（应走 SSOT）"
        assert "fs_lock.atomic_write_text(" in src

    def test_date_layering_three_types_distinct(self):
        """B3：日期三层契约互斥——解析层保守保留 / 抽取型空串 / 字段规范化 None。"""
        # 解析层：无法解析 → 原样保留（保守）
        assert cleaner.normalize_date("待定") == "待定"
        # 抽取型：无法识别 → 空串
        assert crawler_common.normalize_date("待定") == ""
        # 字段规范化型：None/空 → None；list → YYYY-MM-DD
        from collectors.mof.attachments import normalize_date as field_norm
        assert field_norm(None) is None
        assert field_norm([2024, 11, 3, 0, 0]) == "2024-11-03"
        assert field_norm("2024-11-03 00:00:00") == "2024-11-03"


class TestW4DupPrimitiveGate:
    """W4-a 门禁判据测试（§7.6.1 私有名模式堵漏 + 共享库内分裂检测）。"""

    def _gate(self):
        from gates import gate_no_duplicate_libs as g
        return g

    def test_gate_passes_current_tree(self):
        g = self._gate()
        ok, det = g.run()
        assert ok, det["problems"]

    def test_private_and_public_both_scanned(self):
        """公开+私有双形态入扫描（私有名不再是规避通道）。"""
        g = self._gate()
        assert g.PAT_DUP_PRIMITIVE.search("def _norm_docno(x):")
        assert g.PAT_DUP_PRIMITIVE.search("def norm_docno(x):")
        assert g.PAT_DUP_PRIMITIVE.search("def sha256_of(data):")
        assert g.PAT_DUP_PRIMITIVE.search("def write_jsonl(p, rows):")
        assert g.PAT_DUP_PRIMITIVE.search("def normalize_date(v):")
        assert not g.PAT_DUP_PRIMITIVE.search("def unrelated(x):")

    def test_unmarked_foreign_def_fails(self, tmp_path, monkeypatch):
        """负例：非共享库无标记副本 → FAIL（防增量）。"""
        g = self._gate()
        bad = tmp_path / "modules" / "x" / "y.py"
        bad.parent.mkdir(parents=True)
        bad.write_text("def norm_docno(s):\n    return s\n", encoding="utf-8")

        def fake_walk():
            return [str(bad)]

        # _rel 以 tmp_path 为基准（tmp_path 可能在异盘，直接对 ROOT 取相对路径会 ValueError）
        monkeypatch.setattr(g, "_walk_py", fake_walk)
        monkeypatch.setattr(g, "_rel", lambda p: os.path.relpath(p, tmp_path))
        ok, det = g.run()
        assert not ok
        assert any("norm_docno" in p for p in det["problems"]), det["problems"]

    def test_marked_foreign_def_passes(self, tmp_path, monkeypatch):
        """正例：带 `# norm-specialization` 标记的特化存活。"""
        g = self._gate()
        bad = tmp_path / "modules" / "x" / "y.py"
        bad.parent.mkdir(parents=True)
        bad.write_text(
            "# norm-specialization: 测试特化\ndef norm_docno(s):\n    return s\n",
            encoding="utf-8",
        )

        def fake_walk():
            return [str(bad)]

        monkeypatch.setattr(g, "_walk_py", fake_walk)
        monkeypatch.setattr(g, "_rel", lambda p: os.path.relpath(p, tmp_path))
        ok, det = g.run()
        assert ok, det["problems"]

    def test_shared_library_split_fails(self, tmp_path, monkeypatch):
        """负例：共享库内同符号两文件定义且无一标记 → FAIL（B2/B7 消灭的现象复发即报）。"""
        g = self._gate()
        d = tmp_path / "std_lib" / "scraper_std"
        d.mkdir(parents=True)
        (d / "a.py").write_text("def sha256_of(x):\n    return x\n", encoding="utf-8")
        (d / "b.py").write_text("def sha256_of(x):\n    return x\n", encoding="utf-8")

        def fake_walk():
            return [str(d / "a.py"), str(d / "b.py")]

        monkeypatch.setattr(g, "_walk_py", fake_walk)
        monkeypatch.setattr(g, "_rel", lambda p: os.path.relpath(p, tmp_path))
        ok, det = g.run()
        assert not ok
        assert any("sha256_of" in p and "共享库" in p for p in det["problems"]), det["problems"]


class TestCrossSourceNoAbsorb:
    """时效链跨源吸附防护（2026-09-13 续跑治理）：delta.source 明确且 ≠ 命中条目 source
    → 视为未匹配走新增（多源条目并存），不再把本源核验结论吸附到他源条目。"""

    def _load(self):
        import sys as _s
        p = os.path.join(ROOT, "modules", "regulatory_scrapers", "timeliness_review")
        if p not in _s.path:
            _s.path.insert(0, p)
        import consolidate_timeliness as ct
        return ct

    def _delta(self, source):
        return {"ledger_name": "t_20260913.csv", "ledger_date": "20260913", "kind": "change",
                "source": source, "title": "防范和处置非法集资条例",
                "document_number": "中华人民共和国国务院令第737号",
                "publish_date": "2021-01-26", "new_status": "valid",
                "new_source": "北大法宝", "replacement_document": "", "note": ""}

    def _acc_with_nfra_valid(self, ct):
        acc = ct.Accumulator()
        acc.add({"source": "nfra", "title": "防范和处置非法集资条例",
                 "document_number": "N/A", "publish_date": "2021-02-11",
                 "timeliness_status": "valid", "verification_source": "北大法宝",
                 "change_trace": [], "_quality": 3})
        return acc

    def test_cross_source_delta_added_not_absorbed(self):
        ct = self._load()
        acc = self._acc_with_nfra_valid(ct)
        stats = {"added": 0, "confirmed": 0, "applied": 0, "skipped_downgrade": 0}
        ct.apply_delta(acc, self._delta("gov"), stats, [])
        assert stats["added"] == 1
        govs = [r for r in acc.records if r.get("source") == "gov"]
        assert len(govs) == 1 and govs[0]["document_number"] == "中华人民共和国国务院令第737号"
        nf = [r for r in acc.records if r.get("source") == "nfra"]
        assert len(nf) == 1 and nf[0]["document_number"] == "N/A", "nfra 条目不应被吸附污染"

    def test_same_source_still_confirms(self):
        ct = self._load()
        acc = self._acc_with_nfra_valid(ct)
        stats = {"added": 0, "confirmed": 0, "applied": 0, "skipped_downgrade": 0}
        ct.apply_delta(acc, self._delta("nfra"), stats, [])
        assert stats["added"] == 0, "同源单义标题应正常命中"
        assert stats["confirmed"] == 1
