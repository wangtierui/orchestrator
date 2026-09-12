# -*- coding: utf-8 -*-
"""
classify.py — 主题底座强序重建编排器（二期 R8，2026-09-08）

把底座/明细链从「手工分步」固化为强序子步（蓝图 P5 R8），每主题按固定顺序执行并带
hash 断点（幂等：输入未变则跳过对应产物重建）：
  T1–T10 每主题： base → cluster(final) → match_theme_docs(matched/citerefs) → build_detail_tables
                → build_upper_laws（全库，仅明细变化后） → build_clause_graph
  T0 锚点：仅明细（T0_9）+ upper 引用，不生成底座。

用法（经 cli.py classify）：
  python classify.py --all                 # 全部主题强序
  python classify.py --theme T3            # 单主题
  python classify.py --theme T1 --steps base,cluster   # 仅前两子步（验证）
  python classify.py --dry-run             # 仅列计划不执行
断点：data/classify_state.json 记录每 (主题,子步) 输入 sha256；输入（归属表/上步产物/聚类关键词）
未变且产物存在 → 跳过（输出 skip）。输入变化 → 重跑并**级联刷新下游**（后续子步自动失效）。
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys

_THIS = os.path.dirname(os.path.abspath(__file__))       # modules/regulatory_classifier/scripts
_CLASS = os.path.dirname(_THIS)                          # modules/regulatory_classifier
for _p in (_CLASS, os.path.dirname(_CLASS), os.path.dirname(os.path.dirname(_CLASS))):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from rfn import THEME_MAP  # noqa: E402

_SCRIPTS = _THIS
_DATA = os.path.join(_CLASS, "data")
_ATTR = os.path.join(_DATA, "人身保险公司-文件归属表.csv")
_STATE = os.path.join(_DATA, "classify_state.json")
_KEYWORDS = os.path.join(_SCRIPTS, "config", "cluster_keywords.json")
# F-C04（2026-09-12）：主题归属表入断点——re_theme 改判后 base/detail 必须重跑（原 inputs
# 缺该表 → 主题改判永不迁移，M-06/审查 D-02）。
_THEME_CSV = os.path.join(_DATA, "人身保险公司-主题归属表.csv")
# R6（2026-09-08）：clean_index/index.json 作为 clean 快照内容签名——正文同日内容变化时
# index 每源 sha 会变，match/detail/upper 断点据此级联重跑，消除"正文变但条数同"的静默过时。
# F-D05④（2026-09-12）：index.json 现已带每源文件内容 sha256（clean 管道 hash_files=True +
# apply 尾部重建）——同日改写即使 size 相同也会改变 index.json 内容 → 断点级联刷新。
_CLEAN_INDEX = os.path.join(os.path.dirname(_CLASS), "regulatory_scrapers", "clean_index", "index.json")
_PY = sys.executable
_BODY_THEMES = [c for c in THEME_MAP if c != "T0"]       # 有底座的 T1–T10

# 子步执行器：名称 -> (描述, 需要输入列表哈希因子, 命令构造)
def _run(script: str, args: list[str], cwd: str = _SCRIPTS) -> int:
    print(f"    ↳ {os.path.basename(script)} {' '.join(args)}")
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    p = subprocess.run([_PY, script, *args], cwd=cwd, env=env)
    return p.returncode


def _sha(*paths: str) -> str:
    h = hashlib.sha256()
    for p in paths:
        if os.path.exists(p):
            with open(p, "rb") as fh:
                h.update(fh.read())
        else:
            h.update(b"<missing>")
    return h.hexdigest()[:16]


def plan_theme(theme: str):
    """返回该主题有序子步计划 [{step, inputs, desc}]。T0 无底座仅明细。"""
    n = theme[1:]
    if theme == "T0":
        return [{"step": "detail", "inputs": (_ATTR,),
                 "desc": "T0 明细（上位法锚点）",
                 "args": ["--theme", "T0", "--apply"]}]
    base = os.path.join(_DATA, f"_t{n}_base.json")
    final = os.path.join(_DATA, f"_t{n}_final.json")
    matched = os.path.join(_DATA, f"_t{n}_matched.json")
    citerefs = os.path.join(_DATA, f"_t{n}_citerefs.json")
    return [
        {"step": "base", "inputs": (_ATTR, _THEME_CSV),
         "desc": "base 底座（归属表派生）",
         "args": ["--theme", theme]},
        {"step": "cluster", "inputs": (base, _KEYWORDS),
         "desc": "final 子主题聚类",
         "args": ["--input", base, "--output", final, "--theme", theme]},
        {"step": "match", "inputs": (final, _CLEAN_INDEX),   # R6：含 clean 快照签名
         "desc": "matched/citerefs 条款引用匹配",
         "args": ["--input", final, "--output", matched, "--citerefs", citerefs]},
        {"step": "detail", "inputs": (_ATTR, _THEME_CSV, final, _CLEAN_INDEX),  # R6 + F-C04
         "desc": "明细表",
         "args": ["--theme", theme, "--apply"]},
        {"step": "clause_graph", "inputs": (matched, citerefs),
         "desc": "clause_graph",
         "args": ["--theme", theme]},
    ]


def _upper_dirty(state: dict, themes_done: list[str]) -> bool:
    """upper 依赖明细 + 归属表；本批有任一主题明细重建则需重跑 upper。"""
    if not themes_done:
        return False
    for t in themes_done:
        for s in state.get("themes", {}).get(t, []):
            if s["step"] == "detail" and s.get("ran"):
                return True
    return False


def run(theme: str = "", themes: list[str] | None = None, only_steps: set[str] | None = None,
        dry_run: bool = False) -> dict:
    state = {}
    if os.path.exists(_STATE):
        try:
            state = json.load(open(_STATE, encoding="utf-8"))
            # F-D14：读侧剥离版本键（写侧注入 _meta；消费逻辑零感知）
            if isinstance(state, dict):
                state.pop("_meta", None)
        except Exception:
            state = {}
    state.setdefault("themes", {})
    themes = themes or ([theme] if theme else _BODY_THEMES)
    planned = []
    ran = skipped = 0
    ran_details = []
    for t in themes:
        plan = plan_theme(t)
        tst = state["themes"].setdefault(t, {})
        for step in plan:
            if only_steps and step["step"] not in only_steps:
                continue
            inputs = step["inputs"]
            cur = _sha(*inputs)
            prev = tst.get(step["step"])
            record = {"step": step["step"], "theme": t, "input_sha": cur, "ran": False}
            planned.append(record)
            if dry_run:
                continue
            if prev and prev.get("input_sha") == cur and step.get("skip_ok", True):
                # 断点命中：输入未变则跳过（upper/clause_graph 等依赖全链产物存在性由 inputs 校验）
                missing = any(not os.path.exists(p) for p in inputs)
                if not missing:
                    skipped += 1
                    record["ran"] = False
                    continue
            # 执行脚本
            cwd = _SCRIPTS
            script = _step_script(step["step"], t)
            rc = _run(script, step["args"], cwd)
            if rc != 0:
                record["error"] = f"step {step['step']} failed rc={rc}"
                print(json.dumps(record, ensure_ascii=False))
                return {"planned": planned, "ran": ran, "skipped": skipped, "error": record["error"]}
            record["ran"] = True
            ran += 1
            if step["step"] == "detail":
                ran_details.append(t)
            tst[step["step"]] = {"input_sha": cur, "ran_at": __import__("time").strftime("%Y-%m-%d %H:%M:%S")}
    # upper 全库（依赖明细变化）
    if not only_steps or "upper" in only_steps:
        rec = {"step": "upper", "theme": "*", "input_sha": _sha(_ATTR), "ran": False}
        planned.append(rec)
        if not dry_run:
            detail_files = sorted(
                f for f in os.listdir(_DATA)
                if f.endswith("逐份条款引用与上位法依据明细表.csv"))
            cur = _sha(_ATTR, _CLEAN_INDEX, *[os.path.join(_DATA, f) for f in detail_files])  # R6 clean 签名
            up_prev = state.get("upper", {}).get("input_sha")
            if up_prev == cur:
                skipped += 1
            else:
                rc = _run(os.path.join(_SCRIPTS, "build_upper_laws.py"), [])
                if rc != 0:
                    rec["error"] = f"upper failed rc={rc}"
                    return {"planned": planned, "ran": ran, "skipped": skipped, "error": rec["error"]}
                rec["ran"] = True
                ran += 1
                state["upper"] = {"input_sha": cur, "ran_at": __import__("time").strftime("%Y-%m-%d %H:%M:%S")}
    if not dry_run:
        # F-D14（H-01）：状态文件版本锚点——**副本**写入（不污染调用方对象；load 侧剥离）
        _payload = dict(state)
        _payload["_meta"] = {"schema_version": "1.0", "written_by": "classify",
                             "written_at": __import__("time").strftime("%Y-%m-%d %H:%M:%S")}
        tmp = _STATE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(_payload, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, _STATE)
    return {"planned": planned, "ran": ran, "skipped": skipped,
            "ran_details": ran_details, "dry_run": dry_run}


def _step_script(step: str, theme: str) -> str:
    table = {
        "base": "build_base_from_attr.py",
        "cluster": "cluster_by_keywords.py",
        "match": "match_theme_docs.py",
        "detail": "build_detail_tables.py",
        "clause_graph": "build_clause_graph.py",
    }
    return os.path.join(_SCRIPTS, table[step])


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    import argparse  # noqa: PLC0415
    ap = argparse.ArgumentParser(description="主题底座强序重建编排器（R8）")
    ap.add_argument("--theme", default="", help="单主题 T0..T10（默认全部 T1–T10）")
    ap.add_argument("--all", action="store_true", help="全部主题（含 T0 明细）")
    ap.add_argument("--steps", default="", help="子步白名单逗号分隔（base,cluster,match,detail,clause_graph,upper）")
    ap.add_argument("--dry-run", action="store_true", help="仅列计划，不执行")
    a = ap.parse_args()
    themes = None
    if a.all:
        themes = sorted(THEME_MAP, key=lambda x: (len(x), x))
    elif a.theme:
        themes = [a.theme]
    steps = set(s.strip() for s in a.steps.split(",") if s.strip()) or None
    res = run(themes=themes, only_steps=steps, dry_run=a.dry_run)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    if res.get("error"):
        raise SystemExit(1)
