#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""nfra 法规附件抽取链路（refactor 迁入版；原 nfra_attachments_extract.py 逐函数等价）。

纯抽取函数集合（PDF/docx/xlsx/旧版 .doc(OLE2)/.xls(OLE2) + 魔数纠正 + OCR 兜底 + 质量闸门）。
被 nfra_fetch_attachments.py 经包内相对导入复用。
"""
import hashlib
import os
import re
import ssl
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
from ..lib.cache import docs_root, source_cache_root

CACHE = source_cache_root("nfra")
ATT_DIR = docs_root("nfra", "attachments")
DETAIL_GLOB = os.path.join(CACHE, "SelectByDocId__docId_*.json")
QUALITY_REPORT = os.path.join(ATT_DIR, "_quality_report.json")

BASE = "https://www.nfra.gov.cn"
_SSL_CTX = ssl.create_default_context()

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/pdf,application/octet-stream,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": BASE + "/cn/view/pages/ItemDetail.html",
}

_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_X_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

_COMMON_CN = set(
    "的一是中国有人在来发会以行监管理司公办为在和的是有对这中规办法等表指项据标算资风"
    "险产信度备额期初末位单亿元百千分万第条款号施实将及与或并其该各本外币现金债券投资贷"
    "款机构业务系统重要评估披露模板控制安排证券资产交易转换系数比例限额标准规定通知决"
    "定意见批复函照执照请依按结合属于内容如下附件名称时间年月日经已通过未除部分次上下列"
    "前后内外总合计算采用设置要求条件范围对象方式程序结果情况问题说明表示建议允许禁止"
    "必须可以应当不得超过低于高于等于小于大于之间对于关于依据根据按照适用参照引用摘录"
    "值序号类别型号目录收支结算付款存利率限担保抵押评级股股票融资租赁再审批案报示公"
    "细则引范间参净余负债自有其他相应上述下列各项指标名称填报复核负责备注说明摘要"
    "构会监委作金融银业发令年号版本式样页张份件项类目节点线面块格栏行列组合"
)


def is_pdf(att):
    name = (att.get("attachmentName") or "").lower()
    title = (att.get("title") or "").lower()
    return name.endswith(".pdf") or title.endswith(".pdf") or (att.get("extName") or "").lower() == "pdf"


def ocr_pdf(path):
    """OCR 兜底（受控）。委托本地统一 OCR 模块。"""
    from ..lib.ocr import get_ocr
    res = get_ocr().extract_pdf(path, force_ocr=True)
    if not res.success:
        raise RuntimeError(
            f"OCR 失败（engine={res.engine}）：{res.error or 'no engine available'}"
        )
    return res.text or ""


def sha256_of(data):
    return hashlib.sha256(data).hexdigest()


def extract_pdf_text(data):
    """用 pypdf 抽取文本。返回 (text, page_count, per_page_lens, needs_ocr_flag)。"""
    import io

    import pypdf
    reader = pypdf.PdfReader(io.BytesIO(data))
    page_count = len(reader.pages)
    page_texts = []
    for p in reader.pages:
        try:
            t = p.extract_text() or ""
        except Exception:  # noqa: BLE001
            t = ""
        page_texts.append(t)
    full = "\n".join(page_texts)
    from ..lib.text_utils import clean_pdf_text
    full = clean_pdf_text(full)
    needs_ocr = (page_count > 0) and (len(full.strip()) < max(50, 30 * page_count))
    return full, page_count, [len(t) for t in page_texts], needs_ocr


def extract_docx(data):
    """从 .docx（OOXML，ZIP 包）抽取纯文本，按段落换行。"""
    import io
    import xml.etree.ElementTree as ET
    import zipfile
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        xml = z.read("word/document.xml")
    root = ET.fromstring(xml)
    para_texts = []
    for p in root.iter(_W_NS + "p"):
        runs = [(t.text or "") for t in p.iter(_W_NS + "t")]
        para_texts.append("".join(runs))
    return "\n".join(para_texts)


def extract_xlsx(data):
    """从 .xlsx 抽取单元格文本：行式制表符（保留行列结构）。"""
    import io
    try:
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
        rows = []
        for ws in wb.worksheets:
            for r in ws.iter_rows(values_only=True):
                rows.append("\t".join("" if c is None else str(c) for c in r))
        return "\n".join(rows)
    except Exception:  # noqa: BLE001  采集容错（字段/附件缺失不阻断采集）
        pass
    import xml.etree.ElementTree as ET
    import zipfile
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = z.namelist()
        shared = []
        if "xl/sharedStrings.xml" in names:
            sx = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in sx.iter(_X_NS + "si"):
                shared.append("".join((t.text or "") for t in si.iter(_X_NS + "t")))
        cells = []
        for nm in names:
            if re.match(r"xl/worksheets/sheet\d+\.xml$", nm):
                sh = ET.fromstring(z.read(nm))
                for c in sh.iter(_X_NS + "c"):
                    v = c.find(_X_NS + "v")
                    if v is not None and v.text is not None:
                        if c.get("t") == "s":
                            idx = int(v.text)
                            cells.append(shared[idx] if idx < len(shared) else "")
                        else:
                            cells.append(v.text)
    return "\n".join(cells)


def attachment_kind(att):
    """按文件名/URL 后缀判定附件类型。"""
    name = (att.get("attachmentName") or "").lower()
    if name.endswith(".pdf"):
        return "pdf"
    if name.endswith(".docx"):
        return "docx"
    if name.endswith(".xlsx") or name.endswith(".xlsm"):
        return "xlsx"
    if name.endswith(".doc"):
        return "doc_binary"
    url = (att.get("urlOtherName") or att.get("attachmentUrl") or "").lower()
    if url.endswith(".pdf"):
        return "pdf"
    if url.endswith(".docx"):
        return "docx"
    if url.endswith(".xlsx"):
        return "xlsx"
    if url.endswith(".doc"):
        return "doc_binary"
    return "unsupported"


def dispatch_extract(kind, data):
    """按类型抽取，返回 (text, page_count, page_lens, needs_ocr)。"""
    if kind == "pdf":
        return extract_pdf_text(data)
    if kind == "docx":
        t = extract_docx(data)
        return t, None, [len(t)], False
    if kind == "xlsx":
        t = extract_xlsx(data)
        return t, None, [len(t)], False
    raise RuntimeError("unsupported kind %s" % kind)


def sniff_kind(data, name):
    """下载后按魔数纠正类型。"""
    if data[:4] == b"%PDF":
        return "pdf"
    if data[:4] in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"):
        return "xlsx" if (name.lower().endswith(".xlsx") or name.lower().endswith(".xlsm")) else "docx"
    if data[:4] == b"\xd0\xcf\x11\xe0":
        return "ole2"
    return "unknown"


def extract_xls_ole(data):
    """用 xlrd 抽取旧版 .xls（OLE2）单元格文本。"""
    import xlrd
    wb = xlrd.open_workbook(file_contents=data)
    rows = []
    for sh in wb.sheets():
        for r in range(sh.nrows):
            vals = [str(sh.cell_value(r, c)) for c in range(sh.ncols)
                    if str(sh.cell_value(r, c)).strip() != ""]
            if vals:
                rows.append("\t".join(vals))
    return "\n".join(rows)


def _walk_pieces(wd, tbl):
    """走查 CLX→Pcdt→PlcPcd，返回解码后的文本。"""
    if tbl is None:
        return ""
    fcClx = struct.unpack("<I", wd[0x1A2:0x1A6])[0]
    lcbClx = struct.unpack("<I", wd[0x1A6:0x1AA])[0]
    if fcClx + lcbClx > len(tbl):
        return ""
    clx = tbl[fcClx: fcClx + lcbClx]
    pos = 0
    plc = None
    while pos + 1 <= len(clx):
        cb = clx[pos]
        if cb == 0:
            break
        data = clx[pos + 1: pos + 1 + cb]
        if data and (data[0] & 0x01):
            lcb = struct.unpack("<I", data[0:4])[0]
            if 4 <= lcb and (lcb - 4) % 12 == 0 and 4 + lcb <= len(data):
                plc = data[4: 4 + lcb]
                break
        pos += 1 + cb
    if plc is None:
        return ""
    n = (len(plc) - 4) // 12
    if n <= 0:
        return ""
    buf = []
    for i in range(n):
        if (i + 2) * 4 > len(plc):
            break
        cpStart = struct.unpack("<I", plc[i * 4:(i + 1) * 4])[0]
        cpEnd = struct.unpack("<I", plc[(i + 1) * 4:(i + 2) * 4])[0]
        pcd_off = 4 * (n + 1) + i * 8
        if pcd_off + 8 > len(plc):
            break
        cpc = struct.unpack("<I", plc[pcd_off + 4:pcd_off + 8])[0]
        fc = cpc & 0x3FFFFFFF
        comp = (cpc & 0x40000000) != 0
        if comp:
            buf.append(wd[fc: fc + (cpEnd - cpStart)].decode("cp1252", errors="ignore"))
        else:
            buf.append(wd[fc: fc + (cpEnd - cpStart) * 2].decode("utf-16-le", errors="ignore"))
    return "".join(buf)


def _recover_cjk_stream(wd):
    """最终兜底：扫描全流 utf-16-le 回收中文片段。"""
    u = wd.decode("utf-16-le", errors="ignore")
    out: list = []
    cur = ""
    for ch in u:
        o = ord(ch)
        if (0x4E00 <= o <= 0x9FFF) or (0x3000 <= o <= 0x303F) or (0xFF00 <= o <= 0xFFEF) \
           or (ch.isascii() and (ch.isalnum() or ch in "，。、：；（）%—.,:;()/-+")):
            cur += ch
        else:
            if cur:
                _flush_run(cur, out)
                cur = ""
    if cur:
        _flush_run(cur, out)
    return "\n".join(out)


def _flush_run(run, out):
    if len(run) >= 4:
        if any(c in _COMMON_CN for c in run) or (run.isascii() and any(c.isdigit() for c in run)):
            out.append(run)
        return
    if run.isascii() and any(c.isdigit() for c in run):
        out.append(run)


def extract_doc_ole(data):
    """纯 Python 解析旧版 .doc（OLE2 Word 二进制）。返回 (text, ok, reason)。"""
    import olefile
    try:
        ole = olefile.OleFileIO(data)
    except Exception:  # noqa: BLE001
        return "", False, "ole_open"
    try:
        names = [s[-1] if isinstance(s, (list, tuple)) else s for s in ole.listdir()]
        if "WordDocument" not in names:
            return "", False, "no_WordDocument_stream"
        wd = ole.openstream("WordDocument").read()
    except Exception:  # noqa: BLE001
        return "", False, "stream"
    if len(wd) < 0x200:
        return "", False, "wd_too_small"
    flags = struct.unpack("<H", wd[0x0A:0x0C])[0]
    fComplex = (flags >> 2) & 1
    fWhichTbl = (flags >> 9) & 1
    fcMin = struct.unpack("<I", wd[0x18:0x1C])[0]
    ccpText = struct.unpack("<I", wd[0x4C:0x50])[0]
    tbl_name = "1Table" if fWhichTbl else "0Table"
    tbl = None
    try:
        if tbl_name in names:
            tbl = ole.openstream(tbl_name).read()
    except Exception:  # noqa: BLE001
        tbl = None

    def _clean(t):
        t = t.replace("\r", "\n").replace("\x07", "\n").replace("\x0b", "\n")
        return "".join(ch if (ch.isprintable() or ch in "\n\t") else " " for ch in t)

    simple_text = ""
    if fcMin + ccpText * 2 <= len(wd) and ccpText > 0:
        try:
            simple_text = wd[fcMin: fcMin + ccpText * 2].decode("utf-16-le", errors="ignore")
        except Exception:  # noqa: BLE001
            simple_text = ""

    complex_text = _walk_pieces(wd, tbl) if fComplex else ""

    c_clean = _clean(complex_text)
    s_clean = _clean(simple_text)
    if len(c_clean.strip()) >= 30:
        text = c_clean
    elif len(s_clean.strip()) >= 30:
        text = s_clean
    else:
        text = c_clean if len(c_clean) >= len(s_clean) else s_clean
    if len(text.strip()) < 30:
        rec = _recover_cjk_stream(wd)
        if len(rec.strip()) >= 30:
            text = rec
    ok = len(text.strip()) > 30
    return text, ok, "ok" if ok else ("too_short_%d" % len(text.strip()))


def _is_ole2_word(src_path):
    """判定本地 OLE2 源文件是否为 Word（含 WordDocument 流）。"""
    try:
        import olefile
        with olefile.OleFileIO(src_path) as ole:
            names = [s[-1] if isinstance(s, (list, tuple)) else s for s in ole.listdir()]
        return "WordDocument" in names
    except Exception:  # noqa: BLE001
        return False


def _handle_ole2(data):
    """OLE2 复合文档：先尝试 Word(olefile 直抽) → 再试 Excel(xlrd)。"""
    try:
        t, ok, _reason = extract_doc_ole(data)
        if ok:
            return t, None, len(t), False, True, None
    except Exception:  # noqa: BLE001  采集容错（字段/附件缺失不阻断采集）
        pass
    try:
        t = extract_xls_ole(data)
        if t.strip():
            return t, None, len(t), False, True, None
    except Exception:  # noqa: BLE001  采集容错（字段/附件缺失不阻断采集）
        pass
    return "", None, 0, False, False, "unsupported_binary_doc(.doc需外部转换)"


def extract_any(data, name):
    """统一抽取入口：返回 (text, page_count, char_count, needs_ocr, ok, err_reason)。"""
    kind = sniff_kind(data, name)
    if kind == "pdf":
        try:
            t, pc, pl, nor = extract_pdf_text(data)
            return t, pc, len(t), nor, True, None
        except Exception as e:  # noqa: BLE001
            return "", None, 0, False, False, "pdf_error:%s" % type(e).__name__
    if kind == "docx":
        try:
            t = extract_docx(data)
            return t, None, len(t), False, True, None
        except Exception as e:  # noqa: BLE001
            if "BadZip" in type(e).__name__:
                return _handle_ole2(data)
            return "", None, 0, False, False, "docx_error:%s" % type(e).__name__
    if kind == "xlsx":
        try:
            t = extract_xlsx(data)
            return t, None, len(t), False, True, None
        except Exception as e:  # noqa: BLE001
            if "BadZip" in type(e).__name__:
                return _handle_ole2(data)
            return "", None, 0, False, False, "xlsx_error:%s" % type(e).__name__
    if kind == "ole2":
        return _handle_ole2(data)
    return "", None, 0, False, False, "unsupported_archive"


def _temp_pdf(data):
    import tempfile
    fd, p = tempfile.mkstemp(suffix=".pdf")
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    return p


__all__ = ["is_pdf", "ocr_pdf", "sha256_of", "extract_pdf_text", "extract_docx", "extract_xlsx", "attachment_kind", "dispatch_extract", "sniff_kind", "extract_xls_ole", "_walk_pieces", "_recover_cjk_stream", "_flush_run", "extract_doc_ole", "_is_ole2_word", "_handle_ole2", "extract_any", "_temp_pdf"]
