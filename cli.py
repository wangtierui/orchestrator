# -*- coding: utf-8 -*-
"""
cli.py — orchestrator 统一入口（v1 §5 / 专项⑤代码固化）

用法（P0 阶段可用）：
  python cli.py --help
  python cli.py gates                       # 运行交付门禁
  python cli.py source list                 # 列出源（config/sources.yaml 派生）
  python cli.py ping                        # 骨架自检

P1 起扩展子命令：collect/clean/index/retrieval/classify/report/internal/draft/timeliness/pipeline。
设计纪律：
  - 每阶段对应独立实现模块（后续在 commands/ 或 modules 内），本文件只做路由 + 退出码聚合；
  - 幂等/签名断点在阶段实现内；禁止在 cli.py 写业务逻辑。
"""
from __future__ import annotations

import argparse
import sys

import paths


def _cmd_gates(argv):
    from gates import GatesRunner
    ok, results = GatesRunner().run()
    for r in results:
        flag = "[OK] " if r["passed"] else "[FAIL]"
        print(f"  {flag}  {r['desc']}  {r.get('detail')}")
    print("====================")
    print("PASS: 全部门禁通过" if ok else "FAIL: 存在未通过门禁")
    return 0 if ok else 1


def _cmd_source(argv):
    """source list | source add --id <new_id>（R15：yaml 唯一事实源 + collector 路由消费）。"""
    if not argv:
        print("用法: orchestrator source {list|add}")
        return 1
    action = argv[0]
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
    print(f"未知 source 子命令: {action}（可用: list, add）")
    return 1


def _cmd_ping(argv):
    print(f"REG_ORCH_ROOT = {paths.ROOT}")
    print("P0 骨架 OK：paths / config / interfaces / gates / std_lib 已就位")
    return 0


def _cmd_internal(argv):
    """internal index|align [--source-dir ...] — 内部制度摄取/对齐（P6）。"""
    import os
    import sys
    sys.path.insert(0, os.path.join(paths.ROOT, "modules"))
    if not argv:
        print("用法: orchestrator internal {index|align} [--source-dir DIR] [--dry-run]")
        return 1
    sub = argv[0]
    if sub == "index":
        # 透传剩余参数（--source-dir/--enable-ocr/--dry-run）
        return _index_main_internal(argv[1:])
    if sub == "align":
        import json

        from internal_policy_base.align import align_all
        s = align_all()
        print(json.dumps(s, ensure_ascii=False, indent=2))
        return 0
    if sub == "merged":
        import json

        from internal_policy_base.merged import build_merged_view
        s = build_merged_view()
        print(json.dumps(s, ensure_ascii=False, indent=2))
        return 0
    if sub == "backfill":
        # R10/B4（2026-09-08）：backfill_clauses 收敛 CLI（原仅 python -c 手工调用）
        import json

        from internal_policy_base.extract import backfill_clauses
        s = backfill_clauses()
        print(json.dumps(s, ensure_ascii=False, indent=2))
        return 0
    print(f"未知 internal 子命令: {sub}（可用: index, align, merged, backfill）")
    return 1


def _index_main_internal(argv):
    """复刻 indexer.main 的 argparse（cli 内联透传）。"""
    import argparse
    ap = argparse.ArgumentParser(description="internal index")
    ap.add_argument("--source-dir", default="", help="制度源目录")
    ap.add_argument("--enable-ocr", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    src = args.source_dir or __import__("os").environ.get("INTERNAL_POLICY_ROOT", "")
    if not src:
        print("需提供 --source-dir 或设置 INTERNAL_POLICY_ROOT 环境变量")
        return 1
    import json

    from internal_policy_base.indexer import ingest
    s = ingest(src, enable_ocr=args.enable_ocr, dry_run=args.dry_run)
    print(json.dumps(s, ensure_ascii=False, indent=2))
    return 0


def _cmd_classify(argv):
    """classify --theme T3|--all [--steps base,cluster,detail,...] [--dry-run]
    主题底座强序重建（R8：base→cluster→match→detail→upper→clause_graph，hash 断点幂等）。"""
    import os
    import sys
    sys.path.insert(0, os.path.join(paths.ROOT, "modules", "regulatory_classifier", "scripts"))
    sys.path.insert(0, os.path.join(paths.ROOT, "modules", "regulatory_classifier"))
    import argparse  # noqa: PLC0415

    from modules.regulatory_classifier.scripts import classify as _cl  # noqa: PLC0415
    ap = argparse.ArgumentParser(description="主题底座强序重建（R8）")
    ap.add_argument("--theme", default="", help="单主题 T0..T10（默认 T1–T10）")
    ap.add_argument("--all", action="store_true", help="全部主题（含 T0 明细）")
    ap.add_argument("--steps", default="", help="子步白名单 base,cluster,match,detail,clause_graph,upper")
    ap.add_argument("--dry-run", action="store_true", help="仅列计划")
    a = ap.parse_args(argv)
    themes = None
    if a.all:
        themes = sorted(_cl.THEME_MAP, key=lambda x: (len(x), x))
    elif a.theme:
        themes = [a.theme]
    steps = set(s.strip() for s in a.steps.split(",") if s.strip()) or None
    res = _cl.run(themes=themes, only_steps=steps, dry_run=a.dry_run)
    print(__import__("json").dumps(res, ensure_ascii=False, indent=2))
    return 1 if res.get("error") else 0


def _cmd_timeliness(argv):
    """timeliness verify|sync|summary
    - verify [--source ...]：效力缺失核验（R13 三态 exit：0/2/3）
    - sync [--dry-run]：核验变更台账 → 归属表时效同步（F-C03 显式入口）
    - summary：读最新 verify_summary_*.json（F-K07 告警接点消费）"""
    import os  # noqa: PLC0415
    import subprocess  # noqa: PLC0415
    if not argv:
        print("用法: orchestrator timeliness verify|sync|summary ...")
        return 1
    _rev = os.path.join(paths.ROOT, "modules", "regulatory_scrapers", "timeliness_review")
    if argv[0] == "sync":
        # F-C03：state/台账 → 归属表"时效状态"列同步（sync_to_classifier 唯一实现）唯一 CLI 入口。
        import csv as _csv  # noqa: PLC0415
        import glob as _glob  # noqa: PLC0415
        if _rev not in sys.path:
            sys.path.insert(0, _rev)
        import verification_state as _vstate  # noqa: PLC0415
        ledgers = sorted(_glob.glob(os.path.join(_rev, "时效核验_*变更台账_*.csv")))
        if not ledgers:
            print("[timeliness] 无变更台账（时效核验尚未产生变更）；先运行 verify")
            return 1
        changed = []
        for p in ledgers[-1:]:
            with open(p, encoding="utf-8-sig", newline="") as fh:
                changed.extend(list(_csv.DictReader(fh)))
            print(f"[timeliness] 台账: {os.path.basename(p)}（{len(changed)} 行变更）")
        dry = "--dry-run" in argv
        m, um, cpath = _vstate.sync_to_classifier(changed, dry_run=dry)
        print(f"[timeliness] 归属表时效同步：匹配 {m} / 未匹配 {um}（{cpath}）"
              + ("［dry-run 未写盘］" if dry else ""))
        return 0
    if argv[0] == "summary":
        import glob as _glob  # noqa: PLC0415
        import json as _json  # noqa: PLC0415
        files = sorted(_glob.glob(os.path.join(_rev, "verify_summary_*.json")))
        if not files:
            print("[timeliness] 无 verify_summary_*.json（先运行 verify）")
            return 1
        data = _json.load(open(files[-1], encoding="utf-8"))
        print(f"[timeliness] {os.path.basename(files[-1])}")
        print(_json.dumps(data, ensure_ascii=False, indent=1)[:2000])
        return 0
    if argv[0] != "verify":
        print(f"未知 timeliness 子命令: {argv[0]}（可用: verify | sync | summary）")
        return 1
    script = os.path.join(paths.ROOT, "modules", "regulatory_scrapers",
                          "timeliness_review", "verify_missing.py")
    if not os.path.exists(script):
        print(f"[timeliness] 脚本缺失: {script}")
        return 3
    r = subprocess.run([sys.executable, "-X", "utf8", script] + argv[1:])
    return r.returncode


def _cmd_draft(argv):
    """draft [--ipn IPN-xxx] —— 条款级对照素材端到端编排（P8 收口）。
    输入 merged_view + internal processed（R21 clauses）；输出 drafter/data/draft_clause/。
    供六件套之「条款对照表/立法依据」起草打底；退出码 0=成功 1=数据缺（merged 未生成）。"""
    import os  # noqa: PLC0415
    import subprocess  # noqa: PLC0415
    script = os.path.join(paths.ROOT, "modules", "internal_policy_drafter",
                          "scripts", "build_draft_clause_view.py")
    if not os.path.exists(script):
        print(f"[draft] 脚本缺失: {script}")
        return 1
    return subprocess.run([sys.executable, "-X", "utf8", script] + argv).returncode


def _cmd_rfn(argv):
    """rfn register/lookup —— 监管文件登记入口（registry 唯一写口，F-C01）。

    F-C01：registry.register_doc 此前无 CLI 入口，新文件只能靠脚本内部调用（且 supp 侧
    organ= TypeError 静默失败）→ 登记链事实上断裂。本子命令补齐唯一登记入口。

    用法：
      orchestrator rfn register --theme T1 --title "..." [--docno 文号] [--pub-date YYYY-MM-DD] [--source supp]
      orchestrator rfn lookup --docno 文号 | --title 标题
    退出码：0=成功/命中；1=失败/未命中/参数错误。
    """
    import argparse as _ap  # noqa: PLC0415
    import json as _json  # noqa: PLC0415
    import os as _os  # noqa: PLC0415

    cls_root = _os.path.join(paths.ROOT, "modules", "regulatory_classifier")
    if cls_root not in sys.path:
        sys.path.insert(0, cls_root)
    ap = _ap.ArgumentParser(prog="orchestrator rfn")
    sub = ap.add_subparsers(dest="action", required=True)
    pr = sub.add_parser("register", help="登记监管文件（幂等：命中则复用现有 RFN）")
    pr.add_argument("--theme", required=True, help="主题（如 T1；THEME_MAP 归一）")
    pr.add_argument("--title", required=True, help="文件名称（全称）")
    pr.add_argument("--docno", default="", help="发文字号")
    pr.add_argument("--pub-date", default="", dest="pub_date", help="发布日期 YYYY-MM-DD")
    pr.add_argument("--source", default="supp", choices=["gov", "mof", "nfra", "pbc", "supp"])
    pr.add_argument("--fingerprint", default="", help="内容指纹（防重复摄入，可空）")
    pl = sub.add_parser("lookup", help="按去重键查已有登记")
    pl.add_argument("--docno", default="")
    pl.add_argument("--title", default="")
    pl.add_argument("--source", default="")
    pl.add_argument("--pub-date", default="", dest="pub_date")
    args = ap.parse_args(argv)
    try:
        from rfn import registry  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        print(f"[rfn] rfn.registry 不可用: {e!r}")
        return 1
    if args.action == "register":
        try:
            res = registry.register_doc(theme=args.theme, title=args.title, docno=args.docno,
                                        pub_date=args.pub_date, source=args.source,
                                        fingerprint=args.fingerprint)
        except Exception as e:  # noqa: BLE001
            print(f"[rfn] 登记失败: {e!r}")
            return 1
        print(f"[rfn] {res['action']} {res['rfn']} <- {args.docno or '(无文号)'} {args.title[:40]}")
        sync = res.get("sync") or {}
        if sync:
            print("[rfn] sync:", _json.dumps(sync, ensure_ascii=False)[:200])
        return 0
    rec = registry.lookup(docno=args.docno, title=args.title, source=args.source, pub_date=args.pub_date)
    if rec:
        print("[rfn] 命中:", _json.dumps(rec, ensure_ascii=False))
        return 0
    # 部分键回退：全键未命中时按给到的键模糊扫描归属表（查询体验；不做登记）
    if args.docno or args.title:
        import csv as _csv  # noqa: PLC0415
        attr = _os.path.join(cls_root, "data", "人身保险公司-文件归属表.csv")
        if _os.path.exists(attr):
            hits = []
            with open(attr, encoding="utf-8-sig", newline="") as fh:
                for row in _csv.DictReader(fh):
                    if args.docno and args.docno.strip() not in (row.get("发文字号") or ""):
                        continue
                    if args.title and args.title.strip() not in (row.get("文件名称") or ""):
                        continue
                    hits.append(row)
            if hits:
                print(f"[rfn] 模糊命中 {len(hits)} 条（全键未精确命中，以下为部分键扫描）:")
                for h in hits[:5]:
                    print("   ", _json.dumps(h, ensure_ascii=False))
                return 0
    print("[rfn] 未命中（无相同去重键登记）")
    return 1


def _cmd_base(argv):
    """base publish|query|search —— 双底座发布件构建与统一查询（Base Contract v1，F-K03/O09）。

    - publish [--base external|internal|all]：构建发布件（JSONL）+ SQLite/FTS5 索引
    - query   [--rfn/--docno/--theme/--source] [--internal] [--json]：精确查询
    - search  <text> [--kind records|clauses|policies] [--internal] [--limit N] [--json]
    """
    import argparse as _ap  # noqa: PLC0415
    import json as _json  # noqa: PLC0415
    import os as _os  # noqa: PLC0415
    import sys as _sys  # noqa: PLC0415

    mods = _os.path.join(paths.ROOT, "modules")
    for _p in (_os.path.join(paths.ROOT, "interfaces"), mods, _os.path.join(paths.ROOT, "std_lib")):
        if _p not in _sys.path:
            _sys.path.insert(0, _p)
    ap = _ap.ArgumentParser(prog="orchestrator base")
    sub = ap.add_subparsers(dest="action", required=True)
    pp = sub.add_parser("publish", help="构建发布件 + 索引")
    pp.add_argument("--base", default="all", choices=["external", "internal", "all"])
    pq = sub.add_parser("query", help="精确查询发布件")
    pq.add_argument("--rfn", default="")
    pq.add_argument("--docno", default="")
    pq.add_argument("--theme", default="")
    pq.add_argument("--source", default="")
    pq.add_argument("--timeliness", default="")
    pq.add_argument("--chain", default="", help="同文号版本链（F-K08）")
    pq.add_argument("--view", default="", choices=["", "active"], help="视图：active=现行有效")
    pq.add_argument("--internal", action="store_true", help="查内部底座（默认外部）")
    pq.add_argument("--limit", type=int, default=50)
    pq.add_argument("--json", action="store_true")
    ps = sub.add_parser("search", help="全文检索（FTS5 trigram）")
    ps.add_argument("text")
    ps.add_argument("--kind", default="records", choices=["records", "clauses", "policies"])
    ps.add_argument("--internal", action="store_true")
    ps.add_argument("--limit", type=int, default=20)
    ps.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.action == "publish":
        from base_publish import build_external as _be  # noqa: PLC0415
        from base_publish import build_fts as _bf  # noqa: PLC0415
        from base_publish import build_internal as _bi  # noqa: PLC0415
        out = {}
        if args.base in ("external", "all"):
            out["external"] = _be.build()["counts"]
        if args.base in ("internal", "all"):
            out["internal"] = _bi.build()["counts"]
        out["fts"] = {}
        if args.base in ("external", "all"):
            out["fts"]["external"] = _bf.build_external()
        if args.base in ("internal", "all"):
            out["fts"]["internal"] = _bf.build_internal()
        print(_json.dumps(out, ensure_ascii=False, indent=1))
        return 0

    from base_api import (  # noqa: PLC0415
        query_external,
        query_internal,
        search_external,
        search_internal,
    )
    if args.action == "query":
        if args.chain:
            from base_api import version_chain  # noqa: PLC0415
            rows = version_chain(args.chain, limit=args.limit)
        elif args.view == "active":
            from base_api import view_active  # noqa: PLC0415
            rows = view_active(limit=args.limit)
        elif args.internal:
            rows = query_internal(theme=args.theme, limit=args.limit)
        else:
            rows = query_external(rfn=args.rfn, document_number=args.docno, theme=args.theme,
                                  source=args.source, timeliness_status=args.timeliness,
                                  limit=args.limit)
    else:
        if args.internal:
            rows = search_internal(args.text, limit=args.limit, kind=args.kind if args.kind != "records" else "policies")
        else:
            rows = search_external(args.text, limit=args.limit, kind=args.kind)
    if args.json:
        print(_json.dumps(rows, ensure_ascii=False, indent=1))
    else:
        print(f"[base] 命中 {len(rows)} 条")
        for r in rows[:20]:
            key = r.get("rfn") or r.get("ipn") or r.get("record_id", "")
            print(f"  {key:<22} | {(r.get('title') or '')[:44]} | {(r.get('article_no') or r.get('publish_date') or '')}")
    return 0


COMMANDS = {
    "gates": _cmd_gates,
    "source": _cmd_source,
    "internal": _cmd_internal,
    "classify": _cmd_classify,
    "timeliness": _cmd_timeliness,
    "draft": _cmd_draft,
    "rfn": _cmd_rfn,
    "base": _cmd_base,
    "ping": _cmd_ping,
}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="orchestrator",
        description="regulatory_compliance_orchestrator 统一编排入口（P0 骨架）",
    )
    sub = p.add_subparsers(dest="command", metavar="<command>")
    sub.add_parser("gates", help="运行交付门禁（ALL_GATES）")
    sub.add_parser("ping", help="骨架自检")
    p_source = sub.add_parser("source", help="源目录（config/sources.yaml 唯一事实源，R15）")
    p_source.add_argument("action", choices=["list", "add"], help="list 列出源与 collector 路由 | add 新增源 checklist")
    p_int = sub.add_parser("internal", help="内部制度摄取/对齐/引用视图（P6/P7）")
    p_int.add_argument("sub", choices=["index", "align", "merged", "backfill"],
                       help="index 摄取 | align 主题对齐 | merged 制度×RFN 引用视图 | backfill 条文回补(R10)")
    sub.add_parser("classify", help="主题底座强序重建（R8，--theme/--all/--steps/--dry-run）")
    p_tl = sub.add_parser("timeliness", help="时效核验（R13 三态：success/partial/unavailable）")
    p_tl.add_argument("action", choices=["verify"],
                      help="verify 效力缺失核验（透传 --source/--dry-run/--probe/--workers/--token-file）")
    p_draft = sub.add_parser("draft", help="条款级对照素材端到端编排（P8：merged_view × R21 clauses）")
    p_draft.add_argument("--ipn", default="", help="单制度 IPN-xxx（默认全部）")
    sub.add_parser("rfn", help="RFN 登记/查询（registry 唯一写口，F-C01；register/lookup）")
    sub.add_parser("base", help="双底座发布件构建与统一查询（Base Contract v1；publish/query/search）")
    return p


def main(argv=None) -> int:
    # Windows 控制台默认 GBK：强制 stdout UTF-8 防 UnicodeEncodeError（含 ↔ 等符号）
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    argv = list(sys.argv[1:] if argv is None else argv)
    # A-11（2026-09-12）：-h/--help/help 显式处理（原仅无参打印，`cli.py --help` 报"未知命令"）。
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(build_parser().format_help())
        return 0 if argv else 1
    # 兼容 "orchestrator source list" / "orchestrator gates"
    cmd = argv[0]
    handler = COMMANDS.get(cmd)
    if handler is None:
        print(f"未知命令: {cmd}（可用: {sorted(COMMANDS)}）")
        return 1
    return handler(argv[1:])


if __name__ == "__main__":
    sys.exit(main())
