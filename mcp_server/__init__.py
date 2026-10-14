# -*- coding: utf-8 -*-
"""mcp_server — MCP 门面（W2，v2 §4）：给 agent / IDE 的**只读**编排观测面。

定位（与 `cli.py`、`web/` 门面并列，**不是第四个事实源**）：
    interfaces/*_api.py（唯一读取面）
        └── mcp_server（协议适配：tools/resources + schema + 审计）
              └── agent / IDE 客户端

分层纪律：
  · `registry.py` —— tool 注册 + **schema 自动生成**（从类型注解派生，杜绝手写漂移）
                   + 统一调用入口（写级强制审计）；
  · `tools.py`    —— 首批**只读** tool 面（全部经 `interfaces/` 门面取数）；
  · `resources.py`—— E-4 资源（`chain_steps` / `schedule_jobs` / `governance_snapshot`，
                     派生自 `STEP_LAYERS` / `schedule.yaml` / 治理库，版本随内容递增）；
  · `transport_stdio.py` —— JSON-RPC over stdio（**标准库自实现，零硬依赖**；
                     `mcp` SDK 若需可作另一传输接入，三层不变）；
  · `auth.py`     —— token 文件（env-only 路径、0600、常数时间校验）。

零硬依赖：不 import `mcp` SDK；缺失任何可选增强都不影响主链（与 `semantic` 层同款纪律）。
"""

__version__ = "0.1.0"
__all__ = ["__version__"]
