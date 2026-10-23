# -*- coding: utf-8 -*-
"""web — 本地确认台（方案 §8 人机交互层；`cli.py serve` 入口）

定位：**本地/局域网边界内的 Web 控制台**，一个界面承载两类能力：
  1. **待办闭环管理**（操作面）：worklist 查看 → 详情 → 结构化处置（选项 + 理由 + 影响面）
     → 处置后验证（§8.1 五环的图形化落地，CLI 路径保留）；
  2. **项目数据总览**（观测面）：数据流转（桑基图）、数据状态、指标可视化、治理审计、
     文档检索、投放看板、发布事件流。

设计纪律（全部继承本仓既有约定，§8.2.2）：
  · **零新增依赖**：服务端纯 Python 3.13 标准库（http.server / sqlite3 / json），
    前端单文件原生 JS + 手绘 SVG（**零 CDN** —— 出口网络有过滤，外链即不可用风险）；
  · **不裸读**：一切取数经 `interfaces/` 只读门面 + `governance_store`（表级唯一写方）；
    本包**禁止裸拼 SQL**，数据库直连一律 `mode=ro`；
  · **旁路降级**：本包故障（端口占用 / DB 锁 / 渲染异常）**绝不影响主链**；页面局部降级
    （某数据源不可用 → 该卡片显示「不可用 + 原因」，不白屏）；
  · **loopback-only**：默认只绑 127.0.0.1；改绑需显式 `--host` 且给出安全告警；
  · **密钥 env-only**：响应过滤密钥形态字段（对齐 gate_secret_scan 的纪律）。

分层：`web/`（本包，服务与页面资源）→ `commands/serve.py`（CLI 薄壳），
与 `commands/run.py` 薄壳 + `tools/run_production_refresh.py` 实现同构。
"""

from __future__ import annotations

__all__ = ["serve", "build_server", "DEFAULT_HOST", "DEFAULT_PORT"]

#: 默认只绑回环（§8.8 安全设计：不做局域网即无 token 需求）
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


def serve(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, *,
          open_browser: bool = False, verbose: bool = False,
          pid_file: str | None = None, log_file: str | None = None,
          single_instance: bool = False, auth_mode: str = "",
          trusted_proxies: tuple[str, ...] = (),
          tls_cert: str = "", tls_key: str = ""):
    """启动确认台（阻塞直到 Ctrl-C）。薄封装，实现见 `web.server`。

    ⚠️ 新增参数**必须同时加到这里**：`commands/serve.py` 经本函数透传，
    漏加会得到 `TypeError: serve() got an unexpected keyword argument`（实测踩到，
    表现为"服务起不来且只有一行 TypeError"）。
    """
    from .server import serve as _serve

    return _serve(host=host, port=port, open_browser=open_browser,
                  verbose=verbose, pid_file=pid_file, log_file=log_file,
                  single_instance=single_instance, auth_mode=auth_mode,
                  trusted_proxies=tuple(trusted_proxies or ()),
                  tls_cert=tls_cert, tls_key=tls_key)


def build_server(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT):
    """构造（但不启动）HTTP 服务——供测试用（契约测试直接起在随机端口）。"""
    from .server import build_server as _build

    return _build(host=host, port=port)
