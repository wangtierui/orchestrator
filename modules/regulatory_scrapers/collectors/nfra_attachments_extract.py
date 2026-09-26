#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
nfra 法规附件下载 + PDF 文本抽取 + 数据质量校验（高精度专业版）
=================================================================
背景
----
主抓取脚本 `nfra_collector.py` 仅抓取详情页 `docClob`（印发通知序言），
而大量法规（如《保险公司偿付能力监管规则（Ⅱ）》）的**实质正文挂在 PDF 附件**上
（`attachmentInfoVOList`）。本模块补齐这一缺口：把附件 PDF 下载并抽取为纯文本，
落盘为可离线复用的缓存，由主脚本在重建时并入输出。

设计要点（对齐 DAMA 数据治理 / ISO 8000 数据质量）
-------------------------------------------------
  * 完整性(Completeness)：扫描全部含附件的详情缓存，按附件清单全量覆盖；续跑跳过已处理。
  * 有效性(Validity)：仅对 PDF 抽取；非 PDF 类型标记 `unsupported_type` 不丢元数据。
  * 准确性(Accuracy)：逐页统计空页/低文本密度/乱码，输出质量标志，供人工复核。
  * 唯一性(Uniqueness)：附件文件名本身即内容哈希（如 3dc3532b....pdf），天然去重；
                         另计算内容 sha256 写入清单，便于跨文档重复识别。
  * 可追溯(Traceability)：每个附件记录绝对下载 URL + 来源 docId，可回源核验。
  * 局限性声明：图像型 PDF（扫描件）需 OCR；本环境 tesseract 二进制缺失，
                 paddleocr 若不可用则标记 `ocr_status=engine_unavailable` 并透明上报，
                 不擅自下载重型模型（避免沙箱风险），由数据质量清单显式标注待人工补录。

WAF 礼貌原则
-----------
  * 复用基础 python（含 pypdf 6.x），标准库 urllib 下载。
  * 请求随机间隔 + 连续失败冷却（与 fill_details 同纪律），不暴力重试。
  * 单篇/全量均可，缓存落盘，超时由调用方控制，下一周自动续跑。

用法
----
  python nfra_fetch_attachments.py                      # 全量（续跑）
  python nfra_fetch_attachments.py --doc-id 1027892     # 单篇试点
  python nfra_fetch_attachments.py --keep-pdf           # 保留原始 PDF（默认仅留 .txt）
  python nfra_fetch_attachments.py --ocr                # 启用 OCR 兜底（需 paddleocr+模型）
=================================================================
"""

# ---- P3b 仓库引导（migrate_collectors_p3b.py 注入）：使 std_lib/config 可导入 ----
import os as _os
import sys as _sys

_GUIDE_ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "..", ".."))
if _GUIDE_ROOT not in _sys.path:
    _sys.path.insert(0, _GUIDE_ROOT)
del _GUIDE_ROOT, _os, _sys
import hashlib
import os
import re
import ssl
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
from std_lib.scraper_std.cache_store import docs_root, source_cache_root  # noqa: E402

CACHE = source_cache_root("nfra")  # 列表/详情请求缓存根（modules/regulatory_scrapers/cache/nfra）
ATT_DIR = docs_root("nfra", "attachments")  # 附件产物根（统一 data/docs）
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
    """OCR 兜底（受控）。委托统一 OCR 模块：PaddleOCR 3.7.0 默认 + Tesseract v5 降级。

    修复：原实现直接调用 PaddleOCR 2.x API（PaddleOCR(use_angle_cls=True,
    show_log=False) + ocr(img, cls=True)），在 3.x 下因 show_log 参数与 ocr()
    签名变更而崩溃。现统一经 std_lib/scraper_std/ocr_engine，规避 Paddle#77340
    oneDNN/PIR 崩溃（disable_mkldnn 默认开启）。

    任何引擎均不可用 / 全部失败 → 抛 RuntimeError，由调用方标记 engine_unavailable
    而非中断流程（保持与原 paddleocr 直调一致的契约）。
    """
    # 挂载共享库 std_lib（nfra 项目此前未引入 std_lib 路径）
    repo_root = os.path.join(HERE, "..")
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    from std_lib.scraper_std.ocr_engine import get_ocr
    res = get_ocr().extract_pdf(path, force_ocr=True)
    if not res.success:
        raise RuntimeError(
            f"OCR 失败（engine={res.engine}）：{res.error or 'no engine available'}"
        )
    return res.text or ""


def sha256_of(data):
    return hashlib.sha256(data).hexdigest()


def extract_pdf_text(data):
    """用 pypdf 抽取文本。返回 (text, page_count, per_page_lens, needs_ocr_flag)。

    2026-09-10：抽全文后经 clean_pdf_text 页眉/页脚/页码清理（红头文件每页重复
    「XX局文件/文号/—N—」行删除），修复 text 页眉页脚噪声与断句错乱观感。"""
    import io

    import pypdf
    reader = pypdf.PdfReader(io.BytesIO(data))
    page_count = len(reader.pages)
    page_texts = []
    for p in reader.pages:
        try:
            t = p.extract_text() or ""
        except Exception:
            t = ""
        page_texts.append(t)
    full = "\n".join(page_texts)
    from std_lib.scraper_std.crawler_common import clean_pdf_text
    full = clean_pdf_text(full)
    # 低文本密度判定：平均每页字符过少 → 疑似扫描件（阈值按页数线性）
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
    """从 .xlsx（OOXML，ZIP 包）抽取单元格文本：**行式制表符**（保留行列结构，2026-09-10
    修复「每单元格一行」扁平化丢结构问题），供 text 可读；结构化二维另由 structured_table_fields 回填。"""
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
    # 回退：共享字符串拼接（无 openpyxl 时）
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
    """按文件名/URL 后缀判定附件类型，决定抽取策略。"""
    name = (att.get("attachmentName") or "").lower()
    if name.endswith(".pdf"):
        return "pdf"
    if name.endswith(".docx"):
        return "docx"
    if name.endswith(".xlsx") or name.endswith(".xlsm"):
        return "xlsx"
    if name.endswith(".doc"):
        return "doc_binary"          # 旧版二进制 .doc，纯标准库无法可靠抽取
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
    """下载后按魔数纠正类型（应对扩展名误标，如 .docx 实为 OLE2）。"""
    if data[:4] == b"%PDF":
        return "pdf"
    if data[:4] in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"):
        return "xlsx" if (name.lower().endswith(".xlsx") or name.lower().endswith(".xlsm")) else "docx"
    if data[:4] == b"\xd0\xcf\x11\xe0":   # OLE2 复合文档（旧版 .doc/.xls，或扩展名误标）
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
    """走查 CLX→Pcdt→PlcPcd，返回解码后的文本（解析失败返回 ''）。

    修正点：按 MS-DOC 规范定位 Pcdt——clx 条目 data 首字节低比特 fPcdt=1 即 piece
    table，其 data = lcb(4) + PlcPcd，并以 (len(PlcPcd)-4) % 12 == 0 校验件数；
    避免旧逻辑「盲目跳条目」导致的偏移错位（旧逻辑对部分 fComplex=1 文档采到 0 字节）。
    """
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
        # fPcdt：clx 条目 data 首字节低比特置位时为 piece table
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
    """最终兜底：当 piece 表 / fcMin 连续解码均产出过短（正文未落在 fcMin，散落于
    WordDocument 流其他位置，多见于表格型 .doc），扫描全流 utf-16-le 回收中文片段。

    精度闸门（抑制二进制误解码乱码）：
      - 长度 >=4 的中文/混合片段：要求含 >=1 白名单常用字（真实中文长串必含常用字）；
      - 长度 2-3 的短片段：仅保留含数字的纯 ASCII（表格数值），丢弃短中文/英文噪声；
      - 纯 ASCII 且含数字者（如 133.33%、90%）始终保留。
    返回以换行连接的文本片段。
    """
    u = wd.decode("utf-16-le", errors="ignore")
    out = []
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
        # 长片段：真实中文词/句几乎必含常用字；纯生僻字乱码长串（无白名单字）丢弃
        if any(c in _COMMON_CN for c in run) or (run.isascii() and any(c.isdigit() for c in run)):
            out.append(run)
        return
    # 短片段（2-3 字）：仅保留含数字的纯 ASCII（表格数值），其余多为乱码/噪声
    if run.isascii() and any(c.isdigit() for c in run):
        out.append(run)


def extract_doc_ole(data):
    """纯 Python 解析旧版 .doc（OLE2 Word 二进制），无需 LibreOffice/antiword/catdoc。

    返回 (text, ok, reason)：
      - 成功且文本足够（>30 可打印字符）→ ok=True
      - 失败/文本过短（疑似加密/外语/非 Word）→ ok=False，reason 指明原因
    实现要点：读 FIB（WordDocument 流头部 0x0A 标志位）判定 simple/complex 存储；
      simple：连续 utf-16-le 解码 wd[fcMin : fcMin+ccpText*2]；
      complex：走件表(PlcPcd)抽取；**当件表解析异常或产出过短，回退到 wd[fcMin:] 的
              连续 utf-16-le 解码**（实测多数 fComplex=1 文档文本实为单件、连续存放于
              fcMin，件表定位失败时可借此完整回收，避免采到 0 字节导致 too_short）。
    """
    import olefile
    try:
        ole = olefile.OleFileIO(data)
    except Exception:
        return "", False, "ole_open"
    try:
        # 关键坑：ole.listdir() 返回的是「列表的列表」(每条是 ['WordDocument'])，
        # 必须取末元素才是流名，否则 "WordDocument" in names 永远为 False。
        names = [s[-1] if isinstance(s, (list, tuple)) else s for s in ole.listdir()]
        if "WordDocument" not in names:
            return "", False, "no_WordDocument_stream"
        wd = ole.openstream("WordDocument").read()
    except Exception:
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
    except Exception:
        tbl = None

    def _clean(t):
        t = t.replace("\r", "\n").replace("\x07", "\n").replace("\x0b", "\n")
        return "".join(ch if (ch.isprintable() or ch in "\n\t") else " " for ch in t)

    # 连续解码兜底（simple 与 complex 通用）
    simple_text = ""
    if fcMin + ccpText * 2 <= len(wd) and ccpText > 0:
        try:
            simple_text = wd[fcMin: fcMin + ccpText * 2].decode("utf-16-le", errors="ignore")
        except Exception:
            simple_text = ""

    # 件表抽取（仅 complex）
    complex_text = _walk_pieces(wd, tbl) if fComplex else ""

    # 选择：优先件表（若足够），否则连续解码兜底
    c_clean = _clean(complex_text)
    s_clean = _clean(simple_text)
    if len(c_clean.strip()) >= 30:
        text = c_clean
    elif len(s_clean.strip()) >= 30:
        text = s_clean
    else:
        text = c_clean if len(c_clean) >= len(s_clean) else s_clean
    # 最终兜底：标准路径（piece 表 / fcMin 连续）产出过短，说明正文未落在 fcMin，
    # 可能在 WordDocument 流其他位置以 utf-16-le 散落（多见于表格型 .doc）。
    # 扫描全流回收中文片段（常见中文字白名单过滤，抑制二进制误解码乱码）。
    if len(text.strip()) < 30:
        rec = _recover_cjk_stream(wd)
        if len(rec.strip()) >= 30:
            text = rec
    ok = len(text.strip()) > 30
    return text, ok, "ok" if ok else ("too_short_%d" % len(text.strip()))


def _is_ole2_word(src_path):
    """判定本地 OLE2 源文件是否为 Word（含 WordDocument 流），用于抽取后细分类型。"""
    try:
        import olefile
        with olefile.OleFileIO(src_path) as ole:
            names = [s[-1] if isinstance(s, (list, tuple)) else s for s in ole.listdir()]
        return "WordDocument" in names
    except Exception:
        return False


def _handle_ole2(data):
    """OLE2 复合文档：先尝试 Word(olefile 纯Python直抽) → 再试 Excel(xlrd)。

    仅当两者均失败时，才标记 `unsupported_binary_doc` 并源已留存（待外部转换）。
    注意：之前纯标准库无法抽 .doc，自 olefile 引入后约 57% 旧版 Word 可直抽。
    """
    # Word（旧版 .doc）：olefile 解析 OLE2 二进制，零外部依赖
    try:
        t, ok, _reason = extract_doc_ole(data)
        if ok:
            return t, None, len(t), False, True, None
    except Exception:  # noqa: BLE001  采集容错（字段/附件缺失不阻断采集）
        pass
    # Excel（旧版 .xls）：xlrd 抽取单元格
    try:
        t = extract_xls_ole(data)
        if t.strip():
            return t, None, len(t), False, True, None
    except Exception:  # noqa: BLE001  采集容错（字段/附件缺失不阻断采集）
        pass
    return "", None, 0, False, False, "unsupported_binary_doc(.doc需外部转换)"


def extract_any(data, name):
    """统一抽取入口：返回 (text, page_count, char_count, needs_ocr, ok, err_reason)。

    - PDF / 真 docx / 真 xlsx：标准抽取。
    - OLE2：先试 xlrd(Excel)，否则 Word 二进制（标记不可抽取）。
    - 其他（rar 等）：标记不支持。
    扩展名误标由魔数纠正，避免 BadZipFile。
    """
    kind = sniff_kind(data, name)
    if kind == "pdf":
        try:
            t, pc, pl, nor = extract_pdf_text(data)
            return t, pc, len(t), nor, True, None
        except Exception as e:
            return "", None, 0, False, False, "pdf_error:%s" % type(e).__name__
    if kind == "docx":
        try:
            t = extract_docx(data)
            return t, None, len(t), False, True, None
        except Exception as e:
            if "BadZip" in type(e).__name__:
                return _handle_ole2(data)   # 实为 OLE2（扩展名误标）
            return "", None, 0, False, False, "docx_error:%s" % type(e).__name__
    if kind == "xlsx":
        try:
            t = extract_xlsx(data)
            return t, None, len(t), False, True, None
        except Exception as e:
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
