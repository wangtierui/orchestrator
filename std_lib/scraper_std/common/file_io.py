"""File I/O helpers for structured data."""

from __future__ import annotations

import csv
import json
import os
from datetime import datetime
from pathlib import Path


def _ensure_parent(path) -> None:
    """写盘前确保父目录存在（2026-09-04 增强）。

    与 mof mof_law_scraper._atomic_write 同源隐患修复：此前 data/reports、data/state
    等目标目录缺失会抛 FileNotFoundError，主库已写但状态/报告未落盘。
    """
    parent = os.path.dirname(str(path))
    if parent:
        os.makedirs(parent, exist_ok=True)


def write_json(path: Path, payload) -> None:
    _ensure_parent(path)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    _ensure_parent(path)
    path.write_text(content, encoding="utf-8")


# B6（W4，2026-10）：原 `write_jsonl` 已删——**零调用方**，且与 JSONL 原子写 SSOT
# `modules.base_publish.write_jsonl` 语义分化（本件为采集侧**非原子**便捷写入；SSOT 为
# 发布安全件：tmp + os.replace）。JSONL 落盘一律走 SSOT；采集侧临时 JSONL 直接用
# `path.open("w")` 显式表达「非原子」意图（不提供同名函数，防误选）。


def write_csv(path: Path, rows: list[dict]) -> None:
    _ensure_parent(path)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row.keys():
            if key not in seen:
                fieldnames.append(key)
                seen.add(key)
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    index = 2
    while True:
        candidate = path.with_name(f"{stem}__{index}{suffix}")
        if not candidate.exists():
            return candidate
        index += 1


def render_markdown_report(title: str, sections: list) -> str:
    lines = [f"# {title}", ""]
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines.extend([f"生成时间：{generated_at}", ""])
    for heading, payload in sections:
        lines.append(f"## {heading}")
        if isinstance(payload, str):
            lines.extend([payload, ""])
            continue
        if isinstance(payload, dict):
            for key, value in payload.items():
                lines.append(f"- {key}: {value}")
            lines.append("")
            continue
        if isinstance(payload, list):
            if payload and isinstance(payload[0], dict):
                for row in payload:
                    name = row.get("name", "")
                    count = row.get("count", "")
                    extra = row.get("extra", "")
                    text = f"- {name}: {count}"
                    if extra:
                        text += f" ({extra})"
                    lines.append(text)
            else:
                for row in payload:
                    lines.append(f"- {row}")
            lines.append("")
            continue
    return "\n".join(lines).rstrip() + "\n"
