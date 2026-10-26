# -*- coding: utf-8 -*-
"""tools/extract_relations.py — 依据/废止关系抽取编排与**三类关系产物**生成（R-F01）

职责（编排层；文本抽取本身在 `std_lib/common_lib/relations.py` 唯一实现）
--------------------------------------------------------------------
1. 读**监管文件**正文（`cleaned/<src>_cleaned_<date>.jsonl` 的 `body_text`，权威轨）
   与**内部制度**正文（`internal_policy_base/data/processed/<ipn>_fulltext.json`）；
2. 调 `RelationPipeline` 抽取依据/废止关系；
3. **实体解析**：目标（名称/文号）→ `RFN`（监管实体）/ `IPN`（内部实体）；
   解析不到则 `matched_by="unresolved"` + **保留原文**（禁止臆造，与 drafter 引用核验同纪律）；
4. 汇总为**一张关系表**（唯一事实源）并导出三类视图。

三类关系（用户要求明确列明，均落在一张表内，靠 `src_kind`/`dst_kind`/`relation` 区分）
------------------------------------------------------------------------------------
| # | 类别 | 筛选 |
|---|------|------|
| 1 | 监管文件的依据关系与废止关系 | `src_kind=regulatory`（dst 通常亦为 regulatory） |
| 2 | 内部制度的依据关系与废止关系 | `src_kind=internal` 且 `dst_kind=internal` |
| 3 | 内部制度与监管文件之间的依据关系 | `src_kind=internal` 且 `dst_kind=regulatory` 且 `relation=basis` |

产物（**唯一事实源 + 派生视图**，均在 `modules/regulatory_classifier/data/relations/`）
------------------------------------------------------------------------------------
- `relations_index.jsonl`：**SSOT**，全部关系（三类混装，每行一关系）
- `cross_basis.jsonl`：**派生视图**（类别 3），供 drafter/base 高频消费
- `relations_stat.json`：生成元信息与质量指标（计数/解析率/未解析样例）
- `docs/deliverables/监管与制度依据废止关系图谱.md`（`--report` 时）：人类可读摘要

消费方：`regulatory_classifier`（报告/明细）· `internal_policy_base`（merged 关联）·
`internal_policy_drafter`（条款对照素材/引用核验）；统一读取入口 `interfaces/relations_api.py`。

用法
----
    python tools/extract_relations.py                      # 全量（5 源 + 内部制度）
    python tools/extract_relations.py --source nfra        # 单源（调试/增量）
    python tools/extract_relations.py --limit 50 --dry-run # 试跑不落盘
    python tools/extract_relations.py --report             # 额外生成关系图谱报告
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from glob import glob

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (
    ROOT,
    os.path.join(ROOT, "std_lib"),
    os.path.join(ROOT, "modules"),
    os.path.join(ROOT, "modules", "regulatory_classifier"),
):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import paths
from config.enums import (
    RELATION_KIND,
    RELATION_MATCH_METHOD,
)
from config.exitcodes import ExitCode
from std_lib.common_lib.clause_locator import (
    load_internal_spans,
    load_regulatory_index,
    load_regulatory_spans,
    locate_dst_article,
    locate_src_article,
)
from std_lib.common_lib.norm import (
    drop_cn_prefix,
    norm_docno,
    norm_docno_strong,
    norm_title_rel,
    norm_title_strict,
    strip_title_version,
)
from std_lib.common_lib.relations import (
    EXTRACTOR_VERSION,
    SCHEMA_VERSION,
    TARGET_CLASSES,
    TARGET_CORPUS,
    TARGET_ENTITY,
    TARGET_EXTERNAL,
    TARGET_GENERIC,
    TARGET_ORGAN,
    RelationPipeline,
    classify_target,
    docno_signature,
    snippet_half_window,  # N-109：窗口半宽唯一推导（用于"前文剔除"裁剪）
)

_TZ = timezone(timedelta(hours=8))
GENERATED_BY = "tools/extract_relations.py"

CLEANED_DIR = os.path.join(ROOT, "modules", "regulatory_scrapers", "data", "cleaned")
IPB_DATA = os.path.join(ROOT, "modules", "internal_policy_base", "data")
OUT_DIR = os.path.join(ROOT, "modules", "regulatory_classifier", "data", "relations")
REPORT_PATH = os.path.join(paths.DELIVERABLES_DIR, "监管与制度依据废止关系图谱.md")

# 解析方式 → 置信度（写入产物，供下游按阈值筛选）
CONFIDENCE = {
    "docno_exact": 1.00,
    "docno_sig": 0.95,
    "title": 0.90,
    "title_contains": 0.70,
    "unresolved": 0.00,
}
MIN_CONTAINS_LEN = 4  # 标题包含匹配的最短长度（防"办法"类短词误配）
MIN_SIG_LEN = 6  # 文号签名最短长度（四位年 + 至少两位序号）


# ===========================================================================
# 一、实体索引（监管域 RFN / 内部域 IPN）
# ===========================================================================

# v2 §3.6 X3（P1-5，2026-09-26）：状态行改经统一日志设施（原 print）。
# 规则：**状态/进度/告警行 → LOG**；**机器可读载荷（json.dumps）/多列表格行 → 保留 print**
# （后者是 stdout 契约，加日志前缀会破坏编排器 tail 与下游解析）。
# 判据：`gate_runtime_hygiene` 判据④断言本文件的 LOG 使用下限与 print 上限。
from config.enums import SOURCE_ORDER
from std_lib.common_lib.logging import get_logger

LOG = get_logger("relations")

# B6（W4，2026-10）：JSONL 原子写收敛至 `modules.base_publish.write_jsonl`（SSOT）——
# 原本地 `_write_jsonl` 私有实现与之语义重复（tmp + os.replace）；tools 是横切治理层，
# 读取 modules 事实源不受 gate_no_cross_module_import 约束（该门禁扫描范围仅 modules/）。
from base_publish import write_jsonl


def _empty_index() -> dict:
    """实体索引容器（**多档键**，强度递减）。

    - **强索引**（`by_*`）：归属表/主索引 → RFN / IPN（可 join 底座与门禁）
    - **弱索引**（`weak_*`）：cleaned 全集 → `dedup_key`（"已采集但未登记 RFN"的线索；
      关系抽取中大量目标是法律/行政法规，不在归属表内，弱索引可避免全部落入 unresolved）

    分档说明（2026-10-23 新增后三档，见 `std_lib/common_lib/norm.py` 关系增强层）：

    | 键 | 归一化 | 用途 |
    |---|---|---|
    | `by_docno` | `norm_docno` | 标准文号精确 |
    | `by_docno_strong` | `norm_docno_strong` | 全角数字 / 中文序号 / 正文残渣 |
    | `by_title` | `norm_title_strict` | 标准标题精确 |
    | `by_title_rel` | `norm_title_rel` | 去 HTML 实体 / 尖括号 / 引号 |
    | `by_title_ver` | `+ strip_title_version` | 去版本后缀（**仅唯一键可用**） |
    | `by_title_short` | `+ drop_cn_prefix` | 去「中华人民共和国」（**仅唯一键可用**） |

    `amb_*` 记录**多义键**（同一键对应多个实体）：这类键**不得用于解析**
    ——选错实体会把关系挂到错误的文件上，比"未定位"更糟（臆造的反面）。
    """
    return {
        "by_sig": {},
        "by_docno": {},
        "by_docno_strong": {},
        "by_title": {},
        "by_title_rel": {},
        "by_title_ver": {},
        "by_title_short": {},
        "title_list": [],
        "title_rel_list": [],
        "refs": {},
        "weak_docno": {},
        "weak_docno_strong": {},
        "weak_title": {},
        "weak_title_rel": {},
        "weak_title_ver": {},
        "weak_title_short": {},
        "weak_list": [],
        "weak_list_rel": [],
        # 多义键登记（键 → 命中的不同实体数 > 1）
        "amb_title_ver": set(),
        "amb_title_short": set(),
        "amb_weak_ver": set(),
        "amb_weak_short": set(),
    }


def _set_uniq(bucket: dict, amb: set, key: str, val: str) -> None:
    """写入"仅唯一可用"的键：同一键出现第二个不同值时登记为多义，解析侧跳过。"""
    if not key:
        return
    cur = bucket.get(key)
    if cur is None:
        bucket[key] = val
    elif cur != val:
        amb.add(key)


def _add_entity(ix: dict, *, ref: str, name: str, docno: str, extra: dict | None = None) -> None:
    if not ref:
        return
    ix["refs"][ref] = {"ref": ref, "name": name, "docno": docno, **(extra or {})}
    sig = docno_signature(docno)
    if len(sig) >= MIN_SIG_LEN:
        ix["by_sig"].setdefault(sig, ref)
    nd = norm_docno(docno)
    if nd:
        ix["by_docno"].setdefault(nd, ref)
    nds = norm_docno_strong(docno)
    if nds:
        ix["by_docno_strong"].setdefault(nds, ref)
    nt = norm_title_strict(name)
    if nt:
        ix["by_title"].setdefault(nt, ref)
        ix["title_list"].append((nt, ref))
    ntr = norm_title_rel(name)
    if ntr:
        ix["by_title_rel"].setdefault(ntr, ref)
        ix["title_rel_list"].append((ntr, ref))
        # 版本键/简称键只在"唯一"时才建（多义键会被登记进 amb_*）
        _set_uniq(ix["by_title_ver"], ix["amb_title_ver"],
                  strip_title_version(ntr), ref)
        _set_uniq(ix["by_title_short"], ix["amb_title_short"],
                  drop_cn_prefix(ntr), ref)


def build_regulatory_index() -> dict:
    """监管域索引：归属表（RFN 事实源）→ by_sig / by_docno / by_title。"""
    from rfn import get_index

    ix = _empty_index()
    for r in get_index().rows():
        _add_entity(
            ix,
            ref=r.get("监管文件编号", ""),
            name=r.get("文件名称", ""),
            docno=r.get("发文字号", ""),
            extra={"source": r.get("文件来源", ""), "status": r.get("时效状态", "")},
        )
    # 两档标题列表都按长度降序：包含匹配是"最长者优先"，否则短标题（如"XX办法"）
    # 会先命中，把具体文件匹成通用名（这是 contains 匹配的经典误配来源）。
    ix["title_list"].sort(key=lambda t: -len(t[0]))
    ix["title_rel_list"].sort(key=lambda t: -len(t[0]))
    return ix


def add_cleaned_weak_index(ix: dict, sources: list[str]) -> int:
    """把 cleaned 全集（title/document_number → dedup_key）加入**弱索引**。

    动机（实测）：关系抽取的目标大量是**法律/行政法规**（如《中华人民共和国银行业监督管理法》
    《民法典》），而归属表（RFN 事实源）只覆盖 1060 份"业务相关文件"，两者交集有限 →
    若只查归属表，解析率会低到 ~6%。弱索引把"**已采集但未登记 RFN**"的目标也接上，
    使关系可用于补登记线索与人工核对（`dst_ref` 仍为空，语义强度由字段区分）。
    """
    n = 0
    for src in sources:
        p = _latest_cleaned_jsonl(src)
        if not p:
            continue
        with open(p, encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                key = rec.get("dedup_key", "")
                if not key:
                    continue
                title = rec.get("title", "")
                document_number = rec.get("document_number", "")
                nt = norm_title_strict(title)
                if nt:
                    ix["weak_title"].setdefault(nt, key)
                    ix["weak_list"].append((nt, key))
                ntr = norm_title_rel(title)
                if ntr:
                    ix["weak_title_rel"].setdefault(ntr, key)
                    ix["weak_list_rel"].append((ntr, key))
                    _set_uniq(ix["weak_title_ver"], ix["amb_weak_ver"],
                              strip_title_version(ntr), key)
                    _set_uniq(ix["weak_title_short"], ix["amb_weak_short"],
                              drop_cn_prefix(ntr), key)
                nd = norm_docno(document_number)
                if nd:
                    ix["weak_docno"].setdefault(nd, key)
                nds = norm_docno_strong(document_number)
                if nds:
                    ix["weak_docno_strong"].setdefault(nds, key)
                n += 1
    ix["weak_list"].sort(key=lambda t: -len(t[0]))
    ix["weak_list_rel"].sort(key=lambda t: -len(t[0]))
    return n


def build_internal_index() -> dict:
    """内部域索引：主索引（IPN 事实源）→ by_sig / by_docno / by_title。"""
    p = os.path.join(IPB_DATA, "internal_policy_index.json")
    ix = _empty_index()
    if not os.path.exists(p):
        return ix
    for r in json.load(open(p, encoding="utf-8")).get("records", []):
        _add_entity(
            ix,
            ref=r.get("ipn", ""),
            name=r.get("title", ""),
            docno=r.get("docno", ""),
            extra={"extension": r.get("extension", ""), "dept": r.get("drafting_dept", "")},
        )
    # 两档标题列表都按长度降序：包含匹配是"最长者优先"，否则短标题（如"XX办法"）
    # 会先命中，把具体文件匹成通用名（这是 contains 匹配的经典误配来源）。
    ix["title_list"].sort(key=lambda t: -len(t[0]))
    ix["title_rel_list"].sort(key=lambda t: -len(t[0]))
    return ix


def resolve_target(
    name: str, docno: str, index: dict, *, strict: bool = False
) -> tuple[str, str, str]:
    """目标（名称/文号）→ `(strong_ref, matched_by, weak_key)`。

    - `strong_ref`：RFN / IPN（**强实体**，可 join 归属表/底座/门禁）；
    - `weak_key`：cleaned `dedup_key`（**弱引用**，文件已采集但未登记 RFN）；
    - 二者皆空且 `matched_by="unresolved"`：文本引用的名称未在语料中定位（如《民法典》），
      **保留原文**供人工/法规库补全（**禁止臆造**——与 drafter 引用核验同纪律）。

    `strict=True` 时**禁用** `title_contains`（只允许 `docno_exact/docno_sig/title`）：
    **跨域解析必须严格**——实测 `中华人民共和国发票管理办法`（法规）会经 contains 误配到
    内部制度 `…发票管理办法`，把法规当制度（语义污染）。同域（内部制度废止旧版）则允许
    contains（`…应急预案` vs `…应急预案（2024版）` 属真实版本差异）。
    """
    if docno:
        # ①-a 强·文号强归一（全角数字/中文序号/正文残渣）—— 先于标准层，
        #     因为标准层对这三类形态直接失配（见 norm.py 关系增强层说明）。
        nds = norm_docno_strong(docno)
        if nds and nds in index.get("by_docno_strong", {}):
            return index["by_docno_strong"][nds], "docno_exact", ""
        nd = norm_docno(docno)
        if nd and nd in index["by_docno"]:
            return index["by_docno"][nd], "docno_exact", ""
        sig = docno_signature(docno)
        if len(sig) >= MIN_SIG_LEN:
            # 签名尾匹配（归属表文号前缀机关代字与正文写法常不一致，取"数字尾"对齐）
            for k, ref in index["by_sig"].items():
                if k == sig or k.endswith(sig) or sig.endswith(k):
                    return ref, "docno_sig", ""
        if nds and nds in index.get("weak_docno_strong", {}):
            return "", "docno_exact", index["weak_docno_strong"][nds]
        if nd and nd in index.get("weak_docno", {}):
            return "", "docno_exact", index["weak_docno"][nd]

    ntr = norm_title_rel(name)
    nt = norm_title_strict(name)
    # ② 强·标题（三档强度递减：rel 精确 → 标准精确 → 去版本/去国名前缀的**唯一**键）
    if ntr:
        if ntr in index.get("by_title_rel", {}):
            return index["by_title_rel"][ntr], "title", ""
        ver = strip_title_version(ntr)
        if ver and ver != ntr and ver in index.get("by_title_ver", {}) \
                and ver not in index.get("amb_title_ver", ()):
            return index["by_title_ver"][ver], "title", ""
        short = drop_cn_prefix(ntr)
        if short and short != ntr and short in index.get("by_title_short", {}) \
                and short not in index.get("amb_title_short", ()):
            return index["by_title_short"][short], "title", ""
    if nt and nt in index["by_title"]:
        return index["by_title"][nt], "title", ""

    # ③ 标题包含（strict 模式禁用：跨域用 contains 会把法规误配成内部制度）
    if not strict:
        probe = ntr or nt
        if probe and len(probe) >= MIN_CONTAINS_LEN:
            for cand, ref in index.get("title_rel_list", []):
                if len(cand) < MIN_CONTAINS_LEN:
                    break
                if cand in probe or probe in cand:
                    return ref, "title_contains", ""
        # 3 字法定简称（公司法 / 保险法 / 民法典）：`MIN_CONTAINS_LEN=4` 会把它整类挡掉。
        # 收紧条件，**必须"候选以简称结尾"**——只认"《中华人民共和国公司法》"这种
        # 法规全名，不认"关于适用公司法的若干规定"（那是另一份文件，挂了就是错挂）。
        if probe and 3 <= len(probe) < MIN_CONTAINS_LEN:
            for cand, ref in index.get("title_rel_list", []):
                if len(cand) < len(probe):
                    continue
                if cand.endswith(probe):
                    return ref, "title_contains", ""
        if len(nt) >= MIN_CONTAINS_LEN:
            for cand, ref in index["title_list"]:
                if len(cand) < MIN_CONTAINS_LEN:
                    break
                if cand in nt or nt in cand:
                    return ref, "title_contains", ""

    # ④ 弱索引（已采集未登记 RFN）：返回 dedup_key，`dst_ref` 仍留空（禁止臆造）
    if ntr:
        if ntr in index.get("weak_title_rel", {}):
            return "", "title", index["weak_title_rel"][ntr]
        ver = strip_title_version(ntr)
        if ver and ver != ntr and ver in index.get("weak_title_ver", {}) \
                and ver not in index.get("amb_weak_ver", ()):
            return "", "title", index["weak_title_ver"][ver]
        short = drop_cn_prefix(ntr)
        if short and short != ntr and short in index.get("weak_title_short", {}) \
                and short not in index.get("amb_weak_short", ()):
            return "", "title", index["weak_title_short"][short]
    if nt and nt in index.get("weak_title", {}):
        return "", "title", index["weak_title"][nt]
    if not strict:
        probe = ntr or nt
        if probe and len(probe) >= MIN_CONTAINS_LEN:
            for cand, key in index.get("weak_list_rel", []):
                if len(cand) < MIN_CONTAINS_LEN:
                    break
                if cand in probe or probe in cand:
                    return "", "title_contains", key
    return "", "unresolved", ""


# ===========================================================================
# 二、正文迭代
# ===========================================================================
#: cleaned 目录里的**辅助产物**后缀（不是正文轨，绝不能当主文件读）
#:
#: ⚠️ 2026-10-23 实测事故（本条修复的由来）：
#:   `nfra_cleaned_<date>.jsonl` 与 `nfra_cleaned_<date>.quarantine.jsonl` 并存，
#:   字典序 `…22.jsonl` < `…22.quarantine.jsonl`（'j' < 'q'）⇒ `sorted()[-1]` 取到
#:   **隔离文件**；其每行 `body_text` 为空、`iter_regulatory_docs` 直接 `continue`
#:   ⇒ **nfra 全域 1935 份文件 / 111,970 条条款对关系链零贡献**，而日志只写
#:   "正文可抽取 0 份"，完全看不出是"取错了文件"。
#:   教训：**按文件名排序取"最新"时，必须先把非主轨文件排除**；且"抽到 0 份"
#:   应该告警而不是静默（见 `_latest_cleaned_jsonl` 末尾的探测）。
_CLEANED_AUX_MARKERS = (".quarantine.", ".bad.", ".bak.", ".tmp.", ".partial.")


def _is_cleaned_main(path: str) -> bool:
    """是否 cleaned 目录里的**主正文文件**（排除 quarantine/临时/备份）。"""
    name = os.path.basename(path).lower()
    return not any(m in name for m in _CLEANED_AUX_MARKERS)


def _latest_cleaned_jsonl(source: str) -> str:
    """取某源最新的**主** cleaned JSONL（排除 quarantine/备份等辅助产物）。

    返回空串表示"该源没有可用主文件"。若目录里只有辅助文件，会**显式告警**
    ——这类"源被整体漏掉"的故障必须能在日志里一眼看见。
    """
    all_fs = sorted(glob(os.path.join(CLEANED_DIR, f"{source}_cleaned_*.jsonl")))
    if not all_fs:
        return ""
    fs = [p for p in all_fs if _is_cleaned_main(p)]
    if not fs:
        LOG.warning(
            "[relations][warn] 源 %s 只有辅助文件（%s），**无主 cleaned 正文** ⇒ 该源不参与抽取",
            source, ", ".join(os.path.basename(p) for p in all_fs[:3]))
        return ""
    return fs[-1]


def iter_regulatory_docs(sources: list[str], limit: int = 0):
    """迭代监管文件（正文取 cleaned JSONL 的 body_text；权威轨）。"""
    for src in sources:
        p = _latest_cleaned_jsonl(src)
        if not p:
            LOG.info(f"[relations] 跳过源 {src}：无 cleaned JSONL")
            continue
        n = 0
        with open(p, encoding="utf-8") as fh:
            for line in fh:
                if limit and n >= limit:
                    break
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                text = rec.get("body_text") or rec.get("summary") or ""
                if not text.strip():
                    continue
                n += 1
                yield {
                    "doc_kind": "regulatory",
                    "source": src,
                    "name": rec.get("title", ""),
                    "docno": rec.get("document_number", ""),
                    "url": rec.get("source_url", ""),
                    "text": text,
                    "extra_text": rec.get("attachment_content") or "",
                    "dedup_key": rec.get("dedup_key", ""),
                }
        if n:
            LOG.info(f"[relations] 源 {src}：正文可抽取 {n} 份（{os.path.basename(p)}）")
        else:
            # "0 份"是**故障信号**（正常主文件必然有正文）：必须告警，
            # 否则整源静默缺席（nfra 就是这样漏了整整一轮）。
            LOG.warning(f"[relations][warn] 源 {src}：正文可抽取 **0 份**"
                        f"（{os.path.basename(p)}，{os.path.getsize(p)} 字节）"
                        " —— 检查是否取错文件或 body_text 全空")


def iter_internal_docs(limit: int = 0):
    """迭代内部制度（正文取 processed/<ipn>_fulltext.json）。"""
    p = os.path.join(IPB_DATA, "internal_policy_index.json")
    if not os.path.exists(p):
        LOG.info("[relations] 跳过内部制度：缺 internal_policy_index.json")
        return
    recs = json.load(open(p, encoding="utf-8")).get("records", [])
    n = 0
    for r in recs:
        if limit and n >= limit:
            break
        ipn = r.get("ipn", "")
        fp = os.path.join(IPB_DATA, "processed", f"{ipn}_fulltext.json")
        if not os.path.exists(fp):
            continue
        try:
            text = json.load(open(fp, encoding="utf-8")).get("text") or ""
        except (OSError, ValueError):
            continue
        if not text.strip():
            continue
        n += 1
        yield {
            "doc_kind": "internal",
            "source": "internal",
            "ref": ipn,
            "name": r.get("title", ""),
            "docno": r.get("docno", ""),
            "url": "",
            "text": text,
            "extra_text": "",
        }
    LOG.info(f"[relations] 内部制度：正文可抽取 {n} 份")


# ===========================================================================
# 三、关系行构建（统一 schema，三类关系同表）
# ===========================================================================
def _relation_id(
    src_kind: str,
    src_ref: str,
    relation: str,
    dst_ref: str,
    normalized_name: str,
    *,
    src_key: str = "",
    dst_kind: str = "",
    dst_docno: str = "",
    article: str = "",
    action: str = "",
    scope: str = "",
    reason: str = "",
) -> str:
    """关系行稳定去重键 `REL-<16hex>`。

    2026-09-20（相邻项修复，extractor 1.0 → 1.1）：纳入此前**遗漏的判别字段**
    （`article/action/scope/reason/dst_docno/dst_kind/src_key`）。旧式仅取 5 元组，
    同一对文件在同一文档的不同条款、或不同处置方式（废止/部分废止/另有规定）下会派生
    **同一 id**（实测 5266 行仅 4859 个 id、366 个 id 命中 2 行且行内容互不相同），
    迫使治理库改用合成 `row_key` 主键（保全了行数，但 id 语义失真）。
    """
    raw = "|".join(
        [
            src_kind,
            src_ref,
            relation,
            dst_ref,
            normalized_name,
            src_key,
            dst_kind,
            dst_docno,
            article,
            action,
            scope,
            reason,
        ]
    )
    return "REL-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _ensure_unique_ids(rows: list[dict]) -> int:
    """关系 id **全局唯一化**（确定性）：完全同判别字段的重复引用按出现序加 `-N` 后缀。

    返回被改写的行数（写入统计，供审计；正常情况下应为 0）。
    """
    seen: dict[str, int] = {}
    n = 0
    for r in rows:
        rid = r.get("relation_id") or ""
        if rid not in seen:
            seen[rid] = 1
            continue
        seen[rid] += 1
        r["relation_id"] = f"{rid}-{seen[rid]}"
        n += 1
    return n


def build_rows(
    doc: dict, pipeline: RelationPipeline, reg_ix: dict, int_ix: dict, *, generated_at: str
) -> tuple[list[dict], list[str], int, int]:
    """单篇文档 → `(关系行, 警告, 被过滤的泛指词数, **被排除的机关名目标数**)`。

    R-DEP-06（2026-10-26）：**机关名不建依据/废止边**。规则原文要求"依据与废止关系
    的目标必须是具体文件"；`根据《国务院》…` 这类程序性表述的目标是机关，不是文件。
    处置选择"**不建边**"而不是"建边后标 organ"：图谱只要存在这种边，读者就会看到
    "某办法依据了国务院"，而那是**语义错误**（不是"未定位"）。
    被排除的数量记入 stat（`excluded_organ`），可审计、不静默。
    """
    text = doc["text"]
    res = pipeline.extractor.extract(text)
    src_kind = doc["doc_kind"]
    # 源侧实体：监管取归属表解析（本条记录的文号/标题），内部取 IPN
    src_key = ""
    if src_kind == "regulatory":
        src_ref, _mb, src_key = resolve_target(doc["name"], doc["docno"], reg_ix)
        src_ref = src_ref or ""
        src_key = src_key or doc.get("dedup_key", "")
    else:
        src_ref = doc.get("ref", "")
    # N-49（2026-09-27）：条款级定位资源（进程内单例；首次调用读五源 clauses，此后零成本）
    #   源侧：内部制度走 processed（IPN）；**监管文件走 clauses 条号表 + 本文正文**
    #         （正文由 cleaned 提供，不重复读盘）；两类都只取"有条文结构"的文档。
    #   目标侧：监管 clauses 双键空间（RFN 强实体 / cleaned dedup_key 弱键）。
    _cindex = load_regulatory_index()
    if src_kind == "internal":
        _spans = load_internal_spans(src_ref)
    else:
        _spans = load_regulatory_spans(doc.get("dedup_key", ""), text, _cindex)

    rows: list[dict] = []
    excluded_organ = 0
    # 同域优先、再跨域：监管侧只解析监管域；内部侧先内部域再监管域
    for item, relation in [(b, "basis") for b in res.basis] + [(r, "repeal") for r in res.repeal]:
        name = item.target_name
        docno = getattr(item, "target_docno", "")
        dst_ref, matched_by, dst_key = resolve_target(name, docno, reg_ix)
        dst_kind = "regulatory"
        if not dst_ref and src_kind == "internal":
            # 跨域回退：**严格匹配**（禁 contains，防"法规名 ⊃ 制度名"误配）
            i_ref, i_mb, _ = resolve_target(name, docno, int_ix, strict=True)
            if i_ref:
                dst_ref, matched_by, dst_kind, dst_key = i_ref, i_mb, "internal", ""
        # N-49（2026-09-27）：条款级定位（两侧均**唯一命中**才填）
        _snip = getattr(item, "source_snippet", "")   # ±100 字符**窗口**（目标侧定位**沿用**）
        _span = getattr(item, "source_span", "")      # 引用跨度（**仅披露**，不参与定位）
        _src_art = locate_src_article(getattr(item, "offset", -1), _spans) if _spans else ""
        _place = ""  # N-106：目标侧定位来源标记（非 regulatory 分支保持空）
        if dst_kind == "regulatory":
            # 目标侧：优先强实体 RFN（dst_ref），退化 cleaned 弱键（dst_key）
            #
            # N-98（2026-09-28）**实测否决**：原计划"定位改用**引用跨度**（剔除窗口内源侧
            # 自身条号的污染）"，但两种跨度口径实测**均显著劣化**目标侧覆盖：
            #   · 命中文本本身（`m.group(0)`）          → 97 行（2.3%）
            #   · 命中 + 前向 200 字符（截至句末）      → 106 行（2.5%）
            #   · 原 ±100 对称**窗口**（保留）          → **237 行（5.7%）**
            # 根因：引用模式多只匹配到 `《名》`，而目标条号既可能在后（"第186条"），也可能
            # **在前**（"本办法第12条依据《X》"）；**前向**取会丢前半，**收窄**取会两头都丢。
            # → 结论：窗口作定位输入**优于**任何跨度；N-98 结案为"**实测否决**"，
            #   `source_span` 降级为**纯披露字段**（为后续研究留存数据，零行为影响）。
            #
            # N-106（2026-09-28）**并集定位**：N-98 的度量给出决定性事实 ——
            #   · 窗口 vs 跨度在 1733 个有目标键的行上**零冲突**（"窗口含源侧条号致误定位"的
            #     原假设**无实测证据**）；但**仅跨度能定位的有 101 行**（窗口弃权）→ 真短板是"**漏**"。
            #   · 故最优解不是二选一而是**取并集**：窗口优先（覆盖更广），窗口未唯一命中时退跨度
            #     （补 101 行）；**两侧仍各自坚持"唯一命中才填"** → 不放松质量闸门，且因零冲突
            #     不会引入互相矛盾的取值。
            #
            # N-109（2026-09-28）**前文剔除**（位置归因实测驱动）：对"仅窗口命中"的 147 行做
            # **条号在窗口内的位置**归因 → **125 行（85%）的条号落在引用起点之「前」**，即
            # **源侧自身**条款（"本办法第12条……依据《X》"）→ 属**误定位**。
            # ⚠️ 该发现**推翻**了第二十批的"窗口无实测误定位"结论：当时以"窗口↔跨度**冲突**"
            #    为唯一信号，而跨度无命中时**根本无从冲突** → 检验不充分。位置归因才揭示真相。
            # 处置：目标侧定位的窗口输入**只用引用之后的部分** `[pos, pos+h)`；代价覆盖
            # 338 → 213（剔 125 可疑），收益精度 ~63% → ~100%（纪律：宁可弃权，不得臆造）。
            _h = snippet_half_window()
            _pos = int(getattr(item, "offset", -1) or -1)
            _fwd = _snip[max(0, _pos - max(0, _pos - _h)):] if _pos >= 0 else _snip
            _dst_art, _place = "", ""
            for _pk, _pt in (("snippet", _fwd), ("span", _span)):
                if not _pt:
                    continue
                _got = (locate_dst_article(_pt, dst_ref, _cindex) if dst_ref else "") or (
                    locate_dst_article(_pt, dst_key, _cindex) if dst_key else ""
                )
                if _got:
                    _dst_art, _place = _got, _pk
                    break
        else:
            _dst_art = ""
        _dst_class = classify_target(
            dst_ref=dst_ref,
            dst_key=dst_key,
            name=name,
            basis_type=getattr(item, "basis_type", "") if relation == "basis" else "",
        )
        # R-DEP-06：机关名/非文件对象**不建边**（见 docstring）
        if _dst_class in (TARGET_ORGAN, TARGET_GENERIC):
            excluded_organ += 1
            continue
        rows.append(
            {
                "relation_id": _relation_id(
                    src_kind,
                    src_ref or src_key or doc["name"],
                    relation,
                    dst_ref or dst_key,
                    item.normalized_name,
                    src_key=src_key,
                    dst_kind=dst_kind,
                    dst_docno=docno,
                    article=item.article,
                    action=getattr(item, "action", "") if relation == "repeal" else "",
                    scope=getattr(item, "scope", "") if relation == "repeal" else "",
                    reason=getattr(item, "reason", "") if relation == "repeal" else "",
                ),
                "src_kind": src_kind,
                "src_ref": src_ref,
                "src_key": src_key,
                "src_name": doc["name"],
                "src_docno": doc["docno"],
                "src_source": doc["source"],
                "dst_kind": dst_kind,
                "dst_ref": dst_ref,
                "dst_key": dst_key,
                # 目标性质分类（entity/corpus/external；organ/generic 已在上面剔除）
                "dst_class": _dst_class,
                "dst_name": name,
                "dst_docno": docno,
                "dst_normalized_name": item.normalized_name,
                "relation": relation,
                "basis_type": getattr(item, "basis_type", "") if relation == "basis" else "",
                # ---- 《依据抽取规则》2026-10-26 对齐字段（纯增量，不进 relation_id）----
                # R-DEP-01：立法依据 / 适用依据
                "relation_subtype": (getattr(item, "relation_subtype", "")
                                     if relation == "basis" else "explicit_repeal"),
                # R-DEP-03：同一引导词下的并列依据共享 group_id
                "group_id": getattr(item, "group_id", ""),
                # R-DEP-04：书名号后紧跟的条号（条款级依据）
                "target_clause": getattr(item, "target_clause", ""),
                # R-REP-01：`《X》自……废止` 的终止日（不复用 effective_date）
                "target_end_date": (getattr(item, "target_end_date", "")
                                    if relation == "repeal" else ""),
                "article": item.article,
                "is_explicit": bool(getattr(item, "is_explicit", True)),
                "action": getattr(item, "action", "") if relation == "repeal" else "",
                "scope": getattr(item, "scope", "") if relation == "repeal" else "",
                "reason": getattr(item, "reason", "") if relation == "repeal" else "",
                "matched_by": matched_by,
                "confidence": CONFIDENCE.get(matched_by, 0.0),
                "source_offset": item.offset,
                # N-98（2026-09-28）：`source_snippet` 语义**保持不变**（±100 字符窗口）——
                # 因"改用跨度定位"实测劣化（见上方否决注记），故**不做语义变更**；
                # 引用跨度以**新字段** `source_span` 披露（纯增量，零行为影响）。
                "source_snippet": _snip[:400],
                "source_span": _span[:400],
                # N-49（2026-09-27）：条款级定位（**纯增强字段**——不进 `_relation_id` 判别
                #   字段，故既有 id 与下游依赖保持稳定，可安全回填）：
                #   · src_article_located：源侧条款（`source_offset` 在条款区间反查，精确）；
                #   · dst_article：目标侧条款（**窗口**抽『第 M 条』+ 目标条款表命中，唯一）；
                #   · article_placement：定位来源标记（"" = 未定位，供审计与后续改进统计）。
                "src_article_located": _src_art,
                "dst_article": _dst_art,
                "article_placement": (
                    ("src_offset" if _src_art else "") + (f"+{_place}" if _place else "")
                ).lstrip("+"),
                "generated_by": GENERATED_BY,
                "generated_at": generated_at,
            }
        )
    return rows, list(res.warnings), int(res.filtered_generic or 0), excluded_organ


# ===========================================================================
# 四、门面：全流程
# ===========================================================================
def run(
    *,
    sources: list[str] | None = None,
    limit: int = 0,
    dry_run: bool = False,
    with_internal: bool = True,
    with_attachments: bool = False,
    report: bool = False,
) -> dict:
    cfg_sources = sources or list(SOURCE_ORDER)
    generated_at = datetime.now(_TZ).strftime("%Y-%m-%d %H:%M:%S")
    reg_ix, int_ix = build_regulatory_index(), build_internal_index()
    n_weak = add_cleaned_weak_index(reg_ix, cfg_sources)
    LOG.info(
        f"[relations] 实体索引：监管 {len(reg_ix['refs'])} 个 RFN / 内部 {len(int_ix['refs'])} 个 IPN "
        f"/ 弱索引 {n_weak} 份 cleaned（未登记 RFN 的引用目标可追溯到 dedup_key）"
    )

    pipeline = RelationPipeline()
    rows: list[dict] = []
    warnings: list[str] = []
    docs_stat: dict = {"regulatory": {}, "internal": 0}
    filtered_generic = 0
    excluded_organ = 0

    for doc in iter_regulatory_docs(cfg_sources, limit=limit):
        docs_stat["regulatory"][doc["source"]] = docs_stat["regulatory"].get(doc["source"], 0) + 1
        if with_attachments and doc.get("extra_text"):
            doc["text"] = doc["text"] + "\n" + doc["extra_text"]
        r, w, fg, eo = build_rows(doc, pipeline, reg_ix, int_ix, generated_at=generated_at)
        rows.extend(r)
        warnings.extend(w)
        filtered_generic += fg
        excluded_organ += eo

    if with_internal:
        for doc in iter_internal_docs(limit=limit) or []:
            docs_stat["internal"] += 1
            r, w, fg, eo = build_rows(doc, pipeline, reg_ix, int_ix, generated_at=generated_at)
            rows.extend(r)
            warnings.extend(w)
            filtered_generic += fg
            excluded_organ += eo

    # 关系 id 唯一化（2026-09-20 相邻项修复）：判别字段完全相同的重复引用加确定性后缀
    n_id_dups = _ensure_unique_ids(rows)
    if n_id_dups:
        LOG.info(
            f"[relations] relation_id 撞车 {n_id_dups} 行（同判别字段重复引用）→ 已按出现序加后缀"
        )

    stat = _build_stat(
        rows,
        docs_stat,
        warnings,
        generated_at,
        filtered_generic=filtered_generic,
        excluded_organ=excluded_organ,
        id_dups=n_id_dups,
    )
    if dry_run:
        LOG.info("[relations] dry-run：不落盘。")
        print(
            json.dumps(
                {k: stat[k] for k in ("documents", "relations", "resolution")},
                ensure_ascii=False,
                indent=2,
            )
        )
        return {"rows": rows, "stat": stat, "written": False}

    written = write_products(rows, stat)
    if report:
        write_report(rows, stat)
    LOG.info(f"[relations] 完成：{len(rows)} 条关系 → {written['index']}")
    print(
        json.dumps({k: stat[k] for k in ("relations", "resolution")}, ensure_ascii=False, indent=2)
    )
    return {"rows": rows, "stat": stat, "written": True, "paths": written}


def _build_stat(
    rows: list[dict],
    docs_stat: dict,
    warnings: list[str],
    generated_at: str,
    *,
    filtered_generic: int = 0,
    excluded_organ: int = 0,
    id_dups: int = 0,
) -> dict:
    def _cnt(pred) -> int:
        return sum(1 for r in rows if pred(r))

    by_kind = {k: _cnt(lambda r, k=k: r["relation"] == k) for k in sorted(RELATION_KIND)}
    # 《依据抽取规则》(2026-10-26) 对齐后的口径：
    #   · by_subtype：立法依据（条款含"制定本×"）/ 适用依据 —— R-DEP-01
    #   · groups：并列依据的组数（同 group_id 多条）—— R-DEP-03
    #   · excluded_organ：因目标是机关名/非文件对象而**不建边**的条数 —— R-DEP-06
    by_subtype: dict[str, int] = {}
    for _r in rows:
        if _r.get("relation") != "basis":
            continue
        _k = str(_r.get("relation_subtype") or "unknown")
        by_subtype[_k] = by_subtype.get(_k, 0) + 1
    _groups = {r.get("group_id") for r in rows if r.get("group_id")}
    _multi = sum(
        1 for g in _groups
        if sum(1 for r in rows if r.get("group_id") == g) > 1
    )
    group_stat = {"groups": len(_groups), "multi_member_groups": _multi,
                  "with_target_clause": _cnt(lambda r: bool(r.get("target_clause"))),
                  "with_docno": _cnt(lambda r: bool(r.get("dst_docno")))}
    cross = {
        "regulatory->regulatory": _cnt(
            lambda r: r["src_kind"] == "regulatory" and r["dst_kind"] == "regulatory"
        ),
        "internal->internal": _cnt(
            lambda r: r["src_kind"] == "internal" and r["dst_kind"] == "internal"
        ),
        "internal->regulatory": _cnt(
            lambda r: r["src_kind"] == "internal" and r["dst_kind"] == "regulatory"
        ),
        "regulatory->internal": _cnt(
            lambda r: r["src_kind"] == "regulatory" and r["dst_kind"] == "internal"
        ),
    }
    resolution = {
        m: _cnt(lambda r, m=m: r["matched_by"] == m) for m in sorted(RELATION_MATCH_METHOD)
    }
    unresolved = [r for r in rows if r["matched_by"] == "unresolved"]
    n_ref = _cnt(lambda r: bool(r["dst_ref"]))  # 强关联：解析到 RFN/IPN
    n_any = _cnt(lambda r: r["matched_by"] != "unresolved")  # 含弱关联（cleaned dedup_key）

    # ---- 目标性质分层（2026-09-14 口径修正）------------------------------------
    # 只有 `external` 是"真·文件引用未定位"；`organ`（机关名）与 `generic`（泛指词）
    # 不是文件引用，混入分母会把真实覆盖度**系统性低估**。
    by_class = {c: _cnt(lambda r, c=c: r.get("dst_class") == c) for c in TARGET_CLASSES}
    file_denom = by_class[TARGET_ENTITY] + by_class[TARGET_CORPUS] + by_class[TARGET_EXTERNAL]
    file_resolved = round(by_class[TARGET_ENTITY] / file_denom, 4) if file_denom else 0.0
    file_located = (
        round((by_class[TARGET_ENTITY] + by_class[TARGET_CORPUS]) / file_denom, 4)
        if file_denom
        else 0.0
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "extractor_version": EXTRACTOR_VERSION,
        "generated_by": GENERATED_BY,
        "generated_at": generated_at,
        "documents": docs_stat,
        "relations": {"total": len(rows), "by_kind": by_kind, "by_cross": cross},
        # 关系 id 唯一性（2026-09-20）：行数 / 不同 id 数 / 撞车改写数 —— 三者须满足
        # `rows == distinct_ids` 且 `id_dups == 0`（gate_relations 判据 9 断言）。
        "relation_id": {
            "rows": len(rows),
            "distinct": len({r.get("relation_id") for r in rows}),
            "renamed": id_dups,
        },
        "resolution": resolution,
        # 两级解析率（口径显式命名，避免"解析率"歧义）
        "resolved_to_entity_ratio": round(n_ref / len(rows), 4) if rows else 0.0,
        "located_in_corpus_ratio": round(n_any / len(rows), 4) if rows else 0.0,
        # ---- 目标性质分层与**文件级**覆盖率（分母排除 organ/generic，2026-09-14）----
        "target_class": by_class,
        "file_level_denominator": file_denom,
        "file_resolved_ratio": file_resolved,
        "file_located_ratio": file_located,
        "by_subtype": by_subtype,
        "groups": group_stat,
        "excluded_organ": int(excluded_organ),
        "filtered_generic_total": filtered_generic,
        "warnings_total": len(warnings),
        "warnings_sample": sorted(set(warnings))[:10],
        "unresolved_sample": [
            {
                "src_ref": r["src_ref"] or r["src_key"][:12],
                "relation": r["relation"],
                "dst_class": r.get("dst_class", ""),
                "dst_name": r["dst_name"][:60],
                "dst_docno": r["dst_docno"][:40],
            }
            for r in unresolved[:20]
        ],
    }


def write_products(rows: list[dict], stat: dict) -> dict:
    """落盘：SSOT（relations_index.jsonl）+ 派生视图（cross_basis.jsonl）+ 统计。"""
    os.makedirs(OUT_DIR, exist_ok=True)
    index_p = paths.relations_index()
    cross_p = os.path.join(OUT_DIR, "cross_basis.jsonl")
    stat_p = os.path.join(OUT_DIR, "relations_stat.json")

    rows_sorted = sorted(
        rows,
        key=lambda r: (r["src_kind"], r["src_ref"], r["relation"], r["dst_ref"], r["dst_name"]),
    )
    write_jsonl(index_p, rows_sorted)
    cross = [
        r
        for r in rows_sorted
        if r["src_kind"] == "internal"
        and r["dst_kind"] == "regulatory"
        and r["relation"] == "basis"
    ]
    write_jsonl(cross_p, cross)
    stat["cross_basis_rows"] = len(cross)
    # 就地注入文件信息（调用方（write_report 等）在同一 stat 上继续消费）
    stat["files"] = {
        "relations_index": os.path.relpath(index_p, ROOT).replace(os.sep, "/"),
        "cross_basis": os.path.relpath(cross_p, ROOT).replace(os.sep, "/"),
        "relations_stat": os.path.relpath(stat_p, ROOT).replace(os.sep, "/"),
    }
    with open(stat_p + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(stat, fh, ensure_ascii=False, indent=2)
    os.replace(stat_p + ".tmp", stat_p)
    return {"index": index_p, "cross": cross_p, "stat": stat_p, "cross_rows": len(cross)}


# B6（W4，2026-10）：原私有 `_write_jsonl` 已删（与 SSOT base_publish.write_jsonl 语义
# 重复：tmp + os.replace 原子替换）；本工具 JSONL 落盘一律 `write_jsonl`（SSOT）。


def render_report(rows: list[dict], stat: dict) -> str:
    """渲染关系图谱正文（**纯函数，不落盘**）。

    F-L01 纳管（2026-09-14）：本工具 `--report`（`write_report`）与 analysis 交付库
    （`tools/gen_analysis_deliveries.py` 2.1.2.4）**共用本实现——单一渲染源，禁止分叉**。
    交付库侧从已落盘产物（`relations_index.jsonl` + `relations_stat.json`）重建，**零重抽取**。
    """
    lines = [
        "# 监管与制度依据·废止关系图谱",
        "",
        f"> 由 `{GENERATED_BY}` 生成于 {stat['generated_at']}（抽取器 {stat['extractor_version']}，"
        f"schema {stat['schema_version']}）。**本文件为派生报告**，事实源为 "
        f"`{stat['files']['relations_index']}`。",
        "",
        "## 一、总览",
        "",
        "| 维度 | 值 |",
        "| :--- | :--- |",
        f"| 关系总数 | {stat['relations']['total']} |",
        f"| 依据关系 / 废止关系 | {stat['relations']['by_kind'].get('basis', 0)} / "
        f"{stat['relations']['by_kind'].get('repeal', 0)} |",
        f"| **文件级强解析率**（RFN/IPN，可 join 底座） | {stat['file_resolved_ratio']:.1%} |",
        f"| **文件级定位率**（含 cleaned 弱引用） | {stat['file_located_ratio']:.1%} |",
        f"| 监管文件（可抽取） | {sum(stat['documents']['regulatory'].values())} 份 |",
        f"| 内部制度（可抽取） | {stat['documents']['internal']} 份 |",
        "",
        "> **口径（2026-09-14 修正）**：目标按**性质**分层（`dst_class`），只有 `external` 是"
        "「真·文件引用未定位」；`organ`（机关名）与 `generic`（泛指词）**不是文件引用**，"
        "不计入分母。",
        "",
        "| `dst_class` | 含义 | 条数 |",
        "| :--- | :--- | ---: |",
        f"| `entity` | 强实体：解析到 RFN / IPN | {stat['target_class'].get('entity', 0)} |",
        f"| `corpus` | 弱引用：已采集（cleaned 命中）但未登记 RFN | {stat['target_class'].get('corpus', 0)} |",
        f"| `organ` | 机关名（程序性依据目标，非文件） | {stat['target_class'].get('organ', 0)} |",
        f"| `generic` | 纯类型泛指词（已过滤，抽取侧不产出） | {stat['target_class'].get('generic', 0)} |",
        f"| `external` | **语料外文件**（法律法规等，客观未采集） | {stat['target_class'].get('external', 0)} |",
        "",
        f"> 文件级分母（entity+corpus+external）= **{stat['file_level_denominator']}** 条；"
        f"另有过滤的泛指词目标 {stat['filtered_generic_total']} 条（抽取侧净化，不产出关系）。",
        "> `corpus` 类即 **RFN 补登候选**：处置入口 `python tools/rfn_backlog.py`"
        "（清单 + `--apply` 批量登记；登记后本表重跑即升级为 `entity`）。",
        "",
        "## 二、三类关系（用户要求明确列明）",
        "",
        "| # | 类别 | 口径 | 关系数 |",
        "| :--- | :--- | :--- | ---: |",
        f"| 1 | 监管文件的依据关系与废止关系 | `src_kind=regulatory` | "
        f"{stat['relations']['by_cross']['regulatory->regulatory']} |",
        f"| 2 | 内部制度的依据关系与废止关系 | `src_kind=internal ∧ dst_kind=internal` | "
        f"{stat['relations']['by_cross']['internal->internal']} |",
        f"| 3 | 内部制度 → 监管文件的依据关系 | `src_kind=internal ∧ dst_kind=regulatory` | "
        f"{stat['relations']['by_cross']['internal->regulatory']} |",
        "",
        f"> 其中类别 3 的**纯依据**边（`relation=basis`）共 **{stat.get('cross_basis_rows', 0)}** 条，"
        f"另存为派生视图 `{stat['files']['cross_basis']}`（供 drafter / base 高频消费）；"
        "其余为「内部制度废止监管文件」的罕见组合（保留在同一事实源内，不另立视图）。",
        "",
        "## 三、实体解析分布",
        "",
        "| matched_by | 条数 | 置信度 |",
        "| :--- | ---: | ---: |",
    ]
    for m, c in sorted(stat["resolution"].items(), key=lambda kv: -kv[1]):
        lines.append(f"| `{m}` | {c} | {CONFIDENCE.get(m, 0.0):.2f} |")
    lines += ["", "## 四、未解析样例（需人工/清单补全，**不得臆造**）", ""]
    if stat["unresolved_sample"]:
        lines += ["| 源 | 关系 | 目标名称 | 目标文号 |", "| :--- | :--- | :--- | :--- |"]
        for u in stat["unresolved_sample"]:
            lines.append(
                f"| {u['src_ref'] or '—'} | {u['relation']} | {u['dst_name']} | "
                f"{u['dst_docno'] or '—'} |"
            )
    else:
        lines.append("（无）")
    lines += ["", "## 五、按源统计", "", "| 源 | 关系数 |", "| :--- | ---: |"]
    by_src: dict[str, int] = {}
    for r in rows:
        by_src[r["src_source"]] = by_src.get(r["src_source"], 0) + 1
    for k, v in sorted(by_src.items(), key=lambda kv: -kv[1]):
        lines.append(f"| {k} | {v} |")
    lines.append("")
    return "\n".join(lines)


def write_report(rows: list[dict], stat: dict) -> str:
    """生成人类可读关系图谱摘要（classifier 报告族的一份；渲染见 `render_report`）。"""
    content = render_report(rows, stat)
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as fh:
        fh.write(content)
    return REPORT_PATH


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description="依据/废止关系统一抽取（三类关系产物）")
    ap.add_argument(
        "--source",
        action="append",
        default=None,
        help="限定监管源（可多次；默认 gov/mof/nfra/pbc/supp）",
    )
    ap.add_argument("--no-internal", action="store_true", help="不抽取内部制度")
    ap.add_argument("--with-attachments", action="store_true", help="并入附件正文抽取")
    ap.add_argument("--limit", type=int, default=0, help="每个源最多处理 N 份（0=全部）")
    ap.add_argument("--dry-run", action="store_true", help="只统计不落盘")
    ap.add_argument("--report", action="store_true", help="额外生成关系图谱报告")
    a = ap.parse_args()
    run(
        sources=a.source,
        limit=a.limit,
        dry_run=a.dry_run,
        with_internal=not a.no_internal,
        with_attachments=a.with_attachments,
        report=a.report,
    )
    return ExitCode.OK


if __name__ == "__main__":
    sys.exit(main())
