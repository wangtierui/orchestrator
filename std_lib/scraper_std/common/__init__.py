"""Shared utilities for cn-law-hub crawlers (integrated into scraper_std.common).

Re-exports the same public symbols as the original ``cn-law-hub-2.0.1/scripts/common``
so crawler import statements only need their absolute path changed from
``from common import ...`` to ``from std_lib.scraper_std.common import ...``.

W4-b（2026-10，B1 死代码处置）：`ratelimit` 模块已删——`SmartRateLimiter` /
`init_limiter` / `http_request` / `RateLimitMode` / `RateLimitConfig` **零生产调用方**
（仅本文件 re-export），且它是全仓**唯一 requests 硬依赖**（共享库层）。删除后
requests 依赖面收缩至 gov_collector 单采集器（会话级，L2 分层保留，见 http.py
docstring 与 ADR-0009）；限速/退避能力由 http.py 的 `AdaptiveHttpClient` 承载
（更细：冷却/熔断/Retry-After/代理池）。
"""

from __future__ import annotations

# Re-export cache
from .cache import CacheManager, get_cache

# Re-export Chinese numeral conversion
from .chinese_numerals import chinese_to_int, int_to_chinese

# Re-export CLI helpers
from .cli_utils import (
    add_common_cli_args,
    add_no_cache_arg,
    add_output_arg,
    add_rate_limit_arg,
)

# Re-export constants
from .constants import (
    DEFAULT_USER_AGENT,
    MAX_BACKOFF,
    MAX_RETRIES,
    RETRYABLE_STATUS_CODES,
    VERIFY_SSL,
)

# Re-export DOCX / article helpers
from .docx_utils import (
    extract_article_number,
    extract_paragraphs_from_docx,
    is_article_line,
    match_article_query,
    split_into_articles,
)

# Re-export file I/O
# B6（W4，2026-10）：`write_jsonl` re-export 已删（零调用 + 与原子写 SSOT 语义分化；
# JSONL 落盘一律 `from base_publish import write_jsonl`）。
from .file_io import (
    read_jsonl,
    render_markdown_report,
    unique_path,
    write_csv,
    write_json,
    write_text,
)

# Re-export logger (thin wrapper over scraper_std.logging_setup)
from .logger import setup_logger

# Re-export text utilities + factory
from .text_utils import (
    clean_text,
    create_crawler_headers,
    decode_filename_from_url,
    ensure_dir,
    extract_year,
    format_request_exception,
    redact_url,
    sanitize_filename,
)

__all__ = [
    "CacheManager",
    "get_cache",
    "chinese_to_int",
    "int_to_chinese",
    "add_common_cli_args",
    "add_no_cache_arg",
    "add_output_arg",
    "add_rate_limit_arg",
    "DEFAULT_USER_AGENT",
    "MAX_BACKOFF",
    "MAX_RETRIES",
    "RETRYABLE_STATUS_CODES",
    "VERIFY_SSL",
    "extract_article_number",
    "extract_paragraphs_from_docx",
    "is_article_line",
    "match_article_query",
    "split_into_articles",
    "read_jsonl",
    "render_markdown_report",
    "unique_path",
    "write_csv",
    "write_json",
    "write_text",
    "setup_logger",
    "clean_text",
    "create_crawler_headers",
    "decode_filename_from_url",
    "ensure_dir",
    "extract_year",
    "format_request_exception",
    "redact_url",
    "sanitize_filename",
]

# ---------------------------------------------------------------------------
# Backward-compatible aliases (old private names still work)
# ---------------------------------------------------------------------------
_CacheManager = CacheManager
_cache = get_cache()
