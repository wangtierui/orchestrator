# -*- coding: utf-8 -*-
"""commands.source — orchestrator 命令：source（自 cli.py 迁移，2026-09-13 审查 P3）。"""
from __future__ import annotations

import sys

import paths


def run(argv):
    """source list | add | diff（R15：yaml 唯一事实源；diff=快照变更监听 F-O02）。"""
    if not argv:
        print("用法: orchestrator source {list|add|diff}")
        return 1
    action = argv[0]
    if action == "diff":
        # F-O02（2026-09-12）：快照变更监听——对比"监听基线"（data/watch_baseline.jsonl，
        # 编排每次运行经 --record 追加）与当前 clean_index 快照的记录数/内容 sha 差异。
        # 磁盘不保留历史快照（clean 覆盖式），故基线由本命令留痕（同日改写可见）。
        import datetime as _dt  # noqa: PLC0415
        import json as _json  # noqa: PLC0415
        import os as _os  # noqa: PLC0415
        sys.path.insert(0, _os.path.join(paths.ROOT, "modules", "regulatory_scrapers"))
        from clean_index import get_clean_index  # noqa: PLC0415
        idx = get_clean_index()
        base_p = _os.path.join(paths.MODULES_DIR, "regulatory_scrapers", "data",
                               "watch_baseline.jsonl")
        last: dict = {}
        if _os.path.exists(base_p):
            with open(base_p, encoding="utf-8") as fh:
                for line in fh:
                    if not line.strip():
                        continue
                    try:
                        rec = _json.loads(line)
                    except ValueError:
                        continue
                    last[rec.get("source", "")] = rec
        now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        rows, changed = [], 0
        print("[source:diff] 变更监听（基线 → 当前）")
        for sid in sorted(idx.source_ids()):
            lt = idx.latest(sid) or {}
            sn = idx.snapshot(sid, lt.get("date") or "") or {}
            f = (sn.get("files") or {}).get("jsonl") or {}
            cur = {"source": sid, "date": lt.get("date"), "records": f.get("record_count"),
                   "sha": (f.get("sha256") or "")[:16], "at": now}
            rows.append(cur)
            p = last.get(sid)
            if not p:
                changed += 1
                print(f"  {sid:5s} | （无基线）→ {cur['date']} | records {cur['records']}"
                      f" | sha {cur['sha']}")
                continue
            delta = (cur["records"] or 0) - (p.get("records") or 0)
            sha_upd = cur["sha"] != (p.get("sha") or "")
            if sha_upd or delta:
                changed += 1
            print(f"  {sid:5s} | {p.get('date')} → {cur['date']} | records {p.get('records')}"
                  f" → {cur['records']}（Δ{delta:+d}）| sha {p.get('sha')} → {cur['sha']}"
                  f" | {'★变化' if (sha_upd or delta) else '无变化'}")
        print(f"[source:diff] 共 {len(rows)} 源；有变更 {changed} 源")
        if "--record" in argv:
            with open(base_p, "a", encoding="utf-8") as fh:
                for cur in rows:
                    fh.write(_json.dumps(cur, ensure_ascii=False) + "\n")
            print(f"[source:diff] 已记录基线（{base_p}）")
        return 0
    if action == "list":
        try:
            from config.loader import (  # noqa: PLC0415
                active_source_ids,
                collector_module,
                collector_path,
                load_sources,
            )
        except Exception as e:  # pragma: no cover
            print(f"[source] config.loader 不可用（PyYAML 未装？）: {e}")
            return 2
        srcs = load_sources(refresh=True)
        from config.enums import SOURCE_SET  # noqa: PLC0415
        for sid, cfg in srcs.items():
            enabled = cfg.get("enabled", True)
            flag = "ON " if enabled else "OFF"
            line = f"  [{flag}] {sid:12s} {cfg.get('note', '')}"
            if enabled and "." not in sid and sid != "internal":
                mod = collector_module(sid)
                ok = "✓" if collector_path(sid) else "✗缺模块"
                line += f"  | collector={mod} {ok}"
            print(line)
        print(f"  [..] enums.SOURCE_SET={sorted(SOURCE_SET)} | yaml active={active_source_ids()}")
        return 0
    if action == "add":
        import argparse  # noqa: PLC0415
        ap = argparse.ArgumentParser(description="source add checklist（R15 新增源步骤）")
        ap.add_argument("--id", required=True, help="新源标识（如 flk）")
        a = ap.parse_args(argv[1:])
        print(f"[source add] 登记新源 {a.id!r} 的清单（sources.yaml 唯一事实源）：")
        print("  1. sources.yaml external_sources 追加条目：")
        print(f"       - id: {a.id}")
        print("         enabled: false          # 先停用登记，待 collector/清洗验证后置 true")
        print("         collector: collectors.<{id}_collector|{id}_ingest>   # 与 collectors/ 拍平命名对齐")
        print(f"         clean_project: {a.id}")
        print("         note: …")
        print("         disabled_reasons: [待采集实现验证]")
        print("  2. config/enums.py SOURCE_SET 加值（受控变更；assert_enum_bindings 断言条数随动）")
        print("  3. 提供 collectors 模块并跑：python -m py_compile + nfra_validate_cache 式只读冒烟")
        print("  4. python cli.py gates（gate_sources_config 校验 collector 模块/clean_project/enums 一致）")
        return 0
    print(f"未知 source 子命令: {action}（可用: list, add, diff）")
    return 1
