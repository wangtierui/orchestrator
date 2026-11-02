# -*- coding: utf-8 -*-
"""采集总入口：``python -m collectors [--source all|pbc|nfra|gov|mof] [通用参数]``。

把四源散落的 ``main()`` 收敛到一个统一入口，支持单次跑全部源或指定单源，
复用同一套 ``add_common_args`` 参数语义（--out-dir/--mode/--limit/--no-detail/--delay...）。

各源 ``collect()`` 签名不完全一致（pbc/mof 接受 category/no_attachments，gov 接受
source/backfill，nfra 接受 cache_dir/offline），这里用 ``inspect`` 按签名过滤传参，
对任意源都安全通用，以后加源只需在 SOURCES 里登记一行。
"""
from __future__ import annotations

import argparse
import importlib
import inspect
import sys

from .lib.cli import add_common_args, DEFAULT_OUT, configure_logging

# 统一配置 logging：各源进度日志走 logging（如 gov 的 LOG.info），入口不配置则会
# 被 Python 的静默兜底吞掉（pbc 用 print 所以照常显示，造成"有的源有日志有的没有"）。
configure_logging()

# 源名 -> (模块路径, 采集器类名)
SOURCES: dict[str, tuple[str, str]] = {
    "pbc": ("collectors.pbc.collector", "PbcCollector"),
    "nfra": ("collectors.nfra.collector", "NfraCollector"),
    "gov": ("collectors.gov.collector", "GovCollector"),
    "mof": ("collectors.mof.collector", "MofCollector"),
}


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="监管法规采集总入口（指定单源或全部）")
    ap.add_argument(
        "--source", choices=["all", *SOURCES], default="all",
        help="采集源；all=依次跑全部源（默认）")
    add_common_args(
        ap, with_category=False, with_source=False, with_mode=True,
        with_delay=True, with_limit=True, with_detail=True,
        with_attachments=True, default_out=DEFAULT_OUT)
    return ap


def _call_collect(collector, **opts):
    """按 collect() 实际签名过滤传参，避免各源签名差异导致 TypeError。"""
    sig = inspect.signature(collector.collect)
    kwargs = {k: v for k, v in opts.items() if k in sig.parameters}
    return collector.collect(**kwargs)


def run_one(name: str, args: argparse.Namespace) -> str:
    mod_path, cls_name = SOURCES[name]
    mod = importlib.import_module(mod_path)
    collector = getattr(mod, cls_name)()
    opts = dict(
        out_dir=args.out_dir, mode=args.mode, limit=args.limit,
        no_detail=args.no_detail, delay=args.delay,
        no_attachments=getattr(args, "no_attachments", False),
        refetch_attachments=getattr(args, "refetch_attachments", False),
    )
    return _call_collect(collector, **opts)


def main() -> int:
    args = _build_parser().parse_args()
    names = list(SOURCES) if args.source == "all" else [args.source]
    results: dict[str, str] = {}
    for name in names:
        print(f"\n===== 采集源: {name} =====", flush=True)
        try:
            loc = run_one(name, args)
            results[name] = f"ok ({loc})" if loc else "ok"
        except Exception as e:  # noqa: BLE001
            results[name] = f"ERROR: {type(e).__name__}: {e}"
            print(f"  [!] {name} 采集失败: {e}", file=sys.stderr, flush=True)
    print("\n===== 汇总 =====", flush=True)
    for name, st in results.items():
        print(f"  {name}: {st}", flush=True)
    failed = [n for n, st in results.items() if st.startswith("ERROR")]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
