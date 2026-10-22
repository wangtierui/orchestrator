# -*- coding: utf-8 -*-
"""共享 CLI 参数层。

所有采集器共用同一套"日常 + 通用高级"参数，保证跨源同名同义（日常参数必须统一，
不能某个源有、另一个源没有）。各采集器 ``main()`` 调用 :func:`add_common_args` 后，
再追加源特定高级项（如 nfra 的 ``--cache-dir/--offline``、gov 的 ``--backfill``、
internal 的 ``--in-dir/--org``）。

日常（5 源统一）：``--out-dir  --category/--source  --mode  --delay``
通用高级（偶尔用，不强制各源都有）：``--limit  --no-detail  --no-attachments  --refetch-attachments``

统一语义：
  --mode incremental（默认）：增量续抓，跳过主库已知条目。
  --mode full：全量重抓，忽略主库已知（实现为 known_pred 返回 False）。
  --delay：请求平均间隔(秒)，各源在代码侧自动 ±抖动抗检测，取代原先的 min/max 双参。
  --limit：最多处理 N 条（0=不限制），取代原先分散的 max-items/max-pages/limit 等。
"""
from __future__ import annotations

import argparse
import os

from ..base import REPO_ROOT

DEFAULT_OUT = os.path.join(REPO_ROOT, "data", "raw")


def add_common_args(
    parser: argparse.ArgumentParser,
    *,
    with_category: bool = True,
    with_source: bool = False,
    with_mode: bool = True,
    with_delay: bool = True,
    with_limit: bool = True,
    with_detail: bool = True,
    with_attachments: bool = True,
    default_out: str = DEFAULT_OUT,
) -> None:
    """向 parser 追加统一的日常/高级参数。"""
    parser.add_argument(
        "--out-dir", default=default_out,
        help="输出目录（默认统一 data/raw/）")
    if with_category:
        parser.add_argument(
            "--category", default="",
            help="按分类/栏目筛选（单个；internal 用作录入元数据）")
    if with_source:
        parser.add_argument(
            "--source", choices=["xzfgk", "zhengceku", "all"], default="all",
            help="gov 子源（默认 all）")
    if with_mode:
        parser.add_argument(
            "--mode", choices=["incremental", "full"], default="incremental",
            help="incremental=增量续抓(跳过已知)；full=全量重抓(忽略已知)")
    if with_delay:
        parser.add_argument(
            "--delay", type=float, default=1.5,
            help="请求平均间隔(秒)，代码侧自动 ±抖动抗检测")
    if with_limit:
        parser.add_argument(
            "--limit", type=int, default=0,
            help="最多处理 N 条（0=不限制）")
    if with_detail:
        parser.add_argument(
            "--no-detail", action="store_true",
            help="仅抓列表、跳过详情页（快速验证列表遍历）")
    if with_attachments:
        parser.add_argument(
            "--no-attachments", action="store_true",
            help="跳过附件下载（仅记录链接）")
        parser.add_argument(
            "--refetch-attachments", action="store_true",
            help="强制重新抓取并下载全部条目的附件")


def is_full_mode(args: argparse.Namespace) -> bool:
    """是否全量重抓模式。"""
    return getattr(args, "mode", "incremental") == "full"


def make_known_pred_full() -> callable:
    """全量模式下让所有主库记录都不算'已知'，从而逐条重抓。"""
    return lambda _r: False
