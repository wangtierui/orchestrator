# -*- coding: utf-8 -*-
"""config/exitcodes.py — 进程退出码唯一事实源（v2 方案 §3.6 X1）

背景（v2 §2.4.2）：改造前同一数值在不同命令含义冲突——
    `2` = 配置不可用（source）/ 治理库缺失（governance_sync）/ 网络失败（wiki 上游）
          / 空值率超阈值（clean）；
    `3` = 时效脚本缺失（commands.timeliness）/ 主链失败（run_production_refresh）
          / 单实例锁被占用（同上）/ 尾部固定节点失败（clean）；
    `4` = 非源码树安装（cli.py，**唯一使用处**）。

设计取舍（执行中记录为 N-3）：
    本枚举**不重新编号**，而是把既有语义显式化为成员（重复值即别名），
    以免改变对外契约（`cli.py` 的 rc=4、编排器的 rc=2/3 已被文档、记忆与
    外部调度引用）。新增代码必须使用枚举成员；既有裸整数 `return N` 以
    「基线冻结、只减不增」方式由 `gate_runtime_hygiene` 逐步收敛。

用法：
    from config.exitcodes import ExitCode
    return ExitCode.OK
    raise SystemExit(ExitCode.DATA)
"""

from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    """进程退出码（语义化）。同名同值成员为**别名**（Python Enum 语义）。"""

    # ---- 0：成功 ----
    OK = 0

    # ---- 1：通用失败（用法错误、门禁 FAIL）----
    FAIL = 1
    USAGE = 1  # 别名：未知命令 / 参数用法错误
    GATE = 1  # 别名：门禁 FAIL（`cli.py gates`）

    # ---- 2：数据 / 配置 / 外部依赖不可用 ----
    DATA = 2
    CONFIG = 2  # 别名：sources.yaml 等配置不可用
    DEPENDENCY = 2  # 别名：治理库未启用 / 网络失败

    # ---- 3：环境不满足 / 前置未就绪 ----
    ENV = 3
    LOCKED = 3  # 别名：单实例锁被占用（编排器既有语义）
    PRECONDITION = 3  # 别名：外部脚本/组件缺失、尾部固定节点失败

    # ---- 4：非源码树安装（`cli.py` 既有对外契约，保持不变）----
    NOT_SOURCE_TREE = 4


__all__ = ["ExitCode"]


if __name__ == "__main__":  # 离线自检
    assert ExitCode.USAGE is ExitCode.FAIL
    assert ExitCode.LOCKED is ExitCode.ENV
    assert int(ExitCode.NOT_SOURCE_TREE) == 4
    print("[config.exitcodes] 自检通过：", [(e.name, int(e)) for e in ExitCode])
