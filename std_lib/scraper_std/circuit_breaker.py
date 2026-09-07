# -*- coding: utf-8 -*-
"""
circuit_breaker.py —— 动态字段变化监控与熔断（第十节）

规范要求：
  - 解析前执行「关键锚点元素检测」（标题/正文容器特征 ID/Class，或政府网站特有的
    索引号、文号标识元素）；
  - 锚点连续 3 次未找到 → 立即熔断：暂停爬虫、告警、输出当前页面 HTML 快照至
    logs/fail_snapshots/；
  - 不允许在未更新选择器的情况下强制跳过或填充空值。

实现：
  - AnchorDetector.check(html, selectors) -> (found, matched_selector, reason)
  - Fuse.run_guarded(fn, html_provider)    : 连续 miss 计数，达阈值触发熔断告警
  - Fuse.trip()                            : 手动触发熔断（调用方保存快照后退出）
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable

LOG = logging.getLogger("scraper_std.circuit_breaker")


class AnchorDetector:
    """
    锚点检测器。selectors 支持：
      - CSS 形式：'#detail-title' / '.article-content' / 'h1'
      - XPath 形式：'//div[@id="content"]'
      - 政府网站标识文本：'索引号' / '文号' / '成文日期'（正则关键词）
    """

    _XPATH_RE = re.compile(r"^//")

    def __init__(self, selectors: list[str] | None = None):
        self.selectors = list(selectors or [])
        # 预编译关键词
        self._kw = [s for s in self.selectors if not s.startswith(("#", ".", "//")) and not s.startswith(tuple("abcdefghijklmnopqrstuvwxyz"))]
        self._kw_re = [re.compile(re.escape(k)) for k in self._kw]

    def check(self, html: str) -> tuple[bool, str]:
        """返回 (found, matched_selector)。html 为空 → (False, 'empty_html')。"""
        if not html:
            return False, "empty_html"
        html_low = html.lower()
        for sel in self.selectors:
            if self._XPATH_RE.match(sel):
                # 简化 XPath 支持：按 tag + id 属性做启发式匹配
                m = re.search(r"//([a-zA-Z0-9_]+)(?:\[@id=['\"]([^'\"]+)['\"]\])?", sel)
                if m:
                    tag, ident = m.groups()
                    if ident:
                        if re.search(r'id=["\']%s["\']' % re.escape(ident), html_low, re.I):
                            return True, sel
                    elif re.search(r"<%s[\s>]" % re.escape(tag), html_low, re.I):
                        return True, sel
            elif sel.startswith("#"):
                ident = sel[1:]
                if re.search(r'id=["\']%s["\']' % re.escape(ident), html_low, re.I):
                    return True, sel
            elif sel.startswith("."):
                cls = sel[1:]
                if re.search(r'class=["\'][^"\']*\b%s\b' % re.escape(cls), html_low, re.I):
                    return True, sel
            else:
                # 关键词（如“索引号”）
                if re.search(re.escape(sel), html, re.I):
                    return True, sel
        return False, "no_anchor_found"


class Fuse:
    """熔断器：连续未命中锚点达阈值即熔断（暂停 + 快照 + 告警）。"""

    def __init__(
        self,
        *,
        detector: AnchorDetector | None = None,
        max_miss: int = 3,
        snapshot_dir: str = "logs/fail_snapshots",
        on_trip: Callable[[str], None] | None = None,
    ):
        self.detector = detector or AnchorDetector()
        self.max_miss = max(1, max_miss)
        self.snapshot_dir = snapshot_dir
        self.on_trip = on_trip
        self.miss_count = 0
        self.tripped = False

    def check(self, html: str, meta: dict | None = None) -> tuple[bool, str]:
        """检测锚点；连续 miss 达阈值时触发熔断。返回 (ok, reason)。"""
        found, matched = self.detector.check(html)
        if found:
            self.miss_count = 0
            return True, matched
        self.miss_count += 1
        LOG.warning("锚点未命中(%d/%d) matched=%s url=%s",
                    self.miss_count, self.max_miss, matched, (meta or {}).get("url"))
        if self.miss_count >= self.max_miss:
            self.trip(html, meta)
        return False, matched

    def trip(self, html: str, meta: dict | None = None) -> None:
        """触发熔断：保存 HTML 快照 + 调用 on_trip 告警回调。"""
        if self.tripped:
            return
        self.tripped = True
        from .logging_setup import snapshot_failure
        path = snapshot_failure(html or "", self.snapshot_dir, meta)
        LOG.error("熔断触发！连续 %d 次锚点未命中。快照已保存：%s meta=%s",
                  self.miss_count, path, meta or {})
        if self.on_trip:
            try:
                self.on_trip(path)
            except Exception as e:  # 告警回调失败不阻断主流程
                LOG.error("熔断告警回调失败：%s", e)
        raise RuntimeError(f"circuit_breaker tripped after {self.miss_count} misses; "
                           f"snapshot={path}; 请人工检查站点结构变化并更新选择器")


if __name__ == "__main__":  # 离线自检
    html = '<div id="detail-title">关于xxx的通知</div><div class="content">正文</div>索引号：ABC-2024-01'
    ad = AnchorDetector(["#detail-title", "索引号"])
    assert ad.check(html) == (True, "#detail-title")
    assert ad.check("<html><body>no anchor</body></html>") == (False, "no_anchor_found")
    import tempfile
    f = Fuse(detector=AnchorDetector(["#never-exists"]), max_miss=3,
             snapshot_dir=tempfile.mkdtemp())
    try:
        for i in range(3):
            f.check("<html></html>")
        raise AssertionError("should have tripped")
    except RuntimeError as e:
        assert "circuit_breaker" in str(e)
    assert f.tripped
    print("[scraper_std.circuit_breaker] 离线自检通过")
