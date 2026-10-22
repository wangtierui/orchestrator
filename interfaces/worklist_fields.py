# -*- coding: utf-8 -*-
"""interfaces.worklist_fields — 待办条目的**展示字段派生**（单一事实源）。

为什么有这个模块
----------------
生产者写 `subject` 的口径不统一（有时是文号、有时是名称、有时是 RFN/IPN），
业务方无法判断"这条到底指哪份文件"。故按 **kind 能力矩阵** 从 `payload` 取实体字段，
统一派生：**唯一标识号 / 名称 / 文号 / 访问地址 / 本地文件 / 说明**。

2026-10-22（第一百零九批）修的三类真实缺陷
==========================================
**(1) 列与内容不对应**（用户报：`rfn_clean_drift_c2` 名称显示 RFN 编号、文号"未查到"）
根因：`derive()` 只认 `payload.title/document_number`，而该 kind 的真实实体在
**`payload.clean_title` / `payload.clean_docno`**（它描述的是"C2 漂移里 clean 侧那份"），
于是全部回退到 `subject`（= RFN）。修法：改为**逐 kind 的取值键表**（`KIND_SPEC`），
并在字段缺失时区分 `not_applicable`（该 kind 无此概念）与 `not_found`（查不到）。

**(2) 访问地址张冠李戴**（用户报：点"营销行为管理通知"却打开"消费者权益保护评价办法"）
根因：原实现**先按文号命中就直接返回 URL**，从不校验命中的是不是**同一份文件**；
只要某条待办的 `document_number` 与 `title` 来自不同文件（payload 混装/登记录错），
就会稳定地打开另一份文档。修法：命中后做**一致性校验**——
文号命中要求标题相似（反之亦然），不通过则**不返回 URL**，改为
`url_src="mismatch"` 并把两侧标题一起回传，让人**看见**"这里对不上"，
而不是给一个看起来能点、点开却是别的东西的链接。

**(3) 本地文件无法点开**（用户报：内部制度等本地文件需要能直接打开）
修法：`internal_unaligned` 按 `ipn` 反查 `internal_policy_index.json` 的
`original_path`，派生出**仓内相对路径**（`local_path`）；由 `GET /api/local` 提供
**受限于白名单根目录**的只读访问（见 `web/api.py`）。

设计纪律
--------
1. **不猜**：字段只从 `payload` 的显式键取；取不到才走桥表反查。
2. **降级可辨**：每个字段带 `*_src`：`payload:<键>` / `bridge:title` / `bridge:docno` /
   `ipn` / `subject` / `local:file` / `not_applicable` / `not_found` / `mismatch` / `warming`。
   **绝不把"查不到"渲染成空白**（本仓反复踩过的坑）。
3. **汇总项单列**：`relevance_boundary` 的 `summary:NNN` 不是"某份文件"⇒ `aggregate`。
"""
from __future__ import annotations

import threading
from typing import Any

#: **归一化单一事实源**（`std_lib/common_lib/norm.py`）。
#: 本模块只在它之上做两处**极小特化**（均有 `norm-specialization` 豁免标记与理由）：
#:   ① 标题先解码 HTML 实体（跨表匹配需要，SSOT 不解码）；
#:   ② 文号先把 `【】` 换成 `[]`（SSOT 的 `_DOC_CLEAN` 未覆盖该形态）。
#: 其余规则（去括号/书名号/空白/尾注、`N/A` 占位、尾部 `号`）**一律复用 SSOT**，
#: 避免两套归一口径分叉（门禁 `gate_no_duplicate_libs` 亦要求如此）。
from std_lib.common_lib.norm import norm_docno as _S_norm_docno
from std_lib.common_lib.norm import norm_title as _S_norm_title

#: 需要「名称 + 文号 + 访问地址」的 kind
NEEDS_URL = frozenset({"relevance_boundary", "timeliness_conflict",
                       "rfn_theme_uncertain"})

#: 只需「名称 + 文号」的 kind（该域无对外 URL）
NAME_NO = frozenset({"internal_unaligned"})

#: 全部受支持的 kind（前端据此决定列）
SUPPORTED = NEEDS_URL | NAME_NO

#: **kind 能力矩阵**（唯一事实源）。每项：
#:   title/docno   从 payload 取值的**候选键元组**（按序取第一个非空）
#:   identity      唯一标识号的取值键（RFN/IPN/文号）
#:   url           是否应给"访问地址"（外部网页）
#:   local         是否应给"本地文件"（仓内文件，可通过 /api/local 打开）
#:   note          补充说明的取值键（渲染在"详情"列）
#:
#: ⚠️ 新增 kind 必须在此登记，否则 `derive()` 只能退回 `subject`（且会标 `not_applicable`），
#: 守卫 `tests/test_worklist_fields.py::TestKindCoverage` 会检查"实际出现的 kind 都在矩阵里"。
KIND_SPEC: dict[str, dict[str, Any]] = {
    "relevance_boundary": {
        "title": ("title", "文件名称", "name"),
        "docno": ("document_number", "发文字号", "docno"),
        "identity": ("document_number",),
        "url": True, "local": False,
        "note": ("confidence", "snippet"),
    },
    "timeliness_conflict": {
        "title": ("title", "文件名称", "name"),
        "docno": ("document_number", "发文字号", "docno"),
        "identity": ("document_number",),
        "url": True, "local": False,
        "note": ("final_status", "reason"),
    },
    "rfn_theme_uncertain": {
        "title": ("title", "文件名称", "name"),
        "docno": ("document_number", "发文字号", "docno"),
        "identity": ("rfn",),
        "url": True, "local": False,
        "note": ("reason",),
    },
    #: C2 漂移：payload 描述的是**clean 侧那份**（`clean_title`/`clean_docno`），
    #: 唯一标识是 RFN。原实现漏了这两个键 ⇒ 名称退化成 RFN（用户报的就是这个）。
    "rfn_clean_drift_c2": {
        "title": ("clean_title", "title"),
        "docno": ("clean_docno", "document_number"),
        "identity": ("rfn",),
        "url": True, "local": False,
        "note": ("reason", "matched_by"),
    },
    #: 内部制度：无外部文号，标识是 IPN；内容在**本地文件**里（original_path）。
    "internal_unaligned": {
        "title": ("title",),
        "docno": (),
        "identity": ("ipn",),
        "url": False, "local": True,
        "note": ("method",),
    },
    #: 清洗隔离：payload 是"某类问题的汇总"（class/count/file/sample），无单条实体。
    "clean_quarantine_triage": {
        "title": ("sample", "class"),
        "docno": (),
        "identity": (),
        "url": False, "local": False,
        "note": ("class", "count", "file"),
    },
    #: 人工断点：payload 是自由文本（待裁决/现状/缺陷…），没有实体字段。
    "trigger_manual_breakpoint": {
        "title": (),
        "docno": (),
        "identity": (),
        "url": False, "local": False,
        "note": ("现状", "待裁决"),
    },
}


# norm-specialization: 标题归一**复用 SSOT**，仅前置解码 HTML 实体。
#   理由：桥表与 clean 产物的标题来自不同采集路径，个别含 `&lt;` 等实体
#   （实测 clean 里有 `&lt;` 形态），而 SSOT 的 `norm_title` 只去标点、不解码实体
#   （实测 `&lt;测试&gt;决定` → `&lt测试&gt决定`，实体残留会破坏跨表匹配）。
#   实体解码是**前置修正**，不复制 SSOT 的归一逻辑。
def _norm_title(s: str) -> str:
    """标题归一：前置实体解码 + SSOT `norm_title`。"""
    s = (s or "")
    for a, b in (("&lt;", "<"), ("&gt;", ">"), ("&amp;", "&"),
                 ("&quot;", '"'), ("&#39;", "'")):
        s = s.replace(a, b)
    return _S_norm_title(s)


# norm-specialization: 文号归一**复用 SSOT**，仅前置把中文方头括号 `【】` 换成 `[]`。
#   理由：SSOT 的 `_DOC_CLEAN` 覆盖 `〔〕（）[]` 但**未覆盖 `【】`**，
#   而 clean 产物里确实存在该形态（实测 `保监发【2014】19号`）。
#   先补正字符再归一，不复制 SSOT 的剥离规则。
def _norm_docno(s: str) -> str:
    """文号归一：前置 `【】` 补正 + SSOT `norm_docno`。"""
    s = (s or "").replace("【", "[").replace("】", "]")
    return _S_norm_docno(s)


def _similar(a: str, b: str) -> bool:
    """两个**已归一**标题是否指向同一份文件。

    判据（宁可判"不一致"也不给错链接）：
      · 完全相同 → 一致；
      · 一方包含另一方（短的 ≥6 字）→ 一致（应对"补书名号/加后缀"的正规差异）；
      · 否则相似度 ≥0.82 → 一致；其余 → **不一致**。

    为什么用 0.82：实测同一文件在不同表的标题差异主要是**书名号与空格**
    （归一后已消除）或**末尾"的通知"**（包含关系已覆盖）；真正的不同文件
    （如"…营销行为管理的通知" vs "…消费者权益保护监管评价办法"）相似度在 0.3~0.5。
    """
    if not a or not b:
        return False
    if a == b:
        return True
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    if len(short) >= 6 and short in long_:
        return True
    from difflib import SequenceMatcher

    return SequenceMatcher(None, a, b).ratio() >= 0.82


#: 保护 `_Bridge._building` 的**模块级**短锁。
#: 放模块级而非类体：在类体里写 `_lock = threading.Lock()` 时，`threading` 是
#: 类作用域的名称查找（类体不参与闭包），会 NameError —— 这是 Python 类作用域的
#: 经典坑，也是本文件为什么要用模块级锁的原因。
_BRIDGE_LOCK = threading.Lock()


class _Bridge:
    """访问地址反查索引（懒加载 + 一次建索引，避免 N 次全表扫）。

    **两级来源**（逐级降级，每级都可辨）：
      1. `rfn_clean_bridge.csv`（`登记时标题`/`登记时文号` → `source_url`）——
         只覆盖**已进 RFN 且已 reconcile** 的文件（944 行）。
      2. **clean 产物**（`*_cleaned_*.jsonl` 的 `title`/`document_number`
         → `source_url`）——覆盖**全部已采集**文件（实测 14746 个标题键）。

    2026-10-22：索引值从**裸 URL** 改为 **记录字典**（`{url,title,docno,from}`）——
    因为"命中"不再等于"正确"：必须能拿到命中那条的标题/文号去做一致性校验。
    """

    _by_title: dict[str, dict] | None = None
    _by_docno: dict[str, dict] | None = None
    #: 后台构建中（防重入：多个请求同时触发时只允许一个线程扫 1.6GB）
    _building: bool = False
    #: 保护 `_building` 的短锁（**不保护整段构建**：那会把后台线程变成串行阻塞）
    _lock = _BRIDGE_LOCK

    @classmethod
    def _add(cls, by_t: dict[str, dict], by_d: dict[str, dict],
             title: str, docno: str, url: str, origin: str) -> None:
        url = (url or "").strip()
        if not url:
            return
        rec = {"url": url, "title": (title or "").strip(),
               "docno": (docno or "").strip(), "from": origin}
        t = _norm_title(title)
        d = _norm_docno(docno)
        if t and t not in by_t:
            by_t[t] = rec
        if d and d not in by_d:
            by_d[d] = rec

    @classmethod
    def _build(cls) -> None:
        if cls._by_title is not None:
            return
        by_t: dict[str, dict] = {}
        by_d: dict[str, dict] = {}
        # ── 级 1：桥表 ──────────────────────────────────────────────
        try:
            from interfaces import rfn_api

            for r in rfn_api.bridge_rows():
                cls._add(by_t, by_d,
                         str(r.get("登记时标题") or ""),
                         str(r.get("登记时文号") or ""),
                         str(r.get("source_url") or ""), "bridge")
        except Exception:  # noqa: BLE001  桥表不可读 ⇒ 降级到级 2（可辨）
            pass
        # ── 级 2：clean 产物 ────────────────────────────────────────
        try:
            import glob
            import json as _json
            import os as _os

            root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
            pats = ("data/docs/cleaned/*_cleaned_*.jsonl",
                    "modules/regulatory_scrapers/data/cleaned/*_cleaned_*.jsonl")
            seen_files: set[str] = set()
            for pat in pats:
                for f in sorted(glob.glob(_os.path.join(root, pat)), reverse=True):
                    # 每个源只读**最新一份**（文件名含日期，倒序首见即最新）
                    src = _os.path.basename(f).split("_cleaned_")[0]
                    if src in seen_files:
                        continue
                    seen_files.add(src)
                    try:
                        with open(f, encoding="utf-8", errors="replace") as fh:
                            for ln in fh:
                                if '"source_url"' not in ln:
                                    continue
                                try:
                                    d = _json.loads(ln)
                                except ValueError:
                                    continue
                                cls._add(by_t, by_d,
                                         str(d.get("title") or ""),
                                         str(d.get("document_number") or ""),
                                         str(d.get("source_url") or ""), "clean")
                    except OSError:
                        continue
        except Exception:  # noqa: BLE001
            pass
        cls._by_title, cls._by_docno = by_t, by_d

    @classmethod
    def is_ready(cls) -> bool:
        """索引是否已可用（`False` 表示"还在建"或"建失败"）。"""
        return cls._by_title is not None

    @classmethod
    def build_async(cls) -> None:
        """**后台**建索引（幂等；已在建则不重复）。

        为什么需要（2026-10-21，第一百零六批实测）：索引要扫 **16 个 clean 产物
        共 1.6GB**，冷态 **6513 ms**。若在请求线程里同步建，首个 `/api/worklist`
        要等 6.5 秒，且会让"URL 未找到"与"索引还没建好"混为一谈 —— **后者是假阴性**。
        """
        import threading

        with cls._lock:
            if cls._by_title is not None or cls._building:
                return
            cls._building = True

        def _worker() -> None:
            try:
                cls._build()
            except Exception:  # noqa: BLE001  建失败 ⇒ 视为"无索引"（退化为未找到）
                cls._by_title = cls._by_title or {}
            finally:
                with cls._lock:
                    cls._building = False

        threading.Thread(target=_worker, name="worklist-fields-build",
                         daemon=True).start()

    @classmethod
    def lookup(cls, title: str, docno: str) -> dict:
        """反查访问地址，**并校验命中的是否同一份文件**。

        返回 `{"url", "how", "note"}`：
          · `how` ∈ `bridge:docno` / `bridge:title` / `mismatch` / `warming` / `not_found`
          · `mismatch` 时 `url` 为空，`note` 说明"文号命中 X、标题却是 Y"——
            **宁可不给链接，也不给错链接**（用户报的"点开是另一份文件"就是这个）。
        """
        if cls._by_title is None:
            cls.build_async()
            if cls._by_title is None:
                return {"url": "", "how": "warming", "note": ""}
        nt, nd = _norm_title(title), _norm_docno(docno)
        hit = None
        how = ""
        if nd and cls._by_docno and nd in cls._by_docno:
            hit, how = cls._by_docno[nd], "bridge:docno"
        elif nt and cls._by_title and nt in cls._by_title:
            hit, how = cls._by_title[nt], "bridge:title"
        if hit is None:
            return {"url": "", "how": "not_found", "note": ""}
        # ── 一致性校验（**核心**）──
        if how == "bridge:docno" and nt and hit.get("title"):
            if not _similar(nt, _norm_title(hit["title"])):
                return {"url": "", "how": "mismatch",
                        "note": (f"文号命中「{hit['docno']}」，但其标题是"
                                 f"「{hit['title'][:32]}」，与本条名称不一致 ⇒ 不提供链接")}
        if how == "bridge:title" and nd and hit.get("docno"):
            if not _similar(nd, _norm_docno(hit["docno"])):
                return {"url": "", "how": "mismatch",
                        "note": (f"名称命中「{hit['title'][:28]}」，但其文号是"
                                 f"「{hit['docno']}」，与本条文号不一致 ⇒ 不提供链接")}
        return {"url": hit["url"], "how": how, "note": ""}

    @classmethod
    def url_for(cls, title: str, docno: str) -> tuple[str, str]:
        """兼容旧签名：`(url, how)`。新代码请用 `lookup()`（带校验与说明）。"""
        r = cls.lookup(title, docno)
        return r["url"], r["how"]

    @classmethod
    def reset(cls) -> None:
        """清索引（测试用；桥表/clean 更新后需重建）。"""
        cls._by_title = cls._by_docno = None
        with cls._lock:
            cls._building = False


#: `ipn → 本地文件相对路径` 缓存：`(mtime, map)`。
#: 为什么按 mtime 失效而不是定时：索引由 `cli.py internal` 重建，
#: 用 mtime 可"重建后立刻生效"，无需重启确认台。
_LOCAL_CACHE: tuple[float, dict[str, str]] | None = None


def local_for(ipn: str) -> str:
    """按 IPN 反查**仓内相对路径**（内部制度原件）；查不到返回 `""`。

    数据源：`modules/internal_policy_base/data/internal_policy_index.json`
    （861 条，字段 `ipn`/`original_path`/`relative_path`）。
    路径一律转成**相对仓根**的 POSIX 形式——绝对路径不出口，
    避免把本机目录结构泄露到前端，也让链接在异机部署时不失效。
    """
    global _LOCAL_CACHE
    if not ipn:
        return ""
    import json
    import os

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    p = os.path.join(root, "modules", "internal_policy_base", "data",
                     "internal_policy_index.json")
    try:
        mtime = os.path.getmtime(p)
    except OSError:
        return ""
    if _LOCAL_CACHE is None or _LOCAL_CACHE[0] != mtime:
        m: dict[str, str] = {}
        try:
            d = json.load(open(p, encoding="utf-8"))
            # ⚠️ `original_path` 的基准**不是仓根**：它相对 `source_root` 的**父目录**
            # （实测 `source_root=…/internal_policy_base/data/originals`，
            #  而 `original_path="originals\\xxx.pdf"` ⇒ 真实位置
            #  `…/internal_policy_base/data/originals/xxx.pdf`）。
            # 直接拿 `original_path` 或 `join(source_root, original_path)` 都会 100% miss
            # （实测 861 条全部解析不到）——所以这里**逐个候选试存在性**，
            # 命中即用；都不中才放弃（宁可"打不开"，不可给一个错路径）。
            src_root = str(d.get("source_root") or "")
            base = os.path.dirname(src_root) if src_root else ""
            for r in (d.get("records") or []):
                k = str(r.get("ipn") or "")
                if not k:
                    continue
                orig = str(r.get("original_path") or "")
                relp = str(r.get("relative_path") or "")
                cands = []
                if orig and base:
                    cands.append(os.path.join(base, orig))
                if relp and src_root:
                    cands.append(os.path.join(src_root, relp))
                if orig and src_root:
                    cands.append(os.path.join(src_root, os.path.basename(orig)))
                hit = next((c for c in cands if os.path.isfile(c)), "")
                if not hit:
                    continue
                try:
                    rel = os.path.relpath(hit, root)
                except ValueError:
                    continue
                rel = rel.replace("\\", "/")
                if rel.startswith("../"):
                    continue          # 仓外文件不出口（安全）
                m[k] = rel
        except (OSError, ValueError):
            m = {}
        _LOCAL_CACHE = (mtime, m)
    return _LOCAL_CACHE[1].get(ipn, "")


def _payload(item: dict[str, Any]) -> dict:
    p = item.get("payload")
    return p if isinstance(p, dict) else {}


def _pick(p: dict, keys: tuple[str, ...]) -> str:
    """按 `keys` 顺序取第一个非空（**只认显式键**，不做模糊搜索）。"""
    for k in keys:
        v = p.get(k)
        if v not in (None, "", [], {}):
            return str(v).strip()
    return ""


def derive(item: dict[str, Any]) -> dict[str, Any]:
    """为一条待办派生展示字段（**逐 kind 能力矩阵驱动**）。

    返回：
      `{kind, aggregate, identity, identity_src, title, title_src,
        document_number, document_number_src, url, url_src, url_note,
        local_path, local_src, note}`
    """
    kind = str(item.get("kind") or "")
    p = _payload(item)
    subject = str(item.get("subject") or "").strip()
    spec = KIND_SPEC.get(kind, {})

    out: dict[str, Any] = {
        "kind": kind, "aggregate": False,
        "identity": "", "identity_src": "not_applicable",
        "title": "", "title_src": "not_applicable",
        "document_number": "", "document_number_src": "not_applicable",
        "url": "", "url_src": "not_applicable", "url_note": "",
        "local_path": "", "local_src": "not_applicable",
        "note": "",
    }

    # ── 汇总条目（relevance_boundary 的 `summary:NNN`）───────────────
    if "total" in p and "registered" in p and not _pick(p, ("title",)):
        out.update({
            "aggregate": True,
            "title": f"汇总项（未逐条登记）：候选 {p.get('total')} 条，"
                     f"已登记 {p.get('registered')} 条",
            "title_src": "aggregate",
            "identity_src": "aggregate",
            "extra": {"total": p.get("total"),
                      "registered": p.get("registered"),
                      "note": str(p.get("note") or "")},
        })
        return out

    # ── 唯一标识号（RFN / IPN / 文号）──────────────────────────────
    ident = _pick(p, tuple(spec.get("identity") or ()))
    if ident:
        key = str((spec.get("identity") or ("",))[0])
        out["identity"], out["identity_src"] = ident, f"payload:{key}"
    elif kind in NEEDS_URL or kind in NAME_NO:
        # 契约要求有标识却没有 ⇒ 如实标"查不到"（不拿 subject 冒充）
        out["identity_src"] = "not_found"

    # ── 名称 ─────────────────────────────────────────────────────
    title = _pick(p, tuple(spec.get("title") or ()))
    if title:
        out["title"], out["title_src"] = title, "payload:title"
    elif subject and not spec.get("title"):
        # 该 kind 没有实体名称（如人工断点/汇总）⇒ 用 subject，但**标注来源**
        out["title"], out["title_src"] = subject, "subject"
    elif subject:
        out["title"], out["title_src"] = subject, "subject"

    # ── 文号 ─────────────────────────────────────────────────────
    docno = _pick(p, tuple(spec.get("docno") or ()))
    if docno:
        out["document_number"], out["document_number_src"] = \
            docno, "payload:document_number"
    elif spec.get("docno"):
        out["document_number_src"] = "not_found"
    else:
        out["document_number_src"] = "not_applicable"

    # ── 访问地址（外部网页）──────────────────────────────────────
    if spec.get("url"):
        url = _pick(p, ("url", "source_url", "访问地址"))
        if url:
            out["url"], out["url_src"] = url, "payload:url"
        else:
            r = _Bridge.lookup(out["title"], out["document_number"])
            if r["url"]:
                out["url"], out["url_src"] = r["url"], r["how"]
            else:
                out["url_src"] = r["how"] or "not_found"
                out["url_note"] = r.get("note", "")

    # ── 本地文件（内部制度）──────────────────────────────────────
    if spec.get("local"):
        lp = local_for(out["identity"])
        if lp:
            out["local_path"], out["local_src"] = lp, "local:file"
        else:
            out["local_src"] = "not_found"

    # ── 说明（kind 专属）────────────────────────────────────────
    parts = [_pick(p, (k,)) for k in (spec.get("note") or ())]
    out["note"] = " ｜ ".join(x for x in parts if x)
    return out


def lookup(title: str, docno: str) -> dict:
    """模块级便捷入口：`_Bridge.lookup()` 的唯一外部用法（P4/P6 共用）。

    为什么要有它：`_Bridge` 是**实现细节**（懒加载索引 + 一致校验），
    调用方（`web/snapshots.py` 的 P4/P6）只想要"给我一个可信的 URL 或说明"。
    直接调 `_Bridge.lookup` 会把"内部类"变成事实上的公共 API（且容易写成
    `_wf.lookup` 而报 AttributeError —— 实测踩过）。
    """
    return _Bridge.lookup(title, docno)


def needs_url(kind: str) -> bool:
    return kind in NEEDS_URL


def columns() -> dict[str, Any]:
    """给前端的**列契约**（前端不硬编码 kind 语义）。

    返回 `{"columns": [...], "kinds": {kind: {"url":bool, "local":bool,
    "identity":bool, "docno":bool}}}`。
    """
    kinds = {}
    for k, spec in KIND_SPEC.items():
        kinds[k] = {"url": bool(spec.get("url")),
                    "local": bool(spec.get("local")),
                    "identity": bool(spec.get("identity")),
                    "docno": bool(spec.get("docno"))}
    return {"columns": ["identity", "title", "document_number", "url", "local"],
            "kinds": kinds, "needs_url": sorted(NEEDS_URL)}


def decorate(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """给一批待办条目附加 `fields`（**不改原字典语义**，只加一个键）。"""
    out = []
    for it in items:
        d = dict(it)
        d["fields"] = derive(it)
        out.append(d)
    return out
