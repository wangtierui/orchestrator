# -*- coding: utf-8 -*-
"""
tools/flatten_collectors.py — collectors 物理拍平工具（A 项，2026-09-08）

将 modules/regulatory_scrapers/collectors/ 下 5 源子目录（gov/mof/nfra/pbc/supp 及
supp/utils）的 28 个 .py 平铺到 collectors/ 单层，并加源前缀消歧命名
（与 config/sources.yaml 的 collectors.* 约定对齐），同时：
  1) 统一 P3b 注入的 `_GUIDE_ROOT` 引导深度为 ".."×3（拍平后模块在 collectors/ 单层）；
  2) 同步改写全部内部互引（裸模块 import / 同目录 import / subprocess 文件名 /
     importlib 按文件加载）至新名；
  3) 输出 per-file 报告供人工核查路径装配（缓存根/数据根/state 目录随深度位移）。

幂等：仅当目标不存在且源存在时移动；已拍平（目标已存在）则跳过并提示。

用法：python tools/flatten_collectors.py
"""
from __future__ import annotations

import os
import re

COLLECTORS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "modules", "regulatory_scrapers", "collectors")

# 命名映射：旧相对路径（相对 collectors/） -> 拍平后文件名（collectors/ 单层）
RENAME = {
    "gov/scraper.py": "gov_collector.py",
    "gov/fetch_attachments_gov.py": "gov_fetch_attachments.py",
    "mof/mof_law_scraper.py": "mof_collector.py",
    "nfra/nfra_regulations_scraper.py": "nfra_collector.py",
    "nfra/fetch_lists.py": "nfra_fetch_lists.py",
    "nfra/fill_details.py": "nfra_fill_details.py",
    "nfra/prefetch.py": "nfra_prefetch.py",
    "nfra/seed_cache.py": "nfra_seed_cache.py",
    "nfra/fetch_attachments.py": "nfra_fetch_attachments.py",
    "nfra/validate_cache.py": "nfra_validate_cache.py",
    "nfra/weekly_refresh.py": "nfra_weekly.py",
    "pbc/pbc_law_scraper.py": "pbc_collector.py",
    "pbc/run_batched.py": "pbc_run_batched.py",
    "pbc/recover.py": "pbc_recover.py",
    "pbc/backfill_pdfs.py": "pbc_backfill_pdfs.py",
    "pbc/merge_scrape.py": "pbc_merge_scrape.py",
    "pbc/serve.py": "pbc_serve.py",
    "pbc/fix_doc.py": "pbc_fix_doc.py",
    "supp/ingest_supplements.py": "supp_ingest.py",
    "supp/ingest_batch_supplements.py": "supp_ingest_batch.py",
    "supp/downloader.py": "supp_downloader.py",
    "supp/parser.py": "supp_parser.py",
    "supp/ocr_pdf.py": "supp_ocr_pdf.py",
    "supp/sanitize_newlines.py": "supp_sanitize.py",
    "supp/extract_doc_text.py": "supp_extract_doc_text.py",
    "supp/fix_raw_factual.py": "supp_fix_raw_factual.py",
    "supp/utils/supp_normalize.py": "supp_normalize.py",
    "supp/utils/supp_mapper.py": "supp_mapper.py",
}

# 执行期代码引用替换（旧文件名 -> 新文件名；仅在代码标识出现处替换，避免误伤 docstring 全名）
# key 为「旧 basename（不带 .py）」，value 为新 basename。使用词边界避免前缀重复替换。
CODE_REF = {
    "scraper": None,  # 特殊：scraper.py 只在命令串/动态加载中出现，谨慎不全局替换
    "fetch_attachments_gov": "gov_fetch_attachments",
    "nfra_regulations_scraper": "nfra_collector",
    "mof_law_scraper": "mof_collector",
    "fetch_lists": "nfra_fetch_lists",
    "fill_details": "nfra_fill_details",
    "prefetch": "nfra_prefetch",
    "seed_cache": "nfra_seed_cache",
    "fetch_attachments": "nfra_fetch_attachments",
    "validate_cache": "nfra_validate_cache",
    "weekly_refresh": "nfra_weekly",
    "pbc_law_scraper": "pbc_collector",
    "run_batched": "pbc_run_batched",
    "recover": "pbc_recover",
    "backfill_pdfs": "pbc_backfill_pdfs",
    "merge_scrape": "pbc_merge_scrape",
    "serve": None,
    "fix_doc": "pbc_fix_doc",
    "ingest_supplements": "supp_ingest",
    "ingest_batch_supplements": "supp_ingest_batch",
    "downloader": "supp_downloader",
    "parser": "supp_parser",
    "ocr_pdf": "supp_ocr_pdf",
    "sanitize_newlines": "supp_sanitize",
    "extract_doc_text": "supp_extract_doc_text",
    "fix_raw_factual": "supp_fix_raw_factual",
    "supp_normalize": "supp_normalize",  # 原名保留（上移）
    "supp_mapper": "supp_mapper",        # 原名保留（上移）
}

_GUIDE_RE = re.compile(
    r"_GUIDE_ROOT = _os\.path\.abspath\(_os\.path\.join\("
    r"_os\.path\.dirname\(_os\.path\.abspath\(__file__\)\)"
    r"(?:,\s*\"\.\.\"){2,6}\)\)")


def _rewrite_refs(text: str, new_basename: str) -> str:
    """按 CODE_REF 替换代码中旧模块名/文件名引用（.py 命令串 / 裸 import 两种形态）。"""
    for old, new in CODE_REF.items():
        if not new or old == new_basename or old == os.path.splitext(new_basename)[0]:
            continue
        # 形态1：文件名/命令串/importlib —— "fetch_lists.py" / backfill_pdfs.py（word 边界）
        text = re.sub(
            r"(?<![\w\u4e00-\u9fff])" + re.escape(old) + r"\.py(?![\w\u4e00-\u9fff])",
            new + ".py", text)
        # 形态2：裸模块 import（import <old> / import <old> as x / from <old> import …）
        text = re.sub(
            r"(?m)^(\s*(?:import|from)\s+)" + re.escape(old) + r"(\s+(?:as|import|,)|$)",
            lambda m, _new=new: m.group(1) + _new + m.group(2), text)
    return text


def _fix_guide_depth(text: str) -> str:
    return _GUIDE_RE.sub(
        '_GUIDE_ROOT = _os.path.abspath(_os.path.join('
        "_os.path.dirname(_os.path.abspath(__file__)),"
        ' "..", "..", ".."))', text)


def flatten():
    os.makedirs(COLLECTORS, exist_ok=True)
    moved, skipped, missing = [], [], []
    for rel, newname in RENAME.items():
        src = os.path.join(COLLECTORS, rel.replace("/", os.sep))
        dst = os.path.join(COLLECTORS, newname)
        if not os.path.exists(src):
            missing.append(rel)
            continue
        if os.path.exists(dst):
            skipped.append(rel)
            continue
        # 内容重写先于移动（读旧路径，写新文件）
        text = open(src, encoding="utf-8").read()
        text = _fix_guide_depth(text)
        text = _rewrite_refs(text, newname)
        with open(dst, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.remove(src)
        moved.append(rel)
    # 清理空目录（保留顶层）
    for sub in ("gov", "mof", "nfra", "pbc", "supp"):
        d = os.path.join(COLLECTORS, sub)
        if os.path.isdir(d):
            try:
                os.rmdir(os.path.join(d, "utils"))
            except OSError:
                pass
            try:
                os.rmdir(d)
            except OSError:
                pass
    return {"moved": moved, "skipped": skipped, "missing": missing,
            "files_after": sorted(f for f in os.listdir(COLLECTORS) if f.endswith(".py"))}


# nfra CACHE 语义修正（拍平后 module 已在 collectors/ 单层，缓存根仍应收敛在
# collectors/cache/<src>：dirname(HERE)/dirname×2(file) 会多上溯一层到 scrapers，须改为 HERE）
_NFRA_CACHE_PATTERNS = [
    ('os.path.join(os.path.dirname(HERE), "cache", "nfra")',
     'os.path.join(HERE, "cache", "nfra")'),
    ('os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cache", "nfra")',
     'os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache", "nfra")'),
]


def fix_texts() -> dict:
    """幂等文本修正：对 collectors/ 单层现有全部 .py 重跑 引导深度统一 + 引用改写
    + nfra CACHE 语义修正。用于拍平已发生但正则缺陷导致残留时的修复（可反复执行）。"""
    changed = []
    for f in sorted(os.listdir(COLLECTORS)):
        if not f.endswith(".py"):
            continue
        p = os.path.join(COLLECTORS, f)
        text = open(p, encoding="utf-8").read()
        t1 = _fix_guide_depth(text)
        t2 = _rewrite_refs(t1, f)
        for old, new in _NFRA_CACHE_PATTERNS:
            if old in t2:
                t2 = t2.replace(old, new)
        # supp_sanitize/supp_parser 等兄弟 import：插 HERE（collectors 层）即可（utils 目录已不存在）
        t2 = t2.replace('UTILS = os.path.join(os.path.dirname(HERE), "utils")',
                        'UTILS = HERE  # 拍平后无 utils 层，兄弟模块同在 collectors/ 单层')
        t2 = t2.replace('for _p in (UTILS, os.path.dirname(HERE)):',
                        'for _p in (HERE,):')
        if t2 != text:
            with open(p, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(t2)
            changed.append(f)
    return {"retexted": changed}


if __name__ == "__main__":
    import argparse  # noqa: PLC0415
    ap = argparse.ArgumentParser()
    ap.add_argument("--fix", action="store_true", help="仅幂等文本修正（已拍平后修复残留）")
    args = ap.parse_args()
    if args.fix:
        print(json_dump := __import__("json").dumps(fix_texts(), ensure_ascii=False))
    else:
        res = flatten()
        print("Moved:", len(res["moved"]), res["moved"])
        print("Skipped:", res["skipped"])
        print("Missing:", res["missing"])
        print("After:", len(res["files_after"]))
        for f in res["files_after"]:
            print("  ", f)
