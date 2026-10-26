# -*- coding: utf-8 -*-
"""tools/build_external_authority.py — 生成**外部法规权威表**（独立键空间）。

输入：关系事实（`relations_index.jsonl`，经 `interfaces.relations_api.load`）
筛选：**`dst_class == "external"`**（由抽取器 `tools/extract_relations.py` 按
      `std_lib/common_lib/relations/patterns.py` 的受控枚举判定）。这是**唯一正确判据**：

      | dst_class | 含义 | 归宿 |
      |---|---|---|
      | `entity` | 已解析到 RFN/IPN | 不入本表 |
      | `corpus` | 已采集但未登记 RFN | 走 **RFN 补登记**（不是外部） |
      | `organ`  | **机关名**（"国务院"/"财政部"/"同级人民政府"） | **必须排除**：它不是文件 |
      | `external` | 语料外**文件**（法律法规/制度历史版本） | **本表登记** |

      我第一版用的是"有标题就算"，结果把 1026 条机关名（国务院/财政部/银保监局/
      总公司办公室…）当成外部法规登记了 —— 那是**把"谁说的"记成"说了什么"**。
      改用 `dst_class` 后口径与统计（`target_class`）完全一致，不再各算一套。
聚合：按 `external_api.key_of(title, docno)` 分组（文号优先，退标题）
输出：
  · `modules/regulatory_classifier/data/relations/external_authority.jsonl`
  · `modules/regulatory_classifier/data/relations/external_authority_stat.json`

为什么要有这一步（而不是在 `interfaces` 里现算）
-----------------------------------------------
1. **可审计**：生成物是文件，能 diff、能复核"哪些目标被登记成了外部权威"；
2. **可解释**：每条带 `evidence_count`（被多少条关系引用）与 `sample_src`（举例），
   读者能自己判断这条登记是否合理；
3. **幂等**：编号由键派生 ⇒ 重跑不产生新编号，关系侧的引用**不会漂移**。

用法：
    python -m tools.build_external_authority                # 生成
    python -m tools.build_external_authority --dry-run      # 只统计不落盘
    python -m tools.build_external_authority --check        # 校验（编号稳定/无冲突/无空白）
"""

from __future__ import annotations

import argparse
import collections
import json
import os

# 引导一律经 `bootstrap`（门禁：`tools/` 的 `sys.path.insert` 数量只减不增）。
# 因此本脚本**必须**以 `python -m tools.build_external_authority` 运行 ——
# 链上（`run_production_refresh` 的 relations:ext）已按 `-m` 调用。
from bootstrap import bootstrap

bootstrap("all")

from config.exitcodes import ExitCode
from interfaces import external_api as E
from interfaces import relations_api as R
from std_lib.common_lib.relations.patterns import (
    TARGET_CORPUS,
    TARGET_EXTERNAL,
    TARGET_ORGAN,
)


def _organ_excluded_from_stat(fallback: int = 0) -> int:
    """机关名排除量：**优先读上游 `relations_stat.excluded_organ`**。

    2026-10-26（R-DEP-06）：机关名在抽取层即被剔除，关系产物里没有 organ 行，
    本模块遍历关系得到的计数恒为 0。若沿用它，页面会显示"排除机关名 0 条"——
    与事实（实测 1068 条）相反。故改为读上游统计，并保留 fallback 以便旧产物兼容。
    """
    try:
        import json
        import os

        p = os.path.join(os.path.dirname(R.INDEX_PATH), "relations_stat.json")
        if os.path.exists(p):
            st = json.load(open(p, encoding="utf-8"))
            return int(st.get("excluded_organ") or 0)
    except Exception:  # noqa: BLE001  统计不可读 → 退回遍历计数
        pass
    return int(fallback or 0)


def _collect() -> tuple[list[dict], dict]:
    """→ (权威表行, 统计)。纯函数（除读产物外无副作用），便于 `--check` 复用。"""
    rows = R.load("all")
    groups: dict[str, dict] = {}
    skipped_no_evidence = 0
    corpus_weak = 0
    excluded_organ = 0
    for r in rows:
        cls = str(r.get("dst_class") or "")
        if cls == TARGET_ORGAN:
            # 机关名不是文件（"国务院"/"财政部"）⇒ 既不进本表，也不算"未定位"
            excluded_organ += 1
            continue
        if cls == TARGET_CORPUS:
            corpus_weak += 1             # 语料文件未登记 RFN ⇒ 走补登记，不进本表
            continue
        if r.get("dst_ref"):
            continue                     # 已定位到语料实体
        if r.get("dst_key"):
            corpus_weak += 1             # 兜底：有 corpus 键也算语料侧
            continue
        if cls and cls != TARGET_EXTERNAL:
            continue                     # 其它未知分类：不擅自登记
        title = str(r.get("dst_name") or "")
        docno = str(r.get("dst_docno") or "")
        ext_id = E.resolve(title, docno)
        if not ext_id:
            skipped_no_evidence += 1
            continue
        g = groups.get(ext_id)
        if g is None:
            g = groups[ext_id] = {
                "ext_id": ext_id,
                "title": title,
                "docno": docno,
                "title_norm": E._title_key(title),
                "docno_norm": E._docno_key(docno),
                "guise": E.guess_guise(title, docno),
                "kind": "external",
                "in_corpus": False,
                "dst_class": TARGET_EXTERNAL,
                "evidence_count": 0,
                "relations": collections.Counter(),
                "sample_src": [],
            }
        g["evidence_count"] += 1
        g["relations"][str(r.get("relation") or "")] += 1
        src = str(r.get("src_ref") or "")
        if src and src not in g["sample_src"] and len(g["sample_src"]) < 3:
            g["sample_src"].append(src)

    out: list[dict] = []
    for _ext_id, g in groups.items():
        g["relations"] = dict(g["relations"])
        out.append(g)
    out.sort(key=lambda x: (-x["evidence_count"], x["ext_id"]))

    by_guise = collections.Counter(x["guise"] for x in out)
    stat = {
        "schema_version": "1.0",
        "generated_by": "tools/build_external_authority.py",
        "generator_version": "1.0",
        "total": len(out),
        "evidence_total": sum(x["evidence_count"] for x in out),
        "by_guise": dict(by_guise),
        "skipped_no_evidence": skipped_no_evidence,
        "corpus_weak_excluded": corpus_weak,
        # 机关名（上游已剔除，见下）
        "organ_excluded": _organ_excluded_from_stat(excluded_organ),
        "note": ("只登记 `dst_class == external` 的**语料外文件**。"
                 "'organ_excluded' 是机关名（不是文件，故不登记）；"
                 "'corpus_weak_excluded' 是已采集未登记 RFN 的语料文件（应走 RFN 补登记）。"),
    }
    return out, stat


def _check(rows: list[dict], stat: dict) -> list[str]:
    """校验不变量；返回问题列表（空 = 通过）。"""
    bad: list[str] = []
    ids = [x["ext_id"] for x in rows]
    if len(ids) != len(set(ids)):
        bad.append("ext_id 重复")
    for x in rows:
        if not E.is_ext_id(x["ext_id"]):
            bad.append(f"编号不合规：{x['ext_id']}")
        if x["ext_id"] != E.resolve(x["title"], x["docno"]):
            bad.append(f"编号与键不一致（重算会漂移）：{x['ext_id']}")
        if x.get("in_corpus") is not False:
            bad.append(f"in_corpus 必须为 False：{x['ext_id']}")
        if x.get("kind") != "external":
            bad.append(f"kind 必须为 external：{x['ext_id']}")
        if x["guise"] not in E.GUISES:
            bad.append(f"guise 非受控值：{x['ext_id']} → {x['guise']}")
        if int(x.get("evidence_count") or 0) <= 0:
            bad.append(f"evidence_count 非正：{x['ext_id']}")
    if stat["evidence_total"] != sum(x["evidence_count"] for x in rows):
        bad.append("evidence_total 与明细不一致")
    if len(ids) != stat["total"]:
        bad.append("total 与明细行数不一致")
    # 与语料内 RFN 不得撞：本表前缀 `RFN-EXT-`，语料实体是 `RFN-<16hex>`
    for x in rows:
        body = x["ext_id"][len(E.PREFIX):]
        if len(body) != 12:
            bad.append(f"编号长度异常：{x['ext_id']}")
    return bad


def build(*, dry_run: bool = False) -> dict:
    rows, stat = _collect()
    bad = _check(rows, stat)
    if bad:
        # 校验不过就不落盘：宁可让链路感知失败，也不写一份自相矛盾的表
        return {"ok": False, "problems": bad[:20], "stat": stat}
    if dry_run:
        return {"ok": True, "dry_run": True, "stat": stat,
                "sample": [{k: v for k, v in r.items() if k != "relations"}
                           for r in rows[:5]]}

    os.makedirs(os.path.dirname(E.EXT_PATH), exist_ok=True)
    tmp = E.EXT_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
    os.replace(tmp, E.EXT_PATH)          # 原子替换：读者永远看不到半张表

    stat = dict(stat, generated_at=_now())
    with open(E.EXT_STAT_PATH, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(stat, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    return {"ok": True, "stat": stat, "path": E.EXT_PATH}


def _now() -> str:
    import datetime

    return datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S%z") or ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="orchestrator build-external-authority",
        description="生成外部法规权威表（语料外目标的独立键空间，编号 RFN-EXT-*）")
    ap.add_argument("--dry-run", action="store_true", help="只统计，不落盘")
    ap.add_argument("--check", action="store_true",
                    help="只校验现有表（编号稳定/无重复/无空白）")
    a = ap.parse_args(argv)

    if a.check:
        if not E.exists():
            print("[ext] 表不存在，请先运行生成")
            return ExitCode.FAIL
        rows = E.load()
        stat = E.load_stat()
        bad = _check(rows, stat)
        if bad:
            print("[ext] 校验未通过：")
            for b in bad[:20]:
                print("  -", b)
            return ExitCode.FAIL
        print(f"[ext] 校验通过：{len(rows)} 条外部权威，证据 "
              f"{stat.get('evidence_total')} 条关系")
        return ExitCode.OK

    res = build(dry_run=a.dry_run)
    if not res.get("ok"):
        print("[ext] 生成失败（校验未过，未落盘）：")
        for p in res.get("problems") or []:
            print("  -", p)
        return ExitCode.FAIL
    st = res["stat"]
    if a.dry_run:
        print(f"[ext][dry-run] 将登记 {st['total']} 条外部权威，"
              f"覆盖 {st['evidence_total']} 条关系；按面目 {st['by_guise']}")
        return ExitCode.OK
    print(f"[ext] 已生成 {st['total']} 条外部权威 → {res['path']}")
    print(f"      覆盖 {st['evidence_total']} 条关系；按面目 {st['by_guise']}")
    print(f"      排除：语料弱线索 {st['corpus_weak_excluded']} 条"
          f"（应走 RFN 补登记）、无证据 {st['skipped_no_evidence']} 条")
    return ExitCode.OK


if __name__ == "__main__":
    raise SystemExit(main())
