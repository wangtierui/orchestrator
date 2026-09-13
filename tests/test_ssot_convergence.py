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

from scraper_std import cleaner, crawler_common  # noqa: E402

from interfaces import contract  # noqa: E402


class TestClauseLineContract:
    def test_fd10_dimensions_present(self):
        fields = set(contract.CLAUSE_LINE_FIELDS)
        for k in ("rfn", "timeliness_status", "publish_date", "effective_date"):
            assert k in fields, k

    def test_rfn_bridge_load_readonly(self):
        import clause_index  # noqa: PLC0415
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
        from common_lib.norm import norm_docno  # noqa: PLC0415
        assert norm_docno("银保监办发〔2019〕19号") == "银保监办发201919"
        assert norm_docno("N/A") == ""


class TestCrossSourceNoAbsorb:
    """时效链跨源吸附防护（2026-09-13 续跑治理）：delta.source 明确且 ≠ 命中条目 source
    → 视为未匹配走新增（多源条目并存），不再把本源核验结论吸附到他源条目。"""

    def _load(self):
        import sys as _s  # noqa: PLC0415
        p = os.path.join(ROOT, "modules", "regulatory_scrapers", "timeliness_review")
        if p not in _s.path:
            _s.path.insert(0, p)
        import consolidate_timeliness as ct  # noqa: PLC0415
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
