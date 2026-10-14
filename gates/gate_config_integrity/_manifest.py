# -*- coding: utf-8 -*-
"""gates.gate_config_integrity._manifest — J 族判据（W4-c 五拆）。

tools 清单一致性（J1-J6）/ worklist kind 双向闭合（J7）/ corpus 索引一致性。
公共基础设施（ROOT/_MANIFEST/_load_module_from_path）经 `._common`。
"""

from __future__ import annotations

import json
import os
import re
import subprocess

import paths

from ._common import (
    _REF_EXTS,
    _REF_SKIP_DIRS,
    _RETIRED_DIR,
    _RETIRED_README,
    _SCAN_BASE_SKIP,
    _TOOLS,
    ROOT,
)


def _tools_py() -> set[str]:
    """tools/ 根 + tools/retired/ 的 .py 相对（tools/）POSIX 路径。"""
    out: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(_TOOLS):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            rel = os.path.relpath(os.path.join(dirpath, fn), _TOOLS).replace("\\", "/")
            out.add(rel)
    return out


def _iter_ref_files():
    """J3 扫描范围：仓内 .py/.yaml/.toml/.bat 等（跳过 tools/、.git、缓存）。"""
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [
            d for d in dirnames if d not in _REF_SKIP_DIRS and d not in {"data", ".codebuddy"}
        ]
        for fn in filenames:
            if fn.endswith(_REF_EXTS):
                yield os.path.join(dirpath, fn)


def _check_tools(manifest: dict) -> tuple[list[str], dict, list[str]]:
    problems: list[str] = []
    warnings: list[str] = []
    detail: dict = {}

    entries = manifest.get("entries") or []
    declared = {e.get("file", "") for e in entries}
    actual = _tools_py()

    # J1 双向相等
    missing = sorted(actual - declared)
    ghost = sorted(declared - actual)
    if missing:
        problems.append(
            f"J1: tools/ 下未登记脚本 {missing}（新增脚本必须写入 tools/_manifest.json）"
        )
    if ghost:
        problems.append(f"J1: 清单登记的幽灵条目 {ghost}（文件不存在）")
    detail["tools_total"] = len(actual)

    # J2 retired ⟺ retired/
    by_file = {e.get("file", ""): e for e in entries}
    for f, e in by_file.items():
        is_retired_path = f.startswith(_RETIRED_DIR + "/")
        is_retired_cat = e.get("category") == "retired"
        if is_retired_cat != is_retired_path:
            problems.append(
                f"J2: {f} 的 category={e.get('category')!r} 与路径不一致"
                f"（retired ⟺ tools/retired/，双向）"
            )
    retired = [e for e in entries if e.get("category") == "retired"]
    detail["retired_count"] = len(retired)

    # J3 retired 项零仓内引用（**仅计可执行引用**：注释行提及属历史痕迹，披露不计违规）
    #   依据：退役工具曾把"引导注释"写入 live 代码（如 collectors 的
    #   `# ---- P3b 仓库引导（migrate_collectors_p3b.py 注入）----`），那是注释不是依赖。
    refs: dict[str, list[str]] = {}
    comment_hits: dict[str, list[str]] = {}
    for e in retired:
        base = os.path.basename(e.get("file", ""))
        stem = os.path.splitext(base)[0]
        if not stem:
            continue
        pat = re.compile(rf"(?<![\w-]){re.escape(stem)}(?![\w-])")
        for fp in _iter_ref_files():
            try:
                text = open(fp, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            if not pat.search(text):
                continue
            rel = os.path.relpath(fp, ROOT).replace("\\", "/")
            # 逐行判定：整行注释（`#` 起头）视为历史痕迹
            code_hit = any(
                pat.search(ln) and not ln.lstrip().startswith("#") for ln in text.splitlines()
            )
            (refs if code_hit else comment_hits).setdefault(e["file"], []).append(rel)
    for f, locs in refs.items():
        problems.append(f"J3: 已退役脚本仍被**可执行引用** {f} <- {sorted(set(locs))[:5]}")
    detail["retired_refs"] = {k: sorted(set(v)) for k, v in refs.items()}
    if comment_hits:
        detail["retired_comment_mentions"] = {k: len(set(v)) for k, v in comment_hits.items()}

    # J4 retired README 存在且含退役登记
    if retired:
        if not os.path.exists(_RETIRED_README):
            problems.append("J4: 存在 retired 项但缺少 tools/retired/README.md")
        else:
            txt = open(_RETIRED_README, encoding="utf-8").read()
            if not re.search(r"20\d{2}-\d{2}-\d{2}", txt):
                problems.append("J4: tools/retired/README.md 未登记退役日期（需 原因+日期+替代物）")

    # J5 仓根 .py 白名单
    allow = set(manifest.get("root_py_allowlist") or [])
    actual_root = {
        fn
        for fn in os.listdir(ROOT)
        if fn.endswith(".py") and os.path.isfile(os.path.join(ROOT, fn))
    }
    extra = sorted(actual_root - allow)
    if extra:
        problems.append(
            f"J5: 仓根存在未登记 .py {extra}（须落位/删除，或显式登记进 root_py_allowlist）"
        )
    stale = sorted(allow - actual_root)
    if stale:
        warnings.append(f"J5: 白名单条目已不存在 {stale}（建议收缩）")
    detail["root_py"] = sorted(actual_root)

    # J6 **已转严格**（v2 §3.15.5，P2-7）：仓根未跟踪且未忽略的文件 → FAIL（入库/忽略/删除三选一）。
    # 理由（§3.15.4）：仓根是"最容易被随手丢文件"的地方（`file_list_watcher_bak.py`、`_p.log`
    # 即为此类残留）；仅报告不阻断 → 残留会长期累积。gates/ 与 tools/ 内的同类检查
    # 由 A1–A4 与 J3 承担，此处只盯**仓根**（`/` 不在路径里）。
    try:
        r = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all", "--", "."],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        untracked = [
            ln[3:].strip()
            for ln in (r.stdout or "").splitlines()
            if ln.startswith("??") and "/" not in ln[3:].strip()
        ]
        if untracked:
            problems.append(
                f"J6: 仓根存在未跟踪且未忽略的文件 {sorted(untracked)}"
                "（三选一：入库 / 写进 .gitignore / 删除；勿堆在仓根）"
            )
    except (OSError, subprocess.SubprocessError) as e:
        # git 不可用（tarball/无 git 环境）→ 只告警，不 FAIL（判据不可执行 ≠ 判据通过）
        warnings.append(f"J6: 未跟踪文件检查跳过（{type(e).__name__}）")

    return problems, detail, warnings


# P2-5 待接的产生方（v2 §3.14.3 已登记 kind，但产生方尚未实装）——只披露、不阻断。
# 接入后须同步删除本集合（gate 会提示）。
# 待接的产生方（v2 §3.14.3 已登记 kind、产生方尚未实装者）。D6（`relevance_boundary`）已于
# 第六批接入（filter_clean_relevance.py 的 `_wl_boundary`，**仅显式导出 BOUNDARY 时登记、
# 单次上限 50 条防灌爆**）→ 本集合清空；J7 的双向闭合由此**全量成立**。
_PENDING_PRODUCERS: frozenset[str] = frozenset()

# ⚠️ 刻意**不用正则、不用任何反斜杠转义**：本仓的编辑/同步链路会把 new_str 里的反斜杠
# 二次转义（实测：正则 `\(` 落盘为 `\\(` → 语义变成"字面反斜杠 + 分组"，全部漏匹配；
# 字符集 `" \t\r\n"` 落盘为字面 `\t\r\n` 四字符 → 跳过空白失效）。故此处一律改用
# `str.find` + `str.isspace()` 等**零转义**原语。
_WL_MARK = "worklist_add("
_WL_QUOTES = ('"', "'")


def _wl_kinds_in(text: str) -> list[str]:
    """提取 `worklist_add(<quote>kind<quote>` 中的 kind 字面量（零转义实现）。"""
    out: list[str] = []
    start = 0
    while True:
        i = text.find(_WL_MARK, start)
        if i < 0:
            return out
        j = i + len(_WL_MARK)
        while j < len(text) and text[j].isspace():  # 覆盖空格/制表/换行/CR
            j += 1
        if j < len(text) and text[j] in _WL_QUOTES:
            q = text[j]
            k = text.find(q, j + 1)
            if k > 0:
                cand = text[j + 1 : k]
                # 用标识符判定而非逐字符 islower()：kind 里可能含**数字**
                # （如 `rfn_clean_drift_c2` 的 `2` 不满足 islower() → 曾被漏匹配）
                if cand and cand.isidentifier() and cand == cand.lower():
                    out.append(cand)
                j = k
        start = j + 1


def _check_worklist() -> tuple[list[str], dict]:
    """J7：`WORKLIST_KIND` 与产生方**双向闭合**（v2 §3.14.3 判据 ②）。

    - 代码里出现的 `worklist_add("<kind>", …)` 字面量必须已登记（防"只写队列不登记"）；
    - 每个已登记 kind 必须有产生方（`_PENDING_PRODUCERS` 为 P2-5 待接清单，只披露）。
    """
    problems: list[str] = []
    detail: dict = {}
    from config.enums import WORKLIST_KIND

    used: dict[str, list[str]] = {}
    for dirpath, dirnames, filenames in os.walk(ROOT):
        # `tests` 必须排除：单测会**故意**写入未登记 kind（覆盖软校验负例）并调用
        # 待接的 kind（覆盖队列行为）——若纳入扫描，"产生方"判据会被测试夹具假命中
        # （实测：tests/test_governance_worklist.py 的 `some_new_kind` 曾使 J7 误报）。
        dirnames[:] = [
            d
            for d in dirnames
            if d
            not in (_SCAN_BASE_SKIP
                    | {"data", "reports", "graphify-out", "external", "backups",
                       ".ruff_cache", ".codebuddy", "retired", "tests"})
        ]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            fp = os.path.join(dirpath, fn)
            try:
                text = open(fp, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            for kind in _wl_kinds_in(text):
                rel = os.path.relpath(fp, ROOT).replace(os.sep, "/")
                used.setdefault(kind, []).append(rel)

    literal_kinds = set(used)
    unregistered = sorted(literal_kinds - WORKLIST_KIND)
    if unregistered:
        problems.append(
            f"J7: 代码写入未登记的 worklist kind {unregistered}"
            "（须先加入 config.enums.WORKLIST_KIND）"
        )
    missing = sorted(
        k for k in WORKLIST_KIND if k not in literal_kinds and k not in _PENDING_PRODUCERS
    )
    if missing:
        problems.append(
            f"J7: 已登记 kind 缺产生方 {missing}（或移入 _PENDING_PRODUCERS 并注明期次）"
        )
    pending_hit = sorted(k for k in _PENDING_PRODUCERS if k in literal_kinds)
    if pending_hit:
        problems.append(f"J7: {pending_hit} 已接入产生方，须从 _PENDING_PRODUCERS 移除")
    detail["worklist"] = {
        "declared": len(WORKLIST_KIND),
        "wired": sorted(literal_kinds & WORKLIST_KIND),
        "pending": sorted(_PENDING_PRODUCERS),
        "producers": {k: sorted(set(v)) for k, v in sorted(used.items())},
    }
    return problems, detail


def _check_corpus() -> tuple[list[str], dict]:
    """J8：语料分层自洽（v2 §3.12 / P2-3a，决策 D-9）。

    判据（每条都对应一个已发生的真实缺陷）：
      J8.1 `modules/**/data/corpus` 不得存在 —— 归属不当（挂"采集"模块下却服务 ipb 去重）
      J8.2 `_index.json` 每域在 `data/corpus/<domain>/` 有本体目录
      J8.3 **清单条数 == 本体文件数** —— 实测曾漂移 586 条（源侧含中间目录、本体已拍平 → N-14）
      J8.4 域内不得残留 `_ingest_manifest.json` —— 清单唯一源 = `reports/corpus/`（F2 防回归）
      J8.5 `hash_mode == "sha256"` 且 sha256 非空率 100% —— F1（历史 `--no-hash` 致空哈希）
    """
    problems: list[str] = []
    detail: dict = {"domains": {}, "index": os.path.join("reports", "corpus", "_index.json")}
    corpus = paths.CORPUS_DIR
    man_dir = os.path.join(paths.REPORTS_DIR, "corpus")
    idx_path = os.path.join(man_dir, "_index.json")

    # J8.1 旧落点不得残留
    for pkg_dir in (paths.MODULES_DIR,):
        for dirpath, dirnames, _fns in os.walk(pkg_dir):
            if "data" not in dirnames:
                continue
            legacy = os.path.join(dirpath, "data", "corpus")
            if os.path.isdir(legacy):
                rel = os.path.relpath(legacy, ROOT).replace(os.sep, "/")
                problems.append(f"J8.1: 旧语料落点仍存在 {rel}（本体应统一在 data/corpus/）")

    if not os.path.isdir(corpus):
        return problems, {**detail, "note": "data/corpus/ 尚未创建（P2-3a 未执行或未归集）"}
    if not os.path.exists(idx_path):
        problems.append("J8: 缺少 reports/corpus/_index.json（唯一可审计入口）")
        return problems, detail

    try:
        with open(idx_path, encoding="utf-8") as fh:
            idx = json.load(fh)
    except (OSError, ValueError) as e:
        problems.append(f"J8: _index.json 不可解析（{type(e).__name__}: {e}）")
        return problems, detail

    for dom in idx.get("domains") or []:
        name = str(dom.get("domain") or "")
        if not name:
            problems.append("J8: _index.json 存在无 domain 的条目")
            continue
        body = os.path.join(corpus, name)
        entry: dict = {"body_exists": os.path.isdir(body)}

        # J8.2
        if not entry["body_exists"]:
            problems.append(f"J8.2: 域 {name} 在 _index.json 中登记但本体目录不存在 {body}")
            detail["domains"][name] = entry
            continue

        # J8.4
        if os.path.exists(os.path.join(body, "_ingest_manifest.json")):
            problems.append(
                f"J8.4: 域 {name} 本体残留 _ingest_manifest.json（清单唯一源应为 reports/corpus/）"
            )

        # J8.3
        disk = 0
        for _dp, _dn, fns in os.walk(body):
            disk += len(fns)
        entry["files_on_disk"] = disk
        entry["declared"] = int(dom.get("file_count") or 0)
        if disk != entry["declared"]:
            problems.append(
                f"J8.3: 域 {name} 清单声明 {entry['declared']} 条 ≠ 本体实际 {disk} 个文件"
                "（清单与本体不自洽；可用 --resync-manifest 以本体为准重建）"
            )

        # J8.5
        mp = os.path.join(man_dir, f"{name}.manifest.json")
        if not os.path.exists(mp):
            problems.append(f"J8: 域 {name} 的清单文件不存在 {mp}")
            detail["domains"][name] = entry
            continue
        try:
            with open(mp, encoding="utf-8") as fh:
                man = json.load(fh)
        except (OSError, ValueError) as e:
            problems.append(f"J8: 清单不可解析 {name}（{type(e).__name__}: {e}）")
            detail["domains"][name] = entry
            continue
        files = man.get("files") or []
        empty = sum(1 for r in files if not str(r.get("sha256") or "").strip())
        entry["hash_mode"] = man.get("hash_mode", "")
        entry["sha256_empty"] = empty
        if man.get("hash_mode") != "sha256":
            problems.append(
                f"J8.5: 域 {name} hash_mode={man.get('hash_mode')!r}"
                "（应为 sha256；size_only 不可用于去重判定）"
            )
        if empty:
            problems.append(
                f"J8.5: 域 {name} 清单有 {empty} 条 sha256 为空"
                "（F1：历史 --no-hash 遗留；用 --backfill-hash 回填）"
            )
        detail["domains"][name] = entry

    detail["total_files"] = idx.get("total_files")
    return problems, detail


# --------------------------------------------------------------------------- #
# S. 调度事实源一致性（v2 §3.9 / §3.13.6，P2-2）
#    背景（v2 §3.13.1 M3）：手册定时表为**手抄**，与实测调度记录二义（01:00 vs 01:30、
#    06:30 vs 06:00–06:37）。现 `config/schedule.yaml` 为唯一源、手册由工具反向生成，
#    本判据断言两者一致 —— 手改手册即 FAIL。
# --------------------------------------------------------------------------- #
