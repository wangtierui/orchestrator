# -*- coding: utf-8 -*-
"""normalize_internal_naming.py — 内部制度文件「规范命名 + 归集」工具（2026-09-13 用户规则）

命名规则（用户指令，2026-09-13）
--------------------------------
  · 格式：`文号_名称`（例：`阳光人寿办发〔2025〕34号_关于下发个险营销平台部分基本法薪资项目规则调整的通知.pdf`）
  · 未取得文号：`_名称`（例：`_振兴计划规划师基本管理办法（2022版）.pdf`）
  · **文号与名称优先从文档内容获取**（`scan.parse_content_identity`，内容权威）；
    内容未取得**文号**时，按名称匹配 `originals/制度清单.xlsx`（列：起草部门/制度名称/发文文号/…）兜底；
    名称始终不回退清单（清单仅补文号）。
  · 规范命名后**归集至 `originals/` 根层**（扁平化）。

设计要点
--------
- **正文复用优先**：已纳入索引的文件直接复用 `processed/<ipn>_fulltext.json`（零抽取开销）；
  未纳入索引者才调用 `extract_file` 现场抽取（`--no-extract` 可禁用，退化为文件名解构）。
- **信息不丢失**：扁平化会移走"来源部门"维度，故同步把 `制度清单.xlsx` 的**起草部门**
  写入索引记录 `drafting_dept`；且 `corpus/<domain>/` 仍完整保留原始交付目录树（归集审计层）。
- **冲突消歧**：目标名已存在且内容不同 → 追加 `_2`/`_3`…（确定性、幂等）。
- **安全**：dry-run 默认；`--apply` 前备份 index/state/受影响 processed 到
  `backups/naming_<ts>/`，并写 `manifest.json`（逐条 from→to + 解析来源）供审计与回滚；
  只处理**制度正文类**（pdf/doc/docx），台账（xls/xlsx）与图片/压缩包/数据库一律不动。

用法
----
  python tools/normalize_internal_naming.py                 # dry-run 报告（含解析质量统计）
  python tools/normalize_internal_naming.py --apply         # 执行
  python tools/normalize_internal_naming.py --apply --no-extract --limit 50
"""

from __future__ import annotations

import argparse
import collections
import difflib
import hashlib
import json
import os
import re
import shutil
import sys
import unicodedata
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 同仓引导（独立脚本运行支持）：本仓根 + modules/（internal_policy_base 等业务包）
for _p in (ROOT, os.path.join(ROOT, "modules")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

IPB = os.path.join(ROOT, "modules", "internal_policy_base")
DATA = os.path.join(IPB, "data")
ORIGINALS = os.path.join(DATA, "originals")
PROCESSED = os.path.join(DATA, "processed")
INDEX_PATH = os.path.join(DATA, "internal_policy_index.json")
STATE_PATH = os.path.join(DATA, "_ingest_state.json")
REGISTRY_XLSX = os.path.join(ORIGINALS, "制度清单.xlsx")
BACKUP_ROOT = os.path.join(IPB, "backups")

DOC_EXTS = {".pdf", ".doc", ".docx"}
_ILLEGAL = re.compile(r'[\\/:*?"<>|\r\n\t]')

# 单一事实源：文号归一与内部文号校验统一取自 internal_policy_base.scan
# （用户规则 2026-09-13：文号只在标题处、须以"阳光人寿/阳光保险"开头）
from internal_policy_base.scan import (  # noqa: E402
    is_internal_docno as _is_internal_docno,
)
from internal_policy_base.scan import (
    normalize_docno as _normalize_docno,
)

# "关于下发X的通知" 类包裹词（用于与清单名称做包含匹配）
_WRAP_HEAD = re.compile(r"^(?:关于)?(?:印发|下发|修订下发|发布|征求|征求对)?《?")
_WRAP_TAIL = re.compile(r"》?(?:的通知|的函|的批复|的公告|的通知（.*?)）?$")

# 文件名噪声：编号/附件前缀、盖章/水印/定稿/清洁版等后缀、尾随序号
# （2026-09-13 扩面：实测前缀形态多样——`10：` `3，` `18运营-` `6人管-` `编号4.` `红头文件-`
#   `同步废止` 等，原规则只覆盖 `数字[-.、]` 与 `附件N`，故循环多次剥离并覆盖中文标点。）
_PREFIX_NOISE = re.compile(
    r"^\s*(?:附件\s*[0-9一二三四五六七八九十]+\s*[：:.\s、,，]*"
    r"|编号\s*[0-9]{1,3}\s*[.、,，:：]?\s*"
    r"|红头文件\s*[-—_:：]?\s*"
    r"|同步废止\s*"
    r"|\d{4}[/\-.]\d{1,2}[/\-.]\d{1,2}\s*\d{0,2}[:：]?\d{0,2}\s*"
    r"(?:查看|浏览|打印|下载)?\s*[-—_:：]?\s*"
    r"|[\d\s/.\-:：]{4,24}(?:查看|浏览|打印|下载)\s*[-—_:：]?\s*"
    r"|(?<![\d])\d{1,2}(?=[\u4e00-\u9fa5]{6,})(?!\d)"
    r"|\d+\s*[\u4e00-\u9fa5]{0,4}\s*[-—.、,，:：]\s*"
    r"|\d+\s*[.、,，:：]\s*"
    r"|第\s*\d+\s*[条章]\s*)"
)
# 红头机关名 / 主送机关（名称清洗用，与 scan.clean_title_noise 同源规则）
_REDHEAD_NAME = re.compile(
    r"^[\u4e00-\u9fa5]{4,30}?(?:股份有限公司|有限公司|集团|公司|事业部|分公司)文件(?=[\u4e00-\u9fa5])"
)
_MAIN_TO = re.compile(
    r"(?:各分公司|总公司各部门|各事业部|各中心|各部门|各处室|各单位|各机构)[，,：:].*$", re.S
)

# 名称中**内嵌**的发文文号（文号应只出现在文件名前缀，主体内重复需剔除）
_INLINE_DOCNO = re.compile(
    r"[\u4e00-\u9fa5]{2,20}\s*(?:〔|\(|（)\s*\d{4}\s*(?:〕|\)|）)\s*第?\s*\d{1,4}\s*号\s*"
)

# 标题内多余空白（内容抽取产物，如「（2025 版）」「（2017 修订版）」）——规范名须紧凑。
# 2026-09-13 扩面：数字后的空格后接**任意汉字**即清（原仅限 版/年/条 等度量词，
# 漏掉了「2017 修订版」→ 产出「（2017 修订版）」不合规范命名）。
_SPACE_BEFORE_UNIT = re.compile(r"(?<=\d)\s+(?=[\u4e00-\u9fa5])")
_SPACE_IN_CJK = re.compile(r"(?<=[\u4e00-\u9fa5])\s+(?=[\u4e00-\u9fa5])")
_SPACE_BEFORE_DIGIT = re.compile(r"(?<=[\u4e00-\u9fa5])\s+(?=\d)")
_TAIL_NOISE = re.compile(
    r"(?:_盖章|_-?盖章|\(盖章\)|（盖章）|含水印|_盖章\(\d+\)|-\d+$|_\d+$|"
    r"清洁版\s*V?\d*|[-_]定稿|^-定稿|\(最终版\)|\(修订版\)?$|"
    r"(?<=[\u4e00-\u9fa5])\s*[-_]\s*$)"
)

# 文件名内已含文号时的切分（`xxx号_名称`）
_DOCNO_INLINE = re.compile(r"^[^_]{2,40}号\s*[_—\-]\s*")


# ---------------------------------------------------------------- 名称归一
def norm_name(s: str) -> str:
    """名称归一（匹配用）：去空白/书名号、统一全半角括号、去包裹词。"""
    t = (s or "").strip()
    t = re.sub(r"[\s\u3000]+", "", t)
    t = t.replace("《", "").replace("》", "")
    t = t.replace("（", "(").replace("）", ")").replace("〔", "(").replace("〕", ")")
    return t


def strip_notice(s: str) -> str:
    """剥掉"关于下发…的通知"包裹，尽量取内层制度名（匹配清单用，不用于最终命名）。"""
    t = s or ""
    for _ in range(2):
        prev = t
        t = _WRAP_HEAD.sub("", t.strip())
        t = _WRAP_TAIL.sub("", t.strip())
        t = t.strip(" -_—")
        if t == prev:
            break
    return t


def safe_filename(name: str, *, maxlen: int = 120) -> str:
    """文件名安全化：替换 Windows 非法字符、去尾部点与空格、限长（保留扩展名）。"""
    stem, ext = os.path.splitext(name)
    stem = _ILLEGAL.sub(" ", stem).strip().rstrip(". ")
    stem = re.sub(r"\s{2,}", " ", stem)
    if len(stem) > maxlen:
        stem = stem[:maxlen].rstrip()
    return stem + ext


def fix_variant(s: str) -> str:
    """修异体/兼容字符 → 通用汉字（只处理康熙部首/兼容汉字/兼容表意，**不动**全角括号等中文标点）。

    实证来源：PDF 正文抽取会产出 `⼈⼼`（U+2F08/U+2F3C 康熙部首）等异体形态，
    直接进文件名会造成字符异常与后续匹配失败。全角括号 `（）〔〕` 必须保留（项目命名约定）。
    """
    out = []
    for ch in s or "":
        cp = ord(ch)
        if 0x2E80 <= cp <= 0x2FDF or 0xF900 <= cp <= 0xFAFF or 0x2F00 <= cp <= 0x2FDF:
            out.append(unicodedata.normalize("NFKC", ch))
        else:
            out.append(ch)
    return "".join(out)


def is_valid_docno(s: str) -> bool:
    """内部制度文号校验（单一事实源 = `scan.is_internal_docno`）：

    归一后形态须为 `机关代字〔年〕序号号`，**且**发文机关前缀属白名单（阳光人寿/阳光保险）。
    据此拦掉三类实测反例：公文流转号 `NEWYXYWFASQ 202503310001`、OA 档案号 `SXLQB201912130013`、
    以及监管机关文号 `保监发〔2013〕40号`／`银保监发〔2021〕14号`（非公司内部文号）。
    """
    return _is_internal_docno(s)


def tidy_title(s: str) -> str:
    """标题紧凑化：修异体字 → 剥红头机关名 → 清前缀噪声 → 剔除内嵌文号
    → 去主送机关后缀 → 去多余空格（内容标题与文件名标题**同一套清洗**）。

    内容标题同样需清前缀（实测内容标题会带 `附件1：`：`附件1：共建合作签约申请表`，
    与文件名侧的 `附件N` 处理保持一致）。
    """
    t = fix_variant(s or "")
    t = _REDHEAD_NAME.sub("", t)
    t = _INLINE_DOCNO.sub("", t)
    for _ in range(3):
        t2 = _PREFIX_NOISE.sub("", t).strip(" -_—、,，:：")
        if t2 == t:
            break
        t = t2
    t = _SPACE_BEFORE_UNIT.sub("", t)
    t = _SPACE_IN_CJK.sub("", t)
    t = _SPACE_BEFORE_DIGIT.sub("", t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    t = _MAIN_TO.sub("", t)
    return t.strip(" -_—、,，:：")


def derive_title_from_name(file_base: str) -> str:
    """从文件名解构「名称」（**回退路径**，非内容优先）：去文号前缀、编号/附件前缀、噪声后缀。

    必须产出**完整词干**（而非 `_` 后半段）——后者会得到"盖章"这类片段，
    并使 `parse_content_identity` 的重叠校验失败（实测回退率 73% 的根因）。
    """
    stem = os.path.splitext(file_base or "")[0]
    s = fix_variant(stem)
    s = _DOCNO_INLINE.sub("", s)  # 去文件名自带文号（`文号_名称` 形态）
    if "_" in s:
        # 先取最长片段：既处理已归集的 `_名称`（首段为空），也丢掉 `_盖章` 类零碎尾词
        parts = [p.strip() for p in s.split("_") if p.strip()]
        if parts:
            s = max(parts, key=len)
    for _ in range(4):  # 去 "1-" / "附件2：" / "10：-" / "6人管-" 等**多层**前缀
        s2 = _PREFIX_NOISE.sub("", s).strip(" -_—、,，:：")
        if s2 == s:
            break
        s = s2
    for _ in range(3):  # 去 "盖章/-含水印/-定稿(1)" 等后缀
        s2 = _TAIL_NOISE.sub("", s).strip(" -_—、,，:：")
        if s2 == s:
            break
        s = s2
    return s.strip("《》").strip()


# 文件名词干 = 内容标题 + 纯"版本/变体标记"（如 `（B类）`/`2020年版（A类）`/`（试行）`）
_VARIANT_TAIL = re.compile(
    r"^(?:(?:[（(][^）)]{1,12}[）)])|(?:\d{4}\s*年?版)|(?:[A-Za-z]类)|(?:版)"
    r"|[\s\-_—、,，:：])+$"
)


def keep_variant_suffix(title: str, stem: str) -> str:
    """内容标题 + 文件名变体后缀 → 并回（如 `个人寿险…管理办法` + `（B类）`）。

    严格内容优先的**唯一例外**：文件名词干携带"版本/变体标记"，且其主体部分与内容标题
    相互包含时并回后缀。两种形态都覆盖：
      · `个人寿险…管理办法` + `2020年版（A类）`（词干 = 标题 + 后缀）；
      · `保险代理业务合作协议（分分2024版）` + `阳光人寿保险股份有限公司保险代理业务合作协议`
        （词干 = 标题主体 + 后缀，标题带公司全称前缀）。
    价值：保住 `（A类）/（B类）/（分分2024版）/（2025版）` 区分，避免同类不同版塌成同名
    （实测 A/B/C 三类与 2024/2025 两版都会退化为 `_2`，丢失语义）。
    """
    if not title or not stem:
        return title
    rest = ""
    if stem.startswith(title):
        rest = stem[len(title) :].strip()
    else:
        m = re.match(
            r"^(.*?)((?:[（(][^）)]{1,12}[）)])|(?:\s*\d{4}\s*年?版)|(?:[A-Za-z]类))+$", stem
        )
        if m and norm_name(m.group(1)) and norm_name(m.group(1)) in norm_name(title):
            rest = m.group(2)
    if not rest or len(rest) > 24 or not _VARIANT_TAIL.match(rest):
        return title
    # 防重复追加：词干常因历史命名已含同一变体后缀（`…的通知（2017修订版）`），
    # 若 title 归一后已含该变体 → 不再追加（实测会产生 "…（2017修订版）…（2017修订版）"）。
    if norm_name(rest) in norm_name(title):
        return title
    return title + rest


# 文号归一统一走 `internal_policy_base.scan.normalize_docno`（本工具不再私有副本，
# 遵守 gate_no_duplicate_libs 的 SSOT 纪律：私有归一须 import SSOT 或加 norm-specialization 标记）。
# ---------------------------------------------------------------- 制度清单
def load_registry(path: str = REGISTRY_XLSX) -> list[dict]:
    """读 制度清单.xlsx（起草部门/制度名称/发文文号/当前是否有效）。缺失返回空表。"""
    if not os.path.exists(path):
        return []
    try:
        import openpyxl  # noqa: PLC0415
    except ImportError:
        print("[naming] 警告：未安装 openpyxl，制度清单兜底不可用（仅用内容解析）")
        return []
    rows: list[dict] = []
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    for ws in wb.worksheets:
        it = ws.iter_rows(values_only=True)
        try:
            header = [str(c or "").strip() for c in next(it)]
        except StopIteration:
            continue
        idx = {h: i for i, h in enumerate(header)}

        def col(row, name, default="", _idx=idx):  # 默认参数绑定当轮 idx（避免闭包捕获循环变量）
            i = _idx.get(name)
            return str(row[i] or "").strip() if (i is not None and i < len(row)) else default

        for row in it:
            name = col(row, "制度名称")
            if not name:
                continue
            rows.append(
                {
                    "dept": col(row, "起草部门"),
                    "name": name,
                    "docno": col(row, "发文文号"),
                    "valid": col(row, "当前是否有效"),
                }
            )
    return rows


def match_registry(
    title: str, rows: list[dict], *, threshold: float = 0.85
) -> tuple[str, str, float]:
    """按名称匹配清单 → (文号, 起草部门, 相似度)。名称始终不回退清单。"""
    if not title or not rows:
        return "", "", 0.0
    nt = norm_name(title)
    inner = norm_name(strip_notice(title))
    best, bscore, bdept = "", 0.0, ""
    for r in rows:
        rn = norm_name(r["name"])
        if not rn:
            continue
        # ① 包含关系（剥包裹后更准）→ 高分
        score = 0.0
        for probe in {nt, inner}:
            if not probe:
                continue
            if probe == rn:
                score = max(score, 1.0)
            elif probe in rn or rn in probe:
                score = max(
                    score, 0.85 + 0.15 * min(len(probe), len(rn)) / max(len(probe), len(rn))
                )
        # ② 兜底：序列相似
        if score < threshold:
            score = max(score, difflib.SequenceMatcher(None, inner or nt, rn).ratio())
        if score > bscore:
            best, bscore, bdept = _normalize_docno(r["docno"]), score, r["dept"]
    if bscore < threshold:
        return "", "", bscore
    return best, bdept, bscore


# ---------------------------------------------------------------- 规划
def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for c in iter(lambda: fh.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def _norm(p: str) -> str:
    return os.path.normcase(os.path.abspath(p))


def _walk_docs(root: str) -> list[str]:
    """制度正文类文件（排除 Office 临时锁文件 `~$*` 与隐藏文件）。"""
    out = []
    for dp, _dn, fn in os.walk(root):
        for f in fn:
            if f.startswith(("~$", ".")):
                continue
            if os.path.splitext(f)[1].lower() in DOC_EXTS:
                out.append(os.path.join(dp, f))
    return sorted(out)


def build_plan(
    *,
    root: str = ORIGINALS,
    registry: list[dict] | None = None,
    use_processed_text: bool = True,
    extract_missing: bool = True,
    limit: int = 0,
    inherit_attachments: bool = True,
) -> dict:
    """只读规划：为每个制度正文类文件确定规范名与归集目标。

    文号三级来源（用户规则 2026-09-13）：① 文档标题区域（内容权威）→ ② 制度清单.xlsx
    名称匹配兜底 → ③ **附件继承**（问题 6：作为正文附件存在的文档，内容出现在正文结尾处，
    与正文文号相同）。名称始终"内容优先 → 文件名解构回退"，不回退清单。
    """
    from internal_policy_base.scan import (
        clean_title_noise,
        inherit_docno_by_containment,
        parse_content_identity,
        parse_filename,
    )

    registry = registry if registry is not None else load_registry()
    index = json.load(open(INDEX_PATH, encoding="utf-8")) if os.path.exists(INDEX_PATH) else {}
    recs = index.get("records", [])
    by_path = {}
    for r in recs:
        rel = (r.get("relative_path") or "").replace("/", os.sep)
        if rel:
            by_path[_norm(os.path.join(ORIGINALS, rel))] = r
    by_sha = {}
    for r in recs:
        if r.get("sha256"):
            by_sha.setdefault(r["sha256"], r)

    files = _walk_docs(root)
    if limit:
        files = files[:limit]

    items, stats = [], collections.Counter()
    for p in files:
        rec = by_path.get(_norm(p))
        if rec is None:
            try:
                rec = by_sha.get(_sha256(p))
            except OSError:
                rec = None
        ext = os.path.splitext(p)[1].lower()

        # 名称解构（文件名回退）——必须取**完整词干**并清噪声（见 derive_title_from_name）
        base = os.path.basename(p)
        parsed_title = derive_title_from_name(base)
        if not parsed_title:
            parsed_title = (
                clean_title_noise(parse_filename(base)["title"]) or os.path.splitext(base)[0]
            )
        parsed_title = tidy_title(parsed_title)

        # 正文（复用 processed > 现场抽取 > 无）
        text, text_src = "", "none"
        if rec and use_processed_text:
            fp = os.path.join(PROCESSED, (rec.get("ipn") or "") + "_fulltext.json")
            if os.path.exists(fp):
                try:
                    text = json.load(open(fp, encoding="utf-8")).get("text") or ""
                    text_src = "processed"
                except (OSError, ValueError):
                    text = ""
        if not text and extract_missing:
            try:
                from internal_policy_base.extract import extract_file  # noqa: PLC0415

                text = extract_file(p, name=base).get("text") or ""
                text_src = "extract" if text else "none"
            except Exception:  # noqa: BLE001  抽取失败不阻断（回退文件名解构）
                text = ""

        ident = (
            parse_content_identity(text, fallback_title=parsed_title)
            if text
            else {"docno": "", "title": ""}
        )
        if ident.get("title"):
            # 内容标题 + 文件名变体后缀并回（`（B类）`/`2020年版（A类）`）→ 保住版类区分
            title = tidy_title(keep_variant_suffix(tidy_title(ident["title"]), parsed_title))
            title_src = "content"
        else:
            title = parsed_title
            title_src = "filename"

        # 文号：标题区域（内容）优先 → 清单兜底（附件继承在循环后统一处理）；
        # 两类都必须过 `is_internal_docno`（形态 + 阳光前缀白名单）
        docno = _normalize_docno(ident.get("docno") or "")
        if docno and not is_valid_docno(docno):
            stats["docno_rejected_content"] += 1
            docno = ""
        docno_src = "content" if docno else ""
        dept = ""
        if not docno:
            cand, dept, score = match_registry(title, registry)
            if cand and is_valid_docno(cand):
                docno, docno_src = cand, f"registry({score:.2f})"
            elif cand:
                stats["docno_rejected_registry"] += 1
        if not docno:
            docno_src = "none"

        items.append(
            {
                "src": p,
                "src_rel": os.path.relpath(p, root),
                "ipn": (rec or {}).get("ipn", ""),
                "ext": ext,
                "docno": docno,
                "title": title,
                "dept": dept,
                "docno_src": docno_src,
                "title_src": title_src,
                "text_src": text_src,
                "text": text,
            }
        )

    # 问题 6：附件型文档继承正文文号（内容包含关系，见 scan.inherit_docno_by_containment）
    if inherit_attachments:
        for i, d in inherit_docno_by_containment(items).items():
            items[i]["docno"] = d
            items[i]["docno_src"] = "inherit"

    for it in items:
        it["target_name"] = safe_filename(
            f"{it['docno']}_{it['title']}{it['ext']}"
            if it["docno"]
            else f"_{it['title']}{it['ext']}"
        )
        it["already"] = (
            os.path.normcase(os.path.basename(it["src"])) == os.path.normcase(it["target_name"])
            and os.path.dirname(it["src"]) == root
        )
        stats[f"docno_{it['docno_src'].split('(')[0]}"] += 1
        stats[f"title_{it['title_src']}"] += 1
        stats[f"text_{it['text_src']}"] += 1
        stats["total"] += 1
        stats["已规范且在根层" if it["already"] else "需处置"] += 1
    return {"items": items, "stats": dict(stats), "root": root}


def resolve_targets(plan: dict) -> dict:
    """确定性冲突消解：同一目标名多文件 → 保留第一个，其余追加 _2/_3…（含与磁盘既有名冲突）。"""
    taken = {}
    for it in plan["items"]:
        if it["already"]:
            taken.setdefault(os.path.normcase(it["target_name"]), it["src"])
    for it in plan["items"]:
        if it["already"]:
            continue
        name = it["target_name"]
        stem, ext = os.path.splitext(name)
        k = os.path.normcase(name)
        dst = os.path.join(plan["root"], name)
        n = 1
        while k in taken or (os.path.exists(dst) and _norm(dst) != _norm(it["src"])):
            n += 1
            name = f"{stem}_{n}{ext}"
            k = os.path.normcase(name)
            dst = os.path.join(plan["root"], name)
        taken[k] = it["src"]
        it["dst"] = dst
        it["renamed"] = os.path.normcase(os.path.basename(dst)) != os.path.normcase(
            os.path.basename(it["src"])
        )
        it["moved"] = _norm(os.path.dirname(dst)) != _norm(os.path.dirname(it["src"]))
        it["collision_suffix"] = n > 1
    # 口径修正（2026-09-13）：消歧后 dst 与当前路径一致者（如同名对中带 `_2` 的那个）
    # 视为**已稳定**，否则每次 dry-run 都会把它们报成"待处置"，掩盖真正的幂等状态。
    for it in plan["items"]:
        if it.get("dst") and _norm(it["dst"]) == _norm(it["src"]):
            it["already"] = True
            it.pop("dst", None)
    return plan


def apply_plan(plan: dict, *, backup: bool = True, update_index: bool = True) -> dict:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = os.path.join(BACKUP_ROOT, f"naming_{ts}") if backup else ""
    acts = collections.Counter()
    if backup:
        os.makedirs(backup_dir, exist_ok=True)
        for src in (INDEX_PATH, STATE_PATH):
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(backup_dir, os.path.basename(src)))

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rec_by_ipn = {}
    if update_index and os.path.exists(INDEX_PATH):
        rec_by_ipn = {
            r.get("ipn"): r
            for r in json.load(open(INDEX_PATH, encoding="utf-8")).get("records", [])
        }
    touched_proc = set()
    manifest = []

    def _writeback_identity(it: dict, rec: dict, *, new_rel: str = "") -> None:
        """身份回写（2026-09-13）：文件名已是权威身份，索引须同步纠正，否则下游
        （merged 引用匹配 / draft 引用 / 分析交付）仍按旧文号走。

        - `ipn` 是**稳定代理键**（processed 文件名 / merged / citations 均引用它），
          **不随身份重算**——否则 17 组同名对会撞键，且全链路引用整体漂移。
        - 路径字段仅在文件真被移动/改名时写。
        """
        if new_rel:
            rec["relative_path"] = new_rel
            rec["original_path"] = "originals/" + new_rel
            rec["file_name"] = os.path.basename(new_rel)
        rec["name_normalized_at"] = now
        old_docno, old_title = rec.get("docno", ""), rec.get("title", "")
        changed = it["docno"] != old_docno or it["title"] != old_title
        if changed:
            acts["identity_fixed"] += 1
            rec["identity_prev"] = f"{old_docno}|{old_title}"[:160]
        rec["docno"] = it["docno"]
        rec["title"] = it["title"]
        rec["identity_source"] = f"docno={it['docno_src']};title={it['title_src']}"
        if it["dept"]:
            rec["drafting_dept"] = it["dept"]
        touched_proc.add(rec["ipn"])
        return changed

    for it in plan["items"]:
        rec = rec_by_ipn.get(it["ipn"]) if it["ipn"] else None
        if it["already"] or not it.get("dst"):
            # 已规范命名：仅做**身份回写**（路径未变），使索引与文件名一致
            acts["skip_already"] += 1
            if rec is not None and _writeback_identity(it, rec):
                manifest.append(
                    {
                        "src_rel": it["src_rel"],
                        "dst_rel": it["src_rel"],
                        "ipn": it["ipn"],
                        "docno": it["docno"],
                        "title": it["title"],
                        "dept": it["dept"],
                        "docno_src": it["docno_src"],
                        "title_src": it["title_src"],
                        "text_src": it["text_src"],
                        "identity_only": True,
                    }
                )
            continue
        src, dst = it["src"], it["dst"]
        if _norm(src) == _norm(dst):
            acts["skip_same"] += 1
            if rec is not None:
                _writeback_identity(it, rec)
            continue
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            os.replace(src, dst)
            acts["moved" if it.get("moved") else "renamed"] += 1
        except OSError as e:
            acts["failed"] += 1
            manifest.append(
                {**{k: it[k] for k in ("src_rel", "target_name", "ipn")}, "error": repr(e)[:120]}
            )
            continue
        new_rel = os.path.relpath(dst, ORIGINALS).replace(os.sep, "/")
        if rec is not None:
            _writeback_identity(it, rec, new_rel=new_rel)
        manifest.append(
            {
                "src_rel": it["src_rel"],
                "dst_rel": new_rel,
                "ipn": it["ipn"],
                "docno": it["docno"],
                "title": it["title"],
                "dept": it["dept"],
                "docno_src": it["docno_src"],
                "title_src": it["title_src"],
                "text_src": it["text_src"],
                "collision_suffix": bool(it.get("collision_suffix")),
            }
        )

    if update_index and rec_by_ipn:
        idx = json.load(open(INDEX_PATH, encoding="utf-8"))
        idx["records"] = [rec_by_ipn.get(r.get("ipn"), r) for r in idx.get("records", [])]
        idx["generated_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        tmp = INDEX_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(idx, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, INDEX_PATH)
        # 同步 processed 主记录路径（reocr/backfill_rich 依赖）
        for ipn in sorted(touched_proc):
            p = os.path.join(PROCESSED, ipn + ".json")
            if not os.path.exists(p):
                continue
            try:
                obj = json.load(open(p, encoding="utf-8"))
            except (OSError, ValueError):
                continue
            rec = rec_by_ipn.get(ipn)
            if not isinstance(obj, dict) or rec is None:
                continue
            obj["relative_path"] = rec["relative_path"]
            obj["original_path"] = rec["original_path"]
            obj["file_name"] = rec["file_name"]
            # 身份同步（2026-09-13）：`docno/title` 属 PROCESSED_FIELDS 白名单，索引重建
            # 会从 processed 取值 → 若不同步，本次纠正会在下次重建时被**静默回退**。
            obj["docno"] = rec["docno"]
            obj["title"] = rec["title"]
            tmp = p + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(obj, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, p)
            acts["processed_synced"] += 1

    if backup:
        with open(os.path.join(backup_dir, "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump(
                {"generated_at": now, "actions": dict(acts), "entries": manifest},
                fh,
                ensure_ascii=False,
                indent=2,
            )
    return {"actions": dict(acts), "backup_dir": backup_dir, "entries": len(manifest)}


def main() -> int:
    ap = argparse.ArgumentParser(description="内部制度文件规范命名（文号_名称）与归集")
    ap.add_argument("--apply", action="store_true", help="执行（默认 dry-run）")
    ap.add_argument(
        "--no-extract", action="store_true", help="禁用现场抽取（仅复用 processed + 文件名）"
    )
    ap.add_argument("--no-index", action="store_true", help="只重命名/归集，不改索引")
    ap.add_argument("--no-backup", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--sample", type=int, default=15, help="dry-run 展示条目数")
    args = ap.parse_args()

    for p in (ORIGINALS, PROCESSED):
        if not os.path.isdir(p):
            print(f"[naming] 缺少输入：{p}")
            return 1

    registry = load_registry()
    print(f"[naming] 制度清单条目 {len(registry)}（兜底文号来源：{REGISTRY_XLSX}）")
    plan = build_plan(registry=registry, extract_missing=not args.no_extract, limit=args.limit)
    plan = resolve_targets(plan)
    st = plan["stats"]
    settled = sum(1 for i in plan["items"] if i["already"])
    print(
        f"[naming] 制度正文类文件 {st.get('total', 0)}"
        f" | 已规范且在根层 {settled} | 需处置 {st.get('total', 0) - settled}"
    )
    print(
        f"  文号来源 : content {st.get('docno_content', 0)} / registry "
        f"{st.get('docno_registry', 0)} / 未取得 {st.get('docno_none', 0)}"
    )
    print(
        f"  名称来源 : content {st.get('title_content', 0)} / filename {st.get('title_filename', 0)}"
    )
    print(
        f"  正文来源 : processed {st.get('text_processed', 0)} / extract {st.get('text_extract', 0)}"
        f" / none {st.get('text_none', 0)}"
    )
    todo = [i for i in plan["items"] if not i["already"] and i.get("dst")]
    coll = sum(1 for i in todo if i.get("collision_suffix"))
    print(f"  待执行 {len(todo)}（其中名冲突消歧 {coll}）")
    print(f"  --- 抽样（{args.sample} 条）---")
    for it in todo[: args.sample]:
        print(f"   {it['src_rel'][:52]:52s} → {it['target_name'][:60]}")
        print(f"      docno={it['docno_src']:14s} title={it['title_src']:9s} text={it['text_src']}")

    if not args.apply:
        print("[naming] dry-run 结束（未改动）。加 --apply 执行。")
        return 0

    res = apply_plan(plan, backup=not args.no_backup, update_index=not args.no_index)
    print(f"[naming] 完成：{res['actions']}")
    if res["backup_dir"]:
        print(f"[naming] 备份与清单 → {res['backup_dir']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
