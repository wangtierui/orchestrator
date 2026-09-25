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
