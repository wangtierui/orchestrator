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
from config.exitcodes import ExitCode

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
        from scraper_std import pkulaw_cli

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
        from config.loader import load_ocr

        exe = (load_ocr().get("ocr") or {}).get("tesseract_bin", "")
    except Exception as e:  # noqa: BLE001
        return "warn", f"ocr.yaml 不可读：{type(e).__name__}", ""
    if not exe or not os.path.exists(exe):
        return "fail", f"tesseract 缺失：{exe or '(未配置)'}", "config/ocr.yaml: ocr.tesseract_bin"
    return "ok", os.path.relpath(exe, paths.ROOT), "OCR 主引擎（PaddleOCR 未就绪时的降级）"


def _c_tessdata() -> tuple[str, str, str]:
    # W4（§7.10 案例二）：tessdata 已迁入 `external/tessdata/`（external = 全部外部
    # 资产统一外置；仓根不再平铺工具数据目录）。
    base = paths.TESSDATA_DIR
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
        from config.loader import load_ocr

        cfg = load_ocr().get("ocr") or {}
        d = cfg.get("paddle_model_dir") or cfg.get("paddleocr_dir") or ""
    except Exception as e:  # noqa: BLE001
        return "warn", f"ocr.yaml 不可读：{type(e).__name__}", ""
    if not d:
        return "warn", "未配置 PaddleOCR 目录 → 明确降级 Tesseract", "config/ocr.yaml"
    return ("ok" if os.path.isdir(d) else "warn"), f"PaddleOCR 目录 {d}", "未就绪时降级 Tesseract"


def _c_token() -> tuple[str, str, str]:
    """token 就绪度 —— 经 `pkulaw_cli.load_token_any()`（**唯一事实源**，N-169）。

    修正前的缺陷（实测）：本项**只查仓根** `.pkulaw_token`，而三个核验脚本读的是
    `timeliness_review/.pkulaw_token`（**token 实际在此**）→ token 明明可用、脚本真跑成功，
    **doctor 却报 FAIL**（假 FAIL）。现与脚本共用同一解析 → 自检与真实能力一致。
    """
    try:
        from std_lib.scraper_std import pkulaw_cli as _pk

        tok, src = _pk.load_token_any()
    except Exception as e:  # noqa: BLE001
        return "warn", f"token 解析不可用：{type(e).__name__}", ""
    if not tok:
        return (
            "fail",
            "缺 token（候选：仓根 `.pkulaw_token` / `timeliness_review/.pkulaw_token` / env）",
            "效力核验（阶段 6.9 触发项）依赖",
        )
    return "ok", f"长度 {len(tok)}（来源：{src or 'env'}）", ""


def _c_token_fresh() -> tuple[str, str, str]:
    """token 时效：**无探测手段 → warn（不 FAIL）**——不假装能判定。

    ⚠️ 2026-10-19 修（**N-169 同类缺陷的翻版**，同处代码此前只修了一半）：
    原实现硬编码仓根 `.pkulaw_token`，而 token 实际在
    `timeliness_review/.pkulaw_token` ⇒ 明明有 token 却报"无 token，跳过时效探测"，
    与**紧邻的 `token` 项**（报"长度 36 / 来源 timeliness_review"）**自相矛盾**。
    这种"同一屏两行互相打脸"的自检输出比缺项更有害——用户会不知道该信哪一行。

    现改用 `pkulaw_cli.load_token_any()` 的**同一事实源**（N-169 已确立为唯一来源，
    含 `env:PKULAW_TOKEN` 与 `token_paths()` 多候选），并据**命中来源**取 mtime：
      · env ⇒ 无 mtime 可查（如实说明，不拿仓根文件的 mtime 冒充）；
      · 文件 ⇒ 用**实际命中文件**的 mtime（而非猜测的路径）。
    """
    try:
        from std_lib.scraper_std import pkulaw_cli as _pk

        tok, src = _pk.load_token_any()
    except Exception as e:  # noqa: BLE001  自检项不得因单点异常整体失败
        return "warn", f"token 来源不可判定：{type(e).__name__}: {e}", ""
    if not tok:
        return ("warn",
                "无 token（env PKULAW_TOKEN / 仓根 .pkulaw_token / "
                "timeliness_review/.pkulaw_token 均未命中）⇒ 跳过时效探测", "")
    if src.startswith("env:"):
        return ("warn",
                f"token 来自 {src}（**env 无 mtime 可查**，无法本地判定有效期）", "")
    if not os.path.exists(src):
        return "warn", f"token 命中来源 {src} 已消失（并发删除竞态）", ""
    import datetime

    days = (datetime.datetime.now()
            - datetime.datetime.fromtimestamp(os.path.getmtime(src))).days
    where = f"{os.path.basename(os.path.dirname(src))}/{os.path.basename(src)}"
    return ("warn",
            f"token 最后更新 {days} 天前（来源 {where}；"
            "**无法本地判定有效性**，以首次核验为准）", "")


def _c_gov_db() -> tuple[str, str, str]:
    try:
        import sqlite3

        from std_lib.common_lib import governance_store as gs

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
    import datetime

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
        from bootstrap import bootstrap

        bootstrap("all", include_tools=True)  # commands/ 禁自行注入（gate_import_bootstrap 硬零层）
        import install_schedule

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
        from std_lib.common_lib import triggers as trg

        rows = trg.decide({"argv": []})
        return ("ok" if rows else "fail"), f"{len(rows)} 个触发项可判定", "config/triggers.yaml"
    except Exception as e:  # noqa: BLE001
        return "fail", f"{type(e).__name__}: {e}", ""


def _c_disk() -> tuple[str, str, str]:
    try:
        import shutil as _sh

        total, used, free = _sh.disk_usage(paths.ROOT)
        free_gb = free / (1024**3)
        return (
            ("ok" if free_gb >= DISK_MIN_GB else "fail"),
            f"余量 {free_gb:.1f} GB（阈值 {DISK_MIN_GB}）",
            "备份堆积会加速消耗",
        )
    except Exception as e:  # noqa: BLE001
        return "warn", f"{type(e).__name__}", ""


def _c_git_hooks() -> tuple[str, str, str]:
    """R-1（2026-09-30）：**版本化 git 钩子就绪度**。

    为何入 doctor：本仓"提交后自动同步远程"依赖钩子，而 `.git/hooks/**` **不入库** →
    新克隆/新机器上"自动提交"**根本不跑**（实测事故 N-163：8 批提交从未推送成功）。
    自 N-164 起钩子已**版本化**（`.githooks/` + `core.hooksPath`），但"设一次 config"这一步
    仍需有人做 —— 此处把它变成**环境自检项**，使遗漏**当场可见**（而非下次排查才发现）。
    """
    try:
        from std_lib.common_lib import git_hooks as _gh

        problems = _gh.check()
        if problems:
            return (
                "warn",
                f"未就绪（{len(problems)} 项）：{problems[0]}",
                "处置：python -m tools.install_git_hooks",
            )
        return "ok", "版本化钩子就绪（core.hooksPath=.githooks）", "自动推送 + 图同步"
    except Exception as e:  # noqa: BLE001
        return "warn", f"{type(e).__name__}: {e}", ""


def _c_mirrors() -> tuple[str, str, str]:
    """镜像源就绪度（N-175）—— 权重/依赖的**优先访问源**。

    为何入 doctor：官方权重站 `huggingface.co` 在本机**不可达**，若镜像未生效，
    症状是"**首次调用才联网下载并失败**"（远离配置处、且破坏离线性）。
    本项做**离线级**核验（配置 + 环境变量一致性）；**实测级**（真拉包/真取权重文件）
    由 `python -m tools.check_mirrors --probe` 提供（避免 doctor 每次都联网）。
    """
    try:
        # 实现下沉在 `std_lib/common_lib/mirrors.py`（**不得** `from tools.…` 静态导入：
        # 那会让 mypy 把同一文件解析为两个模块名 → 阻断 CI；见 N-175 与本仓『分层纪律』判据）。
        from std_lib.common_lib.mirrors import check_env, summary

        problems = check_env()
    except Exception as e:  # noqa: BLE001
        return "warn", f"镜像核验不可用：{type(e).__name__}", ""
    if problems:
        return "warn", f"未就绪（{len(problems)} 项）：{problems[0][:110]}", "处置：python -m tools.check_mirrors"
    return "ok", summary(), "实测：python -m tools.check_mirrors --probe"


def _c_mcp() -> tuple[str, str, str]:
    """MCP 门面就绪度（W2）—— token 文件 / tool schema 生成 / stdio 自检。

    缺失语义：**warn 而非 fail**（MCP 是可选门面；§4.5 纪律「可选增强缺失不影响主链」）。
    """
    try:
        from mcp_server import auth as _auth
        from mcp_server.registry import list_tools
    except Exception as e:  # noqa: BLE001
        return "warn", f"MCP 门面不可导入（{type(e).__name__}）", "不影响主链；需要时再排查"
    tok = _auth.load_token()
    try:
        n = len(list_tools())
    except Exception as e:  # noqa: BLE001
        return "warn", f"tool schema 生成失败（{type(e).__name__}）", "python -m mcp_server.transport_stdio"
    if not tok:
        return "warn", f"tool {n} 个已注册；**无 token 文件**", "生成：python -m mcp_server.auth --init"
    return "ok", f"tool {n} 个已注册；token 就绪", "自检：python cli.py mcp selftest"


def _c_pgvector() -> tuple[str, str, str]:
    """pgvector 服务就绪度（W4 §6.4）—— 语义检索视图的后端健康检查。

    缺失语义：**warn 而非 fail**——pgvector 是**可选查询后端**（ADR-0007 L-d；
    降级链 pgvector→sqlite_vec→none，`semantic:search` 步骤 rc0 披露）。未配置
    `PGVECTOR_DSN` 是合法状态（既有 FTS5 检索路径不受影响）；配了但连不上才是
    需要人看的信号。口令仅经 env 注入（`gate_secret_scan` 守护）。
    """
    try:
        from std_lib.common_lib import vector_store as _vs

        h = _vs.health()
    except Exception as e:  # noqa: BLE001
        return "warn", f"健康检查不可用（{type(e).__name__}）", "不影响主链"
    if not h.get("dsn_configured"):
        return "warn", f"未配置 env {h.get('dsn_env')}（降级 {_vs.backend()}）", "语义检索视图走降级披露（rc=0）"
    if h.get("ok"):
        return (
            "ok",
            f"{h.get('database')}:{h.get('port')} vector {h.get('vector_ext')} 就绪"
            f"（用户 {h.get('user')}，非超管={h.get('not_superuser')}）",
            "链步骤 pg:health / semantic:search 披露",
        )
    return "warn", f"不可用：{h.get('detail', '')[:110]}", "检查 PG 服务/env DSN；降级路径不受影响"


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
    ("git_hooks", "版本控制", _c_git_hooks),
    ("mirrors", "依赖", _c_mirrors),
    ("disk", "磁盘", _c_disk),
    ("mcp", "门面", _c_mcp),
    ("pgvector", "门面", _c_pgvector),
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
    import argparse

    ap = argparse.ArgumentParser(description="环境前置自检（v2 §3.13.4；机器判定项见 CHECKS）")
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
    if res["fail"]:
        # 批 61：补齐 `notify.on` 的 `doctor_fail` 发送者（此前无人外发 ✗）
        try:
            from std_lib.common_lib import notify as _notify_d

            _notify_d.notify("doctor_fail", "环境自检未通过（%d 项）" % len(res["fail"]),
                          {"failed": list(res["fail"])[:20]})
        except Exception as e:  # noqa: BLE001  告警失败不得改变退出码
            print(f"[doctor] WARN 告警发送失败: {type(e).__name__}: {e}")
    return ExitCode.ENV if res["fail"] else ExitCode.OK


def run(argv=None) -> int:
    return main(argv)
