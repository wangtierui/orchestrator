# -*- coding: utf-8 -*-
"""
rich_object.py —— 富内容对象抽取共享层（2026-09-09，五源 + internal 统一）

目标：把监管/制度文件中的"非正文结构化对象"识别并产出统一轨（供 raw/processed 存储）：
  - formula（数学公式）：docx OMML（m:oMath）→ Unicode 线性文本
  - diagram（SmartArt/自选图形/文本框）：DrawingML（a:txBody / w:txbxContent）内文本按图聚合
  - image（纯图片/带图 DrawingML）：提取 word/media 图片字节供统一落盘

设计对齐既有轨：
  - 与 table_recovery.structured_table_fields 同构：有对象返回
      {rich_structured: [...], rich_text: str, rich_count: int}
  - .doc/.wps/.rtf/.ceb 旧格式先经 doc_convert（LibreOffice doc→docx）再按 docx 解析
  - 图片字节经 rich_object_fields(..., image_dir, rec_key) 落盘并回填 image_path；
    无图/无图形/无公式返回空（不破坏既有无富内容记录）
  - pdf 扫描示意图不在此层做结构还原（图像型兜底另层 OCR，避免臆造结构）

rich_structured 对象 schema：
  {"index": int, "kind": "formula"|"diagram"|"image",
   "text": str,                    # 图形内文本 / 公式线性文本 / 空
   "image_path": str,              # 有图且落盘时相对路径
   "converted": bool}              # doc→docx 转换路径标记
"""
from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
import zipfile
from typing import Any

_LOG_PREFIX = "[rich_object]"
CONVERT_EXT = (".doc", ".wps", ".rtf", ".ceb")


def _log(msg: str) -> None:
    import logging  # noqa: PLC0415
    logging.getLogger("rich_object").warning("%s %s", _LOG_PREFIX, msg)


def _docx_parts(data: bytes):
    """打开 docx zip，返回 (zip, document.xml bytes, rels dict[str,str] rId→media target)。"""
    z = zipfile.ZipFile(__import__("io").BytesIO(data))
    try:
        doc = z.read("word/document.xml")
    except KeyError:
        return None, None, {}
    rels = {}
    try:
        r = ET.fromstring(z.read("word/_rels/document.xml.rels"))
        for rel in r:
            rid = rel.get("Id", "")
            tgt = rel.get("Target", "")
            if not rid:
                continue
            tgt = tgt.lstrip("/")
            # 关系文件位于 word/_rels/，相对 Target 以 word/ 为基（media/… 等）
            if not tgt.startswith(("word/", "xl/")):
                tgt = "word/" + tgt
            rels[rid] = tgt
    except (KeyError, ET.ParseError):
        pass
    return z, doc, rels


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _omml_linear(el: ET.Element, out: list[str]) -> None:
    """OMML 数学树 → Unicode 线性文本（尽力；m:t 拼接 + 常见结构记号）。"""
    local = _local(el.tag)
    if local == "t":
        out.append(el.text or "")
        return
    if local == "f":  # 分式 (num)/(den)
        parts: list[str] = []
        for c in el:
            if _local(c.tag) in ("num", "den"):
                sub: list[str] = []
                for gc in c:
                    _omml_linear(gc, sub)
                parts.append("".join(sub))
        out.append("(" + ")/(" .join("" if not p else p for p in parts) + ")")
        return
    for c in el:
        _omml_linear(c, out)


def _text_of(el: ET.Element) -> str:
    parts: list[str] = []
    for node in el.iter():
        if _local(node.tag) == "t" and node.text:
            parts.append(node.text)
    return "".join(parts).strip()


def _drawing_lines(el: ET.Element) -> list[str]:
    """DrawingML 图形内按「段落 a:p」聚合文本行（每形状/文本框一段）→ 节点粒度底稿。"""
    lines: list[str] = []
    for p in _iter_descendants(el, "p"):
        seg: list[str] = []
        for t in _iter_descendants(p, "t"):
            if t.text:
                seg.append(t.text)
        s = "".join(seg).strip()
        if s:
            lines.append(s)
    if not lines:
        txt = _text_of(el)
        if txt:
            lines.append(txt)
    return lines


def _iter_descendants(root: ET.Element, localname: str):
    for el in root.iter():
        if _local(el.tag) == localname:
            yield el


def extract_rich_objects(data: bytes, name: str = "", *,
                         max_chars: int = 60000) -> dict[str, Any]:
    """docx/doc/xlsx → 富内容对象与图片字节。

    返回 {objects, images(dict name→bytes), text, count, converted}；
    无法解析/无富内容时返回空壳（{objects:[],images:{},text:"",count:0}）。
    """
    low = (name or "").lower()
    converted = False
    if low.endswith(".pdf"):
        return _extract_pdf_rich(data, max_chars)
    if low.endswith(CONVERT_EXT):
        try:
            from std_lib.scraper_std.doc_convert import doc_bytes_to_docx  # noqa: PLC0415
            conv = doc_bytes_to_docx(data, low)
            if conv:
                data, converted = conv, True
        except Exception:  # noqa: BLE001
            return {"objects": [], "images": {}, "text": "", "count": 0, "converted": False}
    if low.endswith(".xlsx") or low.endswith(".xlsm") or low.endswith(".xlsb") or data[:2] == b"PK":
        # 先按 docx 试（word/document.xml），失败按 xlsx drawings
        objs, images, _z = _extract_docx_rich(data, max_chars)
        if objs is None:
            return _extract_xlsx_rich(data, converted)
        return {"objects": objs, "images": images, "text": _join_text(objs),
                "count": len(objs), "converted": converted}
    return {"objects": [], "images": {}, "text": "", "count": 0, "converted": False}


def _extract_docx_rich(data: bytes, max_chars: int):
    """docx：OMML 公式 + DrawingML 图形。返回 (objects|None(非docx), images, zip)。"""
    try:
        z, doc, rels = _docx_parts(data)
    except Exception:  # noqa: BLE001
        return None, {}, None
    if doc is None:
        return None, {}, z
    try:
        root = ET.fromstring(doc)
    except ET.ParseError as e:  # noqa: BLE001
        _log(f"docx xml parse error: {e}")
        return [], {}, z
    objects: list[dict[str, Any]] = []
    images: dict[str, bytes] = {}

    # 1) OMML 公式
    for m in _iter_descendants(root, "oMath"):
        lin: list[str] = []
        _omml_linear(m, lin)
        txt = "".join(lin).strip()
        if txt:
            objects.append({"index": len(objects) + 1, "kind": "formula",
                            "text": txt[:max_chars], "converted": False})

    # 2) DrawingML 图形/图片（文本按段落聚合 → 节点粒度拓扑底稿）
    for d in _iter_descendants(root, "drawing"):
        paras = _drawing_lines(d)
        sub = "\n".join(paras)
        blips = list(_iter_descendants(d, "blip"))
        media: list[bytes] = []
        img_names: list[str] = []
        for b in blips:
            embed = b.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed")
            if not embed:
                embed = b.get("embed")
            tgt = rels.get(embed or "", "")
            if not tgt:
                continue
            try:
                raw = z.read(tgt)
            except KeyError:
                continue
            media.append(raw)
            img_names.append(os.path.basename(tgt))
        if sub or media:
            obj: dict[str, Any] = {"index": len(objects) + 1,
                                   "kind": "diagram" if sub else "image",
                                   "text": sub[:max_chars] if sub else "",
                                   "converted": False}
            if sub:
                obj["shape_count"] = len(paras)   # 节点数（近似拓扑粒度，行=节点文本）
            objects.append(obj)
            for i, raw in enumerate(media):
                images[f"d{len(objects)}_img{i + 1}_{img_names[i] if img_names else 'media' + str(i)}"] = raw
    return objects, images, z


def _extract_xlsx_rich(data: bytes, converted: bool):
    """xlsx：drawings 内 a:t 文本框/图形文字 + media 图片。"""
    objects: list[dict[str, Any]] = []
    images: dict[str, bytes] = {}
    try:
        z = zipfile.ZipFile(__import__("io").BytesIO(data))
    except Exception:  # noqa: BLE001
        return {"objects": [], "images": {}, "text": "", "count": 0, "converted": converted}
    names = [n for n in z.namelist() if re.match(r"xl/drawings/drawing\d+\.xml$", n)]
    for dname in names:
        try:
            root = ET.fromstring(z.read(dname))
        except (KeyError, ET.ParseError):
            continue
        for d in _iter_descendants(root, "txBody"):
            txt = _text_of(d)
            if txt:
                objects.append({"index": len(objects) + 1, "kind": "diagram",
                                "text": txt, "converted": converted})
        base = os.path.dirname(dname)
        relp = dname.replace(".xml", ".rels")
        rels = {}
        try:
            r = ET.fromstring(z.read(relp))
            for rel in r:
                tgt = rel.get("Target", "").lstrip("/")
                if tgt:
                    rels[rel.get("Id", "")] = (os.path.join(base, tgt)
                                               if not tgt.startswith("xl/") else tgt)
        except (KeyError, ET.ParseError):
            pass
        for b in _iter_descendants(root, "blip"):
            embed = b.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed") or b.get("embed")
            tgt = rels.get(embed or "")
            if not tgt:
                continue
            try:
                images[os.path.basename(tgt)] = z.read(tgt)
            except KeyError:
                pass
    return {"objects": objects, "images": images, "text": _join_text(objects),
            "count": len(objects), "converted": converted}


_MIN_REGION = 60        # 区域渲染最小宽高（像素阈值：过滤装饰线/小徽标）
_MAX_PDF_REGIONS = 20   # 单 pdf 最大 region 数（防示意图页泛滥）


def _extract_pdf_rich(data: bytes, max_chars: int) -> dict[str, Any]:
    """PDF 示意图区域 OCR（2026-09-09 M3）：检测页内图像区 → clip 渲染 → OCR 文本。

    每个区域产出 kind=image 对象（text=OCR 文本或空）+ 渲染 png 字节（供统一落盘）；
    依赖 PyMuPDF(fitz)；OCR 引擎不可用时图仍归档（text 空、诚实标注无结构还原）。
    装饰小区域（宽高 < _MIN_REGION）跳过。返回与 extract_rich_objects 同构。
    """
    objects: list[dict[str, Any]] = []
    images: dict[str, bytes] = {}
    try:
        import pymupdf as fitz  # noqa: PLC0415 PyMuPDF（现代导入名，弃用 fitz 别名）
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as e:  # noqa: BLE001
        _log(f"pdf open: {e}")
        return {"objects": [], "images": {}, "text": "", "count": 0, "converted": False}
    for pno, page in enumerate(doc, start=1):
        if len(objects) >= _MAX_PDF_REGIONS:
            break
        try:
            infos = page.get_image_info(xrefs=True)
        except Exception:  # noqa: BLE001
            continue
        for ii, info in enumerate(infos, start=1):
            if len(objects) >= _MAX_PDF_REGIONS:
                break
            bbox = info.get("bbox")
            if not bbox:
                continue
            try:
                rect = fitz.Rect(bbox)
            except Exception:  # noqa: BLE001
                continue
            if rect.is_empty or rect.width < _MIN_REGION or rect.height < _MIN_REGION:
                continue
            clip = page.rect & rect
            if clip.is_empty:
                continue
            try:
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=clip)
                png = pix.tobytes("png")
            except Exception:  # noqa: BLE001
                continue
            ocr = ""
            try:
                from std_lib.scraper_std.ocr_engine import get_ocr  # noqa: PLC0415
                res = get_ocr().recognize_image(png)
                ocr = (getattr(res, "text", "") or "").strip()
            except Exception:  # noqa: BLE001  OCR 不可用 → 图仍归档
                pass
            objects.append({"index": len(objects) + 1, "kind": "image",
                            "text": ocr[:max_chars] if ocr else "",
                            "shape_count": 0, "converted": False})
            images[f"p{pno}_fig{ii}.png"] = png
    return {"objects": objects, "images": images, "text": _join_text(objects),
            "count": len(objects), "converted": False}


def _join_text(objects: list[dict[str, Any]]) -> str:
    lines = []
    for o in objects:
        tag = {"formula": "公式", "diagram": "图", "image": "图"}.get(o.get("kind"), "对象")
        lines.append(f"{tag}:{o.get('text', '')}".strip() if o.get("text") else tag)
    return "\n".join(lines)


def rich_object_fields(data: bytes, name: str = "", *, image_dir: str | None = None,
                       rec_key: str = "") -> dict[str, Any]:
    """富内容抽取 → 记录轨键（供 raw/processed 存储；同 structured_table_fields 样板）。

    无富内容/异常 → {}。image_dir 给定时将 images 写入
    ``<image_dir>/<rec_key>/<filename>`` 并回填对象 image_path（相对 image_dir）。
    """
    if not data:
        return {}
    res = extract_rich_objects(data, name)
    objs = res.get("objects") or []
    if not objs:
        return {}
    saved = 0
    out = []
    for o in objs:
        out.append({k: v for k, v in o.items() if k != "converted"})
    images = res.get("images") or {}
    if image_dir and images and rec_key:
        sub = os.path.join(image_dir, str(rec_key))
        os.makedirs(sub, exist_ok=True)
        for fname, raw in images.items():
            dest = os.path.join(sub, fname)
            try:
                with open(dest, "wb") as fh:
                    fh.write(raw)
                saved += 1
            except OSError:
                continue
            # 首图关联到首对象（diagram/image）
            for o in out:
                if not o.get("image_path"):
                    o["image_path"] = os.path.relpath(dest, image_dir).replace("\\", "/")
                    break
    return {"rich_structured": out, "rich_text": res.get("text", ""),
            "rich_count": len(out), "_rich_images_saved": saved}
