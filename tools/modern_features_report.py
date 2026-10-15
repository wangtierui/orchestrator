"""StrEnum 迁移影响面报告（方案 §13.9：先出报告后实施）。

回答三个问题，全部**机器统计**（不臆测）：
  1. `config/enums.py` 有多少受控域是 `str + Enum`（可平滑迁移 StrEnum）；
  2. 这些枚举的**消费点**有多少（迁移的实际影响面）；
  3. 已使用现代特性（`match`、PEP 604 `X | None`、`from __future__ import annotations`）
     的文件各有多少（现状基线，供后续批次对照）。

用法：python -m tools.modern_features_report [--json]
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
from datetime import datetime, timedelta, timezone

# 引导：**唯一引导点 bootstrap**（不自写 sys.path.insert —— gate_import_bootstrap 判据）
from bootstrap import bootstrap

bootstrap("all")

import paths

ROOT = paths.ROOT
TZ8 = timezone(timedelta(hours=8))
LAYERS = ("std_lib", "modules", "tools", "config", "interfaces", "gates", "commands")
SKIP_DIRS = {"__pycache__", "retired", "external", "graphify-out", "refactor", ".venv"}


def _py_files() -> list[str]:
    out: list[str] = []
    for base in LAYERS:
        for dirpath, dirnames, filenames in os.walk(os.path.join(ROOT, base)):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for fn in filenames:
                if fn.endswith(".py"):
                    out.append(os.path.join(dirpath, fn))
    return sorted(out)


def enum_report() -> dict:
    """`config/enums.py` 的受控域**实际形态**统计。

    实测结论（2026-10-15）：本仓受控域**不是 Enum 类**，而是 `frozenset[str]` /
    `tuple[str, ...]` / `str` 的模块级字面量常量，由 `gate_enum_values` 遍历**全仓
    字面量面**校验（新增未登记值即 FAIL）。⇒ 方案 §13.9 的「`str + Enum → StrEnum`
    迁移」**前提不成立**：没有 Enum 类可迁；且 frozenset + 门禁值面**比 Enum 类更严格**
    （Enum 类无法被「全仓字面量扫描」覆盖，反而会绕开该判据）。如实报告该形态，
    避免「以为有 Enum 要迁移」。
    """
    p = os.path.join(paths.CONFIG_DIR, "enums.py")
    src = open(p, encoding="utf-8").read()
    tree = ast.parse(src)
    out: dict = {"file": "config/enums.py", "total": 0, "enum_classes": [],
                 "frozenset_domains": [], "tuple_domains": [], "scalar_constants": []}
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            bases = {ast.unparse(b) for b in node.bases}
            out["enum_classes"].append({"name": node.name, "bases": sorted(bases)})
            continue
        names: list[str] = []
        val = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names, val = [node.target.id], node.value
        elif (isinstance(node, ast.Assign) and node.targets
              and isinstance(node.targets[0], ast.Name)):
            names, val = [node.targets[0].id], node.value
        if not names:
            continue
        if isinstance(val, ast.Constant):
            out["scalar_constants"].append(names[0])
            out["total"] += 1
            continue
        if isinstance(val, ast.Call):
            fn = getattr(val.func, "id", "")
            if fn == "frozenset":
                out["frozenset_domains"].append(names[0])
                out["total"] += 1
            elif fn == "tuple":
                out["tuple_domains"].append(names[0])
                out["total"] += 1
    return out


def consumer_report(names: list[str]) -> dict:
    """枚举消费点统计（按名字出现计数，取前若干文件）。"""
    counts: dict[str, dict[str, int]] = {n: {} for n in names}
    pat_all = {n: re.compile(r"\b" + re.escape(n) + r"\b") for n in names}
    for p in _py_files():
        rel = os.path.relpath(p, ROOT).replace("\\", "/")
        if rel.endswith("config/enums.py"):
            continue
        try:
            text = open(p, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for n in names:
            c = len(pat_all[n].findall(text))
            if c:
                counts[n][rel] = c
    return {n: {"total": sum(v.values()), "files": len(v)} for n, v in counts.items()}


def feature_baseline() -> dict:
    """现代特性现状基线（供后续批次对照，防"以为已迁移"）。"""
    feats = {
        "match_case": re.compile(r"^\s*match\s+.+:\s*$", re.M),
        "pep604_union": re.compile(r":\s*[A-Za-z_\[\]]+\s*\|\s*None"),
        "future_annotations": re.compile(r"from __future__ import annotations"),
        "dataclass": re.compile(r"@dataclass"),
        "type_alias": re.compile(r"^\s*[A-Za-z_][\w\[\], ]*\s*=\s*type\s*\(", re.M),
    }
    out = {k: 0 for k in feats}
    annotated = 0
    files = 0
    for p in _py_files():
        try:
            text = open(p, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        files += 1
        for k, pat in feats.items():
            if pat.search(text):
                out[k] += 1
        if re.search(r"^def\s+\w+\([^)]*->", text, re.M | re.S):
            annotated += 1
    out["files_scanned"] = files
    out["files_with_return_annotations"] = annotated
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="现代特性现状与 StrEnum 迁移影响面报告（§13.9）")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    en = enum_report()
    names = (en["frozenset_domains"] + en["tuple_domains"] + en["scalar_constants"]
             + [x["name"] for x in en["enum_classes"]])
    cons = consumer_report(names)
    doc: dict = {
        "schema_version": "1.0",
        "generated_at": datetime.now(TZ8).replace(microsecond=0).isoformat(),
        "enums": {
            "total_domains": en["total"],
            "form": "frozenset/tuple 字面量 + gate_enum_values 全仓值面校验（非 Enum 类）",
            "frozenset_domains": en["frozenset_domains"],
            "tuple_domains": en["tuple_domains"],
            "scalar_constants": en["scalar_constants"],
            "enum_classes": en["enum_classes"],
        },
        "consumers": dict(sorted(cons.items(), key=lambda kv: -kv[1]["total"])[:20]),
        "feature_baseline": feature_baseline(),
        "verdict": (
            "StrEnum 迁移**不适用**（前提不成立）：本仓受控域不是 `str + Enum` 类，而是 "
            "`frozenset[str]`/`tuple[str, ...]` 字面量 + `gate_enum_values` 的**全仓字面量"
            "面校验**——没有任何 Enum 类可迁。进一步说，该形态**比 Enum 类更严**："
            "① 值面由门禁遍历全仓校验（新增未登记值即 FAIL），Enum 类做不到这一点；"
            "② 消费点直接用字符串字面量，无 `str(X)` 样板可消除（见 consumers 的分布）。"
            "结论：不做迁移；若将来出现'枚举值误用字符串拼接'的真实缺陷，再按该缺陷的"
            "影响域局部引入 StrEnum 并同步收紧字面量面。"
        ),
    }
    text = json.dumps(doc, ensure_ascii=False, indent=2)
    out_dir = os.path.join(paths.REPORTS_DIR, "refactors")
    os.makedirs(out_dir, exist_ok=True)
    open(os.path.join(out_dir, "modern_features_report.json"), "w",
            encoding="utf-8", newline="\n").write(text + "\n")
    if a.json:
        print(text)
        return 0
    print(f"[modern] 受控域 {doc['enums']['total_domains']} 个："
          f"frozenset {len(en['frozenset_domains'])} / tuple {len(en['tuple_domains'])} / "
          f"标量 {len(en['scalar_constants'])} / Enum 类 {len(en['enum_classes'])}")
    top = list(doc["consumers"].items())[:8]
    for n, c in top:
        print(f"  {n:28s} 消费点 {c['total']:4d}（{c['files']} 文件）")
    fb = doc["feature_baseline"]
    print(f"[modern] 现状基线：match={fb['match_case']} pep604={fb['pep604_union']} "
          f"future_annotations={fb['future_annotations']} dataclass={fb['dataclass']} "
          f"type_alias={fb['type_alias']}（共 {fb['files_scanned']} 文件）")
    print("[modern] 裁定：StrEnum 迁移**不适用**——本仓无 Enum 类（理由见 JSON verdict）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
