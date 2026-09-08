# -*- coding: utf-8 -*-
"""
recover.py —— pbc_laws.json 灾难恢复脚本（独立于会覆写 JSON 的 pbc_law_scraper.py）

背景：pbc_law_scraper.py 的 --max-items 试跑把 pbc_laws.json（568 条）覆写成 2 条。
本脚本从 pbc_laws.bak.json（568 条，mid-backfill 快照）恢复，并补全正文：

  - 344 条「规范性文件」：content 仅含本地 PDF 文件名 → 直接用已下载的本地 PDF
    （attachments/规范性文件/）做文本层抽取 + Tesseract OCR，不重新下载。
  - 35 条「部门规章」：详情页是 PDF-only stub（无 HTML 正文）→ 抓详情页取 PDF 链接，
    下载并解析，存入 attachments/部门规章/。
  - 6 条「国家法律」：detail_url 直接指向 .doc → 下载并尽力解析（二进制抽取/ python-docx）。

关键：始终以「整份 568 条数组」写盘，绝不截断；每处理 20 条增量落盘。
"""

# ---- P3b 仓库引导（migrate_collectors_p3b.py 注入）：使 std_lib/config 可导入 ----
import os as _os
import sys as _sys

_GUIDE_ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "..", "..", ".."))
if _GUIDE_ROOT not in _sys.path:
    _sys.path.insert(0, _GUIDE_ROOT)
del _GUIDE_ROOT, _os, _sys
import glob
import importlib.util
import io
import json
import os
import re
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

BASE = os.path.dirname(os.path.abspath(__file__))
BAK = os.path.join(BASE, "backups", "pbc_laws.bak.json")
JSON_OUT = os.path.join(BASE, "data", "raw", "pbc_laws.json")
TESS = os.environ.get("OCR_TESSERACT_BIN", "")  # R17：由 config/ocr.yaml/env 提供，空则降级
TESSD = os.path.join(BASE, "tessdata")
SUMMARY_LEN = 200
OCR_DPI = 200
SAVE_EVERY = 20

# 复用 backfill_pdfs 的下载/链接提取工具（经验证可用）
spec = importlib.util.spec_from_file_location("bf", os.path.join(BASE, "backfill_pdfs.py"))
bf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bf)

def log(msg):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

# ---------- 判定是否需要回填 ----------
def needs_extraction(rec):
    c = (rec.get("content") or "").strip()
    if not c:
        return True
    lines = [l.strip() for l in c.splitlines() if l.strip()]
    if not lines:
        return True
    if all(l.lower().endswith(".pdf") for l in lines):
        return True
    return False

def build_summary(text):
    return bf.build_summary(text)

# ---------- 本地 PDF 标题->路径 映射 ----------
def build_local_map():
    m = {}
    d = os.path.join(BASE, "data", "docs", "pbc_regulations_scraper", "attachments", "规范性文件")
    for p in glob.glob(os.path.join(d, "*.pdf")):
        fn = os.path.basename(p)
        tp = fn.split("_", 1)[1][:-4] if "_" in fn else fn[:-4]
        m.setdefault(tp, p)
    return m

def match_local(rec, lmap):
    t = (rec.get("title") or "").strip()
    for tp, p in lmap.items():
        if t.startswith(tp) or tp.startswith(t):
            return p
    return None

# ---------- PDF 解析：文本层优先，Tesseract 直接子进程 OCR 回退 ----------
def parse_pdf_bytes(data):
    # 1) pdfplumber 文本层
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            pages = [(p.extract_text() or "") for p in pdf.pages]
        text = "\n".join(pages).strip()
        cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
        if len(text) >= 50 and cjk >= 10:
            return text, "text"
    except Exception:
        pass
    # 2) 扫描件 → 统一 OCR 模块（PaddleOCR 默认 + Tesseract 降级）
    # 修复：原实现直接调 tesseract 子进程（绕过 pytesseract 包装崩溃），
    # 现统一经 std_lib/scraper_std/ocr_engine，优先 PaddleOCR 3.7.0，失败降级
    # Tesseract v5，并规避 Paddle#77340 oneDNN/PIR 崩溃（disable_mkldnn 默认开启）。
    try:
        import tempfile
        repo_root = os.path.join(BASE, "..")
        if repo_root not in sys.path:
            sys.path.insert(0, repo_root)
        from std_lib.scraper_std.ocr_engine import get_ocr
        fd, tmp = tempfile.mkstemp(suffix=".pdf", prefix="recover_ocr_")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            res = get_ocr().extract_pdf(tmp, force_ocr=True)
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
        if res.success and res.text.strip():
            return res.text.strip(), "ocr"
        if not res.success:
            return "", "ocr_fail:" + str(res.error or "no engine available")[:120]
        return "", "empty"
    except Exception as e:
        return "", "ocr_fail:" + str(e)[:120]
    return "", "empty"

# ---------- .doc 尽力解析 ----------
def parse_doc_bytes(data):
    # 1) 尝试按 .docx（zip）解析
    try:
        import io as _io

        import docx
        d = docx.Document(_io.BytesIO(data))
        paras = [p.text for p in d.paragraphs if p.text.strip()]
        if paras:
            return "\n".join(paras).strip(), "docx"
    except Exception:
        pass
    # 2) 二进制抽取 CJK 连续文本段（Word 97-2003 常有可读文本夹杂）
    for enc in ("utf-8", "gbk", "gb18030"):
        try:
            s = data.decode(enc, errors="ignore")
            runs = re.findall(r"[\u4e00-\u9fff][\u4e00-\u9fff\uff00-\uffef0-9a-zA-Z，。、；：（）《》\s，]{20,}", s)
            t = "\n".join(runs).strip()
            if len(t) >= 80:
                return t, "doc_binary"
        except Exception:
            pass
    return "", "doc_unparsed"

# ---------- HTML 正文兜底抽取（仅当详情页本身含正文时） ----------
def extract_html_body(html):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    for sel in ["div.detail", "div.article", "div.content", "div.pages_content",
                "div.TRS_Editor", "div.trs_editor_view", "td.content"]:
        el = soup.select_one(sel)
        if el and len(el.get_text(strip=True)) > 200:
            return el.get_text("\n", strip=True)
    # 取文本最长的 td/div
    best, bl = "", 0
    for el in soup.find_all(["td", "div"]):
        txt = el.get_text("\n", strip=True)
        if len(txt) > bl:
            best, bl = txt, len(txt)
    return best if bl > 200 else ""

# ---------- 主流程 ----------
def main():
    if not os.path.exists(BAK):
        print(f"[!] 备份文件不存在：{BAK}\n     backups/ 目录可能已被清理。恢复功能需先存在备份，"
              f"请运行 merge_scrape.py 重新生成，或从旧备份恢复后重试。")
        return 1
    data = json.load(open(BAK, encoding="utf-8"))
    log(f"已载入备份：{len(data)} 条")
    lmap = build_local_map()
    log(f"本地 PDF 索引：{len(lmap)} 个")

    targets = [r for r in data if needs_extraction(r)]
    log(f"需回填记录：{len(targets)} 条")

    stats = {"local_text": 0, "local_ocr": 0, "download_pdf": 0,
             "html_body": 0, "doc": 0, "empty": 0, "fail": 0}
    failures = []

    for i, rec in enumerate(targets, 1):
        cat = rec.get("category", "")
        title = rec.get("title", "")
        url = rec.get("detail_url", "")
        rec["error"] = None
        try:
            # A) 本地 PDF 直解
            lp = match_local(rec, lmap)
            if lp and cat == "规范性文件":
                bdata = open(lp, "rb").read()
                text, method = parse_pdf_bytes(bdata)
                if text:
                    rec["content"] = text
                    rec["summary"] = build_summary(text)
                    rec["file_type"] = "pdf"
                    rec["fetch_status"] = "fetched"
                    rec["local_path"] = os.path.relpath(lp, BASE).replace("\\", "/")
                    stats["local_text" if method == "text" else "local_ocr"] += 1
                    log(f"[{i}/{len(targets)}] 本地PDF/{method} | {title[:28]}")
                    json.dump(data, open(JSON_OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
                    continue
                else:
                    rec["error"] = f"本地PDF解析空({method})"

            # B) 国家法律 .doc
            if url.lower().endswith(".doc"):
                st, bdata = bf.http_get(url, binary=True, timeout=60)
                if st == 200 and bdata:
                    adir = os.path.join(BASE, "data", "docs", "pbc_regulations_scraper", "attachments", cat)
                    os.makedirs(adir, exist_ok=True)
                    fname = bf.safe_filename(title, "doc", url)
                    fpath = os.path.join(adir, fname)
                    open(fpath, "wb").write(bdata)
                    rec["local_path"] = os.path.relpath(fpath, BASE).replace("\\", "/")
                    text, method = parse_doc_bytes(bdata)
                    if text:
                        rec["content"] = text
                        rec["summary"] = build_summary(text)
                        rec["file_type"] = "doc"
                        rec["fetch_status"] = "fetched"
                        stats["doc"] += 1
                        log(f"[{i}/{len(targets)}] DOC/{method} | {title[:28]}")
                        json.dump(data, open(JSON_OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
                        continue
                    else:
                        rec["error"] = "DOC 无法解析（需 antiword/libreoffice）"
                else:
                    rec["error"] = f"DOC 下载失败 status={st}"
                stats["fail"] += 1
                failures.append((title, "doc", rec["error"]))
                log(f"[{i}/{len(targets)}] DOC_FAIL | {title[:28]} | {rec['error']}")

            # C) HTML 详情页（部门规章 PDF-only stub 或正文页）
            else:
                st, html_bytes = bf.http_get(url, timeout=25)
                if st != 200 or not html_bytes:
                    rec["error"] = f"详情页获取失败 status={st}"
                    stats["fail"] += 1
                    failures.append((title, "detail", rec["error"]))
                    log(f"[{i}/{len(targets)}] DETAIL_FAIL | {title[:28]} | {rec['error']}")
                else:
                    html = html_bytes.decode("utf-8", "ignore")
                    pdf_links = bf.extract_pdf_links(html, url)
                    if pdf_links:
                        adir = os.path.join(BASE, "data", "docs", "pbc_regulations_scraper", "attachments", cat)
                        os.makedirs(adir, exist_ok=True)
                        texts, errs = [], []
                        for lk in pdf_links:
                            st2, bdata = bf.http_get(lk, binary=True, timeout=60)
                            if st2 != 200 or not bdata:
                                errs.append(f"{lk} 下载{st2}")
                                continue
                            fname = bf.safe_filename(title, "pdf", lk)
                            fpath = os.path.join(adir, fname)
                            open(fpath, "wb").write(bdata)
                            rec.setdefault("local_path", os.path.relpath(fpath, BASE).replace("\\", "/"))
                            text, method = parse_pdf_bytes(bdata)
                            if text:
                                texts.append(text)
                            else:
                                errs.append(f"{lk} 解析空({method})")
                        if texts:
                            full = "\n\n".join(texts).strip()
                            rec["content"] = full
                            rec["summary"] = build_summary(full)
                            rec["file_type"] = "pdf"
                            rec["fetch_status"] = "fetched"
                            if errs:
                                rec["error"] = "部分PDF失败:" + "; ".join(errs[:2])
                            stats["download_pdf"] += 1
                            log(f"[{i}/{len(targets)}] 下载PDF/{method} | {title[:28]}")
                            json.dump(data, open(JSON_OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
                            continue
                        else:
                            rec["error"] = "PDF均解析空 | " + "; ".join(errs[:2])
                            stats["fail"] += 1
                            failures.append((title, "pdf_parse", rec["error"]))
                            log(f"[{i}/{len(targets)}] PDF_PARSE_FAIL | {title[:28]} | {rec['error']}")
                    else:
                        # 详情页本身含正文（兜底）
                        body = extract_html_body(html)
                        if body:
                            rec["content"] = body
                            rec["summary"] = build_summary(body)
                            rec["file_type"] = "html"
                            rec["fetch_status"] = "fetched"
                            stats["html_body"] += 1
                            log(f"[{i}/{len(targets)}] HTML_BODY | {title[:28]}")
                            json.dump(data, open(JSON_OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
                            continue
                        else:
                            rec["error"] = "详情页无PDF链接且无正文"
                            stats["fail"] += 1
                            failures.append((title, "no_content", rec["error"]))
                            log(f"[{i}/{len(targets)}] NO_CONTENT | {title[:28]}")
        except Exception as e:
            rec["error"] = f"未预期异常:{type(e).__name__}:{e}"[:200]
            stats["fail"] += 1
            failures.append((title, "exception", rec["error"]))
            log(f"[{i}/{len(targets)}] EXCEPTION | {title[:28]} | {rec['error']}")

        # 增量落盘（每 SAVE_EVERY 条）
        if i % SAVE_EVERY == 0:
            json.dump(data, open(JSON_OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            log(f"  >> 已增量保存（至第 {i} 条）")

    # 最终落盘（整份 568 条）
    json.dump(data, open(JSON_OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    log("=" * 60)
    log(f"恢复完成。回填统计: {stats}")
    log(f"成功: {sum(v for k,v in stats.items() if k not in ('empty','fail'))}；失败: {stats['fail']}")
    if failures:
        with open(os.path.join(BASE, "data", "reports", "pbc_regulations_scraper", "recover_failures.json"), "w", encoding="utf-8") as f:
            json.dump([{"title": t, "stage": s, "error": e} for t, s, e in failures],
                      f, ensure_ascii=False, indent=1)
        log(f"失败明细写入 recover_failures.json（{len(failures)} 条）")

if __name__ == "__main__":
    main()
