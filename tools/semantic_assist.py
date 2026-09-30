# -*- coding: utf-8 -*-
"""tools.semantic_assist — **P1 语义增强的入链调用点**（`semantic:assist`）

为什么需要它（N-177，2026-09-30）
--------------------------------
在它之前，`bge_base_zh` / `text2vec` / `youtu_embedding` 三个嵌入模型**权重与依赖均已就绪**
（`external/models/` 预置、`preflight` 五道闸全过），但**主链里没有任何调用点** ——
即"模型就绪却未入链"（用户所指「需要接入模型的节点未成功接入」「部分节点模型未能成功入链」）。
且 `gen_flow_map.MODEL_BINDINGS` 把它们绑在 `classify:all` 上，而**该节点并没有嵌入调用点** ——
绑定指向了一个**不存在调用点的节点**（「入链节点配置不佳」）。本工具即**真实调用点**，
绑定随之改指本节点（绑定必须指向真实调用点，否则"已接线"是纸面状态）。

它做什么
--------
以 `THEME_TITLE_KW`（主题关键词 **SSOT**，来自 `internal_policy_base.align`）为**原型**：
把每个主题的关键词拼成原型文本 → 取嵌入向量 → 对 cleaned 记录（限量抽样）取标题向量 →
**最近邻主题裁定**（cosine），输出 top-1 主题、相似度与 **margin（top1−top2，用于判"是否勉强"）**。

**产出是「分析视图」**（`reports/semantic/`），**绝不写主题事实源**（`_t*_base`/`_t*_final`）——
这是 `config/schema/semantic_tools.json:usage_policy` 的硬约束，并由
`tools/audit_health.check_p1_policy()` **机器核验**。

纪律（与本仓"增强层零硬依赖 + 降级可观测"一致）
------------------------------------------------
- `usage_policy.enabled=false` → **显式 SKIP 并说明**（rc=0；未启用是合法状态，不是故障）；
- 嵌入链不可用（`EmbedResult.ok=False`）→ **显式披露**每个候选的失败原因（rc=0，不抛）；
- 指纹（模型名 + 版本 + 权重 sha）随产物落盘 → 断点幂等/水位判据不会因模型差异而失真。

用法
----
    python -m tools.semantic_assist --source all --limit 200
    python -m tools.semantic_assist --source pbc --limit 50 --mode incremental --dry-run
"""

from __future__ import annotations

import argparse
import csv
import datetime
import io
import json
import os
import sys

# 引导：**必须经 `bootstrap`（唯一引导点）** —— `gate_import_bootstrap` 断言 tools/ 层
# 的 `sys.path.insert` **只减不增**；运行方式相应为 `python -m tools.semantic_assist`。
from bootstrap import bootstrap

bootstrap("all")

import paths
from config.enums import SOURCE_ORDER
from config.exitcodes import ExitCode

OUT_DIR = os.path.join(paths.ROOT, "reports", "semantic")
#: 主题关键词 SSOT 的字段名（`internal_policy_base.align.THEME_TITLE_KW`）
_TEXT_FIELDS = ("title", "summary")


def _policy() -> dict:
    from std_lib.common_lib import semantic_tools as st

    return (st.load_manifest().get("usage_policy") or {})


def _load_records(src: str, limit: int) -> list:
    """读某源 cleaned 的**限量抽样**（只取裁定所需字段，不载全文 —— 控制内存与耗时）。"""
    from clean_index import get_clean_index

    p = get_clean_index().latest_csv_path(src)
    if not p or not os.path.exists(p):
        return []
    rows: list = []
    # 大字段（body_text/attachment_content）可能极长 → 提高字段上限，但只取所需列
    csv.field_size_limit(sys.maxsize)
    with open(p, encoding="utf-8", errors="replace") as fh:
        rd = csv.DictReader(fh)
        for r in rd:
            if len(rows) >= limit:
                break
            title = (r.get("title") or "").strip()
            if not title:
                continue
            rows.append(
                {
                    "source": src,
                    "title": title,
                    "theme_name": (r.get("theme_name") or "").strip(),
                    "document_number": (r.get("document_number") or "").strip(),
                    "key": (r.get("dedup_key") or r.get("source_url") or title)[:120],
                }
            )
    return rows


def _theme_prototypes() -> dict:
    """主题 → 原型文本（关键词 SSOT 拼接；顺序稳定以便指纹可复现）。"""
    out: dict = {}
    try:
        from modules.internal_policy_base.align import THEME_TITLE_KW

        for theme, kws in THEME_TITLE_KW.items():
            text = " ".join(str(k) for k in (kws or []))
            if text.strip():
                out[str(theme)] = text
    except Exception as e:  # noqa: BLE001  主题表缺失 → 由调用方显式披露
        out["__error__"] = f"{type(e).__name__}: {e}"
    return out


def _cosine(a: list, b: list) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def run(source: str = "all", limit: int = 200, mode: str = "full", dry_run: bool = False) -> dict:
    """→ 结果字典（同时落盘分析视图）。**不写任何事实源。**"""
    from std_lib.common_lib import semantic_enhance as se

    pol = _policy()
    res: dict = {
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": source,
        "limit": limit,
        "mode": mode,
        "status": "",
        "note": "",
        "policy_enabled": bool(pol.get("enabled")),
        "records": 0,
        "themes": 0,
        "agreement": None,
        "margin": {},
        "distribution": {},
        "disagreements": [],
        "fingerprint": {},
        "embedder_notice": "",
    }

    # ① 口径闸：未启用 → 显式 SKIP（rc=0；未启用是合法状态）
    if not res["policy_enabled"]:
        res["status"] = "skipped"
        res["note"] = (
            "`usage_policy.enabled=false` → 语义辅助**未启用**（合法状态，非故障）。"
            "启用前置见 `semantic_tools --preflight`（五道闸）与 `usage_policy.enable_preconditions`。"
        )
        _write(res, dry_run)
        return res

    # ② 主题原型（SSOT）
    protos = _theme_prototypes()
    if "__error__" in protos:
        res["status"] = "degraded"
        res["note"] = f"主题关键词 SSOT 不可读：{protos['__error__']}（跳过裁定）"
        _write(res, dry_run)
        return res
    res["themes"] = len(protos)
    if dry_run:
        res["status"] = "dry_run"
        res["note"] = f"dry-run：将用 {len(protos)} 个主题原型对 cleaned 抽样做最近邻裁定"
        _write(res, dry_run)
        return res

    # ③ 加载记录
    srcs = list(SOURCE_ORDER) if source == "all" else [source]
    rows: list = []
    for s in srcs:
        rows += _load_records(s, limit)
    res["records"] = len(rows)
    if not rows:
        res["status"] = "degraded"
        res["note"] = "无可用 cleaned 记录（各源 cleaned 缺失或标题为空）"
        _write(res, dry_run)
        return res

    # ④ 嵌入（含指纹 + 降级披露）
    try:
        res["fingerprint"] = se.fingerprint(mode=mode)
    except Exception as e:  # noqa: BLE001
        res["fingerprint"] = {"error": f"{type(e).__name__}: {e}"}
    theme_names = sorted(protos)
    pr = se.embed([protos[t] for t in theme_names], mode=mode)
    rr = se.embed([r["title"] for r in rows], mode=mode)
    if not (pr.ok and rr.ok):
        res["status"] = "degraded"
        res["embedder_notice"] = (pr.notice or "") + (" | " + rr.notice if rr.notice else "")
        res["note"] = (
            "嵌入链不可用 → 显式降级（**不抛**）。候选尝试："
            + ", ".join(str(x) for x in (pr.chain_tried or rr.chain_tried or []))
        )
        _write(res, dry_run)
        return res

    # ⑤ 最近邻裁定（cosine；报 top1/top2 与 margin）
    dist: dict = {}
    ag_hit = ag_tot = 0
    margins: list = []
    for r, vec in zip(rows, rr.vectors, strict=False):
        sims = sorted(((t, _cosine(vec, pv)) for t, pv in zip(theme_names, pr.vectors, strict=False)),
                      key=lambda x: x[1], reverse=True)
        top1, s1 = sims[0]
        s2 = sims[1][1] if len(sims) > 1 else 0.0
        r["suggested_theme"] = top1
        r["similarity"] = round(s1, 4)
        r["margin"] = round(s1 - s2, 4)
        dist[top1] = dist.get(top1, 0) + 1
        margins.append(r["margin"])
        base = r.get("theme_name") or ""
        if base:  # 有确定性基线才计一致性（否则如实计 n/a）
            ag_tot += 1
            if base == top1:
                ag_hit += 1
            else:
                res["disagreements"].append(
                    {"source": r["source"], "key": r["key"], "title": r["title"][:80],
                     "baseline": base, "suggested": top1,
                     "similarity": r["similarity"], "margin": r["margin"]}
                )
    res["agreement"] = (round(ag_hit / ag_tot, 4) if ag_tot else None)
    res["agreement_n"] = ag_tot
    res["margin"] = {
        "min": round(min(margins), 4) if margins else None,
        "median": round(sorted(margins)[len(margins) // 2], 4) if margins else None,
        "max": round(max(margins), 4) if margins else None,
    }
    res["distribution"] = dict(sorted(dist.items(), key=lambda x: -x[1]))
    res["samples"] = [
        {"source": r["source"], "key": r["key"], "title": r["title"][:80],
         "suggested": r["suggested_theme"], "similarity": r["similarity"], "margin": r["margin"]}
        for r in rows[:20]
    ]
    res["status"] = "ok"
    res["note"] = (
        f"最近邻裁定完成：{len(rows)} 条 × {len(theme_names)} 主题；"
        f"嵌入模型 = {res['fingerprint'].get('model') or res['fingerprint'].get('requested') or '?'}"
        f"（策略链 {res['fingerprint'].get('chain_tried') or []}）"
    )
    _write(res, dry_run)
    return res


def _write(res: dict, dry_run: bool) -> None:
    """落盘**分析视图**（`reports/semantic/`）—— 路径硬校验：**绝不写事实源**。"""
    today = datetime.datetime.now().strftime("%Y%m%d")
    os.makedirs(OUT_DIR, exist_ok=True)
    jp = os.path.join(OUT_DIR, f"semantic_assist_{today}.json")
    mp = os.path.join(OUT_DIR, f"semantic_assist_{today}.md")
    assert os.path.abspath(jp).startswith(os.path.abspath(os.path.join(paths.ROOT, "reports"))), (
        "语义辅助只能写 reports/ 下的分析视图（usage_policy.write_fact_source=false）"
    )
    payload = dict(res)
    if dry_run:
        payload["dry_run"] = True
        jp = os.path.join(OUT_DIR, f"semantic_assist_{today}.dryrun.json")
        mp = os.path.join(OUT_DIR, f"semantic_assist_{today}.dryrun.md")
    with open(jp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    lines = [
        f"# P1 语义辅助裁定（分析视图）· {res['generated_at']}",
        "",
        "> **只读分析视图**：不写主题事实源（`usage_policy.write_fact_source=false`）。",
        f"> 状态：**{res['status']}** ｜ 记录 {res['records']} 条 ｜ 主题 {res['themes']} 个 ｜ "
        f"模式 `{res['mode']}` ｜ 限样 {res['limit']}/源",
        "",
        f"- 说明：{res['note']}",
    ]
    if res.get("embedder_notice"):
        lines.append(f"- ⚠️ 嵌入降级披露：{res['embedder_notice']}")
    if res.get("agreement") is not None:
        lines.append(
            f"- **与确定性主题归属的一致性**：{res['agreement']:.1%}"
            f"（{res.get('agreement_n')} 条有基线；基线为空者计入分布/边距但**不计**一致性）"
        )
    else:
        lines.append("- 一致性：**n/a**（样本内无确定性基线 `theme_name`）")
    m = res.get("margin") or {}
    if m.get("median") is not None:
        lines.append(f"- **margin（top1−top2）**：中位 {m['median']}，min {m['min']}，max {m['max']}")
    if res.get("distribution"):
        lines.append("- 建议主题分布：" + "，".join(f"{k}×{v}" for k, v in res["distribution"].items()))
    fp = res.get("fingerprint") or {}
    lines.append(f"- 指纹：`{json.dumps(fp, ensure_ascii=False)}`")
    if res.get("disagreements"):
        lines += ["", "## 分歧清单（前 20）", "", "| 源 | 标题 | 基线 | 建议 | 相似度 | margin |", "|---|---|---|---|---|---|"]
        for d in res["disagreements"][:20]:
            lines.append(
                f"| {d['source']} | {d['title']} | {d['baseline']} | {d['suggested']} | "
                f"{d['similarity']} | {d['margin']} |"
            )
    with open(mp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    res["artifacts"] = [os.path.relpath(jp, paths.ROOT), os.path.relpath(mp, paths.ROOT)]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="P1 语义辅助裁定（分析视图；不写事实源）")
    ap.add_argument("--source", default="all", help="源: all/gov/mof/nfra/pbc/supp")
    ap.add_argument("--limit", type=int, default=200, help="每源抽样条数（默认 200）")
    ap.add_argument("--mode", default="full", help="选型模式: full（默认）/ incremental")
    ap.add_argument("--dry-run", action="store_true", help="只披露将做什么，不加载模型")
    a = ap.parse_args(argv)
    res = run(a.source, a.limit, a.mode, a.dry_run)
    print(f"[semantic_assist] status={res['status']}  records={res['records']}  "
          f"themes={res['themes']}  agreement={res['agreement']}")
    if res.get("note"):
        print(f"  {res['note']}")
    for p in res.get("artifacts") or []:
        print(f"  产物 → {p}")
    # 纪律：增强层**不阻断主链**（未启用/降级均为合法状态）；只有真正的内部错误才非 0
    return int(ExitCode.OK)


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    raise SystemExit(main())
