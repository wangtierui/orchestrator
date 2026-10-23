# -*- coding: utf-8 -*-
"""interfaces.external_api — **外部法规权威表**（独立键空间，编码沿用 RFN）。

要解决的问题（2026-10-23，用户第 3 项）
--------------------------------------
关系事实里有大量目标是**语料外**文件：国家法律（民法典/水法）、行政法规
（国务院令）、部委规章，以及本仓未收录的**公司制度历史版本**。它们有标题/文号，
但没有 `dst_ref`（语料内实体编号）⇒ 图谱上画不出来，只能计数（实测 2632 条）。

"补登记 RFN" 对它们**不成立**：登记 RFN 意味着"我把这份文件收进了语料"，
而那是不折不扣的谎（文件根本不在库内，正文/URL 全无）。

所以建**独立键空间**：
  · 每条外部目标分配一个稳定编号 `RFN-EXT-<12hex>`；
  · 编号由归一化后的 (文号, 标题) 派生 ⇒ **可重算、跨批稳定、无需查表**；
  · `EXT` 命名空间段让"语料外"在**编码层面**就与"语料内实体"（`RFN-<16hex>`）
    不可混淆 —— 这比在展示层加个灰色标签可靠得多（标签会被复制粘贴丢掉，
    编号不会）。

三条纪律
--------
1. **只从事实推导**：本表由 `tools/build_external_authority.py` 从
   `relations_index.jsonl` 的"无 dst_ref 且无 dst_key"行聚合生成，**不手工编辑**
   （手改会在下次重建时被覆盖，且不可追溯）。
2. **不冒充实体**：`in_corpus=False` 恒为假；`kind="external"`；前端必须与
   语料内节点**视觉可分**（虚线/浅色/"外部"标记）。
3. **分成两类指标**：`resolved_to_entity_ratio`（语料内）与 `attributed_ratio`
   （含外部权威）**分别报**，不许合并成一个好看的数字。
"""

from __future__ import annotations

import hashlib
import json
import os

#: 与 `relations_api.RELATIONS_DIR` 同目录（关系事实所在的产物目录）。
#: 这里**不做 sys.path 引导**：`interfaces/` 的引导基线已由门禁冻结（只减不增）；
#: 本模块依赖工程根在 `sys.path` 上，这与其它 interfaces 模块的调用前提一致。
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RELATIONS_DIR = os.path.join(ROOT, "modules", "regulatory_classifier", "data", "relations")
EXT_PATH = os.path.join(RELATIONS_DIR, "external_authority.jsonl")
EXT_STAT_PATH = os.path.join(RELATIONS_DIR, "external_authority_stat.json")

#: 编号前缀。与语料内实体（`RFN-[0-9a-f]{16}`）**正则不相交**，见 `is_ext_id`。
PREFIX = "RFN-EXT-"

#: 外部目标的"面目"分类（**受控值**；新增需同步本元组与前端标签表）
GUISES: tuple[str, ...] = (
    "law",               # 国家法律（…法/法典）
    "admin_regulation",  # 行政法规（国务院令/条例）
    "department_rule",   # 部委规章/规范性文件（…〔年〕N号）
    "internal_history",  # 本公司制度的历史版本（未收录）
    "unknown",           # 只有标题线索，无法判类
)

GUISE_LABELS = {
    "law": "国家法律",
    "admin_regulation": "行政法规",
    "department_rule": "部委规章",
    "internal_history": "制度历史版本",
    "unknown": "未分类",
}


def _docno_key(v: object) -> str:
    """文号归一 —— **直接用 SSOT**（`std_lib.common_lib.norm`），不再包一层。

    为什么不再包 try/except：`norm_docno_strong` 是纯函数（只做正则与映射），
    对任何输入都返回字符串；包一层只会让"归一逻辑"出现第二个实现位（门禁把它
    判成"在册特化符号的本地复制"——它是对的，那就该消灭，而不是加豁免标记）。
    """
    from std_lib.common_lib.norm import norm_docno_strong

    out = norm_docno_strong(v)
    return out if isinstance(out, str) else str(out or "")


def _title_key(v: object) -> str:
    """标题归一 —— 同上，直接走 SSOT。"""
    from std_lib.common_lib.norm import norm_title_rel

    out = norm_title_rel(v)
    return out if isinstance(out, str) else str(out or "")


def key_of(title: object, docno: object) -> str:
    """外部目标的**稳定键**：有文号走文号，否则走标题。

    为什么文号优先：同一部法在不同年份被不同文件引用时，标题常带版本后缀
    （"…（2021版）"），而文号是唯一且稳定的。没有文号时才退到标题
    （实测 2632 条里 608 条有文号、全部有标题）。
    """
    d = _docno_key(docno)
    if d:
        return "docno:" + d
    return "title:" + _title_key(title)


def make_id(key: str) -> str:
    """键 → `RFN-EXT-<12hex>`（纯函数：同一键永远同一编号）。"""
    return PREFIX + hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def resolve(title: object, docno: object) -> str:
    """由标题/文号直接算出外部编号（**不需要查表** ⇒ ego 可零成本接上）。"""
    k = key_of(title, docno)
    # 标题与文号都为空时给空串：无证据的目标不该拿到编号（否则等于伪造一条记录）
    if k in ("title:", "docno:"):
        return ""
    return make_id(k)


def is_ext_id(v: object) -> bool:
    """是否外部权威编号（`RFN-EXT-` + 12 位小写十六进制）。"""
    s = str(v or "")
    if not s.startswith(PREFIX):
        return False
    body = s[len(PREFIX):]
    return len(body) == 12 and all(c in "0123456789abcdef" for c in body)


def guess_guise(title: object, docno: object) -> str:
    """按标题/文号猜"面目"（**只用于展示分组**，不参与关系判定）。

    判定顺序即优先级：先判"本公司制度"（含公司名/发文代字），再判法律/行政法规，
    最后落到部委规章/未知。宁可 unknown 也不硬塞 —— 这一列只影响分组显示。
    """
    t = str(title or "")
    d = _docno_key(docno)
    if "阳光" in t or "阳光" in d:
        return "internal_history"
    if "法典" in t or t.endswith("法"):
        return "law"
    if "国务院令" in t or t.endswith("条例"):
        return "admin_regulation"
    if d and ("〔" in d or "[" in d or "第" in d) and ("公告" in t or "令" in t
                                                      or "通知" in t or "办法" in t):
        return "department_rule"
    if t.endswith(("办法", "规定", "细则", "规则", "指引", "通知")):
        return "department_rule"
    return "unknown"


# --------------------------------------------------------------------------- #
# 读取（表由 tools/build_external_authority.py 生成；本模块只读）
# --------------------------------------------------------------------------- #
def exists() -> bool:
    return os.path.exists(EXT_PATH)


def load() -> list[dict]:
    """读全表（缺文件返回空表 —— 调用方据此降级，不抛异常）。"""
    if not os.path.exists(EXT_PATH):
        return []
    out: list[dict] = []
    with open(EXT_PATH, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def load_stat() -> dict:
    if not os.path.exists(EXT_STAT_PATH):
        return {"available": False, "reason": "外部权威表未生成",
                "source": "interfaces.external_api"}
    try:
        with open(EXT_STAT_PATH, encoding="utf-8") as fh:
            d = json.load(fh)
        d["available"] = True
        return d
    except (OSError, json.JSONDecodeError) as e:
        return {"available": False, "reason": f"{type(e).__name__}: {str(e)[:120]}"}


def index() -> dict[str, dict]:
    """`{ext_id: row}`（供 ego / 列表按 id 取元数据）。"""
    return {str(r.get("ext_id")): r for r in load() if r.get("ext_id")}


def get(ext_id: str) -> dict | None:
    return index().get(str(ext_id or ""))


def list_authorities(*, q: str = "", guise: str = "", limit: int = 200) -> dict:
    """按关键词/面目筛选（列表页与自检用）。"""
    rows = load()
    q = (q or "").strip().lower()
    out = []
    for r in rows:
        if guise and r.get("guise") != guise:
            continue
        if q:
            hay = " ".join(str(r.get(k) or "") for k in
                           ("ext_id", "title", "docno", "title_norm")).lower()
            if q not in hay:
                continue
        out.append(r)
    out.sort(key=lambda x: (-int(x.get("evidence_count") or 0), str(x.get("ext_id"))))
    return {"available": True, "items": out[:limit], "total": len(out),
            "guises": list(GUISES), "guise_labels": dict(GUISE_LABELS),
            "source": "interfaces.external_api"}


def summary() -> dict:
    """对外一页话：有多少外部目标、被引用多少次、按面目分布。"""
    st = load_stat()
    if not st.get("available"):
        return st
    return {
        "available": True,
        "total": st.get("total", 0),
        "by_guise": st.get("by_guise", {}),
        "evidence_total": st.get("evidence_total", 0),
        "generated_at": st.get("generated_at", ""),
        "source": "interfaces.external_api",
        "note": ("外部权威是**语料外**文件的登记键空间：编号 `RFN-EXT-*` 表示"
                 "「被引用但未收录」，不代表库里有这份文件。"),
    }


if __name__ == "__main__":  # 离线自检
    import sys as _sys  # 局部导入：本模块主体**不需要** sys（门禁禁新增引导）

    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    print(json.dumps({"stat": load_stat(), "sample": load()[:1]},
                     ensure_ascii=False, indent=2))
