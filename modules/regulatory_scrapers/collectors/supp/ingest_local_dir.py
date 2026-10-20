# -*- coding: utf-8 -*-
"""collectors/supp/ingest_local_dir.py —— supplementary 本地目录收录（"本地提供"文件的标准化归集入口）

用途：将"已取得官方全文/规范附件"的本地目录（如 EAST 规范包），按 6.4 标准命名归集进
      data/docs/supplementary_regulations_scraper/，并把附件实体登记（local_path/sha256/bytes）
      写回 data/raw/supplementary_regulations.json 的对应记录（按 --task-index/--doc-number 匹配）；
      xls/xlsx 附件用 excel_classified_v2 重建 table_structured。

与 collectors/supp/ingest_batch.py 的关系：
  batch 入口消费 backlog JSON（负责正文与记录级合并）；本脚本负责"本地目录附件实体"的归集与登记，
  二者互不覆盖（本脚本只更新匹配记录的附件字段/表格结构，不新增记录、不改正文）。

严禁：本脚本不臆造正文；记录正文仍以既有已核验 body_text 为准。

用法：
  python collectors/supp/ingest_local_dir.py --src-dir <目录> --task-index EAST-20250410-01 \
      --doc-number "金非银检函〔2025〕82号" --date 20250410 [--run-clean] [--dry-run]
"""

# ---- P3b 仓库引导（migrate_collectors_p3b.py 注入）：使 std_lib/config 可导入 ----
import os as _os
import sys as _sys

_GUIDE_ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "..", "..", ".."))
if _GUIDE_ROOT not in _sys.path:
    _sys.path.insert(0, _GUIDE_ROOT)
del _GUIDE_ROOT, _os

import argparse
import datetime as _dt
import hashlib
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))            # …/collectors/supp

# ⚠️ 2026-10-20 修（第一百零四批，**同一根因的第 7 处**）：原
# `SCRAPERS_ROOT = os.path.dirname(HERE)` 只上溯**一级** ⇒ `.../collectors`
# （注释却写 "modules/regulatory_scrapers"）。它被用于四处，**四处全错**：
#   ① `RAW_PATH` ⇒ `collectors/data/raw/…`（不存在）⇒ L149 `open()` 必然
#      `FileNotFoundError`（**本脚本根本跑不起来**）；
#   ② `relpath(dst, SCRAPERS_ROOT)` ⇒ `../data/docs/…` **带 `..` 的脏路径**
#      （与 pbc 同病：只在 cwd=collectors 时解析正确）；
#   ③ `clean/run_clean_pipeline.py` 拼成 `collectors/clean/…`（不存在）；
#   ④ `cwd=SCRAPERS_ROOT` 工作目录也错。
#
# 修法：**统一到单一事实源** `supp_constants`。双形态导入（可能被脚本形态执行）。
if HERE not in _sys.path:
    _sys.path.insert(0, HERE)
del _sys
try:
    from .supp_constants import RAW_OUT, SCRAPERS_ROOT, WS_ROOT
except ImportError:  # pragma: no cover  脚本形态
    from supp_constants import RAW_OUT, SCRAPERS_ROOT, WS_ROOT

if SCRAPERS_ROOT not in sys.path:
    sys.path.insert(0, SCRAPERS_ROOT)

# 2026-10-20：`RAW_PATH` 改为**直接复用** `supp_constants.RAW_OUT`（单一事实源）。
# 二者字面量本就相同，此前是两处独立派生 ⇒ 一处修了上溯层级另一处没修就静默分叉。
RAW_PATH = RAW_OUT

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

    from std_lib.scraper_std.cache_store import docs_root
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
    #
    # ⚠️ 2026-10-20 修（第一百零四批，**实跑才暴露的第 8 处**）：原实现
    #     `with open(RAW_PATH) as fh: rows = json.load(fh)`
    # 有两处必然失败：
    #   ① `RAW_PATH` 字面量是 `.json`，而批 49/T1 起主库实际落盘是 `.jsonl`
    #      ⇒ `FileNotFoundError`（实测）；
    #   ② 即便文件存在，主库是 **JSONL**（首行 `_meta` 信封 + 一行一记录），
    #      `json.load` 会抛 `JSONDecodeError: Extra data: line 2 column 1`（实测）。
    # ⇒ 本脚本在修复前**从未跑通过**。改用与其余脚本**同一套助手**：
    #    `resolve_raw_path` 解析逻辑路径到实际文件（兼容历史 `.json` 名），
    #    `read_raw_records` 双格式读取（.jsonl 逐行 / 旧 .json 整条）。
    from std_lib.scraper_std.pipeline import read_raw_records, resolve_raw_path

    _rp = resolve_raw_path(RAW_PATH)
    rows = read_raw_records(_rp) if os.path.exists(_rp) else []
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
    body_docs = []   # 批 50：正文载体（role=body）单独存放，不进 attachments
    body_path = None
    for f, target, is_body, kind, num in plan:
        src = os.path.join(args.src_dir, f)
        dst = os.path.join(docs_dir, target)
        shutil.copy2(src, dst)
        sha = _sha256(dst)
        # ⚠️ 基准改为**仓根**（`WS_ROOT`）：与 mof/pbc/gov 统一（2026-10-20 第一百零五批）。
        # 原用 `SCRAPERS_ROOT` ⇒ 写成 `data/docs/…`（与其它源不一致）；
        # 更早的实现（未修 REPO_ROOT 时）会写成 `../data/docs/…` 脏路径。
        rel = os.path.relpath(dst, WS_ROOT).replace("\\", "/")
        item = {"name": f, "kind": kind, "bytes": os.path.getsize(dst),
                "status": "collected", "local_path": rel, "sha256": sha}
        if num:
            item["attachment_no"] = num
        if is_body:
            # 批 50：正文文件（_classify 判为 body 者）**不是公告附件**而是正文载体；
            # 正文内容已由 body_text/downloaded_doc_path 承载 ⇒ 条目移入 body_docs 仅作溯源
            body_path = rel
            item["role"] = "body"
            body_docs.append(item)
            continue
        atts.append(item)

    rec["attachments"] = atts
    rec["attachment_count"] = str(len(atts))
    if body_docs:
        rec["body_docs"] = body_docs
    if body_path:
        rec["downloaded_doc_path"] = body_path

    # xls/xlsx 附件 → excel_classified_v2 重建
    from std_lib.scraper_std.table_recovery import structured_table_fields
    v2_list: list = []
    methods: set = set()
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
        # 批 51/靶心⑩：**合并**既有表结构（原实现直接覆盖 ⇒ 原始采集已抽到的表可能被抹掉）
        _prev = rec.get("table_structured")
        if isinstance(_prev, list) and _prev:
            v2_list = list(_prev) + v2_list
        rec["table_structured"] = v2_list
        rec["table_recovery_method"] = "excel_classified_v2（本地目录收录重建：%s）" % "+".join(sorted(m for m in methods if m))
    rf = dict(rec.get("_raw_fields") or {})
    rf["east_dir"] = args.src_dir
    rf["collected_at"] = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rf["collect_tool"] = "ingest_local_dir.py"
    rec["_raw_fields"] = rf

    # ⚠️ 2026-10-20 修（第一百零四批，**第 9 处**）：原实现
    #     `tmp = RAW_PATH + ".tmp"; json.dump(rows, ...); os.replace(tmp, RAW_PATH)`
    # 三处都不对：
    #   ① `RAW_PATH` 是 `.json` 字面量 ⇒ 会**新建**一个 `.json` 与主库 `.jsonl`
    #      **分叉**（下游只读 `.jsonl` ⇒ 写进去等于没写）；
    #   ② 写成 **JSON 数组**，而全仓 raw 规范是 **JSONL + 首行 `_meta` 信封**
    #      （批 49/T1）⇒ 格式不合规，`read_raw_records` 虽兼容但下游其它读者未必；
    #   ③ 手写 `.tmp` + `os.replace` 未做 fsync，异常时可能留半截文件。
    # ⇒ 改用 `write_raw_jsonl`（自动把 `.json` 规范化成 `.jsonl`、原子写 + 信封）。
    from std_lib.scraper_std.pipeline import write_raw_jsonl

    _written = write_raw_jsonl(RAW_PATH, rows, source="supp")
    print("[收录] raw 已写入:", _written)
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
