# -*- coding: utf-8 -*-
"""modules.regulatory_scrapers.collectors.mof.limits — TD-11 的**软上限与保数据**原语。

背景（2026-10-20 全链实跑实测）
-------------------------------
`collect:mof` 用尽 4h 硬上限（14419.7s ≈ `COLLECT_TIMEOUT["mof"]=14400`）仍跑不完
⇒ **mof raw 停滞 10 天**。两条成因：

  ① **网络异常不熔断**：`download_attachment` 只把 502/503 计入连续失败，
     而**超时/连接拒绝**不计入 ⇒ 主机不可达时每附件走满 6 次退避 ≈150s 且**永不终止**。
  ② **无软耗时上限**：gov 三个采集早就用 `--max-seconds`（1500/5100/21000），
     只有 mof 没有 ⇒ 只能等 4h 硬杀，而硬杀**不落盘**（落盘在 try 末尾）⇒ 全丢。

本模块把三条"保数据"原语收敛成**纯逻辑**（无 IO、无网络、可单测）：

  · `EarlyStopGuard`   —— 软耗时上限：到期后停止抓新条目，并把未处理的历史记录
                          **按原样保留**（标 unchanged），绝不判为"已移除"。
  · `content_should_revert` —— 详情未拿到或新正文明显短于历史时，判为
                          "疑似被源站截断" ⇒ 应沿用历史正文。
  · `merge_attachments` —— 本次没抓附件时**沿用历史附件**（"跳过下载"≠"丢弃历史"）。

三条原语的共同点：**早停/跳过时必须保住已有数据**。
这是本模块存在的唯一理由——缺了它们，早停机制本身会变成数据破坏源
（实测：跳过第 2 类别后 780/870 条被误判 removed；`--no-attachments`
把 29.4MB 附件元数据抹掉；详情截断让 6.0MB 正文静默缩水）。
"""
from __future__ import annotations

import time
from collections.abc import Iterable
from typing import Any

#: 「正文疑似被源站截断」的判定阈值：新正文短于历史**该比例**以下即判疑似截断。
#: 取 0.8 而非 0.9/0.95：法规正文偶发的格式差异可造成小幅波动，20% 以上的缩水
#: 才值得警惕（既避免"一点变化就回退"，也不放过真正的截断）。
CONTENT_SHRINK_RATIO = 0.8


class EarlyStopGuard:
    """软耗时上限守卫。

    **不静默**：每次决策都可经 `summary()` 取出（`at_s` / `reason` /
    `skipped_in_run` / `restored_from_history`），写入采集 status 供事后复核。

    ⚠️ **不做 IO**：本类只判定与构造要保留的记录；落盘由调用方负责
    （`collector.py` 的 `_flush_store`）。早停与正常结束必须共用同一落盘出口，
    否则早停等于白跑——这是本类存在的另一半理由。
    """

    __slots__ = ("max_seconds", "started_at", "stopped", "at_s", "reason",
                 "skipped_in_run", "restored_from_history")

    def __init__(self, max_seconds: float) -> None:
        self.max_seconds = float(max_seconds or 0)
        self.started_at = time.time()
        self.stopped = False
        self.at_s = 0.0
        self.reason = ""
        self.skipped_in_run = 0
        self.restored_from_history = 0

    def check(self) -> bool:
        """到期即置 stopped（幂等：多次调用只取首次到期时刻）。"""
        if self.stopped or self.max_seconds <= 0:
            return self.stopped
        if (time.time() - self.started_at) > self.max_seconds:
            self.stopped = True
            self.at_s = round(time.time() - self.started_at, 1)
            self.reason = "--max-seconds=%.0f" % self.max_seconds
        return self.stopped

    def keep_prev(self, prev: dict | None) -> dict | None:
        """早停后**按原样保留**一条历史记录（active=True / change=unchanged）。

        为什么不直接跳过：跳过会让 `new_store` 缺该 id，而采集方的
        「历史存在但本次列表已无 ⇒ removed」逻辑会把它判成**已移除**
        （active=False）——那是数据破坏，比"没刷新"严重得多（实测 780/870 条
        被误判，须从备份恢复）。

        ⚠️ `prev` 为 None（新增条目）时返回 None：历史里没有，无从保留。
        """
        if not prev:
            return None
        keep = dict(prev)
        keep["active"] = True
        keep["change"] = "unchanged"
        return keep

    def restore_missing(self, prev_by_id: dict[str, dict],
                        new_store: list[dict]) -> int:
        """把**任何**未进 `new_store` 的历史记录补回来（早停兜底）。

        一个兜底点覆盖三种早停现场：① 内层已停但被跳过的类别；
        ② 外层直接 continue 的类别；③ `fetch_category_records` 中途异常。
        三者都会让某些历史 id 缺席，而不做兜底它们就会被判为 removed。
        """
        if not self.stopped:
            return 0
        seen = {str(e.get("id")) for e in new_store}
        n = 0
        for rid, prev in prev_by_id.items():
            if rid in seen:
                continue
            keep = self.keep_prev(prev)
            if keep is not None:
                new_store.append(keep)
                n += 1
        self.restored_from_history = n
        return n

    def summary(self) -> dict:
        """供写入采集 status 的现场摘要（字段稳定，未触发时 reason 为空串）。"""
        return {
            "at_s": self.at_s,
            "reason": self.reason,
            "skipped_in_run": self.skipped_in_run,
            "restored_from_history": self.restored_from_history,
            "note": ("软耗时上限到期：主数据已落盘，附件/未抓条目待下次续跑"
                     if self.stopped else ""),
        }


def content_should_revert(new_text: str, history_text: str,
                          detail_missing: bool) -> bool:
    """新正文是否应被**历史正文**取代（疑似截断）。

    判据：`detail_missing`（本次没有可靠新正文）**或**新正文短于历史的
    `CONTENT_SHRINK_RATIO` 以下。

    ⚠️ 不能用"新正文为空"做判据：源站截断后**不为空但已失真**
    （实测开头一致、尾部被切：55558 → 3724），空值判据完全抓不到。
    """
    new_len = len(new_text or "")
    hist_len = len(history_text or "")
    if detail_missing:
        return hist_len > new_len
    if not hist_len:
        return False
    return new_len < hist_len * CONTENT_SHRINK_RATIO


def merge_attachments(fetched: Iterable[Any] | None,
                      history: Iterable[Any] | None,
                      did_fetch: bool) -> list:
    """本次要写入的附件元数据。

    **「跳过下载」≠「丢弃历史」**：`build_entry(..., attachments=[])` 会把记录里的
    `attachments` / `attachment_count` / `table_structured` / `table_raw_text`
    四字段整体清空。实测 `--no-attachments` 补抓主数据时，被重新处理的 130 条
    把主库从 63.6MB 抹到 36.4MB——差值 ≈29.4MB，正是 `attachments` 的体量。

    故：本次抓了 ⇒ 用抓到的；没抓 ⇒ **沿用历史**（历史也没有则为空）。
    """
    if did_fetch:
        return list(fetched or [])
    return list(history or [])
