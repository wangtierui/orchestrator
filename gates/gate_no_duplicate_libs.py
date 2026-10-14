# -*- coding: utf-8 -*-
"""
gates/gate_no_duplicate_libs — 重复工具/重名再定义扫描（专项三 / R20）

P1 实装。规则：
  1) 全仓 .py（排除 backups/data/cache/logs/reports/tests/refactor/retired/archive）中，
     若下列"应唯一实现"符号出现于 **非共享库文件**，即视为重复实现候选 → FAIL：
        atomic_write_text / atomic_write_json / atomic_write_csv_dict / sha256_file / fingerprint
  2) **W4-a（2026-10，§7.6.1）5 符号公开+私有统一豁免路径**：
     `norm_docno / norm_title / normalize_date / sha256_of / write_jsonl`（`_?` 双形态）——
     非共享库定义须带 `# norm-specialization: <理由>` 标记（在册特化）或属登记 SSOT
     （NON_SHARED_SSOT）；共享库内同符号多文件定义须全部带标记（防库内再分裂）。
     动因：私有名曾是规避通道（sha256_of/write_jsonl 私有副本各 2~3 处逃检），
     B2~B7 收敛后由本判据断后路。
  3) 非共享库文件中出现 sys.path.insert 且带盘符 → FAIL（与 gate_hardcoded_paths 联动，
     此处专防 modules/* 迁移时遗留跨仓插入）。
  4) msvcrt 自锁（应统一走 common_lib.fs_lock）。

P0→P1 语义切换：本 gate 自 P1 起 require_impl=True（见 gates/__init__.py ALL_GATES）。
"""

from __future__ import annotations

import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 构建产物目录亦排除（2026-09-13）：pip wheel / pip install . 会把整棵树复制到 build/lib，
# 若被扫描会报"符号重复定义"误报（实测）——这些目录 .gitignore 已忽略，非源码。
EXCLUDE_DIRS = {
    "backups",
    "data",
    "cache",
    "logs",
    "venv",
    ".venv",
    ".git",
    "__pycache__",
    "reports",
    "build",
    "dist",
    ".pytest_cache",
    # 2026-09-26（v2 §3.15.3 A1）：退役脚本隔离层不再参与"增量防漏"；
    # 其"不再被引用"由 gate_config_integrity 的 J3 判据保证。
    "retired",
    # v2 §3.10（P2-3）：归档层（可能含与在役文件同名的历史副本，不参与防漏）
    "archive",
    # W4-a（2026-10，B2~B7 收口）：第三方子项目 refactor/ —— 边界纪律「只读评估、
    # 不修改其任何文件、不拷贝其代码」（方案 §7.9）；其 lib/ 与 std_lib 的大面积重复
    # 是「重复换独立」的自觉选择，不纳入本仓 SSOT 判据。
    "refactor",
    # W4-a：tests/ 为验证层（私有 fixture helper 自治，不构成生产重复面；
    # 生产符号的重复由源码路径判负——tests 引用生产实现本身即被契约测试覆盖）。
    "tests",
}
# 本文件自身含 msvcrt.locking 字面量（检测正则定义，非使用）
EXCLUDE_FILES = {"gate_no_duplicate_libs.py"}
# 共享库为事实源，允许其定义规范符号（只允许一次）
SHARED_PREFIXES = (os.path.join("std_lib", "common_lib"), os.path.join("std_lib", "scraper_std"))
# 必须单点实现于共享库的符号（在各文件出现 def <sym> 即计数）。
# 注：不带下划线的公共原子写/指纹名须唯一；"_atomic_write" 等采集器本地私有实现不纳入
# （属模块内自包含封装，非跨仓规范符号；共享库公共名为 atomic_write_text 等）。
# W4-a（2026-10，§7.6.1）：norm_docno/norm_title 从此表移交 PAT_DUP_PRIMITIVE
# （公开+私有统一「须豁免标记」路径——在册特化如 pkulaw_cli.norm_docno 需要豁免通道，
# 旧规则对非共享库公开名「直接 FAIL」无豁免机制，已不适用）。
SINGLE_IMPL_SYMBOLS = (
    "atomic_write_text",
    "atomic_write_json",
    "atomic_write_csv_dict",
    "sha256_file",
    "fingerprint",
)
# 盘符 sys.path 插入
PAT_SYSPATH_DRIVE = re.compile(r"sys\.path\.(?:insert|append)\(\s*[0-9]*\s*,\s*[\"'][A-Za-z]:")
# msvcrt 自锁（应统一走 common_lib.fs_lock）
PAT_MSVCRT = re.compile(r"\bmsvcrt\.locking")
# A-10（2026-09-12）：私有副本扫描（_norm_docno/_norm_title）——原符号表仅查公开名 →
# 私有副本长期逃检。P3A 清零后：标准层副本一律 import std_lib/common_lib/norm（SSOT 分层）；
# 有意特化须加 `# norm-specialization: <理由>` 豁免标记——**未标注副本 = FAIL**（防增量）。
# W4-a（2026-10，§7.6.1 私有名模式堵漏）：扫描从 `_norm_docno|_norm_title` 扩展为
# **5 符号 × 公开/私有双形态**——`_?(norm_docno|norm_title|normalize_date|sha256_of|write_jsonl)`。
# 动因：B2~B7 收敛时发现私有名长期是规避通道（sha256_of/write_jsonl 各 2~3 处私有副本
# 逃检），公开名在共享库外亦一律纳入（须带标记或在册 SSOT 登记）。
PAT_DUP_PRIMITIVE = re.compile(
    r"^def\s+_?(norm_docno|norm_title|normalize_date|sha256_of|write_jsonl)\s*\(", re.M
)
MARK_TOKEN = "# norm-specialization"
# 经登记的非共享库 SSOT（B6：JSONL 原子写唯一实现，README:79 明载）—— 带 MARK_TOKEN 存活
NON_SHARED_SSOT = {"write_jsonl"}
# W4-a：共享库 SSOT 定义点登记（sym -> 规范实现文件相对路径）。SSOT 自身**免标记**
# （它就是事实源）；同符号在**其他**共享库文件再定义 = 分裂，须带标记（在册特化）。
# 与 norm.py docstring 的「特化豁免表」一一对应——两处登记必须同步（审计判据化）。
SHARED_SSOT_DEF = {
    "norm_docno": os.path.join("std_lib", "common_lib", "norm.py"),
    "norm_title": os.path.join("std_lib", "common_lib", "norm.py"),
    "normalize_date": os.path.join("std_lib", "scraper_std", "cleaner.py"),
}
# 标记上下文窗口（字符）：覆盖「多行中文标记注释」（实测 4 行 ≈ 300 字符）——
# A-10 原窗口 200 会截断长标记致在册特化误报（W4-a 实测两处）。
MARK_WINDOW = 500


def _walk_py():
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS and not d.endswith(".egg-info")]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            if fn in EXCLUDE_FILES:
                continue
            yield os.path.join(dirpath, fn)


def _rel(p: str) -> str:
    return os.path.relpath(p, ROOT)


def _is_shared(rel: str) -> bool:
    return rel.startswith(SHARED_PREFIXES)


def run():
    problems = []
    impl_hits: dict[str, list[str]] = {s: [] for s in SINGLE_IMPL_SYMBOLS}
    # W4-a：5 符号全部定义点（公开+私有）按「共享库 SSOT / 共享库特化 / 非共享库」三分
    dup_defs: dict[str, list[tuple[str, int, bool]]] = {}   # sym -> [(rel, line, marked)]
    for fp in _walk_py():
        rel = _rel(fp)
        try:
            text = open(fp, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for sym in SINGLE_IMPL_SYMBOLS:
            # 精确匹配 "def sym("
            for m in re.finditer(rf"^def\s+{re.escape(sym)}\s*\(", text, re.M):
                # 非共享库出现即违规候选
                if not _is_shared(rel):
                    impl_hits[sym].append(f"{rel}:{text[: m.start()].count(chr(10)) + 1}")
        # W4-a（§7.6.1）：5 符号公开/私有定义点全量登记（标记上下文前置 MARK_WINDOW 字符）
        for m in PAT_DUP_PRIMITIVE.finditer(text):
            line = text[: m.start()].count(chr(10)) + 1
            marked = MARK_TOKEN in text[max(0, m.start() - MARK_WINDOW) : m.start()]
            dup_defs.setdefault(m.group(1), []).append((rel, line, marked))
        if not _is_shared(rel):
            if PAT_SYSPATH_DRIVE.search(text):
                problems.append(f"{rel}: 含盘符 sys.path 插入（应经 paths/interfaces）")
            if PAT_MSVCRT.search(text):
                problems.append(f"{rel}: 直接使用 msvcrt.locking（应统一 common_lib.fs_lock）")
    for sym, locs in impl_hits.items():
        if locs:
            problems.append(f"符号 {sym} 在非共享库重复定义: {locs[:5]}")
    # W4-a 三分判定（防「私有名规避通道」与「共享库内部再分裂」双漏洞）：
    for sym, defs in sorted(dup_defs.items()):
        shared = [d for d in defs if _is_shared(d[0])]
        foreign = [d for d in defs if not _is_shared(d[0])]
        # ① 非共享库定义：须带豁免标记（在册特化）或为登记 SSOT（NON_SHARED_SSOT）
        for rel, line, marked in foreign:
            if not marked:
                kind = "登记 SSOT" if sym in NON_SHARED_SSOT else "在册特化"
                problems.append(
                    f"{kind}符号 {sym} 在非共享库无豁免标记（应 import SSOT 或加 "
                    f"`# norm-specialization: <理由>`）: {rel}:{line}"
                )
        # ② 共享库内部分裂：同符号多文件定义，**非 SSOT 登记文件**的副本须带标记
        #    （B2/B7 消灭的正是「共享库内 sha256_of/write_jsonl 各两处逐字重复」）
        ssot_file = SHARED_SSOT_DEF.get(sym)
        shared_files = {d[0] for d in shared}
        if len(shared_files) > 1:
            unmarked = [
                f"{rel}:{line}"
                for rel, line, marked in shared
                if not marked and rel != ssot_file
            ]
            if unmarked:
                problems.append(
                    f"符号 {sym} 在共享库内多文件定义且存在未标记副本（SSOT 唯一实现纪律；"
                    f"SSOT={ssot_file}）: 未标记 {unmarked}；全部 {sorted(shared_files)}"
                )
    exempt_detail = {
        sym: [f"{rel}:{line}" for rel, line, marked in defs if marked]
        for sym, defs in sorted(dup_defs.items())
    }
    return (not problems), {
        "problems": problems,
        "checked_symbols": SINGLE_IMPL_SYMBOLS,
        "dup_primitive_defs": {
            sym: [f"{rel}:{line}{'*' if marked else ''}" for rel, line, marked in defs]
            for sym, defs in sorted(dup_defs.items())
        },
        "dup_primitive_exempt": {k: v for k, v in exempt_detail.items() if v},
        "note": "5 符号（norm_docno/norm_title/normalize_date/sha256_of/write_jsonl，公开+私有）"
        "统一豁免路径：非共享库须带 `# norm-specialization` 标记或在册 SSOT；"
        "共享库内同符号多文件定义须全部带标记（* = 已标记）。",
    }
