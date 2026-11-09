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
    - gov 的双子源 + resume 由子源层自行编排；合并（旧∪新·按 key 新优先）统一由存储层 upsert 负责，本基类提供单源骨架。
    - pbc 的逐栏目增量落盘（batch_key_of）与首页反爬退避（iter_list_entries 内实现）
      均为可覆盖钩子，可继承本基类。
"""
from __future__ import annotations

import abc
import json
import logging
from typing import Any, Iterator

from ..storage import open_storage
from .incremental import KnownTracker
from .progress import NULL_PROGRESS, ProgressLogger
from .resilience import (
    get_coordinator,
    extract_status,
    take_drain_records,
    FailureKind,
)

LOG = logging.getLogger("pipeline")


def load_master_context(storage, key_of, known_pred=None, *,
                        stop_after_empty_pages=0, scan_all=False):
    """读主库并构建 ``(master, tracker, master_by_key)``。

    - ``tracker.known``：满足 ``known_pred`` 的主库记录键（默认全部），用于增量
      "已知→复用"判定（如 pbc 仅 ``fetch_status=='ok'``）。
    - ``master_by_key``：键 → 主库记录，供 ``handle_known`` 取回旧记录内容。
    - ``tracker`` 同时是**通用翻页闸门**（note_page/should_stop），阈值与 full
      标志在这里统一注入，四源不再各带一份早停配置。
    """
    master = storage.load_master()
    tracker = KnownTracker(key_of=key_of, scan_all=scan_all,
                           stop_after_empty_pages=stop_after_empty_pages)
    known_items = [r for r in master.items if known_pred is None or known_pred(r)]
    tracker.load_known(known_items)
    master_by_key = {key_of(r): r for r in master.items if key_of(r) is not None}
    return master, tracker, master_by_key


class CollectorContext:
    """一次运行的上下文（由 ``build_context`` 构造）。"""

    __slots__ = ("storage", "master", "tracker", "master_by_key", "out_dir",
                 "opts", "progress")

    def __init__(self, storage, master, tracker, master_by_key, out_dir, opts,
                 progress=None):
        self.storage = storage
        self.master = master
        self.tracker = tracker
        self.master_by_key = master_by_key
        self.out_dir = out_dir
        self.opts = opts
        # 单测/手写 ctx 未注入时用空实现，调用方（含各源）无需判空。
        self.progress = progress or NULL_PROGRESS


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
    # 通用增量翻页闸门：0=不早停（默认，列表页全扫）；正数=连续 N 页无新即停。
    # 由总入口从 --stop-after-empty-pages 统一注入，四源共用一份策略。
    early_stop_pages: int = 0

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

    def _accumulate(self, rec: dict) -> None:
        """逐条落库时累加轻量统计（替代原先攒全量 records 列表），供治理 sidecar 使用。"""
        ch = rec.get("change")
        if ch in self.run_stats:
            self.run_stats[ch] += 1
        if rec.get("active"):
            self.run_stats["active"] += 1

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
        # full 模式需要「覆盖修复」：已知闸门只放行 ok，非 ok 记录被重抓后必须能
        # 覆盖原行；incremental 维持各源默认冲突策略（写一次 / mof 变更追踪）。
        full = bool(opts.get("full") or opts.get("mode") == "full")
        storage = open_storage(
            self.source_id, out_dir, filename=self.master_filename,
            on_conflict="update" if full else None)
        progress = ProgressLogger(
            self.source_id, "full" if full else "incremental", out_dir=out_dir)
        progress.start()
        try:
            master, tracker, master_by_key = load_master_context(
                storage, self.key_of, known_pred=self.known_pred,
                stop_after_empty_pages=opts.get("stop_after_empty_pages",
                                               self.early_stop_pages),
                scan_all=full)
        except Exception:
            progress.finish()
            raise
        return CollectorContext(storage, master, tracker, master_by_key, out_dir,
                                opts, progress=progress)

    def _collect(self, ctx: CollectorContext) -> None:
        """核心路由（与存储/锁无关，便于单测）。

        每条 new/known/missing 记录处理成功后**立即落库**（逐条流式写入），
        不再攒批——落盘不是性能瓶颈，采集慢才是；逐条落库让慢源也能实时可见、
        且中断不丢数据。写入语义由基类统一保障，各源无需 override batch_key_of。
        对 new 态的 handle_new 做容错包装：失败按分类进死信（临时）/ 永久跳过，
        成功与失败都计入可观测指标；熔断冷却中自动暂停。
        """
        tracker = ctx.tracker
        progress = getattr(ctx, "progress", NULL_PROGRESS)
        cap = self.max_items()
        coord = get_coordinator()
        source = self.source_id
        # 轻量运行统计（替代原先攒全量 records 列表），由 _accumulate 累加；
        # 放在 _collect 开头，保证直调 _collect（如测试）也有 run_stats。
        self.run_stats = {"added": 0, "updated": 0,
                          "removed": 0, "unchanged": 0, "active": 0}
        handled = 0
        try:
            entries = self.iter_list_entries(ctx)
        except Exception as e:  # 列表枚举级失败（多为站点封锁）：记录并退出本轮
            LOG.error("列表枚举失败（源=%s）：%s", source, e)
            progress.error()
            coord.metrics.record_status(extract_status(e))
            return
        for entry in entries:
            key = self.key_of(entry)
            # cur 优先显示标题/链接，比裸 key(mof 是一串数字 id) 更可读。
            label = (entry.get("title") or entry.get("detail_url") or key
                     if isinstance(entry, dict) else key)
            progress.scan(label=label)
            cls = tracker.classify(entry)
            progress.branch(cls)
            if cls == "dup":
                continue
            if cls == "known":
                rec = self.handle_known(key, entry, ctx)
            else:  # new
                coord.metrics.record_attempt()
                coord.pause_if_needed(source)
                try:
                    rec = self.handle_new(entry, ctx)
                    coord.metrics.record_ok()
                except Exception as e:  # 抓取失败：分类后产出死信，不阻断其余条目
                    st = extract_status(e)
                    progress.error()
                    coord.metrics.record_status(st)
                    if coord.classify(st, str(e)) != FailureKind.PERMANENT:
                        coord.enqueue_failure(source, key, entry, st, str(e)[:500])
                    rec = None
            if rec is not None:
                progress.result(rec)
                self.save([rec], ctx)      # 逐条落库
                self._accumulate(rec)      # 轻量统计（供治理 sidecar）
                handled += 1
                if cap and handled >= cap:
                    break
        # 主库有、本次列表缺失的条目：按各源策略处理（nfra 并集保护 / mof 标 removed）
        if self.preserve_missing_master(ctx):
            for key, _prev in ctx.master_by_key.items():
                if key in tracker.seen_in_run:
                    continue
                rec = self.handle_missing(key, ctx)
                if rec is not None:
                    progress.result(rec)
                    self.save([rec], ctx)
                    self._accumulate(rec)

    def run(self, out_dir: str, **opts) -> str:
        ctx = self.build_context(out_dir, **opts)
        try:
            coord = get_coordinator()
            coord.start_run(self.source_id)
            self._collect(ctx)  # 内部已逐条落库
            # 冷却补抓趟：重放 pending 死信（硬上限 + 零回收早停），回收成果逐条落库
            coord.drain(self.source_id, retry_fn=lambda e: self.handle_new(e, ctx), max_passes=3)
            for rec in take_drain_records():
                self.save([rec], ctx)
                ctx.progress.result(rec)
                self._accumulate(rec)
            summary = coord.end_run(self.source_id)
            # 可观测：结束打印 JSON 汇总（fetch_runs 已在 end_run 落库）
            print(json.dumps({"source": self.source_id, **summary}, ensure_ascii=False))
            # 汇总落库一次（仅写 runs 审计记录，不产生新 raw_docs 条目），返回 locator
            return self.save([], ctx)
        finally:
            # 无论成功/异常都收尾：停心跳线程 + 打一行最终汇总。
            ctx.progress.finish()
