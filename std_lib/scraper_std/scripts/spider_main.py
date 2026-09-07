# -*- coding: utf-8 -*-
"""
spider_main —— 爬虫启动入口核心（单一事实源，对应四源 scripts/spider_main.py）。

职责：作为各项目统一启动入口，自动定位项目根的既有爬虫主程序
（scraper.py / *_scraper.py）并委托执行；若根模块不可用则打印模块拆分指引。
底层抓取逻辑仍由项目根爬虫 + crawler_common 提供，行为不变。

各项目 scripts/spider_main.py 仅保留 sys.path 引导 + 一行委托：
    from scraper_std.scripts.spider_main import run_spider_main
    def main(argv=None):
        return run_spider_main(_PROJECT_ROOT, argv)
本模块不依赖自身文件位置，project_root 由调用方显式传入，保证四源行为一致。
"""
from __future__ import annotations

import glob
import importlib
import os


def run_spider_main(project_root: str, argv: list = None) -> int:
    """启动入口：委托项目根爬虫主程序执行。

    project_root: 爬虫项目根目录（含 scraper.py / *_scraper.py 与 scripts/）。
    argv: 透传给被委托模块 main/run 的参数列表；None 表示无参调用。
    """
    _PROJECT_ROOT = project_root

    # ============ Schedule 元数据（调度约定显式声明，2026-08-27 固化） ============
    SCHEDULE = {
        "project": os.path.basename(_PROJECT_ROOT).replace("_regulations_scraper", ""),
        "schedule_type": "cron_weekly",
        "cron": "0 1 * * 2",            # 每周二 01:00（周调度约定）
        "timezone": "Asia/Shanghai",
        "enabled": True,
        "pid_lock": True,               # 使用 PID-based lock 防陈旧锁
        "policy": "incremental_diff",   # 增量 diff 更新；拒绝触发 WAF 的全量列表重抓
        "pipeline": "run_clean_pipeline",       # 统一清洗管道
        "timeliness": "timeliness_review",      # 效力检查统一入口
        "note": "定时抓取源（gov 官网法规库）",
    }
    # ==============================================================================

    def _locate_scraper_module() -> str:
        """在项目根查找主爬虫模块（scraper.py 或 *_scraper.py）。"""
        candidates = [os.path.join(_PROJECT_ROOT, "scraper.py")]
        candidates += sorted(glob.glob(os.path.join(_PROJECT_ROOT, "*_scraper.py")))
        for path in candidates:
            if os.path.exists(path):
                return os.path.splitext(os.path.basename(path))[0]
        return ""

    mod = _locate_scraper_module()
    if not mod:
        print("[spider_main] 未找到项目根主爬虫模块（scraper.py / *_scraper.py）。")
        print("模块拆分说明：scripts/downloader.py（下载）、scripts/parser.py（解析/清洗）、")
        print("spider_main.py（启动入口）；清洗管道请运行 scripts/run_clean_pipeline.py。")
        return 1
    try:
        m = importlib.import_module(mod)
        fn = getattr(m, "main", None) or getattr(m, "run", None)
        if callable(fn):
            return fn() if argv is None else fn(argv)
        print(f"[spider_main] {mod} 无 main()/run()，仅导入成功（委托目标）")
        return 0
    except Exception as e:  # 根模块依赖（playwright 等）缺失时给出明确指引
        print(f"[spider_main] 委托 {mod} 失败：{type(e).__name__}: {e}")
        return 2
