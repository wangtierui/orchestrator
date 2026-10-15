# -*- coding: utf-8 -*-
"""gates.gate_config_integrity._manifest — J 族判据（W4-c 五拆）。

tools 清单一致性（J1-J6）/ worklist kind 双向闭合（J7）/ corpus 索引一致性（J8）/
**modules 脚本归属（J9，W4 §7.2 A 类）**。
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
# J9. modules 脚本归属（W4 §7.2 A 类：零引用脚本 37 → 0 的门禁化）
#
# 背景（D-10 / R-8）：tools/ 的 manifest 机制（J1–J6）不覆盖 modules/**——
# 21 个 modules 脚本零引用却无登记机制（supp_ingest 更以「纸面 collector」
# 形态在 sources.yaml 登记 13 天零执行）。本判据把同构机制扩展到 modules：
#
#   三态归属（每个 modules/*.py 必属其一）：
#     · chain       —— 被生产消费（编排器 argv / 接口 import / 触发器 argv / 配置点号路径）；
#     · independent —— modules/_manifest.json 登记（category=independent + independent_why）；
#     · retired     —— 位于 <module>/retired/（清单登记 + 零可执行引用 + README 备案）。
#
#   口径说明：
#     · `__init__.py`（包标记）不参与——包的存在即归属，其消费方 import 子模块；
#     · 引用判定只认**可执行形态**（import 语句 / argv 路径），注释与文档中的历史
#       提及只披露不阻断（与 J3 同构）；
#     · tests/ 不计生产消费（单测读源码不构成生产归属——supp_parser 教训）；
#     · 文本只加载一次（_load_prod_texts），J9.4/J9.6 共用（防逐文件重复读盘）。
# --------------------------------------------------------------------------- #
_MODULES_MANIFEST = os.path.join(paths.MODULES_DIR, "_manifest.json")
_MODULES_DIR = paths.MODULES_DIR
# retired/ 的 README 备案（J4 同构：存在 + 含退役日期）
_MODULES_RETIRED_READMES = (
    os.path.join(_MODULES_DIR, "regulatory_scrapers", "retired", "README.md"),
    os.path.join(_MODULES_DIR, "regulatory_classifier", "retired", "README.md"),
)


def _modules_py() -> dict[str, str]:
    """modules/ 全部 .py（排除 retired/ 与 __pycache__ 与包标记 __init__.py）→ 模块标识。

    ⚠️ 返回的 rel 路径相对 **modules/**（与 modules/_manifest.json 的 file 字段同基准——
    曾误用仓根基准致 independent 登记全部失配）。
    """
    out: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(_MODULES_DIR):
        dirnames[:] = [d for d in dirnames if d not in {"__pycache__", "retired"}]
        for fn in filenames:
            if not fn.endswith(".py") or fn == "__init__.py":
                continue
            fp = os.path.join(dirpath, fn)
            rel = os.path.relpath(fp, _MODULES_DIR).replace("\\", "/")
            out[rel] = os.path.splitext(fn)[0]
    return out


def _load_prod_texts() -> list[tuple[str, str]]:
    """生产引用扫描范围：仓内 .py + yaml/toml 等（_REF_EXTS；**不含 .json**——
    modules/ 下的 index.json 等是**派生产物**（gitignore），其路径字符串不是消费）。

    排除：tests/（单测不构成生产归属）、retired/（退役层）、reports/（输出层——
    含 _snap 快照与机器台账）、graphify-out/external/refactor/（派生与第三方，
    其路径字符串是索引不是消费）、data/ 与各缓存。清单自身也排除（声明非消费）。
    ⚠️ tools/ **不排除**——编排器（run_production_refresh）是 modules 脚本的
    头号消费方（argv 形态），J3 跳 tools 的口径不适用 J9。文本一次性加载。
    """
    skip = _SCAN_BASE_SKIP | {
        "tests",
        "retired",
        "reports",
        "graphify-out",
        "external",
        "refactor",
        "node_modules",
        ".venv",
        "venv",
        "backups",
        "archive",
        ".ruff_cache",
        "data",
        ".codebuddy",
    }
    out: list[tuple[str, str]] = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in skip]
        for fn in filenames:
            if not fn.endswith(_REF_EXTS):
                continue
            fp = os.path.join(dirpath, fn)
            if os.path.abspath(fp) == os.path.abspath(_MODULES_MANIFEST):
                continue
            try:
                out.append((os.path.relpath(fp, ROOT).replace("\\", "/"),
                            open(fp, encoding="utf-8", errors="replace").read()))
            except OSError:
                continue
    return out


def _is_exec_ref(stem: str, line: str) -> bool:
    """可执行引用判定（零转义原语构造——编辑链路会把正则反斜杠二次转义，故不用正则）。

    四支形态：
      ① argv：`"<stem>.py"` / 全相对路径尾段（triggers.yaml 的 argv 清单）
      ② `import <stem>` / `import a.b.<stem>`
      ③ `from a.b import <stem>`（被导入名在 import 子句）
      ④ `from a.b.<stem> import …`（模块路径在 from 子句）
    """
    s = line.strip()
    if s.startswith("#"):
        return False
    # ① argv 形态：行内含 `<stem>.py` 且紧随引号（防 "xx_stem.py" 前缀误配——stem
    #    前的字符必须是路径分隔或引号，由 endswith 检查 + 调用方按行扫描保证）。
    if (stem + ".py") in s and (stem + '.py"' in s or stem + ".py'" in s):
        return True
    # ②③ import 子句 token
    i = s.find("import ")
    if i < 0:
        return False
    head = s[:i].strip()
    tail = s[i + 7:].split(" as ")[0].split("#")[0]
    for tok in tail.replace(",", " ").split():
        if tok == stem or tok.endswith("." + stem):
            return True
    # ④ from 子句模块路径（head 形如 `from a.b.<stem>`；兼容 `x; from a.b` 罕见形态）
    if head.endswith("from") or head.startswith("from") or " from " in head:
        for tok in head.replace("import", " ").split():
            tok = tok.strip("();,=")
            if tok == stem or tok.endswith("." + stem):
                return True
    return False


def _check_modules_manifest() -> tuple[list[str], dict, list[str]]:
    """J9：modules 脚本三态归属闭合。"""
    problems: list[str] = []
    warnings: list[str] = []
    detail: dict = {}

    if not os.path.exists(_MODULES_MANIFEST):
        problems.append("J9: 缺少 modules/_manifest.json（W4 §7.2 A 类归属事实源）")
        return problems, detail, warnings
    try:
        with open(_MODULES_MANIFEST, encoding="utf-8") as fh:
            man = json.load(fh)
    except (OSError, ValueError) as e:
        problems.append(f"J9: modules/_manifest.json 不可解析（{type(e).__name__}: {e}）")
        return problems, detail, warnings

    entries = man.get("entries") or []
    declared = {e.get("file", "") for e in entries}
    by_file = {e.get("file", ""): e for e in entries}

    # J9.1 清单条目必须存在（幽灵条目）。⚠️ 清单路径相对 **modules/**（与 tools
    # 清单相对 tools/ 同构），存在性判定须 join _MODULES_DIR（曾误 join ROOT 致全量假 ghost）。
    ghosts = sorted(f for f in declared if not os.path.exists(os.path.join(_MODULES_DIR, f)))
    if ghosts:
        problems.append(f"J9.1: modules/_manifest.json 幽灵条目 {ghosts}（文件不存在）")

    # J9.2 retired 项必须位于 <module>/retired/ 且 category=retired（双向）
    for f, entry in sorted(by_file.items()):
        is_retired_path = "/retired/" in f
        is_retired_cat = entry.get("category") == "retired"
        if is_retired_cat != is_retired_path:
            problems.append(
                f"J9.2: {f} 的 category={entry.get('category')!r} 与路径不一致"
                "（retired ⟺ <module>/retired/，双向）"
            )
    retired_files = sorted(f for f in declared if "/retired/" in f)
    detail["modules_retired_count"] = len(retired_files)

    # J9.3 retired/ 实际文件必须全部登记（漏登记即逃逸引用扫描）。
    # 磁盘路径与清单同基准：相对 **modules/**。
    on_disk_retired: list[str] = []
    for dirpath, _dirnames, filenames in os.walk(_MODULES_DIR):
        if os.path.basename(dirpath) != "retired":
            continue
        for fn in filenames:
            if fn.endswith(".py"):
                on_disk_retired.append(
                    os.path.relpath(os.path.join(dirpath, fn), _MODULES_DIR).replace("\\", "/")
                )
    unregistered = sorted(set(on_disk_retired) - declared)
    if unregistered:
        problems.append(
            f"J9.3: retired/ 存在未登记脚本 {unregistered}"
            "（移入 retired/ 必须同步登记 modules/_manifest.json）"
        )

    texts = _load_prod_texts()

    # J9.4 retired 项零**可执行**引用（J3 同构；注释行提及只披露）
    refs: dict[str, list[str]] = {}
    comment_hits: dict[str, list[str]] = {}
    for f in retired_files:
        stem = os.path.splitext(os.path.basename(f))[0]
        if not stem:
            continue
        for rel, text in texts:
            if stem not in text:
                continue
            code_hit = any(_is_exec_ref(stem, ln) for ln in text.splitlines())
            if code_hit:
                refs.setdefault(f, []).append(rel)
            elif any(stem in ln for ln in text.splitlines()):
                comment_hits.setdefault(f, []).append(rel)
    for f, locs in refs.items():
        problems.append(f"J9.4: 已退役脚本仍被**可执行引用** {f} <- {sorted(set(locs))[:5]}")
    if comment_hits:
        detail["retired_comment_mentions"] = {k: len(set(v)) for k, v in comment_hits.items()}

    # J9.5 retired README 备案（存在 + 含退役日期；仅当该模块确有 retired 条目时要求）
    for rm in _MODULES_RETIRED_READMES:
        rel_dir = os.path.relpath(os.path.dirname(rm), ROOT).replace("\\", "/")
        if not any(f.startswith(rel_dir + "/") for f in retired_files):
            continue
        if not os.path.exists(rm):
            problems.append(f"J9.5: 存在 retired 项但缺少 {os.path.relpath(rm, ROOT)}")
        else:
            txt = open(rm, encoding="utf-8", errors="replace").read()
            if not re.search(r"20\d{2}-\d{2}-\d{2}", txt):
                problems.append(
                    f"J9.5: {os.path.relpath(rm, ROOT)} 未登记退役日期（需 原因+日期+替代物）"
                )

    # J9.6 无归属清零：每个 modules .py = chain（生产引用）∨ independent（登记）
    independent_files = {f for f, e in by_file.items() if e.get("category") == "independent"}
    unowned: list[str] = []
    for rel, stem in sorted(_modules_py().items()):
        if rel in independent_files:
            if not str(by_file[rel].get("independent_why") or "").strip():
                problems.append(f"J9.6: independent 登记缺 independent_why {rel}")
            continue
        self_rel = "modules/" + rel  # 自指排除（文本清单是仓根基准）
        for relf, text in texts:
            if relf == self_rel or stem not in text:
                continue
            if any(_is_exec_ref(stem, ln) for ln in text.splitlines()):
                break
        else:
            unowned.append(rel)
    if unowned:
        problems.append(
            f"J9.6: modules/ 存在无归属脚本 {unowned}"
            "（三选一：接入生产消费 / 登记 modules/_manifest.json independent / 移入 retired/）"
        )
    detail["modules_unowned"] = unowned
    detail["modules_independent"] = sorted(independent_files)
    return problems, detail, warnings


# --------------------------------------------------------------------------- #
# J10. README 门禁计数 ≡ ALL_GATES（W5，2026-10-15）
#
# 背景：`gates/__init__.py` 的纪律写明「门禁**数量以 ALL_GATES 为准，禁止文本写死**」，
# 但 README 有 8 处手写「29 道门禁」且**无任何机器校验** ⇒ 每新增一道门禁都要人肉同步，
# 漏改即文档漂移（读者按过期数字判断覆盖率）。本判据把「文档声明数」与「注册表长度」
# 双向对齐：文档里出现的 `N 道门禁` 必须等于 len(ALL_GATES)。
# 实现形态用零转义原语构造（正则在本仓编辑链路上会被二次转义，见 W4 教训）。
# --------------------------------------------------------------------------- #
_README = os.path.join(ROOT, "README.md")


def _check_readme_gate_count() -> tuple[list[str], dict]:
    """J10：README 中所有「N 道门禁」声明 ≡ len(ALL_GATES)。"""
    problems: list[str] = []
    detail: dict = {}
    if not os.path.exists(_README):
        return ["J10: 缺少 README.md"], detail
    from gates import ALL_GATES

    n = len(ALL_GATES)
    detail["all_gates"] = n
    text = open(_README, encoding="utf-8", errors="replace").read()
    found: dict[int, int] = {}
    marker = "道门禁"
    for ln in text.splitlines():
        # 归一化：去掉 markdown 粗体/反引号与空白，使 `**29 道**门禁` ≡ `29道门禁`
        norm = ln.replace("*", "").replace("`", "").replace(" ", "")
        pos = 0
        while True:
            i = norm.find(marker, pos)
            if i < 0:
                break
            j = i
            while j > 0 and norm[j - 1].isdigit():
                j -= 1
            if j < i:
                found[int(norm[j:i])] = found.get(int(norm[j:i]), 0) + 1
            pos = i + len(marker)
    detail["readme_counts"] = dict(sorted(found.items()))
    wrong = sorted(k for k in found if k != n)
    if wrong:
        problems.append(
            f"J10: README 声明的门禁数 {wrong} ≠ ALL_GATES 实际 {n} 道"
            "（新增/删除门禁后须同步 README；或改用不写死数字的表述）")
    if not found:
        detail["note"] = "README 未出现「N 道门禁」表述（可接受：不写死数字）"
    return problems, detail


# --------------------------------------------------------------------------- #
# S. 调度事实源一致性（v2 §3.9 / §3.13.6，P2-2）
#    背景（v2 §3.13.1 M3）：手册定时表为**手抄**，与实测调度记录二义（01:00 vs 01:30、
#    06:30 vs 06:00–06:37）。现 `config/schedule.yaml` 为唯一源、手册由工具反向生成，
#    本判据断言两者一致 —— 手改手册即 FAIL。
# --------------------------------------------------------------------------- #


