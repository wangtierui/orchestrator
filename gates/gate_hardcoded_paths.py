# -*- coding: utf-8 -*-
"""
gates/gate_hardcoded_paths — 盘符 / 跨仓 sys.path / 非代码文件硬编码路径扫描（R4 / 专项三收口）

扫描本仓，发现以下任一模式即 FAIL：
  1) 盘符字面量：D:/ D:\\ d:/ d:\\ C:/ C:\\（URL 协议 http(s):// 不命中——见 PAT_DRIVE 负向前瞻）
  2) sys.path.insert / sys.path.append 指向旧仓绝对路径（跨仓导入行为；仅对 .py 有意义）
  3) OCR 旧硬编码：C:\\Program Files\\Tesseract（R17）

**扫描范围（2026-09-13 扩面，修盲区）**：
  原实现只遍历 `*.py`，导致**非代码文件中的盘符字面量完全不受检**。实证后果：
  `modules/regulatory_scrapers/clean_index/index.json` 内嵌 31 处本机绝对路径、
  `data_migration_manifest.json` 97 处——两者长期"门禁全绿"却异机不可用/误导恢复
  （见 `reports/克隆可移植性检视报告_20260913.md` · CP-B02/CP-D02/CP-D03）。
  现扩至 SCAN_EXTS（代码 + 文档 + 配置），使"文档把本机路径当环境事实"同样被拦截。

排除（三类，均非"入库的运行时源码"）：
  - EXCLUDE_DIRS：备份/数据/缓存/日志/虚拟环境/git/报告/发布件/本地环境目录（external、tessdata、
    .codebuddy 等，本就 gitignore）——避免扫描第三方 junction 源码且避免误报；
  - EXCLUDE_RELPATHS：**派生产物**（运行时按当前机器生成的索引/报告），其内容天然含绝对路径，
    异机应由生成器重建，不构成"硬编码违规"，但也不得入库传播；
  - EXCLUDE_FILES：.py 豁免（paths.py 定义 ROOT；一次性迁移工具以旧仓为复制源）。

输出 detail 同时给出 总命中 / 代码命中 / 非代码命中，避免"非代码命中"再次被忽略。
"""
from __future__ import annotations

import os
import re

# 盘符字面量：排除 URL 协议（http://、https:// 等前有字母/冒号）与十六进制/带冒号符号场景。
# 负向前瞻 (?<![A-Za-z:]) 使 "p:/"（http）/"s:/"（https）不命中，而 "D:/"、'C:\\' 正常命中。
PAT_DRIVE = re.compile(r"(?<![A-Za-z:])[A-Za-z]:[/\\]")
# sys.path 场景的跨仓引用（仅对 .py 生效）
PAT_SYSPATH_LEGACY = re.compile(r"sys\.path\.(?:insert|append)\([^)]*(?:regulatory_scrapers|regulatory_classifier|internal_policy_drafter|internal_policy_base)")
# OCR 旧硬编码
PAT_TESS = re.compile(r"Program Files[/\\]Tesseract", re.IGNORECASE)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo root
EXCLUDE_DIRS = {
    "backups", "data", "cache", "logs", "venv", ".venv", ".git", "__pycache__",
    "reports", "published", ".pytest_cache", "node_modules",
    # 构建产物（.gitignore 已忽略；pip wheel / pip install . 会整树复制到 build/lib）
    "build", "dist",
    # 本地环境/agent 状态目录（均不入 git；external 为 PaddleOCR/Tesseract 第三方 junction）
    "external", "tessdata", ".codebuddy",
}
# 2026-09-13 扩面：代码 + 文档 + 配置
SCAN_EXTS = (".py", ".json", ".yaml", ".yml", ".toml", ".cfg", ".ini", ".md", ".txt", ".mermaid")
# 派生产物（不入库，内容含运行时绝对路径）：由生成器在目标机重建，不属"硬编码违规"
EXCLUDE_RELPATHS = {
    "modules/regulatory_scrapers/clean_index/index.json",   # clean_index 派生索引
    "modules/regulatory_classifier/recall_audit/output",    # recall 四门禁产物目录
}

# 定义/注释含"盘符"字样者跳过：本文件、paths.py（ROOT 定义）；tools 显式 --root 默认值豁免在下方处理。
# 一次性迁移工具豁免（仅引用旧仓只读源，非运行时业务代码，运行时仅开发期手工执行）：
#   - migrate_collectors_p3b.py：定义旧仓父目录作复制源；
#   - build_migration_manifest.py：--root 默认指向旧仓父目录（可被参数覆盖）。
EXCLUDE_FILES = {"paths.py", "gate_hardcoded_paths.py",
                 "migrate_collectors_p3b.py", "build_migration_manifest.py"}

# tools/* 默认参数中的演示路径白名单（如 build_migration_manifest --root default）
ALLOW_SUBSTR = ("--root", "--out", "default=")


def _is_allowed_line(line: str) -> bool:
    """允许行：tools CLI 默认参数演示路径（非业务硬编码）。"""
    return any(a in line for a in ALLOW_SUBSTR)


def _is_repo_relative_syspath(line: str) -> bool:
    """同仓相对 sys.path 引导（paths.ROOT / os.path.join(同仓) 且无盘符）——R4 语义合法，
    仅拦截跨仓盘符绝对引用（旧仓 sys.path）。"""
    if not PAT_DRIVE.search(line):
        if "paths.ROOT" in line or "os.path.join" in line and "sys.path" in line:
            return True
        # 相对插入：sys.path.insert(0, "modules/...") / os.path.join(同仓, "modules", ...)
        if "sys.path" in line and "os.path.dirname(os.path.abspath(__file__))" in line:
            return True
    return False


def _rel_posix(fp: str) -> str:
    return os.path.relpath(fp, ROOT).replace("\\", "/")


def _excluded_rel(rel: str) -> bool:
    """派生产物排除：精确文件或目录前缀。"""
    return any(rel == p or rel.startswith(p + "/") for p in EXCLUDE_RELPATHS)


def _walk():
    """产出 (绝对路径, 仓库相对 POSIX 路径, 小写扩展名)。"""
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames
                       if d not in EXCLUDE_DIRS and not d.endswith(".egg-info")]
        for fn in filenames:
            ext = os.path.splitext(fn)[1].lower()
            if ext not in SCAN_EXTS:
                continue
            if ext == ".py" and fn in EXCLUDE_FILES:
                continue
            fp = os.path.join(dirpath, fn)
            rel = _rel_posix(fp)
            if _excluded_rel(rel):
                continue
            yield fp, rel, ext


def run():
    findings = []
    for fp, rel, ext in _walk():
        is_py = ext == ".py"
        try:
            with open(fp, encoding="utf-8", errors="replace") as fh:
                for lineno, line in enumerate(fh, 1):
                    # 豁免仅对 .py 生效（文档/配置中的演示参数不应整体放行）
                    if is_py and (_is_allowed_line(line) or _is_repo_relative_syspath(line)):
                        continue
                    hit = (PAT_DRIVE.search(line) or PAT_TESS.search(line)
                           or (is_py and PAT_SYSPATH_LEGACY.search(line)))
                    if hit:
                        findings.append((rel, lineno, line.strip()[:90]))
        except OSError:
            continue
    py_hits = sum(1 for f in findings if f[0].endswith(".py"))
    passed = not findings
    detail = {"count": len(findings),
              "py_count": py_hits,
              "non_py_count": len(findings) - py_hits,
              "scan_exts": list(SCAN_EXTS),
              "examples": [f"{f}:{line_no}: {text}" for f, line_no, text in findings[:10]]}
    return passed, detail
