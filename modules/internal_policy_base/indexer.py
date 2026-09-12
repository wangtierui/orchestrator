# -*- coding: utf-8 -*-
"""
internal_policy_base.indexer — 内部制度摄取索引（P6 indexer）

流程（幂等，可重跑）：
  1) scan_directory(源)  → 文件元数据（IPN/docno/title/sha256）
  2) 已摄入且 sha256 未变 → 跳过（增量）
  3) 新/变 → 复制原文件入仓 data/originals/<rel> + 抽取正文 → data/processed/<ipn>.json
  4) 汇总写 data/internal_policy_index.json（主索引）+ 侧写 data/_ingest_state.json

产物（数据随仓，D-05）：
  modules/internal_policy_base/data/
    originals/<rel>           原始件副本
    processed/<ipn>.json      单文件结构化（元数据 + 规范化正文 + 抽取状态）
    internal_policy_index.json  主索引（版本链/状态/主题对齐位预留）
    _ingest_state.json          摄取游标（已处理 sha256，幂等）
"""
from __future__ import annotations

import glob
import json
import os
import re
import sys
from datetime import datetime

# 同仓引导：使 `import paths`（orchestrator 根）与本包可导入（独立脚本运行支持）
_THIS = os.path.dirname(os.path.abspath(__file__))              # modules/internal_policy_base
_MODULES = os.path.dirname(_THIS)                                # modules/
_ORCH_ROOT = os.path.dirname(_MODULES)
for _p in (_ORCH_ROOT, _MODULES):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from internal_policy_base.extract import copy_original, extract_file  # noqa: E402
from internal_policy_base.scan import (  # noqa: E402
    clean_title_noise,
    ipn_of,
    parse_content_identity,
    scan_directory,  # noqa: E402
)

# R21：条文结构解析（章-条），供 merged_view/drafter 条款对照
from std_lib.scraper_std.document_structure import (  # noqa: E402
    extract_structure,
    render_markdown,
)

# 富内容(图形/公式)轨：流程图/SmartArt/公式 OMML 抽取与图片落盘（2026-09-09）
from std_lib.scraper_std.rich_object import rich_object_fields  # noqa: E402

_DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
_ORIGINALS = os.path.join(_DATA, "originals")
_PROCESSED = os.path.join(_DATA, "processed")
_INDEX_PATH = os.path.join(_DATA, "internal_policy_index.json")
_STATE_PATH = os.path.join(_DATA, "_ingest_state.json")

# processed json 字段（稳定 schema）
PROCESSED_FIELDS = [
    "ipn", "file_name", "relative_path", "extension", "docno", "title",
    "size_bytes", "sha256", "file_type", "status", "extract_status",
    "text_chars", "needs_ocr", "original_path", "extracted_at",
    "chapter_count", "article_count",   # R21 条文结构（clauses json 存明细）
    "rich_count",                        # 富内容(图形/公式)对象数（_rich.json 存明细）
]


def _write_rich(ipn: str, data: bytes, fname: str) -> int:
    """富内容抽取 → <ipn>_rich.json + processed/<ipn>_images/。返回对象数（0 无富内容）。"""
    try:
        fields = rich_object_fields(
            data, fname,
            image_dir=os.path.join(_PROCESSED, ipn + "_images"), rec_key=ipn)
    except Exception:  # noqa: BLE001  富内容失败不阻断摄取
        return 0
    if not fields:
        return 0
    json.dump({"ipn": ipn, "rich_structured": fields["rich_structured"],
               "rich_text": fields["rich_text"], "rich_count": fields["rich_count"]},
              open(os.path.join(_PROCESSED, ipn + "_rich.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    return int(fields["rich_count"])


def _load_state() -> dict:
    if os.path.exists(_STATE_PATH):
        try:
            d = json.load(open(_STATE_PATH, encoding="utf-8"))
            # F-D14：读侧剥离版本键（写侧注入 _meta；遍历/查询零感知）
            if isinstance(d, dict):
                d.pop("_meta", None)
            return d
        except Exception:
            return {}
    return {}


def _save_state(state: dict) -> None:
    # F-D14（H-01）：状态文件版本锚点（读侧剥离；_ingest_state 键空间为 sha256，_meta 独立键位）
    # 修复（2026-09-12）：版本键经**副本**写入——原就地注入会污染调用方对象，
    # 后续"主索引汇总"遍历 state 时 _meta 条目触发 KeyError: 'ipn'（本日摄取实证）。
    import time as _t  # noqa: PLC0415
    payload = dict(state)
    payload["_meta"] = {"schema_version": "1.0", "written_by": "internal_policy_base.indexer",
                        "written_at": _t.strftime("%Y-%m-%d %H:%M:%S")}
    os.makedirs(_DATA, exist_ok=True)
    tmp = _STATE_PATH + ".tmp"
    json.dump(payload, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    os.replace(tmp, _STATE_PATH)


def classify_file_type(title: str, extension: str) -> str:
    """按标题/扩展名粗分类（INTERNAL_FILE_TYPE 5 值；可后续人工精修）。"""
    t = title or ""
    if re.search(r"手册", t):
        return "manual"
    if re.search(r"办法|管理规定|制度|暂行规定|实施办法", t):
        return "policy"
    if re.search(r"细则|指引|规范|操作规程|注意事项", t):
        return "guideline"
    if re.search(r"流程|作业指导|SOP|操作流程", t):
        return "process"
    return "other"


def ingest(source_root: str, *, enable_ocr: bool = False, dry_run: bool = False) -> dict:
    """主流程。dry_run 只报告将摄取项。返回 summary。"""
    files = scan_directory(source_root)
    state = _load_state()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    new_items, changed_items, skipped = [], [], []
    for f in files:
        key = f["sha256"]
        prev = state.get(key)
        if prev and prev.get("ipn") == f["ipn"]:
            skipped.append(f["file_name"])
            continue
        if prev:
            changed_items.append(f["file_name"])
        else:
            new_items.append(f["file_name"])
        if dry_run:
            continue
        # 摄入：复制 + 抽取 + 写 processed
        orig = copy_original(f["source_path"], _ORIGINALS, f["relative_path"])
        res = extract_file(f["source_path"], name=f["file_name"], enable_ocr=enable_ocr)
        text = res.get("text") or ""
        # 内容权威（2026-09-12，用户指令）：文号/标题以**正文**为准；文件名仅解构回退
        # （title 提取失败时对文件名 title 做噪声清洗：-清洁版V3/_盖章/（含水印） 等）。
        ident = parse_content_identity(text)
        _nd = ident["docno"] or f["docno"]
        _nt = (ident["title"] or clean_title_noise(f["title"]) or f["title"]
               or os.path.splitext(f["file_name"])[0])   # 末位兜底：文件名干
        if _nd != f["docno"] or _nt != f["title"]:
            f["docno"], f["title"] = _nd, _nt
            f["ipn"] = ipn_of(_nd, _nt, extension=f["extension"])   # IPN 随身份重算
        rec = {
            "ipn": f["ipn"], "file_name": f["file_name"],
            "relative_path": f["relative_path"], "extension": f["extension"],
            "docno": f["docno"], "title": f["title"],
            "size_bytes": f["size_bytes"], "sha256": key,
            "file_type": classify_file_type(f["title"], f["extension"]),
            "status": "active", "extract_status": res.get("extract_status", ""),
            "text_chars": len(text), "needs_ocr": bool(res.get("needs_ocr")),
            "original_path": os.path.relpath(orig, _DATA), "extracted_at": now,
        }
        # R21：条文结构解析 → <ipn>_clauses.json（章/条明细；正文为空或非条文型得空结构）
        stru = extract_structure(text)
        rec["chapter_count"] = stru["chapter_count"]
        rec["article_count"] = stru["article_count"]
        # 富内容轨（2026-09-09）：docx/doc/xlsx 内图形/公式 → <ipn>_rich.json + processed/<ipn>_images/
        try:
            with open(orig, "rb") as _fh:
                _raw = _fh.read()
            rec["rich_count"] = _write_rich(f["ipn"], _raw, f["file_name"])
        except Exception:  # noqa: BLE001  富内容失败不阻断摄取
            rec["rich_count"] = 0
        os.makedirs(_PROCESSED, exist_ok=True)
        json.dump(rec, open(os.path.join(_PROCESSED, f["ipn"] + ".json.tmp"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        os.replace(os.path.join(_PROCESSED, f["ipn"] + ".json.tmp"),
                   os.path.join(_PROCESSED, f["ipn"] + ".json"))
        # 正文单独存（避免 processed 内嵌大 text 混入 schema；_fulltext.json 便于下游）
        json.dump({"ipn": f["ipn"], "text": text},
                  open(os.path.join(_PROCESSED, f["ipn"] + "_fulltext.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        json.dump({"ipn": f["ipn"], "chapters": stru["chapters"], "articles": stru["articles"]},
                  open(os.path.join(_PROCESSED, f["ipn"] + "_clauses.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        # MD 渲染视图（JSON 规范源 → MD 供 drafter 条款对照/人工审阅，格式决策 2026-09-08）
        open(os.path.join(_PROCESSED, f["ipn"] + "_clauses.md"), "w", encoding="utf-8").write(
            render_markdown(stru, title=f["title"]))
        state[key] = {"ipn": f["ipn"], "sha256": key, "ingested_at": now}

    if not dry_run:
        _save_state(state)

    # 主索引汇总
    records = []
    seen_ipn: dict = {}
    ext_map: dict = {}
    n_state = 0
    for key, rec in sorted(state.items()):
        # 防御（2026-09-12）：版本/元数据键不参与记录汇总（_ 前缀或旧 meta 约定）
        if str(key).startswith("_") or key == "meta":
            continue
        n_state += 1
        r = _load_processed(rec.get("ipn", ""))
        if not r:
            continue
        # 同 IPN 多 sha（同名同文号同介质的不同内容版本）→ 主索引保留 extracted_at 最新一条
        # （2026-09-12：与 merged/policies 同口径；重复数透明登记于 stat）
        ipn = r.get("ipn", "")
        if ipn not in seen_ipn or (r.get("extracted_at") or "") >= ext_map.get(ipn, ""):
            seen_ipn[ipn] = {k: r.get(k, "") for k in PROCESSED_FIELDS}
            ext_map[ipn] = r.get("extracted_at") or ""
    records = list(seen_ipn.values())
    index = {
        "schema_version": "1.0",
        "generated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "source_root": source_root,
        "stat": {"state_entries": n_state,
                 "deduped_duplicates": n_state - len(records)},
        "records": records,
        "count": len(records),
    }
    os.makedirs(_DATA, exist_ok=True)
    json.dump(index, open(_INDEX_PATH + ".tmp", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    os.replace(_INDEX_PATH + ".tmp", _INDEX_PATH)

    return {
        "scanned": len(files), "new": len(new_items), "changed": len(changed_items),
        "skipped": len(skipped), "indexed": len(records), "dry_run": dry_run,
        "index_path": _INDEX_PATH,
    }


def _load_processed(ipn: str) -> dict | None:
    p = os.path.join(_PROCESSED, ipn + ".json")
    if not os.path.exists(p):
        return None
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return None


_THEME_CARRY_FIELDS = ("primary_theme", "secondary_themes", "align_method")


def _prev_theme_fields() -> dict:
    """读现主索引中的主题列（align 回写产物）→ {ipn: {field: value}}（F-D01 防冲）。"""
    out = {}
    if not os.path.exists(_INDEX_PATH):
        return out
    try:
        idx = json.load(open(_INDEX_PATH, encoding="utf-8"))
    except Exception:
        return out
    for r in (idx.get("records") or []):
        ipn = r.get("ipn", "")
        if not ipn:
            continue
        carry = {k: r.get(k) for k in _THEME_CARRY_FIELDS if r.get(k) not in (None, "")}
        if carry:
            out[ipn] = carry
    return out


def _rebuild_index() -> int:
    """按 state 重建主索引（backfill 后同步 rich_count 字段）。返回 indexed 数。

    F-D01：保留既有主索引中的主题列（primary_theme/secondary_themes/align_method，
    align 回写产物）——原白名单重建仅收 PROCESSED_FIELDS，会静默冲掉主题列
    （实测 internal_policy_index.json primary_theme 非空 0/107）。
    """
    state = _load_state()
    carry = _prev_theme_fields()
    records = []
    for key, rec in sorted(state.items()):
        if key == "meta":
            continue
        r = _load_processed(rec.get("ipn", ""))
        if r:
            item = {k: r.get(k, "") for k in PROCESSED_FIELDS}
            item.update(carry.get(rec.get("ipn", ""), {}))
            records.append(item)
    index = {
        "schema_version": "1.0",
        "generated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "source_root": "",
        "records": records,
        "count": len(records),
    }
    os.makedirs(_DATA, exist_ok=True)
    json.dump(index, open(_INDEX_PATH + ".tmp", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    os.replace(_INDEX_PATH + ".tmp", _INDEX_PATH)
    return len(records)


def refine_identity_backfill(limit: int | None = None) -> dict:
    """存量制度身份纠正（2026-09-12，内容权威）：文号/标题以正文为准。

    - 逐份 processed 主记录：读 `_fulltext.json` → `parse_content_identity` →
      覆盖 docno/title（title 提取失败时用 `clean_title_noise` 清洗原 title）；
    - IPN 重算（ipn_of）；与原不同且不冲突时重命名 5 类 processed 文件
      （主 json/_fulltext/_clauses.json/_clauses.md/_rich.json + `_images` 目录）
      并同步 `_ingest_state`（sha→ipn）；
    - 幂等断点：重复运行无变化即全 skip；返回统计（含 conflicts 明细）。
    """
    import glob as _glob  # noqa: PLC0415

    proc_dir = _PROCESSED
    state = _load_state()
    state_dirty = False
    stats: dict = {"total": 0, "changed": 0, "renamed": 0, "conflict": 0,
                   "unchanged": 0, "details": []}
    mains = sorted(p for p in _glob.glob(os.path.join(proc_dir, "*.json"))
                   if not p.endswith(("_fulltext.json", "_clauses.json", "_rich.json")))
    if limit:
        mains = mains[: int(limit)]
    for p in mains:
        try:
            rec = json.load(open(p, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        stats["total"] += 1
        ipn_old = rec.get("ipn", "")
        fpath = os.path.join(proc_dir, ipn_old + "_fulltext.json")
        text = ""
        if os.path.exists(fpath):
            try:
                text = json.load(open(fpath, encoding="utf-8")).get("text") or ""
            except Exception:  # noqa: BLE001
                text = ""
        ident = parse_content_identity(text)
        nd = ident["docno"] or rec.get("docno", "")
        nt = (ident["title"] or clean_title_noise(rec.get("title", "")) or rec.get("title", "")
              or os.path.splitext(rec.get("file_name", ""))[0])   # 末位兜底：文件名干
        if nd == rec.get("docno") and nt == rec.get("title"):
            stats["unchanged"] += 1
            continue
        ipn_new = ipn_of(nd, nt, extension=rec.get("extension", ""))
        if ipn_new != ipn_old and os.path.exists(os.path.join(proc_dir, ipn_new + ".json")):
            # 冲突（同 title 多版本，目标 IPN 已被占）：仍更新 docno/title（内容权威），
            # IPN 保持稳定（标识符优先，不动文件族）——2026-09-12
            rec.update({"docno": nd, "title": nt})
            tmp = p + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(rec, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, p)
            stats["conflict"] += 1
            stats["details"].append({"ipn": ipn_old, "status": "conflict",
                                     "file": rec.get("file_name", "")[:40], "target": ipn_new,
                                     "title": nt[:40]})
            continue
        rec.update({"docno": nd, "title": nt, "ipn": ipn_new})
        if ipn_new != ipn_old:
            for suf in (".json", "_fulltext.json", "_clauses.json", "_clauses.md", "_rich.json"):
                src_f = os.path.join(proc_dir, ipn_old + suf)
                if os.path.exists(src_f):
                    os.replace(src_f, os.path.join(proc_dir, ipn_new + suf))
            dsrc = os.path.join(proc_dir, ipn_old + "_images")
            if os.path.isdir(dsrc):
                ddst = os.path.join(proc_dir, ipn_new + "_images")
                if not os.path.exists(ddst):
                    os.replace(dsrc, ddst)
            stats["renamed"] += 1
        # 写回**新路径**（2026-09-12 修复：原按扫描旧路径写回会重建旧名、与新名撕裂）
        out_p = os.path.join(proc_dir, ipn_new + ".json")
        tmp = out_p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(rec, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, out_p)
        if ipn_new != ipn_old and rec.get("sha256") in state:
            state[rec["sha256"]]["ipn"] = ipn_new
            state_dirty = True
        stats["changed"] += 1
        stats["details"].append({"ipn_old": ipn_old, "ipn_new": ipn_new,
                                 "docno": nd, "title": nt[:40],
                                 "file": rec.get("file_name", "")[:36]})
    if state_dirty:
        _save_state(state)
    return stats


def backfill_rich() -> dict:
    """存量 107 富内容回补：对已 processed 且缺 <ipn>_rich.json 的制度，
    从 data/originals（original_path）读原始件抽取富内容（图/公式），
    写 <ipn>_rich.json + images，并刷新 processed rec.rich_count 与主索引。"""
    done = skipped = found_rich = 0
    for p in sorted(glob.glob(os.path.join(_PROCESSED, "*.json"))):
        base = os.path.splitext(os.path.basename(p))[0]
        if base.endswith(("_fulltext", "_clauses", "_rich")):
            continue
        if os.path.exists(os.path.join(_PROCESSED, base + "_rich.json")):
            skipped += 1
            continue
        try:
            rec = json.load(open(p, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(rec, dict) or not rec.get("ipn"):
            continue
        src = os.path.join(_DATA, rec.get("original_path", ""))
        if not os.path.exists(src):
            continue
        with open(src, "rb") as fh:
            data = fh.read()
        cnt = _write_rich(rec["ipn"], data, rec.get("file_name") or "")
        rec["rich_count"] = cnt
        json.dump(rec, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        done += 1
        if cnt:
            found_rich += 1
    indexed = _rebuild_index()
    return {"backfilled": done, "already": skipped, "with_rich": found_rich,
            "indexed": indexed}


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    import argparse
    ap = argparse.ArgumentParser(description="内部制度摄取索引")
    ap.add_argument("--source-dir", default=os.environ.get("INTERNAL_POLICY_ROOT", ""),
                    help="制度源目录（默认 INTERNAL_POLICY_ROOT env）")
    ap.add_argument("--enable-ocr", action="store_true", help="扫描件 PDF 启用 OCR")
    ap.add_argument("--dry-run", action="store_true", help="演练：仅报告将摄取项")
    args = ap.parse_args()
    if not args.source_dir:
        print("需提供 --source-dir 或设置 INTERNAL_POLICY_ROOT 环境变量")
        return 1
    import json as _j
    s = ingest(args.source_dir, enable_ocr=args.enable_ocr, dry_run=args.dry_run)
    print(_j.dumps(s, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
