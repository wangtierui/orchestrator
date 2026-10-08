# -*- coding: utf-8 -*-
"""nfra 采集脚本的共用件（refactor 迁入版）。

原 modules/regulatory_scrapers/collectors/nfra_common.py 迁出，逻辑逐函数等价。
仅 import 路径改为包内相对导入。
"""
from __future__ import annotations

import subprocess
import time

#: 站点根（两脚本原各自硬编码同一常量）
BASE = "https://www.nfra.gov.cn"

#: 浏览器 UA（站点对空 UA 返回降级页面）；必须与原值逐字一致。
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

#: 预热后冷却秒数（避免连续请求触发限流）
WARMUP_COOLDOWN_S = 1.0


def warmup(cookie_jar: str, base: str = BASE, ua: str = UA, cooldown_s: float = WARMUP_COOLDOWN_S) -> None:
    """访问栏目首页做 cookie 预热（拿到站点会话后再抓详情/附件）。失败不阻断，但必须冷却。"""
    try:
        subprocess.run(
            ["curl", "-s", "-L", "-A", ua, "-c", cookie_jar, "--max-time", "30",
             base + "/cn/view/pages/index/index.html"],
            capture_output=True,
            timeout=40,
        )
    except Exception:  # noqa: BLE001  采集容错（预热失败不阻断采集）
        pass
    time.sleep(cooldown_s)
