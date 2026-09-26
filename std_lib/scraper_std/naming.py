# -*- coding: utf-8 -*-
"""
naming.py —— 文件标准重命名规范（第六节 6.4）

命名规则：
  {索引号}_{文件类型标识}_{标题摘要}_{发文日期}{_序号}{原扩展名}

  索引号   ：政府信息唯一索引号；无索引号则用 URL MD5 前 8 位
  类型标识 ：正文 / 附件（多附件：附件1、附件2…）
  标题摘要 ：原标题前 20 个字符，去除标点及“关于/印发”等冗余前缀，过长以 ... 省略
  发文日期 ：YYYYMMDD
  扩展名   ：小写原扩展名
  非法字符 ：反斜杠/斜杠/冒号/星号/问号/双引号/尖括号/竖线 → 替换为下划线
  超长保护 ：Windows 260 字符限制，配置截断阈值（默认 200），超限自动截断并记录
"""

from __future__ import annotations

import hashlib
import logging
import os
import re

LOG = logging.getLogger("scraper_std.naming")

_ILLEGAL = re.compile(r'[\\/:*?"<>|\r\n\t]+')
_REDUNDANT_PREFIX = re.compile(
    r"^(关于|关于印发|印发|关于进一步|关于做好|关于规范|关于明确|关于调整|关于开展|关于公布)"
)
_PUNCT = re.compile(r"[，。、；：！？「」『』（）()《》〈〉\[\]{}——…·,.;:!?\"'`~\-— ]")
_DEFAULT_MAX_LEN = 200  # Windows 260 字符保护阈值


def clean_title_for_name(title: str) -> str:
    """标题摘要：去冗余前缀 → 去标点 → 截前 20 字符 → 加省略号。"""
    t = (title or "").strip()
    t = _REDUNDANT_PREFIX.sub("", t)
    t = _PUNCT.sub("", t)
    if len(t) > 20:
        t = t[:20] + "..."
    return t or "未命名"


def index_key(index_no: str, url: str = "") -> str:
    """索引号：优先页面索引号；否则 URL MD5 前 8 位。"""
    if index_no and str(index_no).strip() and str(index_no).strip().lower() != "n/a":
        return str(index_no).strip()
    if url:
        return hashlib.md5(url.encode("utf-8")).hexdigest()[:8]
    return "NOINDEX"


def standard_filename(
    *,
    index_no: str,
    title: str,
    pub_date: str,
    ext: str,
    file_type: str = "附件",
    seq: int | None = None,
    url: str = "",
    max_len: int = _DEFAULT_MAX_LEN,
) -> str:
    """
    生成标准文件名（不含目录）。

    file_type: "正文" | "附件"；多附件时调用方传 seq=1,2,3 → “附件1”
    ext      : 原扩展名（含点，如 .docx）；自动转小写
    """
    idx = index_key(index_no, url)
    # 标题摘要 + 清理非法字符
    stem = clean_title_for_name(title)
    stem = _ILLEGAL.sub("_", stem)
    date8 = re.sub(r"\D", "", pub_date or "")[:8] or "00000000"
    type_tag = file_type if seq is None else f"{file_type}{seq}"
    ext_l = (ext or "").lower()
    if ext_l and not ext_l.startswith("."):
        ext_l = "." + ext_l

    name = f"{idx}_{type_tag}_{stem}_{date8}{ext_l}"
    # 超长保护
    if len(name) > max_len:
        LOG.warning("文件名超长(%d>%d)自动截断：%s", len(name), max_len, name)
        budget = max_len - len(idx) - len(type_tag) - len(date8) - len(ext_l) - 4
        stem = stem[: max(1, budget)]
        name = f"{idx}_{type_tag}_{stem}_{date8}{ext_l}"
    return name


def safe_join(directory: str, filename: str) -> str:
    """拼接并做二次非法字符防护（防路径穿越）。"""
    filename = _ILLEGAL.sub("_", filename)
    return os.path.join(directory, filename)


if __name__ == "__main__":  # 离线自检
    fn = standard_filename(
        index_no="ZNBG2024001",
        title="国务院关于进一步优化政务服务提升行政效能的意见",
        pub_date="2024-08-19",
        ext=".docx",
        file_type="正文",
    )
    # 规范示例：标题摘要保留“国务院关于…”，截前 20 字符加省略号
    assert fn.startswith(
        "ZNBG2024001_正文_国务院关于进一步优化政务服务提升行政效能..._20240819.docx"
    ), fn
    assert "/" not in fn and "\\" not in fn and ":" not in fn
    # 冗余前缀剥离：标题以“关于”开头时去除
    fn3 = standard_filename(
        index_no="X1", title="关于规范行业协会商会收费的通知", pub_date="2023-01-01", ext=".pdf"
    )
    assert fn3.startswith("X1_附件_规范行业协会商会收费的通知_20230101.pdf"), fn3
    fn2 = standard_filename(
        index_no="",
        title="附件问答",
        pub_date="2024-01-02",
        ext=".pdf",
        seq=2,
        url="http://x.gov.cn/a?id=1",
    )
    # 无索引号 → URL MD5 前 8 位；多附件序号追加
    assert "_附件2_" in fn2 and fn2.endswith(".pdf"), fn2
    assert fn2.startswith(hashlib.md5(b"http://x.gov.cn/a?id=1").hexdigest()[:8]), fn2
    assert index_key("", "http://x") == hashlib.md5(b"http://x").hexdigest()[:8]
    print("[scraper_std.naming] 离线自检通过")
