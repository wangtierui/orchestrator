# -*- coding: utf-8 -*-
"""通用增量 / 翻页辅助（对应抽象设计中的 G7）。

抽自四源散落的重复实现，统一为一份：
- `should_stop_paging`：原 `gov/zhengceku.should_stop_paging`（纯函数，已带单测守卫）。
- `KnownTracker`：统一 pbc `done_urls` / gov `seen_urls` / mof `prev_by_id` / nfra
  `master_map` 的"运行期已知态"逻辑。

重要边界（纠正"删除内存去重"的误判）：
- 存储层 `(namespace, key_md5)` 写一次只保证"不插入重复行"，**不能**避免运行期
  对已捕获条目重复发详情请求。因此四源的内存 seen/known 集合**不可删**，它们干的
  是"增量跳过详情抓取"的活。
- 本模块不替代存储去重，只把这份"运行期跳过 + 早停"逻辑从四源各写一遍收敛成通用。
- `mof` 的变更检测（指纹 diff / removed）属于 A5（源特有可变性策略），不在此处；
  `KnownTracker` 只负责"有无历史内容 → 是否抓详情"，mutability 由适配器叠加。
"""
from __future__ import annotations

from typing import Callable, Hashable, Iterable, Optional


def should_stop_paging(empty_streak: int, stop_after_empty_pages: int, backfill: bool) -> bool:
    """连续 empty_streak 页无新条目 → 停止翻页（增量模式）。

    backfill（历史存量补全）时返回 False（不早停，须扫完全部列表页）。
    纯函数，便于单测。
    """
    if backfill:
        return False
    return empty_streak >= max(1, int(stop_after_empty_pages or 1))


class KnownTracker:
    """运行期已知态：判定条目是否需抓详情，并支持早停。

    不替代 storage 写一次去重，只用于"本轮 / 本次运行跳过已捕获条目"。
    ``key_of`` 由各源提供（pbc/gov→detail_url，mof→id，nfra→doc_id）。

    语义（与四源既有行为对齐）：
    - ``known``：历史已捕获且有内容（来自 storage 主库）→ 跳过详情抓取。
    - ``seen_in_run``：本轮已出现（跨页去重）→ 跳过重复抓取。
    - ``classify`` 返回 ``"skip"``（known 或 seen）或 ``"fetch"``（需抓详情）。
    """

    def __init__(
        self,
        key_of: Callable[[dict], Hashable],
        *,
        backfill: bool = False,
        stop_after_empty_pages: int = 1,
    ) -> None:
        self.key_of = key_of
        self.backfill = backfill
        self.stop_after_empty_pages = stop_after_empty_pages
        self.known: set = set()          # 历史已捕获（来自 storage）
        self.seen_in_run: set = set()    # 本轮已见（跨页去重）
        self.empty_streak = 0
        self.new_count = 0

    def load_known(self, items: Iterable[dict]) -> None:
        """用 storage 主库条目填充 known（由各源以 key_of 提供键）。"""
        for it in items:
            k = self.key_of(it)
            if k is not None:
                self.known.add(k)

    def classify(self, item: dict) -> str:
        """返回三态：

        - ``"dup"``   ：本轮已出现（跨页重复），应直接跳过、不计入。
        - ``"known"`` ：历史已捕获且有内容（来自 storage），应复用、跳过详情抓取。
        - ``"new"``   ：未见过的条目，需抓详情。

        三态区分是统一四源的关键：pbc/nfra 的"已知→复用"与"跨页重复→丢弃"
        语义不同，gov/mof 的计数用 known、跳过详情用 seen，亦依赖此区分。
        """
        k = self.key_of(item)
        if k is None:
            return "new"
        if k in self.seen_in_run:
            return "dup"
        self.seen_in_run.add(k)
        if k in self.known:
            return "known"
        self.new_count += 1
        self.empty_streak = 0
        return "new"

    def note_empty_page(self) -> None:
        """标记一页无新条目（用于早停判定）。"""
        self.empty_streak += 1

    def should_stop(self) -> bool:
        """是否停止翻页（早停优化）。"""
        return should_stop_paging(
            self.empty_streak, self.stop_after_empty_pages, self.backfill
        )
