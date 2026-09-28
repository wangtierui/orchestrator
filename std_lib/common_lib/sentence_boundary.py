# -*- coding: utf-8 -*-
"""std_lib.common_lib.sentence_boundary — **句读边界**的受控 SSOT（N-90）

背景（口径二重）
----------------
"句子在哪里结束"这件事，原先在两处**各自定义且不一致**：

| 位置 | 层 | 集合 | 语义 |
|---|---|---|---|
| `std_lib/scraper_std/sentence_split.py` | 清洗层（断句/加换行） | `。！？!?` | 分号/西文句点**不算**句末（只追加空格，保段） |
| `std_lib/common_lib/relations.py` | 抽取层（切句读单元） | `。．.；;！!？?` | 含分号/西文句点 |

→ 同一批正文，清洗层认定的句末位置与抽取层不同。差异**本身是有意的**（清洗要保段、抽取要细分），
但**不该由两个文件各自维护字面量**：任何一方调整（如新增 `…` 省略号、全角分号）都不会被对方感知。

本模块
------
把两级集合与其切分实现收为**唯一定义处**（本模块位于**下层**：`scraper_std` → `common_lib`
是既有允许方向；反向被刻意避免，见 `common_lib/logging.py:35` 的 N-38 注记）：

* `SENT_END_STRICT` —— 句末标点（断句/换行用）；
* `SENT_END_LOOSE`  —— 句读级（严格级 + 分号/西文句点；抽取侧分句用）；
* `split_by(text, level)` —— 按指定级别切分为句读单元（**保留分隔符**，与原实现逐字符等价）。

消费方一律 `from std_lib.common_lib.sentence_boundary import ...`，**禁止**再写本地字面量。
"""

from __future__ import annotations

import re

# ---- 受控集合（唯一定义处）----
SENT_END_STRICT = "。！？!?"
SENT_END_LOOSE = SENT_END_STRICT + "．.；;"

# 两级必须构成包含关系（严格级 ⊆ 句读级），否则"松/紧"语义失效
assert set(SENT_END_STRICT) <= set(SENT_END_LOOSE), (SENT_END_STRICT, SENT_END_LOOSE)
# 自卫：两常量会被拼进**字符类** `[...]`，含字符类特殊字符会改变语义
assert not (set(SENT_END_LOOSE) & set(r"]\^-")), "句读集合不得含字符类特殊字符"

# 切分器（分隔符**保留**在右侧片段：`(?<=…)` 零宽断言 + 吸收其后空白）
_SPLIT_STRICT = re.compile(rf"(?<=[{SENT_END_STRICT}])\s*")
_SPLIT_LOOSE = re.compile(rf"(?<=[{SENT_END_LOOSE}])\s*")

_LEVELS = {"strict": _SPLIT_STRICT, "loose": _SPLIT_LOOSE}


def split_by(text: str, level: str = "loose") -> list[str]:
    """按 `level`（`strict`/`loose`）切分为句读单元；**保留分隔符**，去空白、丢空片。

    空输入 → `[]`。未知 level → `ValueError`（不得静默降级）。
    """
    if not text:
        return []
    rx = _LEVELS.get(level)
    if rx is None:
        raise ValueError(f"未知句读级别 {level!r}（可用：{sorted(_LEVELS)}）")
    return [p.strip() for p in rx.split(text) if p.strip()]


if __name__ == "__main__":  # 离线自检
    _s = "第一句。第二句；第三句."
    assert split_by(_s, "strict") == ["第一句。", "第二句；第三句."], split_by(_s, "strict")
    assert split_by(_s, "loose") == ["第一句。", "第二句；", "第三句."], split_by(_s, "loose")
    assert split_by("", "loose") == []
    print("[common_lib.sentence_boundary] 自检通过")
