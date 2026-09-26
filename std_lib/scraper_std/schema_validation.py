# -*- coding: utf-8 -*-
"""
schema_validation.py —— 数据字段级语义验证（第九节）

规范要求：
  - 为每个爬虫定义 JSON Schema / Pydantic 数据模型，字段类型校验
    （string/int/float/datetime/url/enum/array/object）；
  - 空值阈值检测：核心字段（标题/正文/索引号）空值率 > 3% → 自动暂停并告警，
    不生成最终交付文件；
  - 格式校验：日期 → YYYY-MM-DD HH:MM:SS；URL → 协议头校验；数字 → 剔除货币符号。

实现：
  - validate_record(record, schema) -> (ok, errors)
  - NullThresholdMonitor.record(...) / alarm() : 空值率阈值监测
"""

from __future__ import annotations

import datetime as _dt
import logging
import re
from typing import Any

LOG = logging.getLogger("scraper_std.schema_validation")

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}( \d{2}:\d{2}:\d{2})?$")
_URL_RE = re.compile(r"^https?://", re.I)
_MONEY_RE = re.compile(r"[¥￥$€\s,，]+")
_ENUM_TYPES = {"string", "int", "float", "datetime", "url", "enum", "array", "object", "number"}


def _type_ok(value: Any, ftype: str) -> bool:
    if value is None:
        return False  # None 一律视为校验失败（后续空值处理）
    if ftype == "string":
        return isinstance(value, str)
    if ftype in ("int", "number"):
        if isinstance(value, bool):
            return False
        return isinstance(value, (int, float)) and (
            ftype == "float" or not isinstance(value, float)
        )
    if ftype == "float":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if ftype == "datetime":
        if isinstance(value, (_dt.date, _dt.datetime)):
            return True
        if isinstance(value, str):
            if _DATE_RE.match(value.strip()):
                return True
            # 兼容中文日期（2024年3月5日 / 2024/3/5）——可归一化即视为合法
            return bool(normalize_datetime(value))
        return False
    if ftype == "url":
        # 类型判定仅要求是字符串；协议头校验下移至 validate_record 给出精准错误
        return isinstance(value, str)
    if ftype == "enum":
        # enum 允许值由 schema 的 'enum_values' 声明；此处仅校验是字符串
        return isinstance(value, str)
    if ftype == "array":
        return isinstance(value, (list, tuple))
    if ftype == "object":
        return isinstance(value, dict)
    return True


def validate_record(
    record: dict[str, Any], schema: dict[str, Any], allow_missing: set | None = None
) -> tuple[bool, list[str]]:
    """
    按 schema 校验单条记录。schema 形如：
      {
        "title": {"type": "string", "required": True},
        "publish_date": {"type": "datetime"},
        "detail_url": {"type": "url"},
        "category": {"type": "enum", "enum_values": ["法律","法规","规章"]},
        ...
      }
    allow_missing：已知源限制字段集合（如 gov flk 正文存于站内网 OBS 不可达）。
    命中该集合的字段为 null/空时不视为校验失败，仅由调用方记入
    _metadata.validation_warnings（保持透明可审计）。
    返回 (ok, errors)。
    """
    allow_missing = allow_missing or set()
    errors: list[str] = []
    for field, spec in (schema or {}).items():
        ftype = spec.get("type", "string")
        required = spec.get("required", False)
        value = record.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            if required:
                if field in allow_missing:
                    # 已知源限制：记入警告，由调用方追加到 _metadata
                    record.setdefault("_metadata", {}).setdefault("validation_warnings", []).append(
                        f"{field}: null (known source limitation)"
                    )
                    continue
                errors.append(f"{field}: required but null/empty")
            continue
        if not _type_ok(value, ftype):
            errors.append(f"{field}: type mismatch, expected {ftype}, got {type(value).__name__}")
            continue
        if ftype == "datetime" and isinstance(value, str):
            # 日期标准化（含 2024年3月5日 / 2024/3/5 → 2024-03-05）
            norm = normalize_datetime(value)
            record[field] = norm
            if not norm:
                errors.append(f"{field}: invalid datetime format")
        if ftype == "url" and isinstance(value, str):
            if not _URL_RE.match(value.strip()):
                errors.append(f"{field}: missing http(s) scheme")
        if ftype == "number":
            # 数字字段剔除货币符号
            if isinstance(value, str):
                cleaned = _MONEY_RE.sub("", value).strip()
                try:
                    record[field] = float(cleaned)
                except ValueError:
                    errors.append(f"{field}: not a number after money-symbol strip")
        if ftype == "enum":
            allowed = spec.get("enum_values") or []
            if allowed and value not in allowed:
                errors.append(f"{field}: not in enum {allowed}")
    return not errors, errors


def normalize_datetime(text: Any) -> str:
    """统一日期格式为 YYYY-MM-DD（含 HH:MM:SS 可选）；无法识别返回空串。"""
    if not isinstance(text, str):
        if isinstance(text, (_dt.date, _dt.datetime)):
            return (
                text.strftime("%Y-%m-%d %H:%M:%S")
                if isinstance(text, _dt.datetime)
                else text.strftime("%Y-%m-%d")
            )
        return ""
    s = text.strip()
    m = re.match(r"(\d{4})[-年/](\d{1,2})[-月/](\d{1,2})日?", s)
    if m:
        try:
            y, mo, d = (int(g) for g in m.groups())
            return "%04d-%02d-%02d" % (y, mo, d)
        except ValueError:
            return ""
    m = re.match(r"(\d{4})[-年/](\d{1,2})", s)  # 仅年月
    if m:
        try:
            return "%04d-%02d" % (int(m.group(1)), int(m.group(2)))
        except ValueError:
            return ""
    return ""


class NullThresholdMonitor:
    """核心字段空值率监测：>3% 触发告警，返回是否允许交付。

    core_fields   ：统计全部核心字段（含已知源限制字段，指标透明）
    alarm_fields  ：参与 3% 熔断告警的字段子集（默认=core_fields；
                    已知源限制字段可从中豁免，如 gov flk 正文 OBS 不可达）
    """

    THRESHOLD = 0.03  # 3%

    # 2026-09-04 口径修正：缺失判定统一认 None/空/占位符（N/A、NA、null、none），
    # 防止 raw 自带的字面 'N/A'（如 document_number="N/A"）被误计为非空导致空值率低估。
    _NA_LITERALS = ("N/A", "NA", "NULL", "NONE")

    @staticmethod
    def is_missing(v: Any) -> bool:
        if v is None:
            return True
        if isinstance(v, str):
            s = v.strip()
            return (not s) or s.upper() in NullThresholdMonitor._NA_LITERALS
        return False

    def __init__(
        self,
        core_fields: list[str] | None = None,
        alarm_fields: list[str] | None = None,
        on_alarm: Any | None = None,
    ):
        self.core_fields = core_fields or ["title", "body_text", "index_no"]
        self.alarm_fields = alarm_fields or list(self.core_fields)
        self._null = {f: 0 for f in self.core_fields}
        self._total = 0
        self.on_alarm = on_alarm

    def record(self, record: dict[str, Any]) -> None:
        self._total += 1
        for f in self.core_fields:
            if self.is_missing(record.get(f)):
                self._null[f] += 1

    def rates(self) -> dict[str, float]:
        return {f: round(n / max(1, self._total), 4) for f, n in self._null.items()}

    def counts(self) -> dict[str, tuple]:
        """返回 {字段: (空值数, 总数)}，供指标报告同步。"""
        return {f: (n, self._total) for f, n in self._null.items()}

    def alarm(self) -> tuple[bool, dict[str, float]]:
        """
        返回 (allow_delivery, rates)。alarm_fields 中任一字段空值率 > 3% →
        allow_delivery=False 并调用 on_alarm 回调（如邮件/企微告警）。
        rates 返回全部 core_fields 的真实空值率（含豁免字段）。
        """
        rates = self.rates()
        over = {f: r for f, r in rates.items() if f in self.alarm_fields and r > self.THRESHOLD}
        allow = not over
        if not allow:
            LOG.error("空值率超阈值告警：%s（阈值 %.0f%%）", over, self.THRESHOLD * 100)
            if self.on_alarm:
                try:
                    self.on_alarm(over, rates)
                except Exception as e:
                    LOG.error("空值率告警回调失败：%s", e)
        return allow, rates


def check_unique_dedup_keys(records: list[dict]) -> tuple[bool, list[dict]]:
    """批量唯一性校验：`dedup_key` 是**唯一业务主键**（SSOT，v2 §2.3.2 曾标记"无唯一性校验"）。

    同一批次内重复 → 返回 (False, duplicates)，供 clean 管道/门禁据以告警或隔离。
    此前的"唯一性"只有 `assert rec["dedup_key"]`（非空断言），**跨记录去重键冲突无人发现**；
    `reconcile_clean_drift` 等下游按 dedup_key 建索引，重复会静默覆盖。本函数补上唯一性。

    返回 duplicates 列表：`[{dedup_key, count, indices: [首次, ...]}]`（仅重复项，保持出现序）。
    """
    count: dict[str, int] = {}
    indices: dict[str, list[int]] = {}
    for i, rec in enumerate(records):
        if not isinstance(rec, dict):
            continue
        key = rec.get("dedup_key")
        if not key:
            continue
        count[key] = count.get(key, 0) + 1
        indices.setdefault(key, []).append(i)
    dups = [{"dedup_key": k, "count": c, "indices": indices[k]} for k, c in count.items() if c > 1]
    return (not dups), dups


if __name__ == "__main__":  # 离线自检
    schema = {
        "title": {"type": "string", "required": True},
        "publish_date": {"type": "datetime"},
        "detail_url": {"type": "url"},
        "issue_organ": {"type": "string"},
    }
    ok, errs = validate_record(
        {
            "title": "测试",
            "publish_date": "2024年3月5日",
            "detail_url": "https://x.gov.cn/a",
            "issue_organ": "财政部",
        },
        schema,
    )
    assert ok and not errs, errs
    ok, errs = validate_record({"title": "", "detail_url": "ftp://x"}, schema)
    assert not ok and any("required" in e for e in errs)
    assert any("http(s)" in e for e in errs)
    mon = NullThresholdMonitor()
    for i in range(100):
        mon.record({"title": "x" if i % 10 else None, "body_text": "b", "index_no": "i"})
    allow, rates = mon.alarm()
    assert not allow and rates["title"] > 0.03
    print("[scraper_std.schema_validation] 离线自检通过")
