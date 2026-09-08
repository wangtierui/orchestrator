# -*- coding: utf-8 -*-
"""修复 6 条国家法律 .doc（Word97 二进制）正文：UTF-16LE 抽取（pbc_recover.py 漏了此法）。"""

# ---- P3b 仓库引导（migrate_collectors_p3b.py 注入）：使 std_lib/config 可导入 ----
import os as _os
import sys as _sys

_GUIDE_ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "..", ".."))
if _GUIDE_ROOT not in _sys.path:
    _sys.path.insert(0, _GUIDE_ROOT)
del _GUIDE_ROOT, _os, _sys
import json
import os
import re
import sys

import requests

sys.stdout.reconfigure(encoding="utf-8")
BASE = os.path.dirname(os.path.abspath(__file__))
JSON = os.path.join(BASE, "data", "raw", "pbc_laws.json")
HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0 Safari/537.36",
       "Accept": "*/*", "Referer": "https://www.pbc.gov.cn/"}

def strip_att(c):
    ls = [l.rstrip() for l in c.splitlines()]
    while ls and ls[-1].strip().lower().endswith(".pdf"):
        ls.pop()
    return "\n".join(ls).strip()

def has_body(rec):
    c = (rec.get("content") or "").strip()
    if not c: return False
    b = strip_att(c)
    if len(b) < 80: return False
    return len(re.findall(r"[一-鿿]", b)) >= 30

def extract_doc_text(b):
    # 1) UTF-16LE（中文 Word97 .doc 标准）
    try:
        s = b.decode("utf-16le", errors="ignore")
        runs = re.findall(r"[一-鿿][一-鿿，。、；：（）《》\s，]{20,}", s)
        t = "\n".join(runs).strip()
        if len(re.findall(r"[一-鿿]", t)) >= 200:
            return t
    except Exception:
        pass
    # 2) UTF-16BE 兜底
    try:
        s = b.decode("utf-16be", errors="ignore")
        runs = re.findall(r"[一-鿿][一-鿿，。、；：（）《》\s，]{20,}", s)
        t = "\n".join(runs).strip()
        if len(re.findall(r"[一-鿿]", t)) >= 200:
            return t
    except Exception:
        pass
    return ""

def build_summary(text, n=200):
    return text[:n].replace("\n", " ").strip()

data = json.load(open(JSON, encoding="utf-8"))
targets = [r for r in data if r.get("category") == "国家法律"
           and (r.get("detail_url") or "").lower().endswith(".doc")
           and not has_body(r)]
print(f"需修复 .doc 记录: {len(targets)}")
fixed = 0
for r in targets:
    url = r["detail_url"]; title = r.get("title", "")
    try:
        resp = requests.get(url, headers=HDR, timeout=60)
        if resp.status_code != 200 or not resp.content:
            r["error"] = f"下载失败 status={resp.status_code}"; print("  FAIL", title, r["error"]); continue
        adir = os.path.join(BASE, "data", "docs", "pbc_regulations_scraper", "attachments", "国家法律")
        os.makedirs(adir, exist_ok=True)
        fn = re.sub(r"[\\/:*?\"<>|]", "_", title) + ".doc"
        fpath = os.path.join(adir, fn)
        open(fpath, "wb").write(resp.content)
        text = extract_doc_text(resp.content)
        if text:
            r["content"] = text
            r["summary"] = build_summary(text)
            r["file_type"] = "doc"
            r["fetch_status"] = "fetched"
            r["local_path"] = os.path.relpath(fpath, BASE).replace("\\", "/")
            r["error"] = None
            fixed += 1
            print(f"  OK  {title[:24]} | {len(text)} chars | saved {fn}")
        else:
            r["error"] = "UTF-16 抽取为空"; print("  EMPTY", title)
    except Exception as e:
        r["error"] = f"异常:{type(e).__name__}:{e}"[:160]; print("  EXC", title, r["error"])

json.dump(data, open(JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"完成：修复 {fixed}/{len(targets)} 条；已整份写盘（{len(data)} 条）")
