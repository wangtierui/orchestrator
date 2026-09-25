# -*- coding: utf-8 -*-
"""commands.rfn — orchestrator 命令：rfn（自 cli.py 迁移，2026-09-13 审查 P3）。"""
from __future__ import annotations

import paths


def run(argv):
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

    from bootstrap import bootstrap  # noqa: PLC0415
    bootstrap("regulatory_classifier")
    cls_root = _os.path.join(paths.ROOT, "modules", "regulatory_classifier")
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
