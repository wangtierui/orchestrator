# -*- coding: utf-8 -*-
"""rich_object 富内容抽取（公式/图形/图片）回归（2026-09-09）。纯本地合成 OOXML。"""
import io
import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if os.path.join(ROOT, "std_lib") not in sys.path:
    sys.path.insert(0, os.path.join(ROOT, "std_lib"))

from std_lib.scraper_std.rich_object import extract_rich_objects, rich_object_fields  # noqa: E402

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_A = "http://schemas.openxmlformats.org/drawingml/2006/main"


def _docx_with_formula_and_drawing() -> bytes:
    doc = f"""<w:document xmlns:w="{_W}" xmlns:m="{_M}" xmlns:r="{_R}" xmlns:a="{_A}">
 <w:body>
  <w:p><w:r><m:oMath><m:r><m:t>y</m:t></m:r>
    <m:f><m:num><m:r><m:t>1</m:t></m:r></m:num><m:den><m:r><m:t>x</m:t></m:r></m:den></m:f>
    <m:r><m:t>+2</m:t></m:r></m:oMath></w:r></w:p>
  <w:p><w:r><w:drawing><wp:inline xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing">
   <a:graphic><a:graphicData uri="pic"><a:txBody><a:p><a:r><a:t>受理→审核→处理→归档</a:t></a:r></a:p></a:txBody>
   <a:blip r:embed="rId1"/></a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>
 </w:body></w:document>"""
    rels = """<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
                Target="media/img1.png"/></Relationships>"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        z.writestr("word/document.xml", doc)
        z.writestr("word/_rels/document.xml.rels", rels)
        z.writestr("word/media/img1.png", b"\x89PNG\r\n\x1a\nfakepng")
    return buf.getvalue()


def test_extract_formula_and_diagram():
    data = _docx_with_formula_and_drawing()
    res = extract_rich_objects(data, "样例.docx")
    kinds = [o["kind"] for o in res["objects"]]
    assert "formula" in kinds, kinds
    assert "diagram" in kinds, kinds
    formula = next(o for o in res["objects"] if o["kind"] == "formula")
    assert "y" in formula["text"] and "x" in formula["text"]
    dia = next(o for o in res["objects"] if o["kind"] == "diagram")
    assert "受理" in dia["text"] and "归档" in dia["text"]
    assert res["images"], "应提取 media 图片字节"
    assert res["count"] == len(res["objects"])


def test_rich_object_fields_persist_image(tmp_path):
    data = _docx_with_formula_and_drawing()
    img_dir = os.path.join(str(tmp_path), "rich")
    fields = rich_object_fields(data, "样例.docx", image_dir=img_dir, rec_key="recA")
    assert fields["rich_count"] >= 2
    assert fields["rich_text"]
    # 图片落盘（<image_dir>/<rec_key>/…） + 对象回填 image_path
    saved = os.listdir(os.path.join(img_dir, "recA"))
    assert saved and any(f.endswith("png") for f in saved)
    assert any(o.get("image_path") for o in fields["rich_structured"])


def test_empty_for_plain_text():
    assert rich_object_fields(b"hello", "a.txt") == {}
