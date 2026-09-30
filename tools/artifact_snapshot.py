# -*- coding: utf-8 -*-
"""tools.artifact_snapshot — **启用前后产物快照与差异对比**（N-178）

为什么需要它
------------
"启用增强层后产物有何变化"必须**可复算**地回答，而不是凭印象。本工具：
  · `snapshot`：把关键产物目录的 `(相对路径, 字节数, mtime, sha256 前 16)` 写成快照 JSON；
  · `diff`：两份快照对比 → **新增 / 删除 / 内容变化 / 仅 mtime 变化** 四类，输出 Markdown。

产物集合（**与链路产出对齐**，避免只比"想看的那几个文件"）：
  cleaned（csv/jsonl）、分类明细表、关系事实源、主题/总览报告、`docs/reports` 分析交付 + manifest、
  发布清单、语义分析视图（`reports/semantic/`）。

用法
----
    python -m tools.artifact_snapshot snapshot --out reports/_snap/before.json
    python -m tools.artifact_snapshot diff --before <..> --after <..> --out reports/产物差异_<date>.md
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import io
import json
import os
import sys

from bootstrap import bootstrap

bootstrap("all")

import paths
from config.exitcodes import ExitCode

#: 目标目录（相对仓根）。**目录不存在时记录为缺失**（不静默跳过 —— 缺失本身是差异）。
TARGETS = (
    "modules/regulatory_scrapers/data/cleaned",   # 清洗产物（csv/jsonl）
    "modules/regulatory_scrapers/data/clauses",   # 条文/条款产物
    "modules/regulatory_scrapers/data/reports",   # 源侧报告
    "modules/regulatory_classifier/data",         # 分类产物族（T*.csv 明细表等）
    "data/corpus",                                # 语料
    "data/run_state",                             # 运行台账（步骤结果/水位）
    "reports/semantic",                           # P1 语义分析视图（本轮新增）
    "reports",
    "docs/reports",                               # 分析交付库（17 项 + manifest）
)
#: 单文件哈希上限（超过者只记 size/mtime —— 避免为 GB 级产物反复读盘）
HASH_MAX = 64 * 1024 * 1024
SKIP_NAME = ("__pycache__", ".gitkeep")
#: 跳过**本次重构自身产出**的文档（否则"产物差异"会混入人工报告噪声）
SKIP_PREFIX = (
    "reports/_tmp/",
    "reports/_snap/",
    "reports/重构执行报告_",
    "reports/重复实现台账_",
    "reports/全链运行与模型权重可得性_",
)


def _sha16(p: str) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def snapshot() -> dict:
    snap: dict = {
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "files": {},
        "missing_dirs": [],
    }
    for rel in TARGETS:
        d = os.path.join(paths.ROOT, rel)
        if not os.path.isdir(d):
            snap["missing_dirs"].append(rel)
            continue
        for dp, dn, fn in os.walk(d):
            dn[:] = [x for x in dn if x not in SKIP_NAME]
            for f in sorted(fn):
                if f in SKIP_NAME:
                    continue
                fp = os.path.join(dp, f)
                r = os.path.relpath(fp, paths.ROOT).replace(os.sep, "/")
                if any(r.startswith(x) for x in SKIP_PREFIX):
                    continue
                try:
                    st = os.stat(fp)
                except OSError:
                    continue
                # 显式注解：dict 同时承载 int 与 str，字面量推断会收窄为 float → mypy 拒绝后续 str 赋值
                item: dict = {"size": st.st_size, "mtime": round(st.st_mtime, 3)}
                if st.st_size <= HASH_MAX:
                    try:
                        item["sha16"] = _sha16(fp)
                    except OSError:
                        item["sha16"] = ""
                snap["files"][r] = item
    return snap


def diff(before: dict, after: dict) -> dict:
    b, a = before.get("files") or {}, after.get("files") or {}
    added = sorted(set(a) - set(b))
    removed = sorted(set(b) - set(a))
    changed, touched = [], []
    for k in sorted(set(a) & set(b)):
        hi, ho = a[k].get("sha16"), b[k].get("sha16")
        if hi and ho and hi != ho:
            changed.append(k)
        elif a[k]["size"] != b[k]["size"]:
            changed.append(k)
        elif a[k]["mtime"] != b[k]["mtime"]:
            touched.append(k)
    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "mtime_only": touched,
        "before_at": before.get("generated_at"),
        "after_at": after.get("generated_at"),
        "before_n": len(b),
        "after_n": len(a),
    }


def _md(d: dict) -> str:
    L = [
        f"# 产物差异对比（启用前后）· {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        f"> 快照：**启用前** {d['before_at']}（{d['before_n']} 文件） → "
        f"**启用后** {d['after_at']}（{d['after_n']} 文件）",
        "> 口径：`sha256 前 16` 不同或大小不同 ⇒ **内容变化**；仅 mtime 变 ⇒ **仅时间戳变化**（不算内容变化）。",
        "",
        "| 类别 | 数量 |",
        "|---|---|",
        f"| **新增** | {len(d['added'])} |",
        f"| **删除** | {len(d['removed'])} |",
        f"| **内容变化** | {len(d['changed'])} |",
        f"| 仅 mtime 变化 | {len(d['mtime_only'])} |",
        "",
    ]
    for key, title in (("added", "新增产物"), ("changed", "内容变化"), ("removed", "删除产物")):
        if d[key]:
            L += [f"## {title}（{len(d[key])}）", ""]
            L += [f"- `{x}`" for x in d[key][:200]]
            if len(d[key]) > 200:
                L.append(f"- …（其余 {len(d[key]) - 200} 项见 JSON）")
            L.append("")
    if not (d["added"] or d["changed"] or d["removed"]):
        L += ["**结论：产物集合与内容均无变化**（语义层为**新增分析视图**，不改写任何事实源）。", ""]
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="产物快照与差异对比")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s1 = sub.add_parser("snapshot")
    s1.add_argument("--out", required=True)
    s2 = sub.add_parser("diff")
    s2.add_argument("--before", required=True)
    s2.add_argument("--after", required=True)
    s2.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "snapshot":
        snap = snapshot()
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        with open(a.out, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(snap, fh, ensure_ascii=False, indent=1)
        print(f"[artifact_snapshot] {len(snap['files'])} 文件 → {a.out}"
              f"（缺失目录 {len(snap['missing_dirs'])}：{snap['missing_dirs']}）")
        return int(ExitCode.OK)
    before = json.load(open(a.before, encoding="utf-8"))
    after = json.load(open(a.after, encoding="utf-8"))
    d = diff(before, after)
    md = _md(d)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(md)
    jp = a.out.rsplit(".", 1)[0] + ".json"
    with open(jp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=1)
    print(f"[artifact_snapshot] 新增 {len(d['added'])} / 变化 {len(d['changed'])} / "
          f"删除 {len(d['removed'])} / 仅 mtime {len(d['mtime_only'])} → {a.out}")
    return int(ExitCode.OK)


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    raise SystemExit(main())
