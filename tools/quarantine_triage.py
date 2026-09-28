# -*- coding: utf-8 -*-
"""quarantine_triage.py — 清洗**隔离记录**的分类与登记式处置（N-93 / 优化方案 v2 · P0-5）

定位（为何**不**自动回写）
--------------------------
原方案 v1 提议 `quarantine_fixer.py`「隔离记录**自动修复回写**」。本仓 `gates/gate_clean_schema`
对此已有明文纪律：

> 隔离率是「源数据健康度」**软指标**……属**数据治理**待办（**需人工清理源数据**），
> 非代码/流程正确性。故**只披露不阻断**。

因此本工具**零回写**，改为：**分类 → 登记待办 → 给出建议**。处置走既有通道
（`governance_store.worklist_add` → `cli.py worklist resolve`），与 `retention`（dry-run 披露、
`--apply` 人工闸门）、`consolidate_timeliness._wl_add`（冲突登记）同款纪律。

顺带体检（本轮实测发现）
------------------------
`*.quarantine.jsonl` 存在**历史快照残留**（实测 `nfra` 09-26/09-27/09-28 三份、字节数相同；
`supp` 仅 09-26 一份而 cleaned 已是 09-28）→ 隔离件**未随快照轮转清理**。本工具会列出
"非当前快照"的隔离件为体检项（**只报告**，删除须走 `tools/retention.py --apply`）。

用法
----
    python tools/quarantine_triage.py            # 分类 + 登记待办（幂等）
    python tools/quarantine_triage.py --check    # 只分类打印，不登记
"""

from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import paths
from config.enums import WORKLIST_KIND
from config.exitcodes import ExitCode
from std_lib.common_lib.logging import get_logger

LOG = get_logger("tools.quarantine_triage")

ROOT = paths.ROOT
CLEANED_DIR = os.path.join(paths.MODULES_DIR, "regulatory_scrapers", "data", "cleaned")
KIND = "clean_quarantine_triage"

_BODY_KEYS = ("body_text", "body_text_webpage", "body_text_doc")
_LOCAL_PATH = re.compile(r"^[A-Za-z]:[\\/]|^\\\\")


def _cls(rec: dict) -> tuple[str, str]:
    """→ (类别, 建议)。类别稳定且互斥（判定顺序固定，保证可复现）。"""
    body = "".join((rec.get(k) or "") for k in _BODY_KEYS).strip()
    att = (rec.get("attachment_content") or "").strip()
    src_url = (rec.get("source_url") or "").strip()
    doc_path = (rec.get("downloaded_doc_path") or "").strip()
    if body:
        return "body_present_other", "正文非空却仍被隔离：查 schema 校验具体项（字段/枚举）"
    if att:
        return (
            "body_empty_with_attachment",
            "正文空但附件有内容 → 可**受控**回填 body_text（走变更流程，勿直接改产物）",
        )
    if _LOCAL_PATH.match(src_url) or _LOCAL_PATH.match(doc_path):
        return "local_path_source", "来源指向本地路径 → 源数据治理（补公网来源或标注为离线件）"
    if not (rec.get("document_number") or "").strip():
        return "missing_docno", "缺发文字号 → 源侧补录（RFN 登记与文号签名依赖此列）"
    return "body_empty_no_source", "正文与附件均空 → 源侧补抓（重爬/换来源）或评估剔除"


def _current_snapshot_date() -> dict:
    """各源**当前** cleaned 快照日期（用于识别历史残留隔离件）。"""
    cur: dict = {}
    for f in glob.glob(os.path.join(CLEANED_DIR, "*_cleaned_*.jsonl")):
        base = os.path.basename(f)
        if ".quarantine." in base:
            continue
        m = re.match(r"^([a-z]+)_cleaned_(\d{8})\.jsonl$", base)
        if m:
            cur[m.group(1)] = m.group(2)
    return cur


def scan() -> dict:
    """→ {类别统计, 逐源明细, 历史残留隔离件}。**纯只读**。"""
    cur = _current_snapshot_date()
    by_cls: collections.Counter[str] = collections.Counter()
    detail: dict = {}
    stale: list[str] = []
    for f in sorted(glob.glob(os.path.join(CLEANED_DIR, "*.quarantine.jsonl"))):
        base = os.path.basename(f)
        m = re.match(r"^([a-z]+)_cleaned_(\d{8})\.quarantine\.jsonl$", base)
        if not m:
            continue
        src, day = m.group(1), m.group(2)
        if cur.get(src) and day != cur[src]:
            stale.append(f"{base}（当前快照 {cur[src]}）")
            continue  # 历史残留不计入分类（否则同一批隔离被重复登记）
        per: collections.Counter[str] = collections.Counter()
        samples: dict = {}
        for ln in open(f, encoding="utf-8", errors="replace"):
            ln = ln.strip()
            if not ln:
                continue
            try:
                rec = json.loads(ln)
            except ValueError:
                per["unparsable"] += 1
                continue
            c, _adv = _cls(rec)
            per[c] += 1
            samples.setdefault(c, (rec.get("title") or rec.get("index_no") or "")[:80])
        by_cls.update(per)
        detail[src] = {"file": base, "date": day, "classes": dict(per), "samples": samples}
    return {
        "total": sum(by_cls.values()),
        "classes": dict(sorted(by_cls.items())),
        "sources": detail,
        "stale_quarantine_files": stale,
        "current_snapshot": cur,
    }


def register(rep: dict) -> int:
    """按 (源, 类别) 登记待办（**幂等**：同 kind+subject 的 open 项复用）。→ 登记条数。"""
    try:
        from std_lib.common_lib import governance_store as gs
    except Exception as e:  # noqa: BLE001  旁路设施：不可用则跳过，不阻断
        LOG.warning("治理库不可用，跳过登记：%s: %s", type(e).__name__, e)
        return 0
    n = 0
    for src, info in sorted(rep["sources"].items()):
        for cls, cnt in sorted(info["classes"].items()):
            adv = {
                "body_empty_with_attachment": "正文空但附件有内容 → 可受控回填 body_text",
                "body_empty_no_source": "正文与附件均空 → 源侧补抓或评估剔除",
                "missing_docno": "缺发文字号 → 源侧补录",
                "local_path_source": "来源指向本地路径 → 源数据治理",
                "unparsable": "记录不可解析 → 检查落盘编码/截断",
            }.get(cls, "查 schema 校验具体失败项")
            # 传**行内字面量**而非 `KIND`：`gate_config_integrity` 判据 J7 以"行内字面量"为口径
            # 静态识别 kind 的产生方（变量、跨行字面量对扫描均不可见 → 曾使 J7 报"缺产生方"）。
            rid = gs.worklist_add("clean_quarantine_triage", f"{src}:{cls}",
                stage="2",
                artifact_key=f"cleaned:{src}",
                payload={
                    "source": src,
                    "class": cls,
                    "count": cnt,
                    "file": info["file"],
                    "sample": info["samples"].get(cls, ""),
                },
                suggestion=adv,
            )
            if rid:
                n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description="清洗隔离记录分类与登记（零回写）")
    ap.add_argument("--check", action="store_true", help="只分类打印，不登记待办")
    args = ap.parse_args()

    assert KIND in WORKLIST_KIND, f"kind {KIND!r} 未登记 config.enums.WORKLIST_KIND"
    rep = scan()
    if not rep["total"] and not rep["stale_quarantine_files"]:
        LOG.info("无隔离记录（且无历史残留）→ 无需处置")
        return int(ExitCode.OK)

    LOG.info("隔离记录 %d 条；类别：%s", rep["total"], rep["classes"])
    for src, info in sorted(rep["sources"].items()):
        LOG.info("  %s（%s）：%s", src, info["file"], info["classes"])
    if rep["stale_quarantine_files"]:
        LOG.warning(
            "历史快照隔离件 %d 份未清理（**只报告**，清理走 tools/retention.py）：%s",
            len(rep["stale_quarantine_files"]),
            rep["stale_quarantine_files"],
        )
    if args.check:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
        return int(ExitCode.OK)

    n = register(rep)
    LOG.info("已登记待办 %d 条（kind=%s；处置：`cli.py worklist resolve`）", n, KIND)
    return int(ExitCode.OK)


if __name__ == "__main__":
    raise SystemExit(main())
