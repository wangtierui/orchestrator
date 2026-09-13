# -*- coding: utf-8 -*-
"""
tools/build_migration_manifest.py — 生成 data_migration_manifest.json（P0 / R22；2026-09-13 可移植性修复）

作用：扫描本仓 `modules/<模块>` 下的**现行活跃数据**，输出 {相对路径, size, sha256}
清单到仓库根 data_migration_manifest.json（提交入库，作为**异机恢复与完整性核对**的基线）。

**路径纪律（2026-09-13 修复）**：清单内全部 `path` 为**相对仓库根**的 POSIX 路径，绝不写绝对路径。
原实现输出 `D:/WorkBuddy/...` 绝对路径（97 处），清单只在本机可执行、且登记的仍是已过期的
0907 快照；异机（克隆后）完全无法使用（见 `reports/克隆可移植性检视报告_20260913.md` · CP-D03）。

范围（仅活跃数据，不含历史归档/backups/cache/published）：
  scrapers: data/cleaned 每源最新1版 csv+jsonl、data/raw 定长主库 json、
            timeliness_review/verification_state.json + 最新时效性标注结果清单_全量 {csv}
  classifier: data/*.csv(归属表/主题归属表)、data/_t*.json(40)、data/T*明细表*.csv(11)、
              data/_upper_laws.json、rfn/*.csv、rfn/sync_status.json、
              recall_audit/verification_state.mirror.json、docs/**(报告)
  drafter: docs/**（六件套等权威交付物）
  base: data/ 顶层入口件（internal_policy_index / align_result / merged_view）
        —— processed/ 下逐制度产物可由 `orchestrator internal index|reocr` 重建，不入清单。
用法：python tools/build_migration_manifest.py [--root <仓库根>] [--out ...]
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone

# 仓库根（本文件位于 tools/ 下）
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TZ = timezone(timedelta(hours=8))  # 与项目时间基准一致（Asia/Shanghai）

# 活跃数据所在模块（新仓布局：modules/<name>；旧四仓已冻结，数据平移到本仓 modules/）
MODULE_SUBDIRS = {
    "scrapers": "modules/regulatory_scrapers",
    "classifier": "modules/regulatory_classifier",
    "drafter": "modules/internal_policy_drafter",
    "base": "modules/internal_policy_base",
}


def _rel(root: str, path: str) -> str:
    """绝对路径 → 相对仓库根的 POSIX 路径（清单可移植性的唯一保障）。"""
    return os.path.relpath(path, root).replace("\\", "/")

# 每个 (source_root_key, 相对 glob, 目标相对路径模板, 说明)
# 用 {date_latest} 占位不支持——改为手工挑选最新（按文件名日期后缀排序取最大）。
CLEAN_RE = re.compile(r"^(gov|mof|nfra|pbc|supp)_cleaned_(\d{8})\.(csv|jsonl)$")


def _sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            b = fh.read(1 << 20)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _latest_cleaned(scrapers_root: str) -> list[dict]:
    """data/cleaned 下每源最新 1 版 csv+jsonl（按日期取最大）。"""
    cleaned_dir = os.path.join(scrapers_root, "data", "cleaned")
    out = []
    if not os.path.isdir(cleaned_dir):
        return out
    by_src: dict[str, dict[str, str]] = {}
    for name in os.listdir(cleaned_dir):
        m = CLEAN_RE.match(name)
        if not m:
            continue
        src, date, ext = m.group(1), m.group(2), m.group(3)
        cur = by_src.setdefault(src, {})
        if date > cur.get("date", ""):
            cur.update(date=date, **{ext: os.path.join(cleaned_dir, name)})
    for _src, hit in sorted(by_src.items()):
        for ext in ("csv", "jsonl"):
            p = hit.get(ext)
            if p and os.path.exists(p):
                out.append({"src": f"scrapers/data/cleaned/{os.path.basename(p)}",
                            "path": p})
    return out


def _latest_raw_master(scrapers_root: str) -> list[dict]:
    """data/raw 定长主库 json（gov_laws/mof_laws/nfra_regulations/pbc_laws/supplementary_regulations.json）。"""
    raw_dir = os.path.join(scrapers_root, "data", "raw")
    names = ["gov_laws.json", "mof_laws.json", "nfra_regulations.json",
             "pbc_laws.json", "supplementary_regulations.json"]
    out = []
    for n in names:
        p = os.path.join(raw_dir, n)
        if os.path.exists(p):
            out.append({"src": f"scrapers/data/raw/{n}", "path": p})
    return out


def _latest_timeliness(scrapers_root: str) -> list[dict]:
    out = []
    state = os.path.join(scrapers_root, "timeliness_review", "verification_state.json")
    if os.path.exists(state):
        out.append({"src": "scrapers/timeliness_review/verification_state.json", "path": state})
    full_glob = os.path.join(scrapers_root, "timeliness_review",
                             "时效性标注结果清单_全量_*.csv")
    cands = sorted(glob.glob(full_glob))
    if cands:
        p = cands[-1]
        out.append({"src": f"scrapers/timeliness_review/{os.path.basename(p)}", "path": p})
    return out


def _classifier_data(classifier_root: str) -> list[dict]:
    out = []
    data_dir = os.path.join(classifier_root, "data")
    if not os.path.isdir(data_dir):
        return out
    for name in os.listdir(data_dir):
        if name.endswith(".csv") and ("归属表" in name or "主题归属表" in name):
            out.append({"src": f"classifier/data/{name}", "path": os.path.join(data_dir, name)})
        elif re.match(r"^_t\d+_(base|final|matched|citerefs)\.json$", name):
            out.append({"src": f"classifier/data/{name}", "path": os.path.join(data_dir, name)})
        elif re.match(r"^T\d+_\d+逐份条款引用与上位法依据明细表\.csv$", name):
            out.append({"src": f"classifier/data/{name}", "path": os.path.join(data_dir, name)})
        elif name == "_upper_laws.json":
            out.append({"src": f"classifier/data/{name}", "path": os.path.join(data_dir, name)})
    # rfn 索引/指纹/sync
    rfn_dir = os.path.join(classifier_root, "rfn")
    for name in ("监管文件编号索引.csv", "文件指纹.csv", "sync_status.json"):
        p = os.path.join(rfn_dir, name)
        if os.path.exists(p):
            out.append({"src": f"classifier/rfn/{name}", "path": p})
    # mirror
    mp = os.path.join(classifier_root, "recall_audit", "verification_state.mirror.json")
    if os.path.exists(mp):
        out.append({"src": "classifier/recall_audit/verification_state.mirror.json", "path": mp})
    return out


def _docs_md(root: str, prefix: str) -> list[dict]:
    out = []
    docs_dir = os.path.join(root, "docs")
    if not os.path.isdir(docs_dir):
        return out
    for dirpath, _dirnames, filenames in os.walk(docs_dir):
        for fn in filenames:
            if fn.endswith(".md"):
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, root).replace("\\", "/")
                out.append({"src": f"{prefix}/{rel}", "path": full})
    return out


def _internal_base(base_root: str) -> list[dict]:
    """内部制度底座**顶层入口件**（2026-09-13 补：原清单遗漏 internal 链路）。

    index/align/merged 是制度侧权威台账与 drafting/gate_citations 的上游 SSOT；
    processed/ 下逐制度 fulltext/clauses 可由 `orchestrator internal index|reocr` 重建，不入清单
    （957 制度会造成清单数千条噪声，且属可重建派生物）。
    `_ingest_state.json` 为断点/幂等标记，与来源目录强绑定，同样不入清单。
    """
    out = []
    data_dir = os.path.join(base_root, "data")
    for name in ("internal_policy_index.json", "align_result.json", "merged_view.json"):
        p = os.path.join(data_dir, name)
        if os.path.exists(p):
            out.append({"src": f"base/data/{name}", "path": p})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=REPO_ROOT,
                    help="仓库根（默认本仓根；活跃数据位于 modules/<模块>，见 MODULE_SUBDIRS）")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    root = args.root
    items: list[dict] = []
    scrapers_root = os.path.join(root, MODULE_SUBDIRS["scrapers"])
    classifier_root = os.path.join(root, MODULE_SUBDIRS["classifier"])
    drafter_root = os.path.join(root, MODULE_SUBDIRS["drafter"])
    base_root = os.path.join(root, MODULE_SUBDIRS["base"])
    items += _latest_cleaned(scrapers_root)
    items += _latest_raw_master(scrapers_root)
    items += _latest_timeliness(scrapers_root)
    items += _classifier_data(classifier_root)
    items += _docs_md(drafter_root, "drafter")
    items += _internal_base(base_root)

    manifest = []
    for it in items:
        try:
            manifest.append({
                "src": it["src"],
                # 2026-09-13：一律相对仓库根（原为绝对路径，异机不可执行）
                "path": _rel(root, it["path"]),
                "size_bytes": os.path.getsize(it["path"]),
                "sha256": _sha(it["path"]),
            })
        except OSError as e:
            print(f"[warn] 跳过 {it['src']}: {e}")
    out_path = args.out or os.path.join(REPO_ROOT, "data_migration_manifest.json")
    manifest.sort(key=lambda x: x["src"])
    payload = {
        "schema_version": "1.1",
        "generated_at": datetime.now(_TZ).strftime("%Y-%m-%dT%H:%M:%S%z"),
        "path_semantics": "全部 path 为相对仓库根的 POSIX 路径（schema 1.1 起；1.0 为绝对路径，异机不可用）",
        "source_roots": dict(MODULE_SUBDIRS),
        "note": ("现行活跃数据清单：用于异机恢复与完整性核对（sha256）。数据不入 git，"
                 "按本清单从备份复制或按 README §6 重建；校验后比对 sha256。"),
        "items": manifest,
    }
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, out_path)
    print(f"[manifest] {len(manifest)} 条 → {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
