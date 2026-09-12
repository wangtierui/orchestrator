# -*- coding: utf-8 -*-
"""supp_ingest_local_dir.py —— supplementary 本地目录收录（"本地提供"文件的标准化归集入口）

用途：将"已取得官方全文/规范附件"的本地目录（如 EAST 规范包），按 6.4 标准命名归集进
      data/docs/supplementary_regulations_scraper/，并把附件实体登记（local_path/sha256/bytes）
      写回 data/raw/supplementary_regulations.json 的对应记录（按 --task-index/--doc-number 匹配）；
      xls/xlsx 附件用 excel_classified_v2 重建 table_structured。

与 supp_ingest_batch.py 的关系：
  batch 入口消费 backlog JSON（负责正文与记录级合并）；本脚本负责"本地目录附件实体"的归集与登记，
  二者互不覆盖（本脚本只更新匹配记录的附件字段/表格结构，不新增记录、不改正文）。

严禁：本脚本不臆造正文；记录正文仍以既有已核验 body_text 为准。

用法：
  python collectors/supp_ingest_local_dir.py --src-dir <目录> --task-index EAST-20250410-01 \
      --doc-number "金非银检函〔2025〕82号" --date 20250410 [--run-clean] [--dry-run]
"""

# ---- P3b 仓库引导（migrate_collectors_p3b.py 注入）：使 std_lib/config 可导入 ----
import os as _os
import sys as _sys

_GUIDE_ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "..", ".."))
if _GUIDE_ROOT not in _sys.path:
    _sys.path.insert(0, _GUIDE_ROOT)
del _GUIDE_ROOT, _os

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))            # modules/regulatory_scrapers/collectors
SCRAPERS_ROOT = os.path.dirname(HERE)                        # modules/regulatory_scrapers
for _p in (HERE, SCRAPERS_ROOT):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)
del _sys

RAW_PATH = os.path.join(SCRAPERS_ROOT, "data", "raw", "supplementary_regulations.json")

# 6.4 标准命名：<文号>_正文_<标题片段>_<YYYYMMDD>.<ext> / <文号>_附件<编号>_<短名>_<YYYYMMDD>.<ext>
_TITLE_STRIP_PREFIX = "金融监管总局保险业监管数据标准化规范（人身保险公司2024版）"
_ATTACH_SHORTNAME = {
    "1": "规范全文（修订版）",
}


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _clean_seg(s):
    s = re.sub(r"[：:]+", "", str(s or ""))
    s = re.sub(r"\s+", "", s)
    return s.strip("_ ")


def _truncate_balanced(t, n):
    """截断到 n 字；若截断点在括号内（悬空左括号）则回退到括号前。"""
    if len(t) <= n:
        return t
    t = t[:n]
    if t.count("（") > t.count("）"):
        cut = t.rfind("（")
        if cut > 0:
            t = t[:cut]
    return t + "..."


def _attach_shortname(fname, num):
    """附件短名：去公共前缀后的可读名（含修订版标注）。"""
    base = os.path.splitext(fname)[0]
    base = base.split("：", 1)[-1].split(":", 1)[-1]
    rest = base.replace(_TITLE_STRIP_PREFIX, "")
    rest = re.sub(r"\s+", "", rest)
    rest = rest.strip("_ ")
    if num in _ATTACH_SHORTNAME and (not rest or len(rest) <= 6):
        rest = _ATTACH_SHORTNAME[num]
    return rest or f"附件{num}"


def _pack_name(doc_number, date8, fname, is_body):
    """生成 6.4 标准命名。返回 (target_basename, att_num or None)。"""
    ext = os.path.splitext(fname)[1]
    if is_body:
        m = re.search(r"《([^》]+)》", fname)
        title = m.group(1) if m else os.path.splitext(fname)[0]
        title = _truncate_balanced(_clean_seg(title), 28)
        return f"{doc_number}_正文_{title}_{date8}{ext}", None
    m = re.match(r"^附件([\d]+(?:[-－][\d]+)?)[：:]\s*(.+)$", os.path.splitext(fname)[0])
    if m:
        num = m.group(1).replace("－", "-")
        short = _truncate_balanced(_clean_seg(_attach_shortname(fname, num)), 24)
        return f"{doc_number}_附件{num}_{short}_{date8}{ext}", num
    # 无"附件N："前缀的兜底：用文件名短名
    short = _truncate_balanced(_clean_seg(_attach_shortname(fname, "X")), 24)
    return f"{doc_number}_附件_{short}_{date8}{ext}", None


def _classify(fname):
    """(is_body, kind) —— 主件=含"通知"且不含"附件"的 pdf/doc；其余为附件。"""
    base = os.path.splitext(fname)[0]
    low = fname.lower()
    kind = os.path.splitext(fname)[1].lstrip(".").lower() or "file"
    is_body = ("附件" not in base) and base.endswith("通知）") or (
        "附件" not in base and "通知" in base and low.endswith((".pdf", ".doc", ".docx")))
    return is_body, kind


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--src-dir", required=True, help="本地源目录（13 文件等）")
    ap.add_argument("--task-index", required=True, help="目标记录 task_index（唯一匹配）")
    ap.add_argument("--doc-number", required=True, help="发文字号（命名用）")
    ap.add_argument("--date", required=True, help="YYYYMMDD（命名用）")
    ap.add_argument("--run-clean", action="store_true", help="收录后跑 supp 清洗管道")
    ap.add_argument("--dry-run", action="store_true", help="仅预览，不复制/不写盘")
    args = ap.parse_args(argv)

    from std_lib.scraper_std.cache_store import docs_root  # noqa: PLC0415
    docs_dir = docs_root("supp")
    os.makedirs(docs_dir, exist_ok=True)

    files = sorted(f for f in os.listdir(args.src_dir) if os.path.isfile(os.path.join(args.src_dir, f)))
    if not files:
        print("[收录] 源目录无文件:", args.src_dir)
        return 2

    print(f"[收录] 源 {len(files)} 文件 → {docs_dir}")
    plan = []
    for f in files:
        is_body, kind = _classify(f)
        target, num = _pack_name(args.doc_number, args.date, f, is_body)
        plan.append((f, target, is_body, kind, num))
        print("   %s %s\n      → %s" % ("[正文]" if is_body else f"[附件{num or '?'}]", f, target))

    # 读 raw 并定位记录
    with open(RAW_PATH, encoding="utf-8") as fh:
        rows = json.load(fh)
    rec = None
    for r in rows:
        if (r.get("task_index") or "") == args.task_index:
            rec = r
            break
    if rec is None:
        for r in rows:
            if (r.get("document_number") or "").strip() == args.doc_number.strip():
                rec = r
                break
    if rec is None:
        print("[收录] 未找到目标记录:", args.task_index, args.doc_number)
        return 3
    print(f"[收录] 目标记录: {rec.get('document_number')} | {rec.get('title', '')[:40]}")

    if args.dry_run:
        print("[收录] DRY-RUN：未复制、未写盘。")
        return 0

    # 复制 + 计算摘要
    atts = []
    body_path = None
    for f, target, is_body, kind, num in plan:
        src = os.path.join(args.src_dir, f)
        dst = os.path.join(docs_dir, target)
        shutil.copy2(src, dst)
        sha = _sha256(dst)
        rel = os.path.relpath(dst, SCRAPERS_ROOT).replace("\\", "/")   # data/docs/...
        item = {"name": f, "kind": kind, "bytes": os.path.getsize(dst),
                "status": "collected", "local_path": rel, "sha256": sha}
        if num:
            item["attachment_no"] = num
        if is_body:
            body_path = rel
            item["role"] = "body"
        atts.append(item)

    rec["attachments"] = atts
    rec["attachment_count"] = str(len(atts))
    if body_path:
        rec["downloaded_doc_path"] = body_path

    # xls/xlsx 附件 → excel_classified_v2 重建
    from std_lib.scraper_std.table_recovery import structured_table_fields  # noqa: PLC0415
    v2_list, methods = [], set()
    for _f, target, _is_body, kind, _num in plan:
        if kind not in ("xls", "xlsx", "xlsm"):
            continue
        dst = os.path.join(docs_dir, target)
        res = structured_table_fields(open(dst, "rb").read(), target)
        ts = res.get("table_structured") or []
        if ts:
            v2_list.extend(ts)
            methods.add(res.get("table_recovery_method") or "")
    if v2_list:
        rec["table_structured"] = v2_list
        rec["table_recovery_method"] = "excel_classified_v2（本地目录收录重建：%s）" % "+".join(sorted(m for m in methods if m))
    rf = dict(rec.get("_raw_fields") or {})
    rf["east_dir"] = args.src_dir
    rf["collected_at"] = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rf["collect_tool"] = "supp_ingest_local_dir.py"
    rec["_raw_fields"] = rf

    tmp = RAW_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, RAW_PATH)
    print("[收录] 已登记 %d 附件 | 正文: %s | v2 结构化: %d 个 workbook 对象" % (
        len(atts), body_path or "(未识别)", len(v2_list)))

    if args.run_clean:
        pipe = os.path.join(SCRAPERS_ROOT, "clean", "run_clean_pipeline.py")
        print("[收录] 运行清洗管道:", pipe, "--project supp")
        rc = subprocess.run([sys.executable, pipe, "--project", "supp"], cwd=SCRAPERS_ROOT,
                            timeout=1800).returncode   # 审查 P2-5（2026-09-12）
        print("[收录] 管道返回码:", rc)
        return rc
    return 0


if __name__ == "__main__":
    sys.exit(main())
