# -*- coding: utf-8 -*-
"""tools/rfn_backlog.py — RFN 补登候选（关系线索驱动，R-F01 提升路径①，2026-09-14）

背景
----
`relations` 产物的目标分类（`dst_class`）中，`corpus` 类 = **已采集（cleaned 全集命中，
有 `dst_key`）但未登记 RFN** 的文件——被制度/监管文件实际引用，却因不在归属表而无法
成为强关联。这些正是**补登候选**：把它们登记进 RFN 归属表后，重跑 `relations gen`
即自动升级为 `entity`（强关联，可 join 底座与报告）。

主题如何定？（**数据驱动，不臆造**）
----------------------------------
归属表与主题表都要求主题字段，而主题判定本属分类器职责。本工具的取法是：
**由「引用者」的主题投票**——某未登记文件被 T5 的多份文件引用，就建议归入 T5；
票数并列或引用者无主题 → 标注 `uncertain` 并**不参与自动登记**（需人工裁决）。
该建议仅为**辅助**，`--apply` 时可通过 `--theme` 强制指定单一主题。

用法
----
    python tools/rfn_backlog.py                      # dry-run：候选清单 + 主题建议 + 统计
    python tools/rfn_backlog.py --apply --theme-mode suggested   # 按投票建议批量登记
    python tools/rfn_backlog.py --apply --theme T5   # 全部登记到 T5（人工已裁决）

产物
----
- `modules/regulatory_classifier/data/relations/rfn_backlog.csv`（机器可读候选清单）
- `docs/reports/RFN补登候选清单.md`（人类可读，含来源与票数）

纪律：写入一律经 `rfn.register_doc`（registry 唯一写口；幂等 + 时效置 `pending`）；
      `--apply` 前自动备份归属表/主题表/索引；登记后需重跑 `relations gen` 与分类链。
"""

from __future__ import annotations

import argparse
import collections
import csv
import json
import os
import shutil
import sys
from datetime import datetime
from glob import glob

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (
    ROOT,
    os.path.join(ROOT, "std_lib"),
    os.path.join(ROOT, "modules"),
    os.path.join(ROOT, "modules", "regulatory_classifier"),
    os.path.join(ROOT, "modules", "regulatory_scrapers"),
):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from config.exitcodes import ExitCode  # noqa: E402

CLS_DATA = os.path.join(ROOT, "modules", "regulatory_classifier", "data")
CLEANED = os.path.join(ROOT, "modules", "regulatory_scrapers", "data", "cleaned")
REL_DIR = os.path.join(CLS_DATA, "relations")
REL_INDEX = os.path.join(REL_DIR, "relations_index.jsonl")
OUT_CSV = os.path.join(REL_DIR, "rfn_backlog.csv")
OUT_MD = os.path.join(ROOT, "docs", "reports", "RFN补登候选清单.md")
BACKUP_ROOT = os.path.join(ROOT, "modules", "regulatory_classifier", "backups")

LEGAL_SOURCES = ("gov", "mof", "nfra", "pbc", "supp")
CSV_FIELDS = (
    "dedup_key",
    "title",
    "docno",
    "source",
    "publish_date",
    "cited_count",
    "cited_by_distinct",
    "suggested_theme",
    "theme_votes",
    "theme_confidence",
    "sample_cited_by",
    "decision",
)  # decision ∈ {votes, law_to_t0, uncertain}：主题建议的**依据**


def _read_jsonl(path: str) -> list[dict]:
    out = []
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return out


# 法律/行政法规形态（`中华人民共和国XX法`、`XX条例`、`最高人民法院…解释`、`…令`）——
# 主题体系里 **T0 = 上位法锚点**（rfn 主题表枚举「T0 上位法锚点 + T1–T10」），
# 故这类候选有**体系依据**的默认主题建议（非臆造）。
_LAW_SHAPE_RE = None


def _law_shape(title: str) -> bool:
    import re  # noqa: PLC0415

    t = (title or "").strip()
    global _LAW_SHAPE_RE
    if _LAW_SHAPE_RE is None:
        _LAW_SHAPE_RE = re.compile(
            r"^中华人民共和国[^，。]{1,60}(?:法|条例|细则)(?:（[^）]*）)?$"
            r"|^最高人民法院关于[^，。]{2,60}(?:解释|规定|批复|答复)$"
            r"|^[^，。]{2,30}条例$"
        )
    return bool(_LAW_SHAPE_RE.match(t))


def load_theme_map() -> dict[str, str]:
    """`监管文件编号 → 主题`：经 **rfn 索引**（`rows()` 已把主题归属表合并进行内）——唯一事实源。"""
    try:
        from rfn import get_index  # noqa: PLC0415

        return {
            r.get("监管文件编号", ""): (r.get("主题") or "")
            for r in get_index().rows()
            if r.get("监管文件编号")
        }
    except Exception:  # noqa: BLE001
        return {}


def load_cleaned_meta() -> dict[str, dict]:
    """`dedup_key → cleaned 记录摘要`（标题/文号/来源/日期）——候选的权威元数据。"""
    out: dict[str, dict] = {}
    for p in sorted(glob(os.path.join(CLEANED, "*_cleaned_*.jsonl"))):
        with open(p, encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                key = rec.get("dedup_key") or ""
                if key and key not in out:
                    out[key] = {
                        "title": (rec.get("title") or "").strip(),
                        "docno": (rec.get("document_number") or "").strip(),
                        "source": (rec.get("source") or "").strip(),
                        "publish_date": (rec.get("publish_date") or "").strip(),
                    }
    return out


def build_backlog() -> dict:
    """聚合补登候选：`dst_class=corpus` 的关系 → 按 `dst_key` 归并 + 引用者主题投票。"""
    rows = [
        r for r in _read_jsonl(REL_INDEX) if r.get("dst_class") == "corpus" and r.get("dst_key")
    ]
    theme_of = load_theme_map()
    meta = load_cleaned_meta()

    by_key: dict[str, dict] = {}
    for r in rows:
        k = r["dst_key"]
        it = by_key.setdefault(
            k,
            {
                "dedup_key": k,
                "cited_count": 0,
                "voters": collections.Counter(),
                "srcs": [],
                "names": collections.Counter(),
                "docnos": collections.Counter(),
            },
        )
        it["cited_count"] += 1
        it["names"][r.get("dst_name") or ""] += 1
        if r.get("dst_docno"):
            it["docnos"][r["dst_docno"]] += 1
        src = r.get("src_ref") or ""
        it["srcs"].append(src or (r.get("src_name") or "")[:24])
        th = theme_of.get(src, "")
        if th:
            it["voters"][th] += 1

    items = []
    for k, it in by_key.items():
        m = meta.get(k, {})
        title = m.get("title") or it["names"].most_common(1)[0][0]
        votes = it["voters"]
        top = votes.most_common()
        suggested, conf, theme_src = "uncertain", 0.0, "uncertain"
        if _law_shape(title):
            # ① 法律/行政法规 → **T0 上位法锚点**（体系语义优先）。
            #    注：此类**不能**用"引用者投票"——法律跨主题通用（实测《商业银行法》会被投成
            #    T1销售行为与消费者保护），投票在此失义。
            suggested, conf, theme_src = "T0", 0.6, "law_to_t0"
        elif top and (len(top) == 1 or top[0][1] > top[1][1]):
            # ② 具体监管文件：引用者主题多数票（须唯一最高，并列则弃权）
            suggested, conf, theme_src = (
                top[0][0],
                round(top[0][1] / sum(votes.values()), 3),
                "votes",
            )
        items.append(
            {
                "dedup_key": k,
                "title": title,
                "docno": m.get("docno")
                or (it["docnos"].most_common(1)[0][0] if it["docnos"] else ""),
                "source": m.get("source", ""),
                "publish_date": m.get("publish_date", ""),
                "cited_count": it["cited_count"],
                "cited_by_distinct": len(set(it["srcs"])),
                "suggested_theme": suggested,
                "theme_votes": json.dumps(dict(votes.most_common(5)), ensure_ascii=False),
                "theme_confidence": conf,
                "sample_cited_by": " / ".join(sorted(set(it["srcs"]))[:5]),
                "decision": theme_src,
            }
        )
    items.sort(key=lambda x: (-x["cited_count"], x["title"]))
    return {"items": items, "theme_of": theme_of, "relations_scanned": len(rows)}


def render_md(bl: dict, *, generated_at: str = "") -> str:
    """渲染补登候选清单正文（**纯函数，不落盘**）。

    F-L01 纳管（2026-09-14）：本工具 CLI（`write_outputs`）与 analysis 交付库
    （`tools/gen_analysis_deliveries.py` 2.1.2.5）**共用本实现——单一渲染源，禁止分叉**。
    `generated_at` 缺省取当前时间；交付库侧传入其统一时间戳（与其余 15 项 frontmatter 同源）。
    """
    items = bl["items"]
    sure = [i for i in items if i["suggested_theme"] != "uncertain"]
    _wl_uncertain(items)
    lines = [
        "# RFN 补登候选清单（关系线索驱动）",
        "",
        f"> 由 `tools/rfn_backlog.py` 生成于 "
        f"{generated_at or datetime.now().strftime('%Y-%m-%d %H:%M:%S')}。",
        "> **来源**：`relations_index.jsonl` 中 `dst_class=corpus` 的关系——即"
        "「已被制度/监管文件引用、且已采集（cleaned 命中）、但未登记 RFN」的文件。",
        "> 登记后重跑 `cli.py relations gen`，这些关系即升级为 `entity`（强关联）。",
        "",
        "## 一、总览",
        "",
        "| 维度 | 值 |",
        "| :--- | ---: |",
        f"| 候选文件数（去重） | {len(items)} |",
        f"| 涉及关系数 | {bl['relations_scanned']} |",
        f"| 主题建议明确（可自动登记） | {len(sure)} |",
        f"| 主题不确定（须人工裁决） | {len(items) - len(sure)} |",
        "",
        "## 二、候选清单（按被引用次数降序，前 40）",
        "",
        "| # | 被引 | 标题 | 文号 | 来源 | 建议主题 | 依据 | 票数 | 置信 |",
        "| ---: | ---: | :--- | :--- | :--- | :--- | :--- | :--- | ---: |",
    ]
    for n, i in enumerate(items[:40], 1):
        lines.append(
            f"| {n} | {i['cited_count']} | {i['title'][:44]} | {i['docno'][:20]} "
            f"| {i['source']} | {i['suggested_theme']} | {i['decision']} "
            f"| {i['theme_votes'][:34]} | {i['theme_confidence']:.2f} |"
        )
    if len(items) > 40:
        lines.append(f"| … | | （其余 {len(items) - 40} 项见 CSV） | | | | | | |")
    lines += [
        "",
        "## 三、主题建议口径（`decision` 列标注依据，**不臆造**）",
        "",
        "| `decision` | 含义 | 可用于自动登记 |",
        "| :--- | :--- | :--- |",
        "| `votes` | **引用者主题投票**：引用它的、已登记 RFN 的文件所属主题多数票（票数须唯一最高） | ✅ |",
        "| `law_to_t0` | 候选形态为**法律/行政法规**（`中华人民共和国…法`/`…条例`/最高法解释）→ 归 **T0 上位法锚点**"
        "（项目主题体系既定语义） | ✅ |",
        "| `uncertain` | 无票／票数并列／形态不明 | ❌（须人工裁决后 `--theme Tx` 强制登记） |",
        "",
        "> 说明：多数候选的**引用者本身也未登记 RFN**（实测 582 条 corpus 关系中仅 105 条引用者带 RFN），",
        "> 故投票覆盖率天然有限——这是「补登」应分批推进的原因（先法律/行政法规，再逐批扩面）。",
        "",
        "## 四、处置",
        "",
        "```powershell",
        "%PY% tools\\rfn_backlog.py                                  # dry-run 复核（本清单）",
        "%PY% tools\\rfn_backlog.py --apply --theme-mode suggested    # 按建议主题批量登记（走 registry 唯一写口）",
        "%PY% cli.py relations gen                                    # 重启关系抽取 → 升级为强关联",
        "%PY% cli.py classify --all                                   # 底座/明细/报告重算（登记改变了主题归属）",
        "```",
        "",
        "> 注：`register_doc` 登记时**时效状态置 `pending`**（未核验显式标注，禁止默认 valid），",
        "> 故补登不会伪造「现行有效」状态。",
        "",
    ]
    return "\n".join(lines)


def write_outputs(bl: dict) -> dict:
    items = bl["items"]
    sure = [i for i in items if i["suggested_theme"] != "uncertain"]
    os.makedirs(REL_DIR, exist_ok=True)
    with open(OUT_CSV + ".tmp", "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        w.writeheader()
        w.writerows(items)
    os.replace(OUT_CSV + ".tmp", OUT_CSV)

    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as fh:
        fh.write(render_md(bl))
    return {"csv": OUT_CSV, "md": OUT_MD, "items": len(items), "sure": len(sure)}


def apply_backlog(bl: dict, *, theme_mode: str = "", fixed_theme: str = "") -> dict:
    """批量登记（经 `rfn.register_doc` 唯一写口；幂等 + 备份）。"""
    from rfn import registry  # noqa: PLC0415

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = os.path.join(BACKUP_ROOT, f"rfn_backlog_{ts}")
    os.makedirs(backup_dir, exist_ok=True)
    for p in (
        glob(os.path.join(ROOT, "modules", "regulatory_classifier", "*归属表.csv"))
        + glob(os.path.join(ROOT, "modules", "regulatory_classifier", "*主题表.csv"))
        + glob(os.path.join(CLS_DATA, "*索引.csv"))
    ):
        if os.path.exists(p):
            shutil.copy2(p, os.path.join(backup_dir, os.path.basename(p)))

    res = {
        "registered": 0,
        "reused": 0,
        "skipped_theme": 0,
        "skipped_nodata": 0,
        "failed": 0,
        "backup_dir": backup_dir,
        "details": [],
    }
    for it in bl["items"]:
        theme = fixed_theme or (it["suggested_theme"] if theme_mode == "suggested" else "")
        if not theme or theme == "uncertain":
            res["skipped_theme"] += 1
            continue
        if not it["title"] or not it["dedup_key"]:
            res["skipped_nodata"] += 1
            continue
        src = it["source"] if it["source"] in LEGAL_SOURCES else ""
        try:
            r = registry.register_doc(
                theme=theme,
                title=it["title"],
                docno=it["docno"] or None,
                pub_date=it["publish_date"],
                source=src,
                source_mark="relations_backlog",
            )
        except Exception as e:  # noqa: BLE001
            res["failed"] += 1
            res["details"].append({"dedup_key": it["dedup_key"], "error": repr(e)[:120]})
            continue
        res["reused" if r.get("action") == "reused" else "registered"] += 1
        res["details"].append(
            {
                "dedup_key": it["dedup_key"],
                "rfn": r.get("rfn"),
                "theme": theme,
                "action": r.get("action"),
            }
        )
    with open(os.path.join(backup_dir, "apply_result.json"), "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=2)
    return res


def sync_timeliness(*, dry_run: bool = False, only_rfns: set[str] | None = None) -> dict:
    """补登后**按 SSOT 键精确继承时效状态**（`verification_state` → 归属表「时效状态」列）。

    必要性（实测）：`register_doc` 按设计把新行时效置 `pending`（"登记即待核验"），但补登的
    文件**可能已在时效核验 SSOT 中**，此时归属表 `pending` 与 SSOT 冲突 → `gate_timeliness_ssot`
    的"SSOT→归属表"层 FAIL。

    ⚠️ **不能用 `verification_state.sync_to_classifier`**（实测踩坑）：该函数的标题匹配是
    `needle in 归属表.文件名称`（**子串包含**），在 3000+ 记录规模下会**误命中并改错既有行的状态**
    （本次实测一次改动 1000+ 行、引入大量新漂移）。本函数改用**与门禁同口径的键匹配**：
    `state_key(发文字号, 文件名称)` 精确查 SSOT——命不中即不改（保持 `pending`，门禁会跳过）。
    """
    try:
        from rfn import registry  # noqa: PLC0415
        from timeliness_review.verification_state import load_state, state_key  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        print(f"[backlog] 时效同步跳过（导入失败: {e!r}）")
        return {"changed": 0, "scanned": 0}
    st = load_state() or {}
    fh = registry._lock()
    try:
        rows = registry._load_rows()
        changed = 0
        for r in rows:
            rfn = r.get("监管文件编号", "")
            if only_rfns is not None and rfn not in only_rfns:
                continue
            rec = st.get(state_key(r.get("发文字号", ""), r.get("文件名称", ""))) or {}
            s = (rec.get("status") or "").strip()
            if s and s != (r.get("时效状态") or "").strip():
                if not dry_run:
                    r["时效状态"] = s
                changed += 1
        if not dry_run and changed:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            bak = os.path.join(BACKUP_ROOT, f"timeliness_sync_{ts}")
            os.makedirs(bak, exist_ok=True)
            for p in (registry._csv_path(), registry._theme_csv_path()):
                if os.path.exists(p):
                    shutil.copy2(p, os.path.join(bak, os.path.basename(p)))
            registry._save_rows(rows)
            registry.rebuild_index()
            print(f"[backlog] 时效备份 → {bak}")
    finally:
        registry._unlock(fh)
    print(
        f"[backlog] 时效同步（键精确）：SSOT {len(st)} 条 → 归属表改动 {changed} 行"
        f"{'（dry-run）' if dry_run else ''}"
    )
    return {"changed": changed, "scanned": len(rows)}


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description="RFN 补登候选（关系线索驱动）")
    ap.add_argument("--apply", action="store_true", help="执行批量登记（默认 dry-run）")
    ap.add_argument(
        "--theme-mode",
        choices=["suggested"],
        default="",
        help="suggested：按「引用者主题投票」建议登记",
    )
    ap.add_argument("--theme", default="", help="强制全部登记到指定主题（如 T5）")
    ap.add_argument(
        "--sync-timeliness",
        action="store_true",
        dest="sync_tl",
        help="仅执行「SSOT→归属表」时效同步（补登后补齐已知状态）",
    )
    a = ap.parse_args()

    if a.sync_tl:
        sync_timeliness(dry_run=not a.apply)
        return ExitCode.OK

    if not os.path.exists(REL_INDEX):
        print(f"[backlog] 关系事实源缺失：{REL_INDEX}（先运行 `cli.py relations gen`）")
        return ExitCode.FAIL
    bl = build_backlog()
    out = write_outputs(bl)
    print(
        f"[backlog] 候选文件 {out['items']} 个（可自动登记 {out['sure']} / 待人工裁决 "
        f"{out['items'] - out['sure']}）；涉及关系 {bl['relations_scanned']} 条"
    )
    print(f"[backlog] 清单 → {out['csv']}")
    print(f"[backlog] 报告 → {out['md']}")
    top = bl["items"][:8]
    for i in top:
        print(
            f"   {i['cited_count']:>3}× {i['title'][:52]:54s} 建议主题 {i['suggested_theme']}"
            f"（{i['theme_confidence']:.2f}）"
        )
    if not a.apply:
        print("[backlog] dry-run 结束（未写归属表）。加 --apply 执行。")
        return ExitCode.OK
    if not (a.theme_mode == "suggested" or a.theme):
        print("[backlog] --apply 需配合 --theme-mode suggested 或 --theme Tx")
        return ExitCode.FAIL
    res = apply_backlog(bl, theme_mode=a.theme_mode, fixed_theme=a.theme)
    print(
        f"[backlog] 登记完成：registered {res['registered']} / reused {res['reused']} / "
        f"跳过(主题) {res['skipped_theme']} / 失败 {res['failed']}"
    )
    print(f"[backlog] 备份与明细 → {res['backup_dir']}")
    if res["registered"]:
        # 补登后必须继承已知时效状态（否则新行 pending 会与 SSOT 冲突 → 门禁 FAIL）
        sync_timeliness()
    print("[backlog] 后续：`cli.py relations gen` 升级强关联；`cli.py classify --all` 重算底座")
    return ExitCode.OK


if __name__ == "__main__":
    sys.exit(main())


def _wl_uncertain(items) -> None:
    """P2-5（D1，v2 §3.14.3）：主题投票 `uncertain`（无票/票数并列/形态不明）登记待办。

    原状：仅标注 `uncertain` 并"不参与自动登记"，**无处置入口**（翻不回去）。
    现登记进 worklist，`cli.py worklist resolve` 即处置通道。旁路设施：失败降级。
    """
    try:
        from std_lib.common_lib import governance_store as _gs  # noqa: PLC0415

        n = 0
        for it in items:
            if it.get("suggested_theme") != "uncertain":
                continue
            key = str(it.get("document_number") or it.get("title") or "")[:80]
            if not key:
                continue
            _gs.worklist_add(
                "rfn_theme_uncertain",
                key,
                stage="2.7",
                artifact_key="rfn_backlog",
                payload={
                    "title": it.get("title", ""),
                    "document_number": it.get("document_number", ""),
                    "decision": it.get("decision", ""),
                    "theme_src": it.get("theme_src", ""),
                    "reason": "无票 / 票数并列 / 形态不明",
                },
                suggestion="人工裁决后 `cli.py rfn register --theme Tx` 强制登记（或 dismiss）",
            )
            n += 1
        if n:
            print(
                f"[rfn_backlog] 待办：{n} 条 uncertain 已登记 worklist（cli.py worklist resolve）"
            )
    except Exception:  # noqa: BLE001  旁路设施：登记失败不得中断生成
        pass
