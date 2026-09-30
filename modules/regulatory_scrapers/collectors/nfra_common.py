# -*- coding: utf-8 -*-
"""collectors.nfra_common — **nfra 采集脚本的共用件**（N-167，2026-09-30）

为什么独立成文件
----------------
`nfra_fill_details.py` 与 `nfra_prefetch.py` 原先**各写一份逐字相同的 `_warmup()`**
（curl 预热 + 冷却 sleep，连 `UA`/`BASE`/`COOKIE_JAR`/冷却秒数四个常量也各写一份）。
风险不在"多几行"，而在**改一漏一**：预热行为（UA、站点、cookie 罐、冷却时长）会出现
两个版本，而**分叉不会报错** —— 只会让其中一条链路以"旧握手"访问站点（被限流/拿到
降级页面），属难排查型缺陷。

纪律：本模块**只放 nfra 采集脚本间的共用件**；跨源公共能力应在 `std_lib/scraper_std/`
（如 `cache_store` / `crawler_common`）——**不要**把跨源逻辑放这里（会造成第二事实源）。
"""

from __future__ import annotations

import subprocess
import time

#: 站点根（两脚本原各自硬编码同一常量）
BASE = "https://www.nfra.gov.cn"

#: 浏览器 UA（站点对空 UA 返回降级页面）
#  ⚠️ **必须与原两处逐字一致**（N-167 抽取时逐字符核对：原值为 `Chrome/124.0.0.0`）：
#  改写 UA 会改变站点握手行为（可能被限流或返回降级页），属**静默行为变更**。
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

#: 预热后冷却秒数（避免连续请求触发限流）
WARMUP_COOLDOWN_S = 1.0


def warmup(cookie_jar: str, base: str = BASE, ua: str = UA, cooldown_s: float = WARMUP_COOLDOWN_S) -> None:
    """访问栏目首页做 **cookie 预热**（拿到站点会话后再抓详情/附件）。

    失败**不阻断**（与采集容错口径一致）：预热只是"拿会话"，即使失败，后续请求仍会尝试
    （最坏结果是多几次 403 重试）。但**必须冷却**——预热本身也是一次真实请求。
    """
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
