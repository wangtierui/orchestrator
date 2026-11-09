# -*- coding: utf-8 -*-
"""句读集合 —— refactor 内的唯一事实源（SSOT）。

历史：`clean/core/sentence_split.py` 原从 `std_lib.common_lib.sentence_boundary`
导入 `SENT_END_STRICT`（N-90 为消除「同一集合在两处各写字面量」而设的 SSOT）。
按「refactor 是独立项目、目录外一律不依赖」的边界，该 SSOT 必须留在本目录内，
故收编于此：**值逐字沿用原生效值，行为不变，仅事实源位置内移**。

分级口径（沿用原设计意图）：
  - 严格级 `SENT_END_STRICT`：句末标点，用于 clean 断句（命中即换行）。
    分号、西文句点不在其中 —— 它们只追加空格以保段（见 `_COMMA`）。
  - 句读级（LOOSE，含分号/逗号等）供下游关系抽取使用，与严格级差异**有意**。
    需要时在本模块定义，不得在调用方各写字面量。
"""
from __future__ import annotations

#: 严格级句末标点集合；作为字符类内容使用（见 sentence_split._SENT_END）。
SENT_END_STRICT = "。！？!?"


if __name__ == "__main__":  # 离线自检
    assert SENT_END_STRICT == "。！？!?"
    assert "；" not in SENT_END_STRICT and "." not in SENT_END_STRICT
    print("[common.sentence_boundary] 离线自检通过")
