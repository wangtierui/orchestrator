#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
回填脚本：将「规范性文件」中 content/summary 仅为 PDF 文件名、file_type=null、fetch_status=skipped_existing
的记录的 PDF 正文提取为结构化文字，并回填。

流程（对每条目标记录）：
  1. 拉取 detail_url 的 HTML 详情页，提取页面内全部 .pdf 链接（绝对化、去重、保序）。
  2. 逐一下载 PDF（带 UA 轮换 / Referer / 退避重试 / 超时）。
  3. 文本解析：优先 pdfplumber 抽取文本层（含中文即采用，避免对正常文本做 OCR）；若文本层缺失/过少（疑似扫描件），回退 OCR（PyMuPDF 渲染 + Tesseract 识别）。
  4. 回填：content=合并全文、summary=前 200 字摘要、file_type="pdf"、fetch_status="fetched"；
     同时把 PDF 原件存到 attachments/规范性文件/ 并记录 local_path（供前端预览）。
  5. 错误分类：no_pdf_link / network_error / http_<code> / parse_failed / ocr_unavailable，
     失败记录保持原 fetch_status 以便后续重试，并在 error 字段标注原因。

设计要点：
  - 保持原 JSON 结构不变：仅修改匹配记录的 content/summary/file_type/fetch_status/local_path/error，
    其余字段与记录原样保留。
  - 断点续跑：以 fetch_status=='skipped_existing' 且 content 为 .pdf 名为"未处理"判定；处理成功后状态改变，
    重跑自动跳过已处理项，且每处理一条即增量写盘。
  - 合规：仅抓取公开法规页，随机延迟 1~3s，遵守网络礼节；缺失字段不臆造。

依赖：标准库 + pdfplumber（文本层解析）+ PyMuPDF（渲染）+ Tesseract/pytesseract（OCR，无需 onnxruntime，本环境兼容）；中文包 chi_sim/eng 置于工作区 tessdata/ 并由 TESSDATA_PREFIX 指向。
"""

# ---- P3b 仓库引导（migrate_collectors_p3b.py 注入）：使 std_lib/config 可导入 ----
import os as _os
import sys as _sys

_GUIDE_ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "..", ".."))
if _GUIDE_ROOT not in _sys.path:
    _sys.path.insert(0, _GUIDE_ROOT)
del _GUIDE_ROOT, _os, _sys
import argparse
import json
import os
import random
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime

# ---------- 配置 ----------
# 2026-09-08 docs_root 统一根：修正原 BASE=collectors 落点（曾指 collectors/data/… 分叉）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # modules/regulatory_scrapers
from std_lib.scraper_std.cache_store import docs_root  # noqa: E402

ATTACHMENTS_DIR = docs_root("pbc", "attachments")
DEFAULT_JSON = os.path.join(ROOT, "data", "raw", "pbc_laws.json")
TARGET_CATEGORY = "规范性文件"
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
]
OCR_MIN_CHARS = 30          # 单页平均低于此值视为扫描件，触发 OCR
TEXT_FALLBACK_THRESHOLD = 50  # pdfplumber 总字符低于此值也认为需 OCR
SUMMARY_LEN = 200

# ---------- 工具 ----------
def safe_filename(title, ext, url):
    """生成本地安全文件名：hash 前缀防重名 + 可读标题 + 小写扩展名。

    （共享库对齐审计 阶段 1）核心逻辑（去非法字符 / 限长 / URL 哈希前缀）委托
    ``crawler_common.safe_filename``，本函数仅补扩展名；**保留 pbc 原有**限长 60**
    （gov 用共享库默认 120），确保落盘文件名长度口径与改造前一致、签名不变。
    """
    repo_root = os.path.join(ROOT, "..")
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    from std_lib.scraper_std.crawler_common import safe_filename as _cc_safe_filename
    base = _cc_safe_filename(title, url, 60)
    ext = (ext or "bin").lower()
    return f"{base}.{ext}"

def is_pdf_name(s):
    return isinstance(s, str) and s.strip().lower().endswith(".pdf")

def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

# ---------- 下载（带重试/退避） ----------
def http_get(url, referer=None, binary=False, timeout=30, max_retry=3):
    last_err = None
    for attempt in range(1, max_retry + 1):
        ua = random.choice(USER_AGENTS)
        headers = {"User-Agent": ua, "Accept-Language": "zh-CN,zh;q=0.9"}
        if referer:
            headers["Referer"] = referer
        if binary:
            headers["Accept"] = "*/*"
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                status = r.getcode()
                data = r.read()
                if status != 200:
                    return status, None
                return status, data
        except urllib.error.HTTPError as e:
            last_err = f"http_{e.code}"
            if e.code in (404, 403, 410):
                return e.code, None
            time.sleep(min(2 ** attempt, 8) + random.random())
        except Exception as e:  # 网络异常 / 超时
            last_err = f"{type(e).__name__}: {e}"
            time.sleep(min(2 ** attempt, 8) + random.random())
    if last_err:
        log(f"下载失败(重试耗尽): {last_err}")
    return None, None  # 标记网络失败

# ---------- 提取 PDF 链接 ----------
def extract_pdf_links(html, page_url):
    # 1) 优先 href / src 中的 .pdf
    links = re.findall(r'(?:href|src)=["\']([^"\']+\.pdf(?:\?[^"\']*)?)["\']', html, re.I)
    if not links:
        # 2) 兜底：任意带路径的 .pdf 引用（排除纯文本提及）
        links = re.findall(r'["\']((?:https?:)?//[^"\']+\.pdf|[^"\']*/[^"\']+\.pdf)["\']', html, re.I)
    seen, out = set(), []
    for lnk in links:
        abs_url = urllib.parse.urljoin(page_url, lnk)
        if abs_url not in seen:
            seen.add(abs_url)
            out.append(abs_url)
    return out

# ---------- PDF 文本解析（pdfplumber 文本层优先，Tesseract OCR 回退） ----------
# OCR 引擎：优先 PaddleOCR（std_lib ocr_engine），Tesseract 作降级。
# P1 去硬编码（R17）：路径由 config/ocr.yaml / env OCR_TESSERACT_BIN 提供，空则视为不可用。
TESSERACT_CMD = os.environ.get("OCR_TESSERACT_BIN", "")
TESSDATA_DIR = os.path.join(ROOT, "tessdata")   # 工作区内 chi_sim/eng 训练数据
OCR_DPI = 220                                  # 渲染分辨率：平衡识别率与耗时
_OCR_AVAIL = None

def tesseract_available():
    """懒校验 Tesseract 可执行文件 + 中文包是否就绪。"""
    global _OCR_AVAIL
    if _OCR_AVAIL is not None:
        return _OCR_AVAIL
    try:
        import pytesseract
        if not os.path.exists(TESSERACT_CMD):
            _OCR_AVAIL = False
            return False
        pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD
        os.environ["TESSDATA_PREFIX"] = TESSDATA_DIR
        langs = pytesseract.get_languages()
        if "chi_sim" not in langs:
            _OCR_AVAIL = False
            return False
        _OCR_AVAIL = True
        return True
    except Exception:
        _OCR_AVAIL = False
        return False

def ocr_image_to_text(img):
    """对 PIL.Image(RGB) 做中文 OCR（统一 OCR 模块：PaddleOCR 默认 + Tesseract 降级）。

    修复：原实现仅走 Tesseract 且硬编码路径/语言包，且缺少 PaddleOCR 路径；
    现经 std_lib/scraper_std/ocr_engine，优先 PaddleOCR 3.7.0（中文 PP-OCRv6），
    失败自动降级 Tesseract v5，并规避 Paddle#77340 oneDNN/PIR 崩溃
    （disable_mkldnn 默认开启）。图像预处理（灰度/自动对比度/中值去噪）已由
    ocr_engine 的 Tesseract 引擎统一实现，相邻中文空格清理亦由 _post_process 完成。
    """
    repo_root = os.path.join(ROOT, "..")
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    from std_lib.scraper_std.ocr_engine import get_ocr
    res = get_ocr().recognize_image(img)
    return res.text or ""

def parse_pdf_text(data):
    """返回 (text, method, note)。method: 'text' | 'ocr' | 'empty'。"""
    # 1) pdfplumber 文本层（优先，避免对正常文本做 OCR）
    try:
        import io

        import pdfplumber
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            pages = [(p.extract_text() or "") for p in pdf.pages]
        text = "\n".join(pages).strip()
        cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
        # 文本层足够且含中文 → 直接采用
        if len(text) >= TEXT_FALLBACK_THRESHOLD and cjk >= 10:
            return text, "text", f"pdfplumber 提取 {len(pages)} 页"
    except Exception as e:
        return "", "empty", f"pdfplumber 失败: {e}"

    # 2) OCR 回退（扫描件 / 文本层缺失）—— 统一 OCR 模块（PaddleOCR 默认 + Tesseract 降级）
    try:
        repo_root = os.path.join(ROOT, "..")
        if repo_root not in sys.path:
            sys.path.insert(0, repo_root)
        from std_lib.scraper_std.ocr_engine import get_ocr
        _ocr_ok = any(get_ocr().health().values())
    except Exception:
        _ocr_ok = False
    if not _ocr_ok:
        return "", "empty", "OCR 引擎(PaddleOCR/Tesseract)均不可用"
    try:
        import pymupdf as fitz  # PyMuPDF（现代导入名）
    except Exception as e:
        return "", "empty", f"PDF 渲染库不可用: {e}"
    try:
        doc = fitz.open(stream=data, filetype="pdf")
        parts = []
        for page in doc:
            pix = page.get_pixmap(dpi=OCR_DPI)
            import io as _io

            import numpy as np
            from PIL import Image
            arr = np.frombuffer(pix.tobytes("png"), dtype=np.uint8)
            pil = Image.open(_io.BytesIO(arr))
            parts.append(ocr_image_to_text(pil))
        text = "\n".join(parts).strip()
        if text:
            return text, "ocr", f"OCR 识别 {len(doc)} 页"
        return "", "empty", "OCR 未识别到文字"
    except Exception as e:
        return "", "empty", f"OCR 执行失败: {e}"

def build_summary(text):
    flat = re.sub(r"\s+", " ", (text or "")).strip()
    if len(flat) <= SUMMARY_LEN:
        return flat
    return flat[:SUMMARY_LEN] + "…"

# ---------- 单条处理 ----------
def process_record(rec, args):
    title = rec.get("title", "")
    page_url = rec.get("detail_url")
    referer = "https://www.pbc.gov.cn/tiaofasi/144941/3581332/"
    used_method = None
    # 1) 拉详情页
    st, html_bytes = http_get(page_url, referer=referer, timeout=25)
    if st != 200 or not html_bytes:
        rec["error"] = f"详情页获取失败 status={st}"
        return "fail_detail", None
    html = html_bytes.decode("utf-8", "ignore")
    # 2) 提取 PDF 链接
    pdf_links = extract_pdf_links(html, page_url)
    if not pdf_links:
        rec["error"] = "详情页未找到 PDF 链接"
        return "no_pdf_link", None
    # 3) 下载 + 解析每个 PDF
    adir = os.path.join(ATTACHMENTS_DIR, TARGET_CATEGORY)
    os.makedirs(adir, exist_ok=True)
    texts, errors = [], []
    any_text = False
    for url in pdf_links:
        st2, bdata = http_get(url, referer=page_url, binary=True, timeout=60)
        if st2 != 200 or not bdata:
            errors.append(f"{url} 下载失败 status={st2}")
            continue
        fname = safe_filename(title, "pdf", url)
        fpath = os.path.join(adir, fname)
        with open(fpath, "wb") as fh:
            fh.write(bdata)
        text, method, note = parse_pdf_text(bdata)
        if text:
            any_text = True
            used_method = method
            texts.append(text)
            rec.setdefault("local_path", os.path.relpath(fpath, ROOT).replace("\\", "/"))
        else:
            errors.append(f"{url} 解析为空({method}: {note})")
    if not any_text:
        rec["error"] = "所有 PDF 均未提取到文字 | " + "; ".join(errors[:3])
        return "parse_failed", None
    # 4) 回填
    full = "\n\n".join(texts).strip()
    rec["content"] = full
    rec["summary"] = build_summary(full)
    rec["file_type"] = "pdf"
    rec["fetch_status"] = "fetched"
    if errors:
        rec["error"] = "部分 PDF 失败: " + "; ".join(errors[:3])
    else:
        rec["error"] = None
    return "ok", used_method

# ---------- 主流程 ----------
def main():
    ap = argparse.ArgumentParser(description="规范性文件 PDF 正文回填")
    ap.add_argument("--json", default=DEFAULT_JSON)
    ap.add_argument("--out-dir", default=os.path.join(ROOT, "data", "raw"), help="输出/附件根目录（默认 data/raw/）")
    ap.add_argument("--limit", type=int, default=0, help="仅处理前 N 条（调试）")
    ap.add_argument("--delay", type=float, default=1.5, help="每条间随机延迟基准秒")
    ap.add_argument("--save-every", type=int, default=10, help="每处理 N 条增量写盘")
    args = ap.parse_args()

    data = json.load(open(args.json, encoding="utf-8"))
    targets = [r for r in data
               if r.get("category") == TARGET_CATEGORY
               and not r.get("file_type")
               and is_pdf_name(r.get("content"))
               and is_pdf_name(r.get("summary"))
               and r.get("fetch_status") == "skipped_existing"]
    log(f"命中目标记录: {len(targets)} 条")
    if args.limit:
        targets = targets[:args.limit]
        log(f"调试模式：仅处理前 {len(targets)} 条")

    stats = {"ok": 0, "fail_detail": 0, "no_pdf_link": 0, "parse_failed": 0}
    failures = []
    for i, rec in enumerate(targets, 1):
        try:
            result, method = process_record(rec, args)
        except Exception as e:
            result, method = "exception", None
            rec["error"] = f"未预期异常: {type(e).__name__}: {e}"
        stats[result] = stats.get(result, 0) + 1
        title = rec.get("title", "")[:30]
        if result == "ok":
            log(f"[{i}/{len(targets)}] OK  | {title}  | 提取方式={method}")
        else:
            failures.append((rec.get("title"), result, rec.get("error")))
            log(f"[{i}/{len(targets)}] {result.upper()} | {title} | {rec.get('error')}")
        # 增量写盘
        if i % args.save_every == 0:
            json.dump(data, open(args.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            log(f"  已增量保存（至第 {i} 条）")
        time.sleep(random.uniform(args.delay, args.delay * 2))

    # 最终写盘
    json.dump(data, open(args.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    log("=" * 50)
    log(f"完成。统计: {stats}")
    log(f"成功回填(fetched): {stats.get('ok',0)}；失败: {sum(v for k,v in stats.items() if k!='ok')}")
    if failures:
        log("失败样本（前 10）:")
        for t, r, e in failures[:10]:
            log(f"  - {t[:30]} | {r} | {str(e)[:80]}")
    # 写出失败清单便于复核
    if failures:
        with open(os.path.join(ROOT, "data", "reports", "pbc_regulations_scraper", "backfill_failures.json"), "w", encoding="utf-8") as f:
            json.dump([{"title": t, "result": r, "error": e} for t, r, e in failures],
                      f, ensure_ascii=False, indent=1)
        log(f"失败明细已写入 backfill_failures.json（{len(failures)} 条）")

if __name__ == "__main__":
    main()
