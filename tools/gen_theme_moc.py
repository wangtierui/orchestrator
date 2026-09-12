# -*- coding: utf-8 -*-
"""gen_theme_moc.py — 主题 MOC 生成器（知识层 §6.3 映射：concepts/ 主题聚合页）

从双底座发布件生成"每主题一页"的 MOC（Map of Content），可直接放入 Obsidian vault：
  - 页名：`T{n}_{主题名}.md`（顺序编号，便于目录浏览）
  - 正文：主题说明 + 外部文件清单 + 内部制度清单（`[[文件名]]` 双链 → 图谱聚合）
  - 数据源：published/external_records.jsonl（theme）+ internal_policies.jsonl（primary_theme）
  - 主题名：rfn.registry.THEME_MAP（若 registry 不可用则用 T{n}）

用法：
  python tools/gen_theme_moc.py --out "D:\\DeMon KB\\监管法规库\\主题索引"
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "modules"), os.path.join(_ROOT, "interfaces")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

EXT_PUB = os.path.join(_ROOT, "modules", "regulatory_scrapers", "published", "external_records.jsonl")
INT_PUB = os.path.join(_ROOT, "modules", "internal_policy_base", "published", "internal_policies.jsonl")


def _slug(s: str, n: int = 48) -> str:
    import re
    s = re.sub(r"[\\/:*?\"<>|\s]+", "", (s or "").strip())
    return s[:n] or "untitled"


def _theme_names() -> dict:
    try:
        from rfn import registry  # type: ignore
        return dict(getattr(registry, "THEME_MAP", {}) or {})
    except Exception:  # noqa: BLE001
        return {}


def _load(path: str):
    out = []
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="主题 MOC 生成（发布件 → concepts 页）")
    ap.add_argument("--out", required=True, help="输出目录（如 Obsidian vault 的主题索引子目录）")
    ap.add_argument("--limit", type=int, default=500, help="每主题最多列示条数")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    tnames = _theme_names()
    ext = _load(EXT_PUB)
    internal = _load(INT_PUB)

    # 2026-09-12：双体系归一——外部用全名（"T1销售行为与消费者保护"）、内部用代号（"T1"）
    # → 按代号前缀归并到同一 MOC；主题名取外部全名（更长者优先）。
    import re as _re

    def _canon(th: str) -> str:
        m = _re.match(r"^(T\d{1,2})", th or "")
        return m.group(1) if m else (th or "未分类")

    names: dict = {}
    by_theme = defaultdict(lambda: {"ext": [], "int": []})
    for r in ext:
        th = (r.get("theme") or "").strip() or "未分类"
        c = _canon(th)
        if len(th) > len(names.get(c, "")):
            names[c] = th
        by_theme[c]["ext"].append(r)
    for p in internal:
        th = (p.get("primary_theme") or "").strip() or "未分类"
        c = _canon(th)
        by_theme[c]["int"].append(p)

    # 清理旧页（防体系变更残留）
    for f in sorted(os.listdir(args.out)):
        if _re.match(r"^T\d", f) and f.endswith(".md"):
            os.remove(os.path.join(args.out, f))

    made = 0
    for th in sorted(by_theme.keys()):
        name = names.get(th) or tnames.get(th, th) or th
        page = os.path.join(args.out, f"{th}_{_slug(name, 28)}.md")
        ext_rows = sorted(by_theme[th]["ext"], key=lambda x: x.get("publish_date") or "")
        int_rows = sorted(by_theme[th]["int"], key=lambda x: x.get("docno") or "")
        lines = [
            "---", f'theme: "{th}"', f'theme_name: "{name}"',
            f"ext_count: {len(ext_rows)}", f"int_count: {len(int_rows)}", "---", "",
            f"# {th} {name} · 主题索引（MOC）", "",
            f"> 外部文件 {len(ext_rows)} 份 ｜ 内部制度 {len(int_rows)} 份 ｜ 由发布件自动生成",
            "> 双链点击可回链到全文页；Obsidian 图谱将按本页聚合该主题。", "",
        ]
        if ext_rows:
            lines += ["## 外部监管文件", ""]
            for r in ext_rows[:args.limit]:
                key = r.get("rfn") or r.get("record_id", "")
                fname = f"{_slug(key, 40)}__{_slug(r.get('title', ''))}"
                lines.append("- [[%s|%s]]（%s｜%s）" % (
                    fname, (r.get("title") or "")[:48],
                    r.get("document_number") or "无文号",
                    r.get("publish_date") or "—"))
            if len(ext_rows) > args.limit:
                lines.append(f"- …（其余 {len(ext_rows) - args.limit} 份见底库）")
            lines.append("")
        if int_rows:
            lines += ["## 内部制度", ""]
            for p in int_rows[:args.limit]:
                fname = f"{_slug(p.get('ipn', ''), 40)}__{_slug(p.get('title', ''))}"
                lines.append("- [[%s|%s]]（%s）" % (
                    fname, (p.get("title") or "")[:48], p.get("docno") or "无文号"))
            if len(int_rows) > args.limit:
                lines.append(f"- …（其余 {len(int_rows) - args.limit} 份见底库）")
            lines.append("")
        with open(page + ".tmp", "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))
        os.replace(page + ".tmp", page)
        made += 1
    print(f"[theme-moc] 生成 {made} 页 → {args.out}")
    for th in sorted(by_theme.keys()):
        nm = names.get(th) or tnames.get(th, th) or th
        print(f"  {th}_{nm}: ext {len(by_theme[th]['ext'])} / int {len(by_theme[th]['int'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
