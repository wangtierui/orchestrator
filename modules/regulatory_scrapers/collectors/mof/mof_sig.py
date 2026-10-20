# -*- coding: utf-8 -*-
"""mof/mof_sig — 增量比对用的**签名/规范化**纯函数。

为什么单独成文件（2026-10-20 第一百零五批）
------------------------------------------
`_sig_current` / `_sig_prev` / `_sig_eq` / `_canon_law_id` 是**纯函数**
（无 IO、无可变状态），只在"判断某条记录是否变化"时被 `main` 调用。
它们与网络/落盘/锁完全无关，混在 800 行的 collector 里让"增量策略"这条
最重要的业务规则难以被单独阅读与测试。

`_sig_current` 用到 `html_to_text` / `normalize_date`（附件层已下沉的纯变换），
从 `mof_constants` 取 ⇒ 与 collector 无循环依赖。
"""
from __future__ import annotations

# ---- P3b 仓库引导（migrate_collectors_p3b.py 注入）：使 std_lib/config 可导入 ----
import os as _os
import sys as _sys

_GUIDE_ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "..", "..", ".."))
if _GUIDE_ROOT not in _sys.path:
    _sys.path.insert(0, _GUIDE_ROOT)
del _GUIDE_ROOT, _os, _sys



try:
    from .mof_constants import (
        html_to_text,
        normalize_date,
    )
except ImportError:  # pragma: no cover  脚本形态
    from mof_constants import (  # type: ignore[no-redef]
        html_to_text,
        normalize_date,
    )


def _sig_current(rec):
    """当前列表记录的变更指纹（用于增量 diff：标题/正文/日期/文号/机关/题注）。"""
    return (
        rec.get("title"),
        html_to_text(rec.get("lcontent") or ""),
        normalize_date(rec.get("lBuDate")),
        normalize_date(rec.get("lSsDate")),
        rec.get("lwh"),
        rec.get("lbbdw"),
        rec.get("lcaption"),
    )


def _sig_prev(entry):
    """历史存储条目的变更指纹（与 _sig_current 口径对齐）。"""
    return (
        entry.get("title"),
        entry.get("content_text"),
        entry.get("publish_date"),
        entry.get("effective_date"),
        entry.get("doc_no"),
        entry.get("issue_org"),
        entry.get("summary"),
    )


def _sig_eq(a, b):
    """指纹等价比较：把 None 归一为 '' 后再比。

    背景（2026-09-22 周调度**P0 定位**）：`_sig_current` 对列表侧缺失/不可解析的
    `lSsDate` 走 `normalize_date` → **None**，而主库历史值多为 **''**；`None != ''`
    使 **254 条/周**被误判为"变更" → 触发无谓的详情+附件重抓（附件为本地重抽取，
    单条数秒级）→ 整轮跑不完、主库长期不更新。该字段在列表接口中本就不可得，
    排除其 null 形态差异不损失任何真实可检测的变更能力。
    """
    return (tuple("" if x is None else x for x in a)
            == tuple("" if x is None else x for x in b))


def _canon_law_id(v):
    """法规主键统一到**单一类型**（主库既有约定：纯数字 → int）。

    背景（2026-09-22 周调度**P0 定位**）：`fgk.mof.gov.cn` 列表接口现以**字符串**
    返回 `id`，而主库历史条目为 **int**（同一数值）。原实现 `prev_by_id.get(rid)`
    按原类型取值 → 恒不命中 → **每一条都被判为 "added"**，记录级指纹增量彻底失效，
    表现为每周对全部 ~870 条重抓详情+附件（数小时），主库因此长期停留在旧日期。
    实测：`OVERLAP_raw=0 / OVERLAP_str=90`（100% 同值异型）。
    """
    if v is None:
        return None
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int):
        return v
    s = str(v).strip()
    return int(s) if s.lstrip("-").isdigit() else s


__all__ = ['_canon_law_id', '_sig_current', '_sig_eq', '_sig_prev']
