# -*- coding: utf-8 -*-
"""
tools/migrate_collectors_p3b.py — 五源"源特有抓取器"复制聚合 + import 适配（P3b，2026-09-08）

原则：
  - 从旧仓（只读）复制各源**特有抓取逻辑**到 modules/regulatory_scrapers/collectors/<src>/，
    保持同目录相对结构（nfra/pbc 有同目录 subprocess/互 import，原样保留文件名）。
  - 每个目标 .py 顶部注入"仓库根引导"（os.pardir 上溯到 orchestrator 根），替换旧仓根 sys.path 依赖，
    使 `std_lib` / `config` 顶级包可导入。
  - 确定性子串/正则替换（每源共用规则 + 特例），消除旧仓根路径推导与旧顶层别名。
  - 逐文件 py_compile 校验；失败项显式报告，不静默。

不复制：各源 scripts/downloader|parser|spider_main 薄壳、utils/alert_mail|db_client（共享，
由 std_lib 提供）、backups/、venv/、data 数据（P4 manifest 复制）。

用法：
  python tools/migrate_collectors_p3b.py          # 复制+适配+编译校验
  python tools/migrate_collectors_p3b.py --only gov   # 仅某源
"""
from __future__ import annotations

import argparse
import os
import py_compile
import re

# --------------------------------------------------------------------------- #
# 路径
# --------------------------------------------------------------------------- #
_OLD_ROOT = "D:/WorkBuddy"                      # 旧仓父目录（只读源）
_ORCH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # orchestrator 根
_COLLECTORS = os.path.join(_ORCH, "modules", "regulatory_scrapers", "collectors")

# 各源目录（旧仓 regulatory_scrapers 之下）
_SRC_DIRS = {
    "gov": os.path.join(_OLD_ROOT, "regulatory_scrapers", "gov_regulations_scraper"),
    "mof": os.path.join(_OLD_ROOT, "regulatory_scrapers", "mof_regulations_scraper"),
    "nfra": os.path.join(_OLD_ROOT, "regulatory_scrapers", "nfra_regulations_scraper"),
    "pbc": os.path.join(_OLD_ROOT, "regulatory_scrapers", "pbc_regulations_scraper"),
    "supp": os.path.join(_OLD_ROOT, "regulatory_scrapers", "supplementary_regulations_scraper"),
}

# 源内相对文件 → collectors/<src>/ 下同名复制（仅特有文件）
COPY_PLAN: dict[str, list[str]] = {
    "gov": ["scraper.py", "fetch_attachments_gov.py"],
    "mof": ["mof_law_scraper.py"],
    "nfra": ["nfra_regulations_scraper.py", "fetch_lists.py", "fill_details.py",
             "prefetch.py", "seed_cache.py", "validate_cache.py", "weekly_refresh.py",
             "fetch_attachments.py"],
    "pbc": ["pbc_law_scraper.py", "run_batched.py", "merge_scrape.py", "backfill_pdfs.py",
            "recover.py", "fix_doc.py", "serve.py"],
    "supp": ["scripts/ingest_supplements.py", "scripts/ingest_batch_supplements.py",
             "scripts/ocr_pdf.py", "scripts/extract_doc_text.py", "scripts/fix_raw_factual.py",
             "scripts/downloader.py", "scripts/parser.py", "scripts/sanitize_newlines.py",
             "utils/supp_mapper.py", "utils/supp_normalize.py"],
}

# --------------------------------------------------------------------------- #
# 文本适配
# --------------------------------------------------------------------------- #
# 每个目标文件在 docstring 后首个 import 前注入仓库根引导。
# 深度 = 目标文件相对 orchestrator 根的目录层级（collectors/<src>/x.py → 上溯 4 级）
_GUIDE_TMPL = (
    "\n"
    "# ---- P3b 仓库引导（migrate_collectors_p3b.py 注入）：使 std_lib/config 可导入 ----\n"
    "import os as _os\n"
    "import sys as _sys\n"
    "_GUIDE_ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "
    "{up}))\n"
    "if _GUIDE_ROOT not in _sys.path:\n"
    "    _sys.path.insert(0, _GUIDE_ROOT)\n"
    "del _GUIDE_ROOT, _os, _sys\n"
)

# 旧仓根路径推导常见形态 → 删除该行（由 _GUIDE_ROOT 承担根寻址）
_ROOT_DERIVE_RE = re.compile(
    r"^(?:_SCRAPERS_ROOT|_STD_LIB_ROOT|_REPO|_PROJECT_ROOT|ROOT|HERE)"
    r"\s*=\s*os\.path\.(?:dirname\(os\.path\.dirname|dirname\(os\.path\.abspath)"
    r"\(os\.path\.abspath\(__file__\)\).*$",
    re.M,
)
_SYSPATH_INSERT_RE = re.compile(
    r"^(?:if .* not in sys\.path:)?\s*\n?"
    r"sys\.path\.(?:insert|append)\(0,\s*(?:_SCRAPERS_ROOT|_STD_LIB_ROOT|_REPO|ROOT|HERE|"
    r"os\.path\.join\([^)]*)\)[^\n]*$",
    re.M,
)

# 通用替换（子串）
_COMMON_SUB = [
    # fs_lock 旧顶层 import → 新仓 std_lib.common_lib.fs_lock（允许行尾 # noqa 注释）
    (re.compile(r"^import fs_lock(?:\s*#.*)?$", re.M),
     "from std_lib.common_lib import fs_lock"),
    (re.compile(r"^from fs_lock import\s+([A-Za-z_][\w, ]*)(?:\s*#.*)?$", re.M),
     r"from std_lib.common_lib.fs_lock import \1"),
    # 旧顶层 scraper_std 别名 → std_lib.scraper_std（supp/utils 使用）
    (re.compile(r"(?<!std_lib\.)from scraper_std\.", ),
     "from std_lib.scraper_std."),
    (re.compile(r"(?<!std_lib\.)import scraper_std(?!\.)"),
     "import std_lib.scraper_std"),
    (re.compile(r"std_lib\.std_lib\.scraper_std"), "std_lib.scraper_std"),  # 防重复
    # OCR/文档解析引擎根注释无关紧要，跳过
]

# 每源额外替换（正则模式或子串）
_PER_SOURCE_SUB: dict[str, list] = {
    # gov：scraper.py 有 from fetch_attachments_gov import（同目录 → 保留），
    #      _SCRAPERS_ROOT fs_lock 引导由通用规则处理
    "gov": [],
    "mof": [],
    "nfra": [],
    "pbc": [],
    "supp": [],
}


def _find_insert_idx(lines: list[str]) -> int:
    """定位引导注入位置：docstring 结束之后 / from __future__ 之后、其余 import 之前。"""
    i = 0
    # 1) 跳过 shebang 行
    if lines and lines[0].lstrip().startswith("#!"):
        i = 1
    # 2) 跳过前导纯注释行与空行（# coding: ... 等）
    while i < len(lines):
        s = lines[i].strip()
        if not s or s.startswith("#"):
            i += 1
        else:
            break
    # 3) 跳过模块 docstring（首字符串字面量，可能单行或多行）
    if i < len(lines):
        s = lines[i].strip()
        if s.startswith('"""') or s.startswith("'''"):
            q = s[:3]
            # 单行 docstring："""..."""
            if q in s[3:] and s.rstrip().endswith(q):
                i += 1
            else:
                i += 1
                while i < len(lines) and q not in lines[i]:
                    i += 1
                i += 1  # 越过闭合行
    # 4) 跳过空行
    while i < len(lines) and not lines[i].strip():
        i += 1
    # 5) 若紧接着是 from __future__ 块 → 注入其之后
    if i < len(lines) and lines[i].lstrip().startswith("from __future__"):
        while i < len(lines) and (not lines[i].strip()
                                  or lines[i].lstrip().startswith("from __future__")):
            i += 1
        return i
    return i


def _inject_guide(text: str, depth: int) -> str:
    """在 docstring / from __future__ 之后注入仓库根引导。depth=上溯级数。"""
    up = ", ".join(["\"..\""] * depth)
    guide = _GUIDE_TMPL.format(up=up).rstrip("\n")
    lines = text.split("\n")
    idx = _find_insert_idx(lines)
    lines.insert(idx, guide)
    return "\n".join(lines)


def _apply_subs(text: str, src: str) -> str:
    for pat, repl in _COMMON_SUB:
        text = pat.sub(repl, text)
    for pat, repl in _PER_SOURCE_SUB.get(src, []):
        text = pat.sub(repl, text)
    # 清理可能产生的重复空行
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def migrate(src: str, plan: list[str]) -> dict:
    result = {"copied": [], "compiled_ok": [], "compile_fail": []}
    src_dir = _SRC_DIRS[src]
    dst_dir = os.path.join(_COLLECTORS, src)
    os.makedirs(dst_dir, exist_ok=True)
    # supp utils 需保留子目录
    for rel in plan:
        old = os.path.join(src_dir, rel)
        new_rel = os.path.relpath(rel, "scripts") if rel.startswith("scripts/") else rel
        # 目标子路径：supp 的 utils/ → collectors/supp/utils/
        if rel.startswith("utils/"):
            new_rel = rel
        elif rel.startswith("scripts/"):
            new_rel = os.path.basename(rel)
        else:
            new_rel = rel
        target = os.path.join(dst_dir, new_rel)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if not os.path.exists(old):
            result["compile_fail"].append(f"{rel}（源缺失 {old}）")
            continue
        try:
            text = open(old, encoding="utf-8").read()
        except Exception as e:  # pragma: no cover
            result["compile_fail"].append(f"{rel}（读失败 {e}）")
            continue
        text = _apply_subs(text, src)
        # 注入引导：目标相对 orchestrator 根的深度
        depth = len(os.path.relpath(target, _ORCH).split(os.sep)) - 1  # 文件本身不算
        text = _inject_guide(text, max(1, depth))
        # 注意：不删除文件内既有 dirname(__file__) 推导与 sys.path.insert（涉及 if/多行，
        # 激进正则可能破坏逻辑）。顶部 _GUIDE_ROOT 已把新仓根 insert 到 sys.path[0]，
        # 使 std_lib/config 解析优先于任何残留旧路径；残留行留待运行期观察/逐源精修。
        # fs_lock 顶层 import 已由 _COMMON_SUB 改为 std_lib.common_lib（见上）。
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(text)
        result["copied"].append(new_rel)
        try:
            py_compile.compile(target, doraise=True)
            result["compiled_ok"].append(new_rel)
        except Exception as e:  # noqa: BLE001
            result["compile_fail"].append(f"{new_rel}: {e}")
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="仅迁移某源（gov/mof/nfra/pbc/supp）")
    args = ap.parse_args()
    sources = [args.only] if args.only else list(COPY_PLAN)
    failed = []
    for src in sources:
        r = migrate(src, COPY_PLAN[src])
        print(f"[{src}] copied={len(r['copied'])} ok={len(r['compiled_ok'])} fail={len(r['compile_fail'])}")
        for f in r["compile_fail"]:
            print(f"    FAIL {f}")
        if r["compile_fail"]:
            failed.extend(r["compile_fail"])
    print("=" * 40)
    print("P3b 迁移完成。" if not failed else f"存在 {len(failed)} 个失败项，见上。")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
