# -*- coding: utf-8 -*-
"""refactor 受控枚举/源标识的单一事实源（脱根目录依赖，原则1：schema 唯一权威）。

本模块收口 clean 阶段用到的全部受控词表，使 refactor 子树自包含、不再 import 根目录
`config.enums`。取值均依据 refactor 子树内的证据派生：

  - SOURCE_SET / SOURCE_ALIASES：依据 refactor/collectors（gov/mof/nfra/pbc/internal）
    与 clean/adapter.py、clean/schema.py:canonical_source 中实际出现的源标识与别名；
  - TIMELINESS_STATUS：依据 clean/schema.py:TIMELINESS_ALIASES 的规范 7 值；
  - CLAUSE_ISSUE_SEVERITY：依据 clause/core/document_structure.py 校验器实际产出的
    严重度字面量（ERROR / WARN 两档）。

注意：源私有字段名（issue_org / doc_no / content_text …）的翻译已在采集器解析时完成，
不属于本模块职责（见 collectors/storage/canonical.py 的 LEGACY_ALIASES）。
"""
from __future__ import annotations

# 规范源标识集合（与 collectors 各源一一对应；未知源经 SOURCE_ALIASES 归并，
# 仍无法识别 → 空串，由调用方决定回退）。
SOURCE_SET: frozenset[str] = frozenset(
    {"gov", "mof", "nfra", "pbc", "internal"}
)

# 子源 / 域名 → 规范源标识的归并表（仅列 refactor 子树内实际出现的别名）。
SOURCE_ALIASES: dict[str, str] = {
    "xzfgk": "gov",            # gov 行政法规库子源
    "flk": "gov",              # gov 国家法律法规库子源
    "fgk.mof.gov.cn": "mof",   # mof 法规库主机
    "www.mof.gov.cn": "mof",   # mof 门户主机
}

# 时效状态受控 7 值（clean/schema.py:TIMELINESS_ALIASES 规范值）。
TIMELINESS_STATUS: frozenset[str] = frozenset(
    {
        "valid",
        "amended",
        "repealed",
        "partially_repealed",
        "expired",
        "pending",
        "uncertain",
    }
)

# 条文解析严重度受控词表（clause/core/document_structure.py 校验器使用，仅 ERROR/WARN 两档）。
CLAUSE_ISSUE_SEVERITY: frozenset[str] = frozenset({"ERROR", "WARN"})
