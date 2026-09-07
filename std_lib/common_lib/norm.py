# -*- coding: utf-8 -*-
"""
std_lib.common_lib.norm — 归一化唯一实现（专项三：消除 5+ 处 norm_docno/norm_title 重复）

P0 提供与旧仓语义一致的实现；P2 起将内部委托合并到 scraper_std.doc_number（若已迁入）
并保持本模块为唯一调用面（classifier recall_audit / registry / drafter verify 全部改 import 本模块）。

约定：
  - norm_docno：半角括号→全角〔〕归一 + 空白压缩 + 去尾部"号"（用于唯一键比对，语义与 rfn/registry 一致）。
  - norm_title：去《》/空白/括号尾注（已废止|已失效|试行|修订）等（与 rfn.__init__.norm_title 语义对齐）。
"""
from __future__ import annotations

import re

_NULL_TOKENS = {"", "n/a", "na", "无", "-", "none", "null", "未注明", "不详"}
_DOC_CLEAN = re.compile(r"[〔\[\]（）()〕\s]")
_TITLE_SUFFIX = re.compile(r"[（(](已废止|已失效|试行|修订)[）)]\s*$")
_TITLE_PUNCT = re.compile(r'[\s（）()、《》"\'，。：:；;,.!！?？、_/-]')


def norm_docno(docno) -> str:
    """文号归一：去除括号/空白 → 返回纯串（去掉尾部'号'）。"""
    if not docno:
        return ""
    s = str(docno).strip()
    if s in _NULL_TOKENS:
        return ""
    s = _DOC_CLEAN.sub("", s)
    return s.rstrip("号")


def norm_title(title) -> str:
    """标题归一：去括号尾注/书名号/空白/常见标点，转小写（ASCII）。"""
    if not title:
        return ""
    t = str(title).strip()
    if t in _NULL_TOKENS:
        return ""
    t = _TITLE_SUFFIX.sub("", t)
    t = _TITLE_PUNCT.sub("", t)
    return t.lower()


if __name__ == "__main__":  # 离线自检
    assert norm_docno("银保监办发〔2019〕19号") == "银保监办发201919"
    assert norm_title("《保险销售行为管理办法》（试行）") == "保险销售行为管理办法"
    print("[common_lib.norm] 自检通过")
