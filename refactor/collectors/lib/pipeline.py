# -*- coding: utf-8 -*-
"""通用采集管线骨架（G 层）。

收口四源共有、但与具体站点无关的"编排骨架"与共享助手：

    build_context:
        加锁(由各源自行) → 开存储 → 读主库 → 建 KnownTracker(known/new/dup)
                       → 构建 master_by_key（键 → 主库记录，供复用取回内容）

    CollectorPipeline.run（模板方法，供各源按需继承）:
        翻列表页(iter_list_entries) → 逐条 classify 分流：
            dup   : 跨页重复，丢弃
            known : 历史已有 → handle_known（默认复用主库旧记录，跳过详情）
            new   : 未见   → handle_new（抓详情+构建）
        → 可选 preserve_missing_master（主库有但列表缺失也保留，如 nfra 并集保护）
        → save（默认 storage.save_master，可覆盖为分批/带 extra）

设计边界（重要）：
    - mof 的"变更指纹 diff（added/updated/removed/unchanged）"是源特有算法，
      不走 known→复用 的默认分支，应在 handle_new/handle_known 中自行实现。
    - gov 的双子源 + resume + merge_with_master 由子源层自行编排；本基类提供单源骨架。
    - pbc 的逐栏目增量落盘（batch_key_of）与首页反爬退避（iter_list_entries 内实现）
      均为可覆盖钩子，可继承本基类。
"""
from __future__ import annotations

import abc
import logging
from typing import Any, Iterator

from ..storage import open_storage
from .incremental import KnownTracker

LOG = logging.getLogger("pipeline")


def load_master_context(storage, key_of, known_pred=None):
    """读主库并构建 ``(master, tracker, master_by_key)``。

    - ``tracker.known``：满足 ``known_pred`` 的主库记录键（默认全部），用于增量
      "已知→复用"判定（如 pbc 仅 ``fetch_status=='ok'``）。
    - ``master_by_key``：键 → 主库记录，供 ``handle_known`` 取回旧记录内容。
    """
    master = storage.load_master()
    tracker = KnownTracker(key_of=key_of)
    known_items = [r for r in master.items if known_pred is None or known_pred(r)]
    tracker.load_known(known_items)
    master_by_key = {key_of(r): r for r in master.items if key_of(r) is not None}
    return master, tracker, master_by_key


class CollectorContext:
    """一次运行的上下文（由 ``build_context`` 构造）。"""

    __slots__ = ("storage", "master", "tracker", "master_by_key", "out_dir", "opts")

    def __init__(self, storage, master, tracker, master_by_key, out_dir, opts):
        self.storage = storage
        self.master = master
        self.tracker = tracker
        self.master_by_key = master_by_key
        self.out_dir = out_dir
        self.opts = opts


class CollectorPipeline(abc.ABC):
    """单源采集管线骨架（模板方法）。

    适用条件：源的控制流是"翻列表 → 按 known/new/dup 分流 → 落库"。
    通用流程（run/_collect）固定；各源差异通过钩子表达：
      iter_list_entries / handle_new / handle_known / preserve_missing_master /
      batch_key_of（分批落盘）/ max_items（调试上限）/ known_pred（已知判定）/
      save / build_extra。
    mof 的变更 diff 与 gov 的双子源编排虽特殊，但其"列表→分流→落库"仍贴合，
    可用上述钩子表达各自特殊步骤（而非强扭控制流）。
    """

    source_id: str
    master_filename: str | None = None  # None → storage 默认文件名
    # known_pred：传给 load_master_context，决定主库哪些记录算"已知可复用"。
    # None = 全部已知；pbc 仅 fetch_status=='ok' 算已知。子类可覆盖为属性或方法。
    known_pred: Any = None

    @abc.abstractmethod
    def key_of(self, item: dict) -> Any:
        """条目/记录的去重键。"""

    @abc.abstractmethod
    def iter_list_entries(self, ctx: CollectorContext) -> Iterator[dict]:
        """翻所有列表页，逐条 yield 列表条目（dict，至少含 key_of 所需键）。"""

    @abc.abstractmethod
    def handle_new(self, entry: dict, ctx: CollectorContext) -> dict | None:
        """new 态：抓详情 + 构建记录；返回记录或 None（跳过）。"""

    def handle_known(self, key: Any, entry: dict, ctx: CollectorContext) -> dict | None:
        """known 态：默认复用主库旧记录（跳过详情）。

        ``entry`` 为本次列表中的当前条目（mof 做"指纹变更 diff"时需要它来
        比对主库旧记录，从而区分 unchanged / updated）。
        """
        rec = ctx.master_by_key.get(key)
        return dict(rec) if rec is not None else None

    def handle_missing(self, key: Any, ctx: CollectorContext) -> dict | None:
        """主库有、本次列表缺失的条目如何处理（默认：按已知复用）。

        覆盖点：nfra 并集保护（直接复用旧记录）、mof 标 removed（active=False）。
        """
        return self.handle_known(key, None, ctx)

    def preserve_missing_master(self, ctx: CollectorContext) -> bool:
        """是否保留"主库有但本次列表缺失"的记录（nfra 并集保护=True）。

        返回 True 时，基类对缺失键调用 :meth:`handle_missing`（默认复用旧记录）。
        mof 不需要此开关——它直接在 :meth:`handle_missing` 里产出 removed 记录。
        """
        return False

    def batch_key_of(self, entry: dict) -> Any:
        """分批落盘键：非空时在该键变化时把累计全量先存档（pbc 逐栏目容错）。

        默认 None = 不分批，仅在 run() 末尾落盘一次。
        """
        return None

    def max_items(self) -> int:
        """调试上限：>0 时累计记录达到即停止（pbc 的 --max-items）。默认 0=不限。"""
        return 0

    def build_extra(self, ctx: CollectorContext, records: list) -> dict | None:
        """落盘 extra（如 nfra 的 errors）。默认 None。"""
        return None

    def save(self, records: list, ctx: CollectorContext) -> str:
        """落存储。默认 storage.save_master；可覆盖（分批/CSV 等）。"""
        return ctx.storage.save_master(records, extra=self.build_extra(ctx, records))

    def build_context(self, out_dir: str, **opts) -> CollectorContext:
        storage = open_storage(self.source_id, out_dir, filename=self.master_filename)
        master, tracker, master_by_key = load_master_context(
            storage, self.key_of, known_pred=self.known_pred)
        return CollectorContext(storage, master, tracker, master_by_key, out_dir, opts)

    def _collect(self, ctx: CollectorContext) -> list:
        """核心路由（与存储/锁无关，便于单测）：返回记录列表。"""
        records: list = []
        tracker = ctx.tracker
        cur_batch = None
        cap = self.max_items()
        for entry in self.iter_list_entries(ctx):
            cls = tracker.classify(entry)
            if cls == "dup":
                continue
            key = self.key_of(entry)
            if cls == "known":
                rec = self.handle_known(key, entry, ctx)
            else:  # new
                rec = self.handle_new(entry, ctx)
            if rec is not None:
                records.append(rec)
                if cap and len(records) >= cap:
                    break
            # 分批落盘：批次键变化且非首批次时，把累计全量先存档（崩溃容错）
            bk = self.batch_key_of(entry)
            if bk is not None and cur_batch is not None and bk != cur_batch:
                self.save(records, ctx)
            cur_batch = bk
        if self.preserve_missing_master(ctx):
            for key, _prev in ctx.master_by_key.items():
                if key in tracker.seen_in_run:
                    continue
                rec = self.handle_missing(key, ctx)
                if rec is not None:
                    records.append(rec)
                    if cap and len(records) >= cap:
                        break
        return records

    def run(self, out_dir: str, **opts) -> str:
        ctx = self.build_context(out_dir, **opts)
        records = self._collect(ctx)
        return self.save(records, ctx)
