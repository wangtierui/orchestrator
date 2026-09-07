# -*- coding: utf-8 -*-
"""
cleaner.py —— 基础清洗（第七节 7.1）

规范要求：
  去重（内容哈希/唯一键）、去噪（广告/导航/页脚/版权）、无效换行与符号标准化、
  格式标准化（日期/编码/全角）、缺失值处理（默认值 + 统计）。

本模块提供纯函数清洗能力；与 sentence_split / ocr_correction / table_recovery
组合使用构成完整清洗管道（见 pipeline.py）。
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import logging
import re
from typing import Any

LOG = logging.getLogger("scraper_std.cleaner")

# --------------------------------------------------------------------------- #
# 空白与控制字符标准化
# --------------------------------------------------------------------------- #
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_ZERO_WIDTH = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff]")
_MULTI_SPACE = re.compile(r"[ \t\u3000]+")
_MULTI_NL = re.compile(r"\n{3,}")
# 全角标点转半角（仅空格类；中文标点保留）
_FULLWIDTH_SPACE = re.compile(r"\u3000")
# 乱码符号（豆腐块/替换符等）
_GARBLE_CHARS = re.compile(r"[�▇□■◇◆●○◎※→←↑↓＊※①-⑩]")


def normalize_ws(text: str, *, keep_newlines: bool = False) -> str:
    """
    将 \r\n / \n / \t / 全角空格等标准化为单个空格（规范 7.1）；
    移除控制字符与零宽字符；压缩连续空行。

    keep_newlines=True 时保留换行结构（仅折叠多余空行、不折叠单换行），
    供断句修复流程在引入句末换行后使用。
    """
    if not isinstance(text, str):
        return ""
    t = text
    t = _CONTROL_CHARS.sub("", t)
    t = _ZERO_WIDTH.sub("", t)
    t = t.replace("\r\n", "\n").replace("\r", "\n")
    if keep_newlines:
        t = _MULTI_SPACE.sub(" ", t)
        t = _MULTI_NL.sub("\n\n", t)
    else:
        t = t.replace("\n", " ")
        t = _MULTI_SPACE.sub(" ", t)
    return t.strip()


# --------------------------------------------------------------------------- #
# 日期格式标准化（7.1）
# --------------------------------------------------------------------------- #
_DATE_FORMATS = [
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d",
    "%Y年%m月%d日 %H:%M:%S", "%Y年%m月%d日", "%Y%m%d",
    "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M",
]


def normalize_date(value: Any) -> str:
    """
    日期格式标准化（7.1）：将常见源站日期格式归一到
      YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS（含时间时）。
    无法解析时原样返回，绝不丢失原始信息（保守策略）。
    """
    if value is None:
        return ""
    s = str(value).strip()
    if not s:
        return ""
    for fmt in _DATE_FORMATS:
        try:
            dt = _dt.datetime.strptime(s, fmt)
        except ValueError:
            continue
        if dt.hour or dt.minute or dt.second:
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        return dt.strftime("%Y-%m-%d")
    # 兜底：正则抽取 YYYY-MM-DD / YYYY/MM/DD / YYYY年MM月DD日
    m = re.search(r"(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})", s)
    if m:
        try:
            dt = _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            pass
    return s


# --------------------------------------------------------------------------- #
# 去噪（政务页面常见噪声片段）
# --------------------------------------------------------------------------- #
_NOISE_PATTERNS = [
    r"版权所有\s*[©]?.*?(?:京ICP备\d+号|保留所有权利|技术支持).{0,60}",
    r"网站标识码[:：]?\d{10,}",
    r"主办单位[:：].{0,80}",
    r"地址[:：].{0,60}",
    r"邮编[:：]\d{6}",
    r"联系电话[:：][0-9\-（）()]{5,20}",
    r"无障碍浏览.{0,40}",
    r"分享到[:：]?[微信微博QQ空间]*",
    r"扫一扫.{0,40}",
    r"打印本页|关闭窗口|返回顶部",
    r"document\.write\(.*?\)",
    r"var\s+\w+\s*=.*?;",
    r"function\s+\w+\s*\(.*?\)\s*\{.*?\}",
]
_NOISE_RE = [re.compile(p, re.S | re.I) for p in _NOISE_PATTERNS]


def denoise(text: str) -> str:
    """按规则过滤广告/导航/页脚/版权/脚本片段。"""
    if not text:
        return ""
    t = text
    for rx in _NOISE_RE:
        t = rx.sub(" ", t)
    return normalize_ws(t)


# --------------------------------------------------------------------------- #
# 去重
# --------------------------------------------------------------------------- #
def content_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def dedup_records(
    records: list[dict[str, Any]],
    *,
    key_fields: list[str] | None = None,
    on_content: bool = True,
) -> list[dict[str, Any]]:
    """
    记录级去重：
      - key_fields 非空 → 按 (字段值元组) 去重（如 索引号+URL+发布日期 主键）；
      - on_content=True → 同时按 body_text 内容哈希去重（完全重复）。
    返回去重后的记录列表；被移除记录数记入日志。
    """
    seen_key: set = set()
    seen_content: set = set()
    out: list[dict[str, Any]] = []
    removed = 0
    for rec in records:
        k = None
        if key_fields:
            parts = []
            for f in key_fields:
                v = rec.get(f)
                parts.append("" if v is None else str(v))
            k = tuple(parts)
            if k in seen_key:
                removed += 1
                continue
        c = None
        if on_content:
            body = rec.get("body_text") or rec.get("content_text") or rec.get("content") or ""
            c = content_hash(normalize_ws(body))
            if c in seen_content and k is None:
                removed += 1
                continue
        if k:
            seen_key.add(k)
        if c:
            seen_content.add(c)
        out.append(rec)
    if removed:
        LOG.info("去重移除 %d 条记录（键=%s 内容哈希=%s）", removed, key_fields, on_content)
    return out


# --------------------------------------------------------------------------- #
# 缺失值处理
# --------------------------------------------------------------------------- #
def fill_missing(record: dict[str, Any], fields: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    空字段/占位符填充默认值（fields: {字段: 默认值}，未指定的用空串 ''）。

    2026-09-07 修正：缺失统一表示为空串 ''，废弃 'N/A' 文本哨兵。原因：
      - 受控枚举字段（source/timeliness_status/body_source/status）经 _ENUM_KEEP_EMPTY
        本就保持 ''，导致同一记录内 'N/A' 与 '' 两种缺失表示并存，破坏口径一致性；
      - 'N/A' 非合法枚举值且污染非枚举字段（如 document_number）；schema_validation 与
        looks_like_doc_number 已同时认 None/空/占位符为缺失，故统一为 '' 不影响任何下游判定。
    缺失判定覆盖：None、空串、及字面占位符 N/A/NA/NULL/NONE/null/none（raw 自带 'N/A'
    也一并归并为 ''，彻底消除双缺失表示）。缺失信息一律经
    record['_metadata']['missing_fields'] 追踪，不依赖 'N/A' 文本。
    """
    defaults = fields or {}
    meta = record.setdefault("_metadata", {})
    missing = []
    for k, v in list(record.items()):
        is_missing = v is None or (
            isinstance(v, str) and v.strip().upper() in ("", "N/A", "NA", "NULL", "NONE")
        )
        if is_missing:
            record[k] = defaults.get(k, "")
            missing.append(k)
    if missing:
        meta["missing_fields"] = meta.get("missing_fields", []) + missing
    return record


# --------------------------------------------------------------------------- #
# 组合清洗入口（正文文本）
# --------------------------------------------------------------------------- #
def clean_text(text: str, *, denoise_on: bool = True) -> str:
    """正文基础清洗：去噪 → 空白标准化 → 全角空格修正 → 去乱码符号。"""
    t = denoise(text) if denoise_on else normalize_ws(text)
    t = _GARBLE_CHARS.sub("", t)
    return t.strip()


# --------------------------------------------------------------------------- #
# 表格污染判定（7.2 前置过滤）
# --------------------------------------------------------------------------- #
_TABLE_HEADER_KW = ("序号", "项目", "金额", "单位", "名称", "数量", "备注",
                    "指标", "合计", "总计", "科目", "收入", "支出", "同比", "环比")
_TABLE_ROW_RE = re.compile(r"^[\s\d|｜\-—\.]+$")


def is_table_block(text: str) -> bool:
    """
    判定文本块是否为表格内容（7.2 前置过滤）。判定条件（满足其一）：
      1. 含表头关键词（序号/项目/金额…）；
      2. 连续多行等长结构（行字符数近似且含数字/竖线分隔）；
      3. 大量竖线 | 分隔符。
    """
    if not text:
        return False
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    if not lines:
        return False
    if any(any(kw in ln for kw in _TABLE_HEADER_KW) for ln in lines[:5]):
        return True
    if text.count("|") >= 2 * len(lines) and len(lines) >= 2:
        return True
    digit_rows = sum(1 for ln in lines if re.search(r"\d", ln))
    if digit_rows / len(lines) >= 0.6 and len(lines) >= 3:
        lens = [len(ln) for ln in lines]
        spread = (max(lens) - min(lens)) / max(1, sum(lens) / len(lens))
        if spread < 0.35:
            return True
    return False


if __name__ == "__main__":  # 离线自检
    assert normalize_ws("a\r\nb\tc\u3000d\x00 e") == "a b c d e"
    assert _ZERO_WIDTH.sub("", "a\u200bb") == "ab"
    noisy = "正文内容 版权所有 京ICP备11000000号 主办单位：某某部"
    assert "版权所有" not in denoise(noisy)
    recs = [{"body_text": "同一正文", "url": "a"}, {"body_text": "同一正文", "url": "b"}]
    assert len(dedup_records(recs, key_fields=["url"])) == 2  # 键不同不去重
    assert len(dedup_records(recs)) == 1  # 内容哈希去重
    assert is_table_block("序号 项目 金额\n1 收入 100\n2 支出 50") is True
    assert is_table_block("这是正常的一段中文正文文本。") is False
    print("[scraper_std.cleaner] 离线自检通过")
