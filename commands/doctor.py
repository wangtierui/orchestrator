# -*- coding: utf-8 -*-
"""
commands/doctor — 环境前置自检（v2 §3.13.4，P2-6）

把"人工逐项确认环境"（历史踩坑：PATH 无 python、tesseract 缺失、token 过期、
治理库不可连、锁被占用…）变成 **18 项机器判定**，输出 `doctor.json` + 人类摘要；
任一项 FAIL → 退出码 `ExitCode.ENV(3)`，且 `cli.py run` 启动前自动跑 `--quick` 子集。

用法：
    python cli.py doctor            # 全量 18 项
    python cli.py doctor --quick    # 只跑 run 前置所需的子集
    python cli.py doctor --json     # 机器可读（写 reports/_tmp/doctor.json 之外再打印）
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

import paths
from config.exitcodes import ExitCode  # noqa: E402  (R3：退出码语义化)

OUT_JSON = os.path.join(paths.ROOT, "reports", "_tmp", "doctor.json")
DISK_MIN_GB = 5.0
# `run` 前置子集（§3.13.4）。
# 2026-09-26 审查：移除 `token` —— token 是**核验步骤**（timeliness verify，阶段 6.9 触发项）的
# 依赖，非 run 主链必要条件；`run --no-scrape`（不采集不核验）缺 token 不应被前置自检拒绝
# （否则统一入口退化为"必须手动 --skip-doctor"，非全流程自动化）。核验步骤自带 R13 降级
# （token 缺失 → unavailable，不误标）；全量 `cli.py doctor` 仍检查 token 供人工处置。
QUICK_IDS = (
    "python",
    "node",
    "deps",
    "pillow",
    "pymupdf",
    "tesseract",
    "gov_db",
    "sourcetree",
    "lock",
)


def _importable(mod: str) -> tuple[bool, str]:
    try:
        __import__(mod)
        return True, "可导入"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def _c_python() -> tuple[str, str, str]:
    v = sys.version_info
    ok = (v.major, v.minor) >= (3, 13)
    return ("ok" if ok else "fail"), f"Python {v.major}.{v.minor}.{v.micro}", "需 ≥3.13"


def _c_node() -> tuple[str, str, str]:
    exe = shutil.which("node")
    if not exe:
        return "warn", "node 不在 PATH", "Node 相关工具（graphify/llm_wiki 同步）将不可用"
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=10)
        return "ok", f"node {out.stdout.strip()}", exe
    except Exception as e:  # noqa: BLE001
        return "warn", f"node 调用失败：{type(e).__name__}", ""


def _c_pkulaw_pkg() -> tuple[str, str, str]:
    try:
        from scraper_std import pkulaw_cli  # noqa: PLC0415

        cli = pkulaw_cli.find_cli()
        return ("ok" if cli else "warn"), f"pkulaw cli={cli or '(未找到)'}", "北大法宝官方 CLI 目录"
    except Exception as e:  # noqa: BLE001
        return "warn", f"pkulaw_cli 不可用：{type(e).__name__}", ""


def _c_deps() -> tuple[str, str, str]:
    mods = (
        "requests",
        "bs4",
        "yaml",
        "lxml",
        "numpy",
        "openpyxl",
        "xlrd",
        "docx",
        "pypdf",
        "pdfplumber",
        "chardet",
    )
    bad = [m for m in mods if not _importable(m)[0]]
    return (
        ("ok" if not bad else "fail"),
        (f"{len(mods) - len(bad)}/{len(mods)} 可导入" + (f"；缺 {bad}" if bad else "")),
        "pyproject [project.dependencies]",
    )


def _c_pillow() -> tuple[str, str, str]:
    ok, msg = _importable("PIL")
    return ("ok" if ok else "fail"), msg, "v2 §3.7 G1（Pillow 上移 base）"


def _c_pymupdf() -> tuple[str, str, str]:
    ok, msg = _importable("fitz")
    if not ok:
        ok2, msg2 = _importable("pymupdf")
        return ("ok" if ok2 else "fail"), msg2 if ok2 else msg, "v2 §3.7 G2（PyMuPDF 上移 base）"
    return "ok", msg, "v2 §3.7 G2"


def _c_tesseract() -> tuple[str, str, str]:
    try:
        from config.loader import load_ocr  # noqa: PLC0415

        exe = (load_ocr().get("ocr") or {}).get("tesseract_bin", "")
    except Exception as e:  # noqa: BLE001
        return "warn", f"ocr.yaml 不可读：{type(e).__name__}", ""
    if not exe or not os.path.exists(exe):
        return "fail", f"tesseract 缺失：{exe or '(未配置)'}", "config/ocr.yaml: ocr.tesseract_bin"
    return "ok", os.path.relpath(exe, paths.ROOT), "OCR 主引擎（PaddleOCR 未就绪时的降级）"


def _c_tessdata() -> tuple[str, str, str]:
    base = os.path.join(paths.ROOT, "tessdata")
    miss = [
        n
        for n in ("chi_sim", "eng", "osd")
        if not os.path.exists(os.path.join(base, n + ".traineddata"))
    ]
    return (
        ("ok" if not miss else "fail"),
        (f"缺失 {miss}" if miss else "chi_sim/eng/osd 就位"),
        base,
    )


def _c_paddle() -> tuple[str, str, str]:
    try:
        from config.loader import load_ocr  # noqa: PLC0415

        cfg = load_ocr().get("ocr") or {}
        d = cfg.get("paddle_model_dir") or cfg.get("paddleocr_dir") or ""
    except Exception as e:  # noqa: BLE001
        return "warn", f"ocr.yaml 不可读：{type(e).__name__}", ""
    if not d:
        return "warn", "未配置 PaddleOCR 目录 → 明确降级 Tesseract", "config/ocr.yaml"
    return ("ok" if os.path.isdir(d) else "warn"), f"PaddleOCR 目录 {d}", "未就绪时降级 Tesseract"


def _c_token() -> tuple[str, str, str]:
    p = os.path.join(paths.ROOT, ".pkulaw_token")
    if not os.path.exists(p):
        return "fail", "缺 .pkulaw_token", "效力核验（阶段 6.9 触发项）依赖"
    try:
        val = open(p, encoding="utf-8", errors="replace").read().strip()
    except OSError as e:
        return "fail", f"不可读：{type(e).__name__}", ""
    return ("ok" if val else "fail"), (f"长度 {len(val)}" if val else "内容为空"), ""


def _c_token_fresh() -> tuple[str, str, str]:
    """token 时效：**无探测手段 → warn（不 FAIL）**——不假装能判定。"""
    p = os.path.join(paths.ROOT, ".pkulaw_token")
    if not os.path.exists(p):
        return "warn", "无 token，跳过时效探测", ""
    import datetime  # noqa: PLC0415

    days = (datetime.datetime.now() - datetime.datetime.fromtimestamp(os.path.getmtime(p))).days
    return "warn", f"token 最后更新 {days} 天前（**无法本地判定有效性**，以首次核验为准）", ""


def _c_gov_db() -> tuple[str, str, str]:
    try:
        import sqlite3  # noqa: PLC0415

        from std_lib.common_lib import governance_store as gs  # noqa: PLC0415

        p = gs.db_path()
        if not os.path.exists(p):
            return "fail", f"治理库不存在：{p}（先 `python cli.py governance init`）", ""
        with sqlite3.connect(p) as c:
            ver = c.execute("PRAGMA user_version").fetchone()[0]
        return "ok", f"{os.path.relpath(p, paths.ROOT)} user_version={ver}", "v2 §3.14 唯一元数据库"
    except Exception as e:  # noqa: BLE001
        return "fail", f"{type(e).__name__}: {e}", ""


def _c_sourcetree() -> tuple[str, str, str]:
    need = (
        os.path.join(paths.ROOT, "paths.py"),
        paths.MODULES_DIR,
        os.path.join(paths.MODULES_DIR, "regulatory_scrapers"),
    )
    miss = [p for p in need if not os.path.exists(p)]
    return (
        ("ok" if not miss else "fail"),
        (f"缺 {miss}" if miss else "仓根 + modules/** 就位"),
        "非源码树安装（缺路径）会使全部模块级脚本失效",
    )


def _c_inbox_corpus() -> tuple[str, str, str]:
    inbox = paths.INBOX_DIR
    corpus = os.path.join(paths.ROOT, "data", "corpus")
    if not os.path.isdir(inbox):
        # N-31：`data/**` 不入库 → 投放区目录须由工具创建，故"缺失"是**待初始化**而非环境缺陷。
        # 判 FAIL 会让每个新克隆都红；给 warn + 明确修复命令（判据本意是"自指环防护"，见下）。
        return (
            "warn",
            f"投放区未初始化：{os.path.relpath(inbox, paths.ROOT)}",
            "跑 `python tools/inbox_scan.py --apply` 创建（幂等）",
        )
    if not os.path.isdir(corpus):
        return "warn", f"语料本体缺失：{os.path.relpath(corpus, paths.ROOT)}", "v2 §3.12"
    if os.path.abspath(inbox) == os.path.abspath(corpus):
        return "fail", "投放区与语料本体同路径（自指环）", "v2 §3.12 自指环防护"
    return "ok", "data/inbox 与 data/corpus 均存在且不同路径", ""


def _c_lock() -> tuple[str, str, str]:
    """单实例锁探测：**非破坏性**（只看锁文件新鲜度，不尝试抢占）。"""
    p = os.path.join(paths.ROOT, "data", "run_production_refresh.lock")
    if not os.path.exists(p):
        return "ok", "无锁文件（未在运行）", ""
    import datetime  # noqa: PLC0415

    age_h = (datetime.datetime.now().timestamp() - os.path.getmtime(p)) / 3600
    if age_h < 48:
        return (
            "warn",
            f"锁文件存在且 {age_h:.1f}h 内更新 → **可能有实例在运行**",
            "并发 run 会被锁拒绝（退出码 3）",
        )
    return "ok", f"锁文件陈旧（{age_h:.1f}h）", ""


def _c_schedule() -> tuple[str, str, str]:
    try:
        from bootstrap import bootstrap  # noqa: PLC0415

        bootstrap("all", include_tools=True)  # commands/ 禁自行注入（gate_import_bootstrap 硬零层）
        import install_schedule  # noqa: PLC0415

        ok, det = install_schedule.verify()
        return (
            ("ok" if ok else "warn"),
            (
                "已安装任务与 schedule.yaml 一致"
                if ok
                else f"{len(det['problems'])} 项不一致（未安装/参数漂移）"
            ),
            "`python cli.py schedule verify`",
        )
    except Exception as e:  # noqa: BLE001
        return "warn", f"无法校验计划任务：{type(e).__name__}", "非 Windows 或 schtasks 不可用"


def _c_triggers() -> tuple[str, str, str]:
    try:
        from std_lib.common_lib import triggers as trg  # noqa: PLC0415

        rows = trg.decide({"argv": []})
        return ("ok" if rows else "fail"), f"{len(rows)} 个触发项可判定", "config/triggers.yaml"
    except Exception as e:  # noqa: BLE001
        return "fail", f"{type(e).__name__}: {e}", ""


def _c_disk() -> tuple[str, str, str]:
    try:
        import shutil as _sh  # noqa: PLC0415

        total, used, free = _sh.disk_usage(paths.ROOT)
        free_gb = free / (1024**3)
        return (
            ("ok" if free_gb >= DISK_MIN_GB else "fail"),
            f"余量 {free_gb:.1f} GB（阈值 {DISK_MIN_GB}）",
            "备份堆积会加速消耗",
        )
    except Exception as e:  # noqa: BLE001
        return "warn", f"{type(e).__name__}", ""


# (id, 组, 函数)
CHECKS: tuple[tuple[str, str, object], ...] = (
    ("python", "运行时", _c_python),
    ("node", "运行时", _c_node),
    ("pkulaw_pkg", "运行时", _c_pkulaw_pkg),
    ("deps", "依赖", _c_deps),
    ("pillow", "依赖", _c_pillow),
    ("pymupdf", "依赖", _c_pymupdf),
    ("tesseract", "OCR", _c_tesseract),
    ("tessdata", "OCR", _c_tessdata),
    ("paddle", "OCR", _c_paddle),
    ("token", "凭据", _c_token),
    ("token_fresh", "凭据", _c_token_fresh),
    ("gov_db", "数据", _c_gov_db),
    ("sourcetree", "数据", _c_sourcetree),
    ("inbox_corpus", "数据", _c_inbox_corpus),
    ("lock", "数据", _c_lock),
    ("schedule", "调度", _c_schedule),
    ("triggers", "调度", _c_triggers),
    ("disk", "磁盘", _c_disk),
)


def run_checks(quick: bool = False) -> dict:
    """执行检查项 → {items, ok, fail, warn}（`quick=True` 只跑 `QUICK_IDS`）。"""
    items: list[dict] = []
    for cid, group, fn in CHECKS:
        if quick and cid not in QUICK_IDS:
            continue
        try:
            state, detail, note = fn()  # type: ignore[operator]
        except Exception as e:  # noqa: BLE001  单项异常不得拖垮自检
            state, detail, note = "fail", f"检查项异常 {type(e).__name__}: {e}", ""
        items.append({"id": cid, "group": group, "state": state, "detail": detail, "note": note})
    fails = [i["id"] for i in items if i["state"] == "fail"]
    warns = [i["id"] for i in items if i["state"] == "warn"]
    return {
        "items": items,
        "checked": len(items),
        "ok": len(items) - len(fails) - len(warns),
        "warn": len(warns),
        "fail": len(fails),
        "failed_ids": fails,
        "warn_ids": warns,
        "quick": quick,
    }


def write_report(res: dict) -> str:
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=1)
    return OUT_JSON


def main(argv=None) -> int:
    import argparse  # noqa: PLC0415

    ap = argparse.ArgumentParser(description="环境前置自检（v2 §3.13.4；18 项机器判定）")
    ap.add_argument("--quick", action="store_true", help="只跑 run 前置子集")
    ap.add_argument("--json", action="store_true", help="输出 JSON（stdout）")
    args = ap.parse_args(argv)
    res = run_checks(quick=args.quick)
    write_report(res)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        print(f"环境自检（{res['checked']} 项；quick={args.quick}）")
        for it in res["items"]:
            mark = {"ok": "[OK]  ", "warn": "[WARN]", "fail": "[FAIL]"}[it["state"]]
            print(f"  {mark} {it['id']:<14} {it['group']:<5} {it['detail']}")
        print(f"结果：ok={res['ok']} warn={res['warn']} fail={res['fail']}")
        if res["fail"]:
            print("环境不满足：修复上方 [FAIL] 项后重试（`cli.py run` 会被前置自检拒绝）")
        print(f"报告：{os.path.relpath(OUT_JSON, paths.ROOT)}")
    return ExitCode.ENV if res["fail"] else ExitCode.OK


def run(argv=None) -> int:
    return main(argv)
