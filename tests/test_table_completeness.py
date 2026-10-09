# -*- coding: utf-8 -*-
"""批 51：表格结构化**保完整性**回归守卫 + W-A/W-B/W-D/W-I 接线守卫。

靶心（批 51 评估结论，4 类"静默丢"中的 3 类已修）：
  ⑤ 统计表无指标值的行（名录/分类标题/小计行）被静默丢弃；
  ④ 稀疏指标列被阈值裁剪（值未留痕）；
  ⑥ 无指标回退时 fill==1 的列值丢失。
"""
from __future__ import annotations

import json
import os

import pytest


def _mk_wb(rows: list[list], cols: int = 0):
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    for i, row in enumerate(rows, start=1):
        for j, v in enumerate(row, start=1):
            if v is not None:
                ws.cell(row=i, column=j, value=v)
    if cols:
        ws.cell(row=len(rows) + 1, column=cols, value="")
    return wb


def _bytes(wb) -> bytes:
    import io

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _all_values(node) -> list:
    out = []
    if isinstance(node, dict):
        out.append(node)
        for v in node.values():
            if isinstance(v, (dict, list)):
                out.extend(_all_values(v))
    elif isinstance(node, list):
        for v in node:
            if isinstance(v, (dict, list)):
                out.extend(_all_values(v))
    return out


def test_no_metric_rows_are_kept() -> None:
    """⑤ 名录行/小计行（无指标值）必须保留其维度内容（原实现静默丢弃）。"""
    from std_lib.scraper_std.excel_structure import process_workbook_bytes

    rows = [["地区", "指标A", "指标B", "指标C", "指标D"]]
    for i in range(1, 25):                     # 有指标值行（占比 ≥1% ⇒ any_metric 成立）
        rows.append(["省%d" % i, i * 10, i * 20, i * 3, i * 4])
    rows.append(["甲类小计", 900, 1800, 270, 360])   # 小计行（指标有值）
    rows.append(["乙类", None, None, None, None])    # 分类标题行（**无指标值** ⇒ 原实现被丢）
    for i in range(25, 31):
        rows.append(["乙类省%d" % i, i * 10, i * 20, i * 3, i * 4])
    out = process_workbook_bytes(_bytes(_mk_wb(rows)), "t.xlsx")
    blob = json.dumps(out, ensure_ascii=False)
    assert "乙类" in blob, "无指标值的分类标题行（仅维度值）不得被静默丢弃（靶心⑤）"
    assert "甲类小计" in blob
    sheets = out.get("sheets") or []
    stat = [s for s in sheets if s.get("type") == "统计表"]
    if stat and "metric_row_count" in (out.get("meta") or {}):
        assert out["meta"]["metric_row_count"] >= 24, "指标行数须单独计数（可审计）"


def test_sparse_metric_columns_values_retained() -> None:
    """④ 宽表稀疏指标列：被裁列的有值单元格须留痕于 meta.sparse_metric_values。"""
    from std_lib.scraper_std.excel_structure import process_workbook_bytes

    head = ["地区"] + ["指标%d" % i for i in range(1, 41)]   # 41 列 ⇒ 走大表阈值分支
    rows = [head]
    for i in range(1, 21):
        rows.append(["省%d" % i] + [i * 10] * 39)             # 前 39 个指标填满
    rows.append(["省稀有", 999] + [None] * 39)                 # 第 2 个指标仅此一处有值 ⇒ 稀疏
    out = process_workbook_bytes(_bytes(_mk_wb(rows)), "wide.xlsx")
    blob = json.dumps(out, ensure_ascii=False)
    meta = out.get("meta") or {}
    sparse = meta.get("sparse_metric_values") or {}
    assert 999 in [_all_values(v) for v in [sparse]][0] or "999" in blob, \
        "被裁稀疏列的有值单元格不得丢失（需出现在 sparse_metric_values 或输出中）"


def test_no_metric_fallback_keeps_fill1_columns() -> None:
    """⑥ 无指标回退：fill==1 的列（孤值）必须保留（原为 fill≥2 ⇒ 静默丢）。"""
    from std_lib.scraper_std.excel_structure import process_workbook_bytes

    rows = [["名称", "孤列"], ["甲", "唯一值"], ["乙", None], ["丙", None]]
    out = process_workbook_bytes(_bytes(_mk_wb(rows)), "fallback.xlsx")
    assert "唯一值" in json.dumps(out, ensure_ascii=False)


def test_projection_is_compact() -> None:
    """⑤ 紧凑投影：空值键省略 + 不裁行（直测 `_build_stat_unit`，避免分类启发式干扰）。"""
    from types import SimpleNamespace as NS

    from std_lib.scraper_std import excel_structure as es

    cols = [NS(key="region", name="地区"), NS(key="m1", name="指标A"), NS(key="m2", name="指标B")]
    rows = [{"region": "省%d" % i, "m1": i * 10} for i in range(1, 30)]   # m2 全空（稀疏列）
    rows.append({"region": "乙类"})                                        # 无指标值行
    block = NS(columns=cols, rows=rows, meta={}, table_id="t1")
    unit = es._build_stat_unit(block, "S", 1)
    proj = unit["rows"]
    assert len(proj) == len(rows), "不得裁行（靶心⑤：无指标值行须保留）"
    assert all(v is not None for r in proj for v in r.values()), "空值键应省略（紧凑投影）"


def test_legacy_xlsx_path_bounded(tmp_path) -> None:
    """⑮ `_tables_from_xlsx`（.xlsm/.xlsb 等无分类路径）须按内容裁剪、不得按声明维度展开。"""
    from std_lib.scraper_std.table_recovery import _tables_from_xlsx

    wb = _mk_wb([["序号", "名称"], [1, "药品甲"]], cols=600)
    tables, _notes = _tables_from_xlsx(_bytes(wb))
    text = "|".join("|".join(c for c in r) for t in tables for r in t)
    assert "药品甲" in text and len(text) < 200, "远端空单元格不得撑大输出"


# --------------------------------------------------------------------------- #
# 接线守卫（W-A / W-B / W-D / W-I）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("rel,needle", [
    ("modules/regulatory_scrapers/collectors/gov_collector.py", 'd["attachment_text"] = att_text'),
    ("modules/regulatory_scrapers/collectors/gov_zhengceku.py", '"attachment_text": att_text'),
])
def test_w_a_raw_writers_do_not_duplicate(rel: str, needle: str) -> None:
    """W-A：gov 写侧不得再写记录级聚合副本（attachments[].text 为唯一权威）。

    注：异常/空分支里的 `attachment_text: ""` 属正常占位，不在守卫范围（只禁"聚合正文"赋值）。
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, rel), encoding="utf-8").read()
    assert needle not in src, "%s 仍写记录级聚合副本（W-A 回归）" % rel
    assert "attachment_count" in src


def test_w_a_mapping_derives_from_attachments() -> None:
    """W-A：cleaned 侧须按**真附件**派生 attachment_content。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "std_lib/scraper_std/unified_schema.py"), encoding="utf-8").read()
    assert src.count('for a in (rec.get("attachments") or [])') >= 3, "导出路径未接 attachments 派生"


def test_w_b_sidecar_wired() -> None:
    """W-B：记录级外置须接入交付链（pipeline），且有可用的阈值语义。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    at = open(os.path.join(root, "std_lib/scraper_std/attachments.py"), encoding="utf-8").read()
    pl = open(os.path.join(root, "std_lib/scraper_std/pipeline.py"), encoding="utf-8").read()
    assert "def sidecar_record_content(" in at and "attachment_content_path" in at
    assert "sidecar_record_content" in pl, "pipeline 未接线外置（W-B 回归）"


def test_w_b_sidecar_unit(tmp_path) -> None:
    """W-B：超阈值 ⇒ 落 .txt + 主数据留路径/hash；未超阈值 ⇒ 原样返回。"""
    from std_lib.scraper_std.attachments import sidecar_record_content

    small = {"attachment_content": "短文本"}
    assert sidecar_record_content(small, str(tmp_path))["attachment_content"] == "短文本"
    big = {"attachment_content": "长" * 300, "_metadata": {}}
    out = sidecar_record_content(big, str(tmp_path), threshold=200)
    assert out["attachment_content"] == "" and out["attachment_content_path"].endswith(".txt")
    p = out["attachment_content_path"]
    assert os.path.exists(p) and open(p, encoding="utf-8").read() == "长" * 300
    assert out["_metadata"]["attachment_content_sidecar"] is True


def test_w_d_pbc_serve_zero_disk() -> None:
    """W-D：pbc_serve 预览须内存响应（不得在预览目录落盘 .json 视图）。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "modules/regulatory_scrapers/collectors/pbc_serve.py"),
               encoding="utf-8").read()
    assert "def send_head(" in src and "BytesIO" in src
    assert 'open(r, "w"' not in src, "translate_path 仍在落盘生成 .json 视图（W-D 回归）"


def test_w_i_supp_multiformat() -> None:
    """W-I：supp 抽取须支持 PDF 之外格式（委托共享抽取器）。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "modules/regulatory_scrapers/collectors/supp_parser.py"),
               encoding="utf-8").read()
    assert "crawler_common import extract_document_text as _shared_extract" in src


def test_w_i_supp_extracts_docx_and_xlsx() -> None:
    """W-I 实证：docx 表格与 xlsx 单元格文本可被 supp 抽取器取出。"""
    import io as _io

    import openpyxl
    from docx import Document

    import bootstrap

    bootstrap.bootstrap("all")
    from modules.regulatory_scrapers.collectors.supp_parser import extract_document_text

    doc = Document()
    doc.add_paragraph("附件正文：续保表述要求")
    t = doc.add_table(rows=1, cols=2)
    t.rows[0].cells[0].text = "项目"
    t.rows[0].cells[1].text = "要求"
    buf = _io.BytesIO()
    doc.save(buf)
    r1 = extract_document_text(buf.getvalue(), "a.docx", enable_ocr=False)
    assert "续保表述要求" in (r1.get("text") or "")

    wb = openpyxl.Workbook()
    wb.active["A1"] = "药品儿童专用清单"
    b2 = _io.BytesIO()
    wb.save(b2)
    r2 = extract_document_text(b2.getvalue(), "a.xlsx", enable_ocr=False)
    assert "药品儿童专用清单" in (r2.get("text") or "")


# --------------------------------------------------------------------------- #
# 批 52：W-L 纳入治理（xlsx 逐行读取唯一共享原语）+ W-N ③⑧ 可审计化
# --------------------------------------------------------------------------- #
def test_w_l_xlsx_row_iteration_is_shared() -> None:
    """W-L：两处非五源工具不得自行遍历声明维度，一律经共享原语 `iter_ws_text_rows`。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for rel in ("tools/build_east_backlog.py",
                "modules/internal_policy_drafter/scripts/dump_related_systems.py"):
        text = open(os.path.join(root, rel), encoding="utf-8").read()
        assert "iter_ws_text_rows" in text, "%s 未接共享原语（W-L 回归）" % rel
        assert ".iter_rows(" not in text, "%s 仍在自查遍历声明维度（W-L 回归）" % rel


def test_shared_iterator_single_source() -> None:
    """DRY：`iter_ws_text_rows` 为逐行读取唯一实现（≥3 处在用）。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    users = 0
    for rel in ("std_lib/scraper_std/crawler_common.py", "std_lib/scraper_std/table_recovery.py",
                "tools/build_east_backlog.py",
                "modules/internal_policy_drafter/scripts/dump_related_systems.py"):
        users += open(os.path.join(root, rel), encoding="utf-8").read().count("iter_ws_text_rows")
    assert users >= 4, "共享原语采纳度不足（实际 %d）" % users


def test_w_n_formula_cells_auditable() -> None:
    """W-N⑧：公式单元格计数可审计；共享原语不按声明维度展开（W-L/⑮ 共同基础）。"""
    import io as _io

    import openpyxl

    from std_lib.scraper_std.crawler_common import count_xlsx_formula_cells

    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"], ws["B1"], ws["C1"] = 2, 3, "=A1+B1"
    buf = _io.BytesIO()
    wb.save(buf)
    assert count_xlsx_formula_cells(buf.getvalue()) >= 1
    assert count_xlsx_formula_cells(b"not-a-zip") == 0
    # 共享原语：声明维度虚高的小表仍只产出内容行（W-L/⑮ 共同基础）
    wb2 = openpyxl.Workbook()
    wb2.active["A1"] = "值"
    wb2.active.cell(row=1, column=300, value="")
    b2 = _io.BytesIO()
    wb2.save(b2)
    from std_lib.scraper_std.crawler_common import iter_xlsx_text_rows

    rows = list(iter_xlsx_text_rows(b2.getvalue()))
    assert rows and len("|".join(rows[0])) < 40, "声明维度不得撑大输出"


def test_w_n_truncation_notes_api() -> None:
    """W-N③：矩阵截断落备注（`excel_matrix.pop_read_notes` 存在且为列表）。"""
    from std_lib.scraper_std import excel_matrix as em

    assert isinstance(em.pop_read_notes(), list)
