# -*- coding: utf-8 -*-
"""tools.migrate_local_path — 把 raw 记录里**带 `..` 的脏 local_path** 迁到仓根基准。

为什么需要
----------
采集器里 `local_path` 由 `os.path.relpath(文件, REPO_ROOT)` 生成。历史上
`REPO_ROOT` 只从采集器目录上溯**一级**（得到 `.../collectors`，而注释写的是
`regulatory_scrapers`），于是产出 `../data/docs/…` 这种**带 `..` 的路径**：

  · 它只在「cwd = collectors」时才解析正确；
  · 下游（`build_fts` 附件表、确认台预览、契约映射）若按仓根拼接 ⇒ **找不到文件**。

实测（2026-10-20）：`pbc_laws.jsonl` 13 条 `local_path` 里 **10 条**是脏的；
`mof_laws.jsonl` 246 条**全干净**（历史就是仓根基准）⇒ 迁移目标是「统一到仓根」，
即**回到 mof 已经在用的既有口径**，不是发明新口径。

安全设计（**只改一个字段，且逐条证明文件可达**）
----------------------------------------------
1. 只处理以 `..` 开头的值，且**只替换已知的脏前缀**（不猜任意 `..`）；
2. 每条都**先验证**「新路径拼仓根后文件真实存在」，验证不过则**原样保留并报告**
   （宁可少改，不可改错）；
3. 备份原文件（`.bak` 时间戳），落盘用原子替换；
4. 默认 dry-run，须 `--execute` 才写。

用法
----
    python -m tools.migrate_local_path                      # 扫全部 raw（dry-run）
    python -m tools.migrate_local_path --execute            # 执行
    python -m tools.migrate_local_path --file pbc_laws.jsonl --execute
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
from datetime import datetime

from bootstrap import bootstrap

bootstrap("all", include_tools=True)

import paths
from config.exitcodes import ExitCode

RAW_DIR = os.path.join(paths.ROOT, "modules", "regulatory_scrapers", "data", "raw")

#: 「脏前缀 → 正确前缀」映射。**只做已知前缀替换**，不对任意 `..` 猜。
#: 依据：脏值形如 `../data/docs/pbc_.../attachments/x.docx`，其真实位置是
#: `<repo>/modules/regulatory_scrapers/data/docs/pbc_.../attachments/x.docx`。
_DIRTY_PREFIX = "../data/"
_CLEAN_PREFIX = "modules/regulatory_scrapers/data/"

#: **第二种形态**（2026-10-20 第一百零四批补充）：`data/docs/...`（**SCRAPERS 基准**）
#: → `modules/regulatory_scrapers/data/docs/...`（**仓根基准**）。
#:
#: 为什么需要：同一源内出现两种基准是**真实缺陷**（实测 mof 有 243 条仓根基准 +
#: 3 条 SCRAPERS 基准，后者是旧代码/别工具写的）。源内不一致比跨源不一致更糟——
#: 读者无法用单一规则解析。
#:
#: **⚠️ 只对「该源多数记录已是仓根基准」的场景使用**：若整源都是 SCRAPERS 基准
#: （如 gov 1189 条 / supp 7 条），那是**既成口径**，迁移会制造新的不一致
#: 且无功能收益（`local_path` 目前不被任何代码拼成路径打开，仅作元数据）。
#: 故本形态需显式 `--allow-scrapers-to-repo` 才启用，避免误伤。
_SCRAPERS_TO_REPO = ("data/docs/", "modules/regulatory_scrapers/data/docs/")


def _fix_one(lp: str, *, allow_scrapers: bool = False) -> tuple[str, bool]:
    """返回 `(新值, 是否改动)`。非脏值原样返回。"""
    s = str(lp or "")
    if s.startswith(_DIRTY_PREFIX):
        return _CLEAN_PREFIX + s[len(_DIRTY_PREFIX):], True
    if allow_scrapers and s.startswith(_SCRAPERS_TO_REPO[0]):
        return _SCRAPERS_TO_REPO[1] + s[len(_SCRAPERS_TO_REPO[0]):], True
    # 其余（含未知形态的 `..` 与已是目标基准的值）：不动，交给报告
    return s, False


def _exists_at_repo(lp: str) -> bool:
    return os.path.exists(os.path.join(paths.ROOT, lp.replace("/", os.sep)))


def migrate_file(name: str, *, execute: bool,
                 allow_scrapers: bool = False) -> dict:
    """处理一个 raw 文件；返回统计。"""
    path = os.path.join(RAW_DIR, name)
    if not os.path.exists(path):
        return {"file": name, "skipped": "not-found"}
    stat = {"file": name, "total": 0, "dirty": 0, "fixed": 0,
            "unverifiable": [], "unknown_shape": [], "untouched_diff": 0}
    out_lines: list[str] = []
    changed = False
    with open(path, encoding="utf-8", errors="replace") as fh:
        for ln in fh:
            if not ln.strip():
                continue
            try:
                d = json.loads(ln)
            except ValueError:
                out_lines.append(ln.rstrip("\n"))
                continue
            stat["total"] += 1
            before = json.dumps(d, ensure_ascii=False, sort_keys=True)

            # 记录级 local_path
            lp = d.get("local_path")
            if lp:
                new, did = _fix_one(lp, allow_scrapers=allow_scrapers)
                if did:
                    stat["dirty"] += 1
                    if _exists_at_repo(new):
                        d["local_path"] = new
                        stat["fixed"] += 1
                    else:
                        stat["unverifiable"].append(str(lp)[:90])
            # 附件级 local_path
            for a in (d.get("attachments") or []):
                if not isinstance(a, dict):
                    continue
                alp = a.get("local_path")
                if not alp:
                    continue
                new, did = _fix_one(alp, allow_scrapers=allow_scrapers)
                if not did:
                    continue
                stat["dirty"] += 1
                if _exists_at_repo(new):
                    a["local_path"] = new
                    stat["fixed"] += 1
                else:
                    stat["unverifiable"].append(str(alp)[:90])

            if json.dumps(d, ensure_ascii=False, sort_keys=True) != before:
                changed = True
            out_lines.append(json.dumps(d, ensure_ascii=False))
    if changed and execute:
        bak = f"{path}.bak_{datetime.now():%Y%m%d_%H%M%S}"
        shutil.copy2(path, bak)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(out_lines) + "\n")
        os.replace(tmp, path)
        stat["backup"] = os.path.basename(bak)
    return stat


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="迁移 raw 里带 .. 的脏 local_path")
    ap.add_argument("--file", default="", help="只处理该文件名（默认全部 *.jsonl）")
    ap.add_argument("--execute", action="store_true", help="真正写入（默认 dry-run）")
    ap.add_argument(
        "--allow-scrapers-to-repo", action="store_true",
        help="同时把 `data/docs/...`（SCRAPERS 基准）归一为 "
             "`modules/regulatory_scrapers/data/docs/...`。"
             "**仅当该源多数记录已是仓根基准时使用**（源内不一致才需归一）；"
             "整源都是 SCRAPERS 基准的（如 gov/supp）属既成口径，不要用本开关")
    a = ap.parse_args(argv)

    if a.file:
        names = [a.file]
    else:
        names = sorted(f for f in os.listdir(RAW_DIR) if f.endswith(".jsonl")
                       and not f.endswith(".bak"))
    print(f"[migrate] {'执行' if a.execute else 'dry-run'}；仓根={paths.ROOT}")
    total_fix = total_dirty = 0
    for n in names:
        s = migrate_file(n, execute=a.execute,
                         allow_scrapers=a.allow_scrapers_to_repo)
        if s.get("skipped"):
            continue
        total_fix += s["fixed"]
        total_dirty += s["dirty"]
        if s["dirty"] or s["unverifiable"]:
            print(f"  {n}: 脏 {s['dirty']} / 可修 {s['fixed']} / "
                  f"不可验证 {len(s['unverifiable'])}")
            for u in s["unverifiable"][:3]:
                print(f"      ⚠ 不可验证（原样保留）: {u}")
            if s.get("backup"):
                print(f"      备份: {s['backup']}")
    print(f"[migrate] 合计：脏 {total_dirty} 条，可修 {total_fix} 条"
          + ("" if a.execute else "（dry-run，未写入）"))
    return ExitCode.OK


if __name__ == "__main__":
    raise SystemExit(main())
