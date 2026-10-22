# -*- coding: utf-8 -*-
"""std_lib.common_lib.timeliness_ssot — 时效 SSOT 的**唯一**匹配与权威性判据。

为什么需要这个模块（2026-10-22，TD-21）
======================================
`verification_state.json`（SSOT）与归属表「时效状态」列之间出现 40 条漂移：
SSOT 有权威结论（valid/repealed/amended），归属表仍是 `pending`。根因是
**传播链缺一段**：`verify` 产出「变更台账」→ `timeliness sync` **只读最新一份台账**
写归属表；而台账只在 `verify` **成功跑完**时才产出。今晨 `verify` 跑了 4662s 后
**因 pkulaw 配额耗尽失败**（rc=2）⇒ 无台账 ⇒ 40 条权威结论永远写不进归属表。
（详见 `docs/tech_debt.md` TD-21 / TD-21 的当日实证。）

修法不能只是"再写一个同步函数"——**判据必须同源**。原实现把"什么叫权威结论"
写在门禁里（`gates/gate_timeliness_ssot.py` 层1），若命令侧另写一份匹配/过滤逻辑，
两份必然分叉：门禁认为不算漂移的（陈旧/pending/规则判断），命令却去改，
就会**覆盖人工判定**；反之门禁认为漂移的，命令却抓不到，漂移永远修不掉。
故本模块承载这套判据，门禁与 `timeliness sync --from-state` 都只调它。

权威性（可断言）的四个必要条件
------------------------------
一条 state 记录**只有同时满足**下列条件，才允许作为"覆盖归属表"的依据：

1. `status` ∉ {`pending`, `uncertain`} —— 那两个是"核验占位"，不是结论；
2. 核验未超期（`last_checked_at` 距今 ≤ 90 日）—— 陈旧结论由下次 verify 刷新，
   不能拿它去改人工判定；
3. `verification_source` 不含「无同名命中」「规则判断」—— 这两种是"法宝没查到 /
   纯规则推断"，只是把原值记录下来，**没有形成权威结论**（否则 1439 条非结论记录
   会与人工归属表大面积假漂移，2026-09-11 已修过一次）；
4. 归属表该行**当前非空**（空 = 尚未定级/待人工），且与本条结论不同。

匹配顺序（与门禁历史行为**逐字一致**）
--------------------------------------
先按**发文字号**归一匹配（要求归一后长度 ≥ 5，避免 `〔2026〕1号` 这类过短噪声
误配），再按**标题**归一后取前 40 字匹配。两者都不中 ⇒ 该行"无 SSOT 记录"，
不算漂移（未核验，不臆断）。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from std_lib.common_lib.norm import norm_docno, norm_title_strict

#: 核验结论的保质期（日）。超过则视为"待重验"，不参与断言（也不用于回填）。
FRESH_DAYS = 90

#: 核验占位状态：不是结论
PLACEHOLDER_STATUS = frozenset({"pending", "uncertain"})

#: `verification_source` 里出现这些词 ⇒ **未形成权威结论**（仅记录原值）
NON_AUTHORITATIVE_MARKERS = ("无同名命中", "规则判断")

#: 发文字号归一后的最小可用长度（短于此易误配）
MIN_DOCNO_LEN = 5

#: 标题匹配键的截断长度（与门禁历史实现一致）
TITLE_KEY_LEN = 40


def index_state(state: dict) -> tuple[dict[str, dict], dict[str, dict]]:
    """把 `verification_state` 拆成 `(by_docno, by_title)` 两个索引。

    键形式：state 里的 `"doc:<归一文号>"` / `"title:<归一标题前 40 字>"`。
    非这两种前缀的键（历史遗留/其它命名空间）忽略。
    """
    by_docno: dict[str, dict] = {}
    by_title: dict[str, dict] = {}
    for k, rec in (state or {}).items():
        if not isinstance(rec, dict):
            continue
        if k.startswith("doc:"):
            by_docno[k[4:]] = rec
        elif k.startswith("title:"):
            by_title[k[6:]] = rec
    return by_docno, by_title


def _is_fresh(rec: dict, fresh_days: int = FRESH_DAYS) -> bool:
    """核验是否在保质期内（`last_checked_at` 解析失败 ⇒ 视为不新鲜）。"""
    try:
        last = datetime.strptime(rec.get("last_checked_at", ""), "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return False
    return (datetime.now() - last) <= timedelta(days=fresh_days)


def authority(rec: dict, fresh_days: int = FRESH_DAYS) -> tuple[bool, str]:
    """判断该 state 记录**能否作为覆盖依据**。返回 `(是否权威, 原因)`。

    原因取值（供报告/日志分类，非受控枚举）：
      `ok` / `placeholder` / `stale` / `non_authoritative_source`
    """
    st = (rec.get("status") or "").strip()
    if st in PLACEHOLDER_STATUS:
        return False, "placeholder"
    if not _is_fresh(rec, fresh_days):
        return False, "stale"
    vsrc = rec.get("verification_source", "") or ""
    if any(m in vsrc for m in NON_AUTHORITATIVE_MARKERS):
        return False, "non_authoritative_source"
    return True, "ok"


def match_row(row: dict, by_docno: dict[str, dict],
              by_title: dict[str, dict]) -> dict | None:
    """按「文号优先、标题兜底」匹配归属表行 → state 记录（无则 `None`）。"""
    nd = norm_docno(row.get("发文字号", ""))
    rec = by_docno.get(nd) if nd and len(nd) >= MIN_DOCNO_LEN else None
    if rec is None:
        nt = norm_title_strict(row.get("文件名称", ""))
        rec = by_title.get(nt[:TITLE_KEY_LEN])
    return rec


def drift_pairs(rows: list[dict], state: dict, *,
                fresh_days: int = FRESH_DAYS) -> dict:
    """算出**权威且与归属表不一致**的行（门禁层1 与回填命令的唯一实现）。

    返回::

        {
          "pairs":   [{"rfn", "docno", "title", "attr", "state", "key"}, ...],
          "checked": int,          # 命中 state 的行数（层1 覆盖数）
          "matched": int,          # 同上（别名，便于阅读）
          "no_state": int,         # 无 state 记录（跳过）
          "skipped": {"placeholder": n, "stale": n, "non_authoritative_source": n},
          "empty_attr": int,       # 有权威结论但归属表为空（**不算漂移**，仅统计）
        }

    ⚠️ `empty_attr` 只统计不修：归属表为空通常表示「尚未人工定级」，
    门禁历史行为也不把它算漂移（`if st and rec_st != st`）。若擅自填入，
    等于用 SSOT 替人做定级决定 —— 超出"修漂移"的范围，故只报告。
    """
    by_docno, by_title = index_state(state)
    pairs: list[dict] = []
    checked = no_state = empty_attr = 0
    skipped = {"placeholder": 0, "stale": 0, "non_authoritative_source": 0}

    for r in rows or []:
        rec = match_row(r, by_docno, by_title)
        if rec is None:
            no_state += 1
            continue
        checked += 1
        ok, why = authority(rec, fresh_days)
        if not ok:
            skipped[why] = skipped.get(why, 0) + 1
            continue
        st = (r.get("时效状态") or "").strip()
        rec_st = (rec.get("status") or "").strip()
        if not st:
            # 归属表未定级：不擅自填（见 docstring 的 empty_attr 说明）
            empty_attr += 1
            continue
        if rec_st == st:
            continue
        pairs.append({
            "rfn": r.get("监管文件编号", ""),
            "docno": r.get("发文字号", ""),
            "title": r.get("文件名称", ""),
            "attr": st,
            "state": rec_st,
            "key": rec.get("last_checked_at", ""),
        })

    return {
        "pairs": pairs,
        "checked": checked,
        "matched": checked,
        "no_state": no_state,
        "skipped": skipped,
        "empty_attr": empty_attr,
    }
