# -*- coding: utf-8 -*-
"""
interfaces — 跨模块唯一调用层（v1 §5.2 / Q3）

纪律：
  - modules/* 之间禁止直接 import、禁止 sys.path.insert、禁止裸 open 兄弟仓数据文件。
  - 对外能力一律经本包：clean_index_api / rfn_api / theme_api / internal_policy_api。
  - 数据契约（列头/枚举/键集/等价别名）见 interfaces/contract.py（程序可读）。
"""

from __future__ import annotations
