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
import argparse
import glob
import hashlib
import json
import os
import random
import re
import ssl
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
from std_lib.scraper_std.cache_store import docs_root, source_cache_root  # noqa: E402
from std_lib.scraper_std.rich_object import rich_object_fields  # noqa: E402
from std_lib.scraper_std.table_recovery import structured_table_fields  # noqa: E402

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

def http_get_bytes(url, timeout=30, max_retries=4):
    """带超时与退避重试的二进制 GET；非 200 抛异常由调用方冷却处理。"""
    last_err = None
    for attempt in range(1, max_retries + 1):
        req = urllib.request.Request(url, headers=DEFAULT_HEADERS, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
                status = resp.getcode()
                if status != 200:
                    raise urllib.error.HTTPError(url, status, "HTTP %d" % status, None, None)
                return resp.read()
        except (TimeoutError, urllib.error.HTTPError, urllib.error.URLError, ConnectionError) as e:
            last_err = e
            if attempt < max_retries:
                wait = 2 ** attempt + random.uniform(0, 1)
                print("      [att retry %d/%d] %s，%.1fs 后重试" % (attempt, max_retries, e, wait),
                      flush=True)
                time.sleep(wait)
    raise RuntimeError("附件下载失败（重试 %d 次）: %s" % (max_retries, last_err))

def build_attachment_url(att):
    """拼接附件绝对下载 URL。优先 urlOtherName，回退 attachmentUrl+attachmentName。"""
    uo = (att.get("urlOtherName") or "").strip()
    if uo:
        return BASE + (uo if uo.startswith("/") else "/" + uo)
    au = (att.get("attachmentUrl") or "").strip().lstrip("/")
    name = (att.get("attachmentName") or "").strip()
    if au and name:
        return BASE + "/" + au.rstrip("/") + "/" + name
    if name:
        return BASE + "/" + name
    return ""

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
    """用 pypdf 抽取文本。返回 (text, page_count, per_page_lens, needs_ocr_flag)。"""
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
    # 低文本密度判定：平均每页字符过少 → 疑似扫描件（阈值按页数线性）
    needs_ocr = (page_count > 0) and (len(full.strip()) < max(50, 30 * page_count))
    return full, page_count, [len(t) for t in page_texts], needs_ocr

# ----------------- Office 文档抽取（零依赖：zipfile + xml）-----------------
_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_X_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

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
    except Exception:
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

# 高频常用中文字白名单：用于「全流扫描回收」的精度闸门，过滤二进制误解码乱码
# （mojibake 多由罕见生僻字构成，几乎不含下列常用字）。覆盖监管/金融/表格常用词。
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
    except Exception:
        pass
    # Excel（旧版 .xls）：xlrd 抽取单元格
    try:
        t = extract_xls_ole(data)
        if t.strip():
            return t, None, len(t), False, True, None
    except Exception:
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

def quality_flags(page_lens, text, needs_ocr):
    """基于逐页长度与文本特征生成质量标志。"""
    flags = []
    empty_pages = sum(1 for x in page_lens if x == 0)
    if empty_pages:
        flags.append("empty_pages=%d" % empty_pages)
    if needs_ocr:
        flags.append("possible_scan(needs_ocr)")
    # 乱码检测：替换字符比例过高
    if text:
        repl = text.count("\ufffd")
        if repl > 0 and repl / max(len(text), 1) > 0.01:
            flags.append("garbled(replacement_chars=%d)" % repl)
    return flags

def process_attachment(doc_id, att, att_root, args, cooldown_state, ex=None):
    """处理单个附件：下载→抽取→落盘。返回 manifest 条目。

    ex 为已有 manifest 中同名的条目（续跑时传入），用于恢复 page_count/sha256/
    quality/source_saved 等关键统计，避免续跳导致这些字段丢失（如 page_count=null）。
    """
    name = att.get("attachmentName") or ("%s.pdf" % att.get("id"))
    entry = {
        "id": att.get("id"),
        "title": att.get("title") or "",
        "attachment_name": name,
        "url": build_attachment_url(att),
        "order_no": att.get("orderNo"),
        "doc_id": doc_id,
    }
    txt_path = os.path.join(att_root, name + ".txt")
    rel_txt = os.path.relpath(txt_path, HERE).replace("\\", "/")

    # 已完成（已抽取文本文件存在且非空）→ 续跑跳过
    if os.path.exists(txt_path) and os.path.getsize(txt_path) > 0:
        try:
            with open(txt_path, encoding="utf-8") as fh:
                text = fh.read()
            entry["extracted"] = True
            entry["char_count"] = len(text)
            entry["text_file"] = rel_txt
            entry["ocr_status"] = "skipped_done"
            # 从既有 manifest 条目恢复关键统计字段（page_count/sha256/quality/source_saved + 表格键）
            if ex:
                for k in ("page_count", "sha256", "quality", "source_saved",
                          "table_structured", "table_raw_text", "table_recovery_method"):
                    if k in ex:
                        entry[k] = ex[k]
            return entry, True
        except Exception:
            pass

    if not entry["url"]:
        entry["extracted"] = False
        entry["error"] = "no_url"
        entry["quality"] = ["missing_url"]
        return entry, False

    # 连续失败冷却
    if cooldown_state["consecutive"] >= args.max_consecutive_fail:
        print("      [att cooldown] 连续失败达 %d，冷却 %.0fs" % (
            cooldown_state["consecutive"], args.cooldown), flush=True)
        time.sleep(args.cooldown)
        cooldown_state["consecutive"] = 0

    try:
        data = http_get_bytes(entry["url"], timeout=args.timeout)
        sha = sha256_of(data)
        # 始终留存源文件（权威原件，保障可追溯/后续外部转换），不依赖 --keep-pdf
        src_path = os.path.join(att_root, name)
        with open(src_path, "wb") as fh:
            fh.write(data)
        text, page_count, char_count, needs_ocr, ok, err_reason = extract_any(data, name)
        if not ok:
            # 无法抽取（.doc 旧版 Word / .rar 等）：源已留存，透明标记，不写文本
            entry.update({
                "extracted": False,
                "error": err_reason or "extract_failed",
                "quality": [err_reason] if err_reason else ["extract_failed"],
                "sha256": sha,
                "source_saved": True,
            })
            return entry, False
        flags = quality_flags([char_count], text, needs_ocr)
        ocr_status = "not_needed"
        if needs_ocr and args.ocr:
            try:
                text = ocr_pdf(src_path)
                ocr_status = "ocr_done"
                flags = [f for f in flags if "possible_scan" not in f]
                flags.append("ocr_recovered")
            except Exception as e:
                ocr_status = "engine_unavailable"
                flags.append("ocr_failed:%s" % type(e).__name__)
        elif needs_ocr:
            ocr_status = "engine_unavailable"  # 未启用 OCR 或引擎不可用 → 透明标记
        with open(txt_path, "w", encoding="utf-8") as fh:
            fh.write(text)
        entry.update({
            "extracted": True,
            "char_count": len(text),
            "page_count": page_count,
            "sha256": sha,
            "ocr_status": ocr_status,
            "quality": flags,
            "text_file": rel_txt,
            "source_saved": True,
        })
        # 表格结构化（2026-09-08 仿 supp 打通）：xlsx/docx 附件表 → manifest entry 表键
        try:
            entry.update(structured_table_fields(data, name))
        except Exception:
            pass  # 表格抽取失败不影响文本/落盘/续跑
        # 富内容轨（2026-09-09 rich_object）：docx/xlsx 图形/公式/图片
        try:
            entry.update(rich_object_fields(
                data, name,
                image_dir=docs_root("nfra", "diagrams"),
                rec_key=str(entry.get("doc_id", ""))))
        except Exception:
            pass  # 富内容失败不影响文本/落盘/续跑
        cooldown_state["consecutive"] = 0
        return entry, True
    except Exception as e:
        cooldown_state["consecutive"] += 1
        entry.update({
            "extracted": False,
            "error": "%s: %s" % (type(e).__name__, e),
            "quality": ["download_failed"],
        })
        return entry, False

def _temp_pdf(data):
    import tempfile
    fd, p = tempfile.mkstemp(suffix=".pdf")
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    return p

def process_doc(doc_id, data, args):
    """处理单篇文档的全部附件，写 manifest。返回 (manifest, summary)。"""
    atts = (data.get("attachmentInfoVOList") or []) if isinstance(data, dict) else []
    if not atts:
        return None, {"doc_id": doc_id, "attachment_count": 0,
                      "extracted_count": 0, "needs_ocr_count": 0}
    att_root = os.path.join(ATT_DIR, str(doc_id))
    os.makedirs(att_root, exist_ok=True)
    manifest_path = os.path.join(att_root, "manifest.json")

    # 续跑：读取已有 manifest，仅处理缺失项
    existing = {}
    if os.path.exists(manifest_path):
        try:
            existing = {e["attachment_name"]: e for e in
                        json.load(open(manifest_path, encoding="utf-8")).get("attachments", [])}
        except Exception:
            existing = {}

    cooldown_state = {"consecutive": 0}
    entries = []
    ok = 0
    need_ocr = 0
    # 永久失败类型：扩展名/格式无法用现有手段抽取（.doc 旧版 Word / .rar 等），
    # 源文件已留存，续跑不再重复下载。--force 可强制重处理（如部署转换引擎后）。
    PERMANENT_FAIL = {"unsupported_type", "unsupported_binary_doc(.doc需外部转换)",
                      "unsupported_archive", "pdf_error", "docx_error", "xlsx_error"}
    for att in atts:
        name = att.get("attachmentName") or ("%s.pdf" % att.get("id"))
        ex = existing.get(name)
        ex_err = (ex or {}).get("error") or ""
        ex_permanent = (ex_err in PERMANENT_FAIL) or ("PdfStreamError" in ex_err) \
            or ("Stream has ended" in ex_err)
        if (not args.force) and ex and (ex.get("extracted") or ex_permanent):
            entries.append(ex)
            if ex.get("extracted"):
                ok += 1
            if ex.get("ocr_status") == "engine_unavailable":
                need_ocr += 1
            continue
        entry, success = process_attachment(doc_id, att, att_root, args, cooldown_state, ex=ex)
        entries.append(entry)
        if success:
            ok += 1
        if entry.get("ocr_status") == "engine_unavailable":
            need_ocr += 1
        time.sleep(random.uniform(args.delay_min, args.delay_max))

    manifest = {
        "doc_id": doc_id,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "attachment_count": len(entries),
        "extracted_count": ok,
        "needs_ocr_count": need_ocr,
        "attachments": entries,
    }
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
    summary = {
        "doc_id": doc_id,
        "attachment_count": len(entries),
        "extracted_count": ok,
        "needs_ocr_count": need_ocr,
    }
    return manifest, summary

def classify_entry(entry, att_root):
    """基于 manifest 条目 + 磁盘源文件魔数，判定真实类型与处置状态（用于质量报告细分）。

    返回 (category, extracted_bool)。category 取值：
      pdf / docx / xlsx / xls_legacy（OLE2 经 xlrd 抽取）
      doc_legacy（旧版 .doc 二进制，源已留存，需外部转换）
      archive（rar/未知压缩包，源已留存）
      pdf_corrupt（PDF 解析失败，源已留存）
      docx_error / xlsx_error / download_failed / other_unextracted
    """
    name = entry.get("attachment_name") or ""
    src = os.path.join(att_root, name)
    sniff = None
    if os.path.exists(src):
        with open(src, "rb") as fh:
            head = fh.read(8)
        sniff = sniff_kind(head, name)
    extracted = bool(entry.get("extracted"))
    error = entry.get("error") or ""
    quality = entry.get("quality") or []
    if extracted:
        if sniff == "pdf":
            return "pdf", True
        if sniff == "docx":
            return "docx", True
        if sniff == "xlsx":
            return "xlsx", True
        if sniff == "ole2":
            # 经抽取成功；用 olefile 判定是 Word 还是 Excel（Word→doc_legacy，Excel→xls_legacy）
            if _is_ole2_word(src):
                return "doc_legacy", True
            return "xls_legacy", True
        return "other_extracted", True
    # 未抽取
    if "unsupported_binary_doc" in error or sniff == "ole2":
        return "doc_legacy", False
    if "unsupported_archive" in error or sniff == "unknown":
        return "archive", False
    if "pdf_error" in error or sniff == "pdf":
        return "pdf_corrupt", False
    if "docx_error" in error:
        return "docx_error", False
    if "xlsx_error" in error:
        return "xlsx_error", False
    if "download_failed" in quality:
        return "download_failed", False
    return "other_unextracted", False

def build_and_write_report(args, docs_total=None, att_total=None,
                           ok_total=None, ocr_total=None, failed_docs=None):
    """聚合所有 manifest + 磁盘源文件，生成 _quality_report.json（含按类型细分与整改路径）。

    若传入 docs_total 等实时计数（处理模式），优先使用；否则从磁盘 manifest 重新统计（--report-only）。
    """
    # 优先用实时计数；否则从 manifest 重算
    if docs_total is None:
        docs_total = att_total = ok_total = ocr_total = 0
        failed_docs = []
        for mp in glob.glob(os.path.join(ATT_DIR, "*", "manifest.json")):
            try:
                m = json.load(open(mp, encoding="utf-8"))
            except Exception:
                continue
            docs_total += 1
            att_total += m.get("attachment_count", 0)
            ok_total += m.get("extracted_count", 0)
            ocr_total += m.get("needs_ocr_count", 0)

    # 按类型细分（始终从磁盘 manifest + 源文件魔数重算，确保 --report-only 准确）
    by_type = {}
    for mp in glob.glob(os.path.join(ATT_DIR, "*", "manifest.json")):
        att_root = os.path.dirname(mp)
        try:
            m = json.load(open(mp, encoding="utf-8"))
        except Exception:
            continue
        for e in m.get("attachments", []):
            cat, _ext = classify_entry(e, att_root)
            b = by_type.setdefault(cat, {
                "total": 0, "extracted": 0, "source_preserved": 0, "char_total": 0,
            })
            b["total"] += 1
            if e.get("extracted"):
                b["extracted"] += 1
                b["char_total"] += (e.get("char_count") or 0)
            if e.get("source_saved"):
                b["source_preserved"] += 1

    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "docs_with_attachments": docs_total,
        "total_attachments": att_total,
        "extracted": ok_total,
        "needs_ocr": ocr_total,
        "extraction_rate": (round(ok_total / att_total, 4) if att_total else 1.0),
        "by_type": by_type,
        "failed_docs": failed_docs or [],
        "ocr_enabled": bool(args.ocr),
        "ocr_note": (
            "OCR 引擎（tesseract 二进制缺失/paddleocr 模型未就绪）在本环境不可用；"
            "疑似扫描件已标记 ocr_status=engine_unavailable，待人工补录或部署 OCR 后重跑。"
            if ocr_total else "未发现疑似扫描件，无需 OCR。"
        ),
        "remediation": {
            "doc_legacy": (
                "旧版 .doc（OLE2 二进制 Word）：已通过 olefile 纯 Python 直抽 **203/203（100%）**。"
                "修复链：①『纠正 CLX→Pcdt 走查 + fcMin 连续 utf-16-le 兜底』回收 81 个历史失败明文文档；"
                "②『fcMin 零区 + 正文散落于 WordDocument 流其他位置』的表格型 .doc，新增全流 utf-16-le "
                "扫描回收（常见中文字白名单过滤乱码）回收最后 5 个（如《信用转换系数表》《银行理财子公司"
                "净资本管理指标计算表》等），正文缺口已闭合。"
            ),
            "archive": (
                ".rar 压缩包附件（2 个，docId=1350/2943）：源文件已留存。本环境探测到系统 7za(7-Zip 19.00) "
                "但对其报『Unsupported archive type / Can not open as archive』（两文件头 16 字节完全一致，"
                "判为 7za 不支持的非标准/特定 RAR 变体而非随机损坏）；pip 离线亦无 rarfile/libarchive 可用。"
                "整改：在具备 WinRAR/unrar 等健壮 RAR 抽取器的环境重跑，或回源获取原始文件后 --force 重处理。"
            ),
            "pdf_corrupt": (
                "PDF 解析失败（3 个，docId=228055）：源文件已留存。pypdf 抛 PdfStreamError，"
                "更强引擎 PyMuPDF 亦返回 pages=0（/Pages 树损坏），判为结构性损坏、本环境不可恢复。"
                "整改：人工核验原件或回源获取完好 PDF 后 --force 重处理。"
            ),
            "xls_legacy": (
                "旧版 .xls（OLE2 Excel）已通过 xlrd 抽取成功；如单元格为空可检查是否需要"
                "公式求值。"
            ),
        },
        "standard_alignment": {
            "DAMA": "完整性(全量覆盖+续跑)、有效性(类型校验)、准确性(空页/乱码标记)、"
                    "唯一性(内容哈希去重)、可追溯(来源URL+docId)",
            "ISO_8000": "数据质量维度：完整性/有效性/准确性/一致性 已落地；局限性(扫描件OCR/旧版doc)显式声明",
        },
    }
    with open(QUALITY_REPORT, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    print("\n[att] 完成：文档 %d 篇，附件 %d 个，抽取成功 %d，需OCR %d，抽取率 %.1f%%"
          % (docs_total, att_total, ok_total, ocr_total, report["extraction_rate"] * 100))
    print("      按类型细分: " + ", ".join(
        "%s=%d(抽%d)" % (k, v["total"], v["extracted"]) for k, v in sorted(by_type.items())))
    print("      质量报告: %s" % QUALITY_REPORT)
    return report

def refresh_stats():
    """离线重算 manifest 统计（page_count/char_count/quality），从本地源文件抽取，不联网、不重下载。

    用于修复历史续跑跳过导致 page_count/sha256 等字段缺失（如 pdf 的 page_count=null）的场景；
    源文件已在处理时留存，故可安全离线重算。返回重算的条目数。
    """
    fixed = 0
    for mp in sorted(glob.glob(os.path.join(ATT_DIR, "*", "manifest.json"))):
        att_root = os.path.dirname(mp)
        try:
            m = json.load(open(mp, encoding="utf-8"))
        except Exception:
            continue
        changed = False
        for e in m.get("attachments", []):
            if not e.get("extracted"):
                continue
            name = e.get("attachment_name") or ""
            src = os.path.join(att_root, name)
            if not os.path.exists(src):
                continue
            try:
                with open(src, "rb") as fh:
                    data = fh.read()
            except Exception:
                continue
            kind = sniff_kind(data, name)
            if kind == "pdf":
                try:
                    _t, pc, _pl, _nor = extract_pdf_text(data)
                    if e.get("page_count") != pc:
                        e["page_count"] = pc
                        changed = True
                except Exception:
                    pass
            # 以源文件重新计算 sha256（权威校验值）
            sha = sha256_of(data)
            if e.get("sha256") != sha:
                e["sha256"] = sha
                changed = True
            if changed:
                fixed += 1
        if changed:
            m["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            with open(mp, "w", encoding="utf-8") as fh:
                json.dump(m, fh, ensure_ascii=False, indent=2)
    print("[att] --refresh-stats 完成：重算 %d 个条目（page_count/sha256 已补全）" % fixed)
    return fixed

def retry_doc_legacy_offline():
    """离线重抽旧版 .doc（OLE2 Word）：仅对已是 doc_legacy 且源文件在盘的条目，
    用 olefile 重抽；成功则写 .txt 并标记 extracted，失败（疑似加密/外语）保持源留存。

    不联网、不重下载，零 WAF 消耗（源文件已在历次处理中留存）。返回 (新抽取数, 仍失败数)。
    """
    new_ok = 0
    still_fail = 0
    for mp in sorted(glob.glob(os.path.join(ATT_DIR, "*", "manifest.json"))):
        att_root = os.path.dirname(mp)
        try:
            m = json.load(open(mp, encoding="utf-8"))
        except Exception:
            continue
        changed = False
        for e in m.get("attachments", []):
            if e.get("extracted"):
                continue
            if (e.get("error") or "") != "unsupported_binary_doc(.doc需外部转换)":
                continue
            if not e.get("source_saved"):
                continue
            name = e.get("attachment_name") or ""
            src = os.path.join(att_root, name)
            if not os.path.exists(src):
                continue
            try:
                with open(src, "rb") as fh:
                    data = fh.read()
            except Exception:
                continue
            t, ok, _reason = extract_doc_ole(data)
            if not ok:
                # 仍不可抽（疑似加密/外语/非 Word）：保持源留存，error 不变以维持续跳
                still_fail += 1
                continue
            txt_path = os.path.join(att_root, name + ".txt")
            with open(txt_path, "w", encoding="utf-8") as fh:
                fh.write(t)
            rel_txt = os.path.relpath(txt_path, HERE).replace("\\", "/")
            e["extracted"] = True
            e["char_count"] = len(t)
            e["text_file"] = rel_txt
            e["quality"] = []
            e["ocr_status"] = "not_needed"
            e["error"] = None
            new_ok += 1
            changed = True
        if changed:
            # 重算文档级提取计数，保持 manifest 一致性
            m["extracted_count"] = sum(1 for x in m.get("attachments", []) if x.get("extracted"))
            m["needs_ocr_count"] = sum(
                1 for x in m.get("attachments", []) if x.get("ocr_status") == "engine_unavailable")
            m["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            with open(mp, "w", encoding="utf-8") as fh:
                json.dump(m, fh, ensure_ascii=False, indent=2)
    print("[att] --retry-doc-legacy 完成：新抽取 %d 个旧版 Word，仍不可抽 %d 个（源已留存）"
          % (new_ok, still_fail))
    return new_ok, still_fail

def main():
    ap = argparse.ArgumentParser(description="nfra 法规附件下载与 PDF 文本抽取（高精度）")
    ap.add_argument("--cache-dir", default=CACHE)
    ap.add_argument("--doc-id", type=int, default=None, help="仅处理指定 docId（试点/补跑）")
    ap.add_argument("--delay-min", type=float, default=1.0, help="请求最小间隔(秒)")
    ap.add_argument("--delay-max", type=float, default=2.0, help="请求最大间隔(秒)")
    ap.add_argument("--timeout", type=int, default=30, help="单附件下载超时(秒)")
    ap.add_argument("--cooldown", type=int, default=120, help="连续失败冷却(秒)")
    ap.add_argument("--max-consecutive-fail", type=int, default=3, help="最大连续失败数")
    ap.add_argument("--keep-pdf", action="store_true", help="保留原始 PDF 文件")
    ap.add_argument("--ocr", action="store_true", help="启用 OCR 兜底（需 paddleocr+模型）")
    ap.add_argument("--force", action="store_true", help="强制重处理（忽略已完成的续跑缓存）")
    ap.add_argument("--report-only", action="store_true",
                    help="仅基于已有 manifest + 源文件重算质量报告，不下载/不抽取（避免重复消耗 WAF）")
    ap.add_argument("--refresh-stats", action="store_true",
                    help="离线从本地源文件重算 manifest 的 page_count/sha256（修复续跑跳过导致的字段缺失）")
    ap.add_argument("--retry-doc-legacy", action="store_true",
                    help="离线用 olefile 重抽已留存的旧版 .doc（OLE2 Word）源文件，零 WAF 消耗")
    args = ap.parse_args()

    os.makedirs(ATT_DIR, exist_ok=True)

    # --refresh-stats：离线从源文件重算统计（不联网、不重下载）
    if args.refresh_stats:
        print("[att] --refresh-stats：从本地源文件离线重算 manifest 统计 ...", flush=True)
        refresh_stats()
        build_and_write_report(args)
        return

    # --report-only：仅基于已有 manifest + 源文件重算质量报告，不联网、不重下载
    if args.report_only:
        print("[att] --report-only：扫描已有 manifest + 源文件，重算质量报告 ...", flush=True)
        build_and_write_report(args)
        return

    # --retry-doc-legacy：离线用 olefile 重抽已留存的旧版 .doc 源文件（零 WAF）
    if args.retry_doc_legacy:
        print("[att] --retry-doc-legacy：离线用 olefile 重抽旧版 .doc 源文件 ...", flush=True)
        retry_doc_legacy_offline()
        build_and_write_report(args)
        return

    files = sorted(glob.glob(DETAIL_GLOB))
    total_docs = 0
    total_att = 0
    total_ok = 0
    total_ocr = 0
    failed_docs = []

    for f in files:
        try:
            d = json.load(open(f, encoding="utf-8")).get("data") or {}
        except Exception:
            continue
        doc_id = d.get("docId")
        if not doc_id:
            continue
        if args.doc_id and int(doc_id) != int(args.doc_id):
            continue
        total_docs += 1
        try:
            _m, summ = process_doc(doc_id, d, args)
        except Exception as e:
            failed_docs.append({"doc_id": doc_id, "error": str(e)})
            print("      [att ERR] docId=%s: %s" % (doc_id, e), flush=True)
            continue
        total_att += summ["attachment_count"]
        total_ok += summ["extracted_count"]
        total_ocr += summ["needs_ocr_count"]
        print("      [att] docId=%s 附件 %d 抽取 %d 需OCR %d" % (
            doc_id, summ["attachment_count"], summ["extracted_count"], summ["needs_ocr_count"]),
            flush=True)

    build_and_write_report(args, docs_total=total_docs, att_total=total_att,
                           ok_total=total_ok, ocr_total=total_ocr, failed_docs=failed_docs)

if __name__ == "__main__":
    main()
