# -*- coding: utf-8 -*-
"""gate_config_integrity — 配置与清单一致性（v2 §3.8 表 A 第 3 项，八类断言）

设计（v2《全链路重构方案·修订版》§3.8、§3.15.5）：
    把"多份清单各自硬编码"收敛为"唯一声明 + 门禁断言"。本门禁聚合以下判据：

    A. tools 清单（§3.15.5，J1–J6）
       J1  tools/*.py ∪ tools/retired/*.py 与 `tools/_manifest.json` **双向相等**（无遗漏、无幽灵）
       J2  category=retired ⟺ 路径以 `retired/` 开头（双向）
       J3  retired 项**零仓内引用**（.py/.yaml/.toml/.bat/.cfg/.ini；文档/清单自身除外）
       J4  有 retired 项时必须存在 `tools/retired/README.md` 且含退役登记（日期）
       J5  仓根 `.py` 文件 ⊆ `root_py_allowlist`（根级脚本必须落位或登记）
       J6  仓根"未被 git 跟踪且未被忽略"的文件 → **报告项**（不阻断；P2-7 转严格）

    B. 结构清单（v2 §3.1.1）
       B1  `config.constants.assert_registry_consistent()`（键/包名唯一、阶段编号唯一）
       B2  5 处派生点与 `MODULE_PKGS` 一致：
           paths.module_dir / gate_no_cross_module_import.MODULES+PKG_OWNER /
           gate_flat_layout 遍历表 / tools._manifest（按 category 无需模块）/
           pyproject ruff per-file-ignores 覆盖全部模块

    C. 退出码（v2 §3.6 X1）
       C1  `config.exitcodes.ExitCode` 可导入且别名关系稳定（0/1/2/3/4 语义不变）

    D. 未就绪项（v2 规划中，尚未落地者**报告不阻断**）
       D1 `config/triggers.yaml`（P2-1）D2 `config/schedule.yaml`（P2-2）
       D3 `governance.db.worklist` 表（P1-6）D4 `contract_manifest.json` 一致性（归 gate_contract）

    过渡策略（v2 D-8）：D 组为"待落地"，仅出现在 detail.pending 中，不影响 PASS/FAIL。
"""
from __future__ import annotations

import json
import os
import re
import subprocess

import paths

ROOT = paths.ROOT
_TOOLS = paths.TOOLS_DIR
_MANIFEST = os.path.join(_TOOLS, "_manifest.json")
_RETIRED_DIR = "retired"
_RETIRED_README = os.path.join(_TOOLS, _RETIRED_DIR, "README.md")

# J3：引用扫描的扩展名（文档 .md 允许提及，但须带"已退役"字样，由人工维护）
_REF_EXTS = (".py", ".yaml", ".yml", ".toml", ".bat", ".cfg", ".ini", ".ps1")
_REF_SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", "tools"}  # tools 由本门禁自身与 README 覆盖


def _load_manifest() -> dict:
    with open(_MANIFEST, encoding="utf-8") as fh:
        return json.load(fh)


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
        dirnames[:] = [d for d in dirnames
                       if d not in _REF_SKIP_DIRS and d not in {"data", ".codebuddy"}]
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
        problems.append(f"J1: tools/ 下未登记脚本 {missing}（新增脚本必须写入 tools/_manifest.json）")
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
                f"（retired ⟺ tools/retired/，双向）")
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
            code_hit = any(pat.search(ln) and not ln.lstrip().startswith("#")
                           for ln in text.splitlines())
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
    actual_root = {fn for fn in os.listdir(ROOT)
                   if fn.endswith(".py") and os.path.isfile(os.path.join(ROOT, fn))}
    extra = sorted(actual_root - allow)
    if extra:
        problems.append(f"J5: 仓根存在未登记 .py {extra}（须落位/删除，或显式登记进 root_py_allowlist）")
    stale = sorted(allow - actual_root)
    if stale:
        warnings.append(f"J5: 白名单条目已不存在 {stale}（建议收缩）")
    detail["root_py"] = sorted(actual_root)

    # J6 报告项：仓根未跟踪且未忽略的文件
    try:
        r = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all", "--", "."],
                           cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=30)
        untracked = [ln[3:].strip() for ln in (r.stdout or "").splitlines()
                     if ln.startswith("??") and "/" not in ln[3:].strip()]
        if untracked:
            warnings.append(f"J6: 仓根存在未跟踪且未忽略的文件 {sorted(untracked)}"
                            "（P2-7 将转严格：入库/忽略/删除三选一）")
    except (OSError, subprocess.SubprocessError) as e:  # noqa: BLE001
        warnings.append(f"J6: 未跟踪文件检查跳过（{type(e).__name__}）")

    return problems, detail, warnings


# P2-5 待接的产生方（v2 §3.14.3 已登记 kind，但产生方尚未实装）——只披露、不阻断。
# 接入后须同步删除本集合（gate 会提示）。
_PENDING_PRODUCERS = {
    "rfn_theme_uncertain",    # D1 tools/rfn_backlog.py（v2 §3.14.5 归 P2-5）
    "internal_unaligned",     # D2 internal_policy_base/align.py（D2 归 P2-5）
    "relevance_boundary",     # D6 filter_clean_relevance.py（relevance/ 当前"有产出无消费"）
    "corpus_needs_review",    # §3.12.6 tools/inbox_scan.py（P2-3b）
    "ingest_quota_blocked",   # 配额阻断显式化（随 P2-1 触发链）
}

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
        while j < len(text) and text[j].isspace():   # 覆盖空格/制表/换行/CR
            j += 1
        if j < len(text) and text[j] in _WL_QUOTES:
            q = text[j]
            k = text.find(q, j + 1)
            if k > 0:
                cand = text[j + 1:k]
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
    from config.enums import WORKLIST_KIND  # noqa: PLC0415

    used: dict[str, list[str]] = {}
    for dirpath, dirnames, filenames in os.walk(ROOT):
        # `tests` 必须排除：单测会**故意**写入未登记 kind（覆盖软校验负例）并调用
        # 待接的 kind（覆盖队列行为）——若纳入扫描，"产生方"判据会被测试夹具假命中
        # （实测：tests/test_governance_worklist.py 的 `some_new_kind` 曾使 J7 误报）。
        dirnames[:] = [d for d in dirnames
                       if d not in {".git", "__pycache__", ".pytest_cache", "data",
                                    "reports", "graphify-out", "external", "backups",
                                    ".ruff_cache", ".codebuddy", "retired", "tests"}]
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
            "（须先加入 config.enums.WORKLIST_KIND）")
    missing = sorted(k for k in WORKLIST_KIND
                     if k not in literal_kinds and k not in _PENDING_PRODUCERS)
    if missing:
        problems.append(f"J7: 已登记 kind 缺产生方 {missing}（或移入 _PENDING_PRODUCERS 并注明期次）")
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
            problems.append(f"J8.4: 域 {name} 本体残留 _ingest_manifest.json"
                            "（清单唯一源应为 reports/corpus/）")

        # J8.3
        disk = 0
        for _dp, _dn, fns in os.walk(body):
            disk += len(fns)
        entry["files_on_disk"] = disk
        entry["declared"] = int(dom.get("file_count") or 0)
        if disk != entry["declared"]:
            problems.append(
                f"J8.3: 域 {name} 清单声明 {entry['declared']} 条 ≠ 本体实际 {disk} 个文件"
                "（清单与本体不自洽；可用 --resync-manifest 以本体为准重建）")

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
            problems.append(f"J8.5: 域 {name} hash_mode={man.get('hash_mode')!r}"
                            "（应为 sha256；size_only 不可用于去重判定）")
        if empty:
            problems.append(f"J8.5: 域 {name} 清单有 {empty} 条 sha256 为空"
                            "（F1：历史 --no-hash 遗留；用 --backfill-hash 回填）")
        detail["domains"][name] = entry

    detail["total_files"] = idx.get("total_files")
    return problems, detail


def _check_constants() -> tuple[list[str], dict]:
    problems: list[str] = []
    detail: dict = {}
    # 引导：本模块顶层 `import paths` 已要求仓根在 sys.path（由 cli.py/GatesRunner 保证），
    # 不再自行注入（P0-6 纪律：注入只经 bootstrap.py）。

    from config import constants as C  # noqa: PLC0415

    try:
        C.assert_registry_consistent()
    except AssertionError as e:  # noqa: BLE001
        problems.append(f"B1: config.constants 自检失败：{e}")

    # B2 派生点一致性
    import paths as _p  # noqa: PLC0415

    try:
        for pkg in C.MODULE_PKGS:
            assert os.path.isdir(_p.module_dir(pkg)), pkg
    except AssertionError as e:  # noqa: BLE001
        problems.append(f"B2: paths.module_dir 无法解析模块 {e}")

    from gates import gate_flat_layout, gate_no_cross_module_import  # noqa: PLC0415

    if set(gate_no_cross_module_import.MODULES) != set(C.MODULE_PKGS):
        problems.append(
            "B2: gate_no_cross_module_import.MODULES 与 MODULE_PKGS 不一致："
            f"{sorted(gate_no_cross_module_import.MODULES)} vs {sorted(C.MODULE_PKGS)}")

    flat_mods = {"regulatory_scrapers", "regulatory_classifier",
                 "internal_policy_base", "internal_policy_drafter"}   # 见 gate_flat_layout 遍历表
    if not flat_mods <= set(C.MODULE_PKGS):
        problems.append(f"B2: gate_flat_layout 遍历的模块未全部登记：{sorted(flat_mods - set(C.MODULE_PKGS))}")
    if set(gate_flat_layout.ALLOWED_DATA_SUBDIRS) - set(C.MODULE_PKGS):
        problems.append("B2: gate_flat_layout.ALLOWED_DATA_SUBDIRS 含未登记模块键")

    for m in C.MODULE_SPECS:      # PKG_OWNER 应覆盖全部模块包
        if m.pkg not in gate_no_cross_module_import.PKG_OWNER:
            problems.append(f"B2: PKG_OWNER 缺模块 {m.pkg}")
    detail["modules"] = list(C.MODULE_PKGS)

    # C1 退出码
    from config.exitcodes import ExitCode  # noqa: PLC0415

    assert int(ExitCode.OK) == 0 and int(ExitCode.FAIL) == 1 and int(ExitCode.DATA) == 2
    assert int(ExitCode.ENV) == 3 and int(ExitCode.NOT_SOURCE_TREE) == 4
    detail["exitcodes"] = {e.name: int(e) for e in ExitCode}

    # D 组：待落地项（报告不阻断）
    pending = []
    for rel in ("config/triggers.yaml", "config/schedule.yaml"):
        if not os.path.exists(os.path.join(ROOT, rel)):
            pending.append(rel)
    detail["pending"] = pending
    return problems, detail


def run() -> tuple[bool, dict]:
    if not os.path.exists(_MANIFEST):
        return False, {"error": f"缺少脚本清单 {_MANIFEST}（v2 §3.15.5 J1）"}

    problems: list[str] = []
    warnings: list[str] = []
    detail: dict = {}

    p1, d1, w1 = _check_tools(_load_manifest())
    problems += p1
    detail.update(d1)
    warnings += w1

    p2, d2 = _check_constants()
    problems += p2
    detail.update(d2)

    p3, d3 = _check_worklist()
    problems += p3
    detail.update(d3)

    p4, d4 = _check_corpus()
    problems += p4
    detail.update(d4)

    if warnings:
        detail["warnings"] = warnings
    if problems:
        detail["problems"] = problems
    return (not problems), detail


if __name__ == "__main__":
    passed, detail = run()
    print("[config_integrity]", "PASS" if passed else "FAIL",
          json.dumps(detail, ensure_ascii=False, indent=1))
    raise SystemExit(0 if passed else 1)
