# -*- coding: utf-8 -*-
"""采集阶段统一的抓取状态机（单一事实来源）。

所有源共用同一组 fetch_status 取值；各源采集器必须把自身结果映射到这些值，
禁止再发明私有状态字符串。取值须与 init/ddl.py 的 FETCH_STATUS_VALUES 逐字一致
（PG 的 CHECK 约束会在写入时强制，这里提供代码侧校验与规整）。
"""
from __future__ import annotations

# 顺序无关；与 init/ddl.py 中 FETCH_STATUS_VALUES 必须逐字一致。
FETCH_STATUS_VALUES = (
    "pending",            # 已入列表、尚未抓取详情（瞬态，一般不应持久化）
    "ok",                 # 抓取+解析成功，有正文
    "no_content",         # 抓到页面但无正文
    "parse_error",        # 抓到但解析失败
    "fetch_failed",       # 列表/详情 HTTP 失败
    "download_failed",    # 附件下载失败（gov）
    "attachment_skipped",  # 仅列链接、未下附件（pbc --no-attachments）
    "attachment_failed",  # 附件下载失败（pbc）
    "attachment_saved",   # 附件已存但正文未解析（需 LibreOffice）
    "skipped_existing",   # 增量已知、复用旧记录（断点续跑）
    "title_link_only",    # 仅标题链接、无详情（nfra is_title_link 分支）
    "offline_missing",    # 离线缓存缺失、仅列表面板（nfra offline）
)

_FETCH_STATUS_SET = frozenset(FETCH_STATUS_VALUES)


def is_valid(status: str) -> bool:
    """状态是否合法（在统一取值集合内）。"""
    return status in _FETCH_STATUS_SET


def normalize(status) -> str:
    """把任意值规整为合法状态；无法识别时回落到 ``no_content``。"""
    if status in _FETCH_STATUS_SET:
        return status
    return "no_content"
