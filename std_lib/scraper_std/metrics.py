# -*- coding: utf-8 -*-
"""
metrics.py —— 运行指标收集与统计报告（第十三节）

规范要求 logs/metrics_{date}.json 至少包含：
  总目标数 / 成功数 / 失败数 / 重试总次数 / 附件下载成功率 / 附件文本提取成功率 /
  表格结构还原成功率 / 正文文档下载成功率 / 各字段空值率 / 总耗时 / 平均单页耗时 /
  OCR 校正统计
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import time
from typing import Any


class MetricsCollector:
    """线程安全（GIL 下计数操作原子）的指标收集器。"""

    def __init__(self, project_name: str, task_id: str = ""):
        self.project_name = project_name
        self.task_id = task_id
        self._t0 = time.time()
        self.counts: dict[str, int] = {
            "total_targets": 0,
            "success": 0,
            "failure": 0,
            "retries": 0,
            "attachments_download_ok": 0,
            "attachments_download_fail": 0,
            "attachments_text_extract_ok": 0,
            "attachments_text_extract_fail": 0,
            "table_recovered": 0,
            "table_fallback_raw": 0,
            "table_total": 0,
            "doc_download_ok": 0,
            "doc_download_fail": 0,
            "ocr_corrected": 0,
            "ocr_uncertain": 0,
            "dedup_removed": 0,
            "schema_failed": 0,
        }
        self.field_null: dict[str, int] = {}
        self.field_total: dict[str, int] = {}
        self.errors: list[dict[str, Any]] = []
        self._page_times: list[float] = []

    # ---------------- 计数 ---------------- #
    def inc(self, key: str, n: int = 1) -> None:
        self.counts[key] = self.counts.get(key, 0) + n

    def record_page_time(self, seconds: float) -> None:
        self._page_times.append(seconds)

    def record_error(self, url: str, error_type: str, detail: str = "") -> None:
        self.inc("failure")
        self.errors.append({
            "url": url,
            "error_type": error_type,
            "detail": detail[:500],
            "ts": _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })

    def record_null(self, field: str, is_null: bool) -> None:
        self.field_total[field] = self.field_total.get(field, 0) + 1
        if is_null:
            self.field_null[field] = self.field_null.get(field, 0) + 1

    # ---------------- 汇总 ---------------- #
    def snapshot(self) -> dict[str, Any]:
        total = self.counts["total_targets"] or 1
        succ = self.counts["success"]
        fail = self.counts["failure"]
        attach_total = (self.counts["attachments_download_ok"]
                        + self.counts["attachments_download_fail"]) or 1
        extract_total = (self.counts["attachments_text_extract_ok"]
                         + self.counts["attachments_text_extract_fail"]) or 1
        table_total = self.counts["table_total"] or 1
        doc_total = (self.counts["doc_download_ok"] + self.counts["doc_download_fail"]) or 1

        field_null_rate = {
            k: round(v / max(1, self.field_total.get(k, 0)), 4)
            for k, v in self.field_null.items()
        }
        elapsed = time.time() - self._t0
        return {
            "project_name": self.project_name,
            "task_id": self.task_id,
            "generated_at": _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "elapsed_seconds": round(elapsed, 2),
            "counts": dict(self.counts),
            "rates": {
                "success_rate": round(succ / total, 4),
                "failure_rate": round(fail / total, 4),
                "attachment_download_success_rate": round(
                    self.counts["attachments_download_ok"] / attach_total, 4),
                "attachment_text_extract_success_rate": round(
                    self.counts["attachments_text_extract_ok"] / extract_total, 4),
                "table_recovery_success_rate": round(
                    (self.counts["table_recovered"]) / table_total, 4),
                "doc_download_success_rate": round(
                    self.counts["doc_download_ok"] / doc_total, 4),
            },
            "field_null_rate": field_null_rate,
            "avg_page_seconds": round(
                (sum(self._page_times) / len(self._page_times)), 3) if self._page_times else 0.0,
            "error_count": len(self.errors),
            "errors_sample": self.errors[:50],
        }

    def write_report(self, log_dir: str) -> str:
        """写入 logs/metrics_{date}.json，返回路径。"""
        os.makedirs(log_dir, exist_ok=True)
        date = _dt.date.today().strftime("%Y%m%d")
        path = os.path.join(log_dir, f"metrics_{date}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.snapshot(), f, ensure_ascii=False, indent=2)
        return path


if __name__ == "__main__":  # 离线自检
    import tempfile
    m = MetricsCollector("test", "t1")
    m.inc("total_targets", 2)
    m.inc("success", 2)
    m.inc("attachments_download_ok", 1)
    m.inc("attachments_text_extract_ok", 1)
    m.inc("table_total", 1)
    m.inc("table_recovered", 1)
    m.record_null("title", False)
    m.record_null("title", True)
    m.record_page_time(0.5)
    m.record_error("http://x", "timeout")
    snap = m.snapshot()
    assert snap["rates"]["success_rate"] == 1.0
    assert snap["field_null_rate"]["title"] == 0.5
    p = m.write_report(tempfile.mkdtemp())
    assert os.path.exists(p)
    print("[scraper_std.metrics] 离线自检通过")
