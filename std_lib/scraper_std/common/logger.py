"""Logging setup for cn-law-hub crawlers (thin wrapper over scraper_std.logging_setup).

Preserves the original signature ``setup_logger(output_root, name="law_crawler")``
so downstream crawler code is unchanged; the actual configuration delegates to
``std_lib.scraper_std.logging_setup.setup_logging`` (single logging source for the
whole project).
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from std_lib.scraper_std.logging_setup import setup_logging


def setup_logger(output_root, name: str = "law_crawler") -> logging.Logger:
    """Configure and return a named logger writing to ``<output_root>/logs``.

    Delegates transport/formatting to the project-wide logging setup so that
    cn-law-hub crawlers share the same observability layer as the five-source
    scrapers.
    """
    if isinstance(output_root, Path):
        log_dir = output_root / "logs"
    else:
        log_dir = os.path.join(str(output_root), "logs")
    os.makedirs(log_dir, exist_ok=True)
    # setup_logging is idempotent w.r.t. repeated calls within a process.
    setup_logging(str(log_dir), project_name=name, json_lines=False, console=True)
    return logging.getLogger(name)
