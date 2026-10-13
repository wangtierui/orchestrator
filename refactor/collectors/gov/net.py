# -*- coding: utf-8 -*-
"""gov 爬虫：网络层 + 缓存绑定 + 站点常量（原 gov_collector.py 的会话/常量部分迁出）。

逻辑逐函数等价；仅 import 路径改为相对导入，仓库引导与 pbc 同口径。
"""
import os
import random
import re
import sys
import time
from typing import Any

try:
    import requests
    from bs4 import BeautifulSoup
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
except ImportError as e:  # pragma: no cover
    sys.stderr.write(
        "缺少依赖，请先执行：\n"
        "  pip install requests beautifulsoup4 lxml\n"
        f"原始错误：{e}\n"
    )
    sys.exit(2)

import logging  # noqa: E402
LOG = logging.getLogger("gov_scraper")

# decode_html 的权威实现在 .parse（与 gov 主链同口径），此处就近复用避免分叉。
from .parse import decode_html  # noqa: E402

# 常用桌面浏览器 UA 池（轮换以降低被识别为脚本的概率）
from ..lib.http import USER_AGENTS

# 统一文号提取器（2026-09-05 五源共享：doc_number 模块）
from ..lib.doc_number import extract_doc_number as _unified_doc_number


DEFAULT_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
    "application/json;q=0.8,*/*;q=0.7",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    # 注意：不要包含 br（brotli）。本机 urllib3 未安装 brotli 解码器，
    # 一旦服务器回 brotli 压缩，resp.content 会是未解压乱码字节，decode_html 解出乱码 → 选择器全部落空。
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

# 详情页正文候选容器选择器（按优先级尝试）
CONTENT_SELECTORS = [
    ".law-chapter",        # 司法部行政法规详情正文
    "#content",
    "#UCAP-CONTENT",
    ".content-text",
    ".pages_content",
    ".article",
    "#zoom",
    "#article-content",
    ".TRS_Editor",
    "div.content",
]

TITLE_SELECTORS = ["h1", ".article-title", "#title", ".title", "h2"]

# 发文机关关键词（用于从『X令第N号』中定位机关名）
_ORGAN_KW = r"(?:国务院|部|委员会|局|人民政府|政府|厅|署|中央军委)"

# 详情页需剔除的噪声元素（下载条 / 历史沿革 / 按钮等）
_NOISE_CLASS_HINTS = ["download", "fold", "historical", "tip", "btn", "share", "qr", "vconsole"]

# http 站点正文页仅返回「JS 协议升级」脚本，requests 不执行 JS → 拿不到正文。
# 检测到该模式后改用 https 重抓。
_SCHEME_UPGRADE_RE = re.compile(r'targetProtocol\s*=\s*["\']https:', re.I)


# --------------------------------------------------------------------------- #
# 健壮的 HTTP 客户端
# --------------------------------------------------------------------------- #
class RobustSession:
    """带重试、退避、UA 轮换与随机延时的请求会话。"""

    def __init__(self, cfg):
        self.cfg = cfg
        self.session = requests.Session()
        retry = Retry(
            total=cfg.retries,
            connect=cfg.retries,
            read=cfg.retries,
            status=cfg.retries,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET", "POST"),
            backoff_factor=1.5,          # 退避：{backoff_factor} * (2 **(n-1))
            respect_retry_after_header=True,
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def _headers(self, referer: str | None = None) -> dict[str, str]:
        h = dict(DEFAULT_HEADERS)
        h["User-Agent"] = random.choice(USER_AGENTS)
        if referer:
            h["Referer"] = referer
        return h

    def _sleep(self) -> None:
        time.sleep(random.uniform(self.cfg.delay_min, self.cfg.delay_max))

    def get(self, url: str, referer: str | None = None,
            params: dict[str, Any] | None = None,
            is_json: bool = False) -> Any | None:
        """带重试/退避/UA 轮换的 GET；纯实时请求（HTTP 响应缓存已移除）。"""
        params = params or {}
        for attempt in range(1, self.cfg.retries + 2):
            try:
                self._sleep()
                resp = self.session.get(
                    url, params=params, headers=self._headers(referer),
                    timeout=self.cfg.timeout,
                )
                if resp.status_code == 200:
                    # 文本响应使用鲁棒解码（UTF-8→GBK/GB18030），避免中文乱码；
                    # JSON 响应交由 requests 自行解码。
                    return resp.json() if is_json else decode_html(resp.content)
                LOG.warning("GET %s -> HTTP %s (尝试 %d)",
                            url, resp.status_code, attempt)
            except Exception as e:  # 网络异常 / 超时  # noqa: BLE001
                LOG.warning("GET %s 异常：%s (尝试 %d)", url, e, attempt)
            # 指数退避
            time.sleep(min(2 ** attempt, 30))
        LOG.error("GET %s 多次重试失败，放弃", url)
        return None

    def get_text(self, url: str, referer: str | None = None,
                 params: dict[str, Any] | None = None) -> str | None:
        return self.get(url, referer=referer, params=params, is_json=False)

    def post_json(self, url: str, json_body: dict[str, Any],
                  referer: str | None = None) -> Any | None:
        """POST JSON 并解析响应（带重试/退避/UA 轮换）。失败返回 None。"""
        for attempt in range(1, self.cfg.retries + 2):
            try:
                self._sleep()
                resp = self.session.post(
                    url, json=json_body,
                    headers=self._headers(referer), timeout=self.cfg.timeout)
                if resp.status_code == 200:
                    return resp.json()
                LOG.warning("POST %s -> HTTP %s (尝试 %d)",
                            url, resp.status_code, attempt)
            except Exception as e:  # noqa: BLE001
                LOG.warning("POST %s 异常：%s (尝试 %d)", url, e, attempt)
            time.sleep(min(2 ** attempt, 30))
        LOG.error("POST %s 多次重试失败，放弃", url)
        return None
