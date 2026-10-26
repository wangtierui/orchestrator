# -*- coding: utf-8 -*-
"""std_lib.common_lib.text_clean — 文件标题规范化（**唯一实现**）。

为什么单独成模块
----------------
脏标题来自**文件名本身**（扫描件把页眉/表头一起 OCR 进了文件名）：

    IPN-1abf474f002e4f40  第1页（共13页）阳光人寿保险股份有限公司操作风险管理办法
    IPN-48eac84c11f8bb1f  虚开发票相关法律法规序号责任分类法律法规相关内容行政责任国务院关于修改《…》的决定

如果在「公司制度」列表页做字符串处理，那么**搜索、导出、关系图谱、报告**会各自
再实现一遍（实测已经在 4 处出现不同写法）——所以清洗必须落在**发布层**（
`internal_policy_base` 索引 + `base_publish` 落库），并只保留这一个实现。

设计纪律
--------
· **只删可判定的噪声**（页码、附件编号、表头词、重复片段），不改写、不概括语义；
· 清洗后若短于 4 字，**回退原文**（宁可难读，不可变成空标题）；
· 纯函数、无 IO、无正则编译开销（模块级编译）。
"""

from __future__ import annotations

import re

#: 页码/页脚：`第1页（共13页）`、`第 1 页`、`(共13页)`、`- 3 -`
_PAGE_RE = re.compile(r"第\s*\d+\s*页\s*(?:[（(]\s*共\s*\d+\s*页\s*[）)])?|[（(]\s*共\s*\d+\s*页\s*[）)]|(?<!\d)-\s*\d+\s*-(?!\d)")
#: 行首附件标记：`附件：`、`附件1：`、`附件二、`
_ATTACH_RE = re.compile(r"^\s*附\s*件\s*[0-9一二三四五六七八九十]*\s*[：:、.．]?\s*")
#: 表格表头词（出现在标题里说明是"正文表格首行被拼进文件名"）
_TABLE_HEAD_RE = re.compile(r"序号|责任分类|法律法规相关内容|行政责任|处罚依据|违规行为|条款内容")
#: 成文日期（`2022年5月20日`）——出现在标题中间时通常是"落款被拼入"
_DATE_RE = re.compile(r"\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日")
#: 连续空白（含全角空格）
_WS_RE = re.compile(r"[\s\u3000]+")


def _strip_repeat_tail(s: str) -> str:
    """去掉"标题后面又被拼了一遍（通常是页眉/页脚的残片）"的尾部。

    形态（实测）：`阳光…招聘管理办法` + `阳光…股份有限公司`
    注意**第二次出现的是首次的"前缀"**，所以不能"取最长重复前缀"（那会截成
    `阳光保险集团股份有限公司`，把"招聘管理办法"丢掉 —— 实测踩到）。
    正确判据：找到**重复片段开始的位置** `start`，取 `s[:start]`，
    即"第一次完整出现的那一段"。
    """
    n = len(s)
    for ln in range(min(40, n - 8), 7, -1):
        head = s[:ln]
        start = s.find(head, ln)
        if start > 0:
            return s[:start]
    return s


def clean_policy_title(raw: str) -> str:
    """文件名/OCR 标题 → 干净标题（幂等；空输入返回空）。"""
    t = _WS_RE.sub(" ", str(raw or "")).strip()
    if not t:
        return ""
    t = _ATTACH_RE.sub("", t)
    # 扫描件常在标题前粘一个孤立拉丁字母（`I阳光人寿…`、`l…`）：它是页码/页眉残片。
    # 判据严格限定"**单个**字母 + 紧跟汉字"，避免误伤 `IT管理办法`、`5G…` 这类真前缀。
    t = re.sub(r"^[A-Za-zⅠⅠⅡⅢⅣⅤ](?=[\u4e00-\u9fa5])", "", t).strip()
    t = _PAGE_RE.sub(" ", t)
    # 表头词之后的内容属于正文表格，不是标题
    m = _TABLE_HEAD_RE.search(t)
    if m and m.start() >= 2:
        t = t[: m.start()]
    # 落款日期及其之后的内容不是标题
    m = _DATE_RE.search(t)
    if m and m.start() >= 6:
        t = t[: m.start()]
    t = _strip_repeat_tail(t)
    t = _WS_RE.sub(" ", t).strip(" ：:、，,。.·-—_")
    return t if len(t) >= 4 else _WS_RE.sub(" ", str(raw or "")).strip()


if __name__ == "__main__":  # 离线自检
    import sys as _sys

    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    cases = [
        ("第1页（共13页）阳光人寿保险股份有限公司操作风险管理办法",
         "阳光人寿保险股份有限公司操作风险管理办法"),
        ("虚开发票相关法律法规序号责任分类法律法规相关内容行政责任国务院关于修改"
         "《中华人民共和国发票管理办法》的决定", "虚开发票相关法律法规"),
        ("附件：阳光保险集团股份有限公司招聘管理办法阳光保险集团股份有限公司"
         "2022年5月20日阳光保险集团股份有限公司招聘管理办法",
         "阳光保险集团股份有限公司招聘管理办法"),
        ("阳光人寿保险股份有限公司有价单", "阳光人寿保险股份有限公司有价单"),
        ("I阳光人寿保险股份有限公司操作风险损失数据库工作指引",
         "阳光人寿保险股份有限公司操作风险损失数据库工作指引"),
        ("", ""),
    ]
    bad = 0
    for raw, want in cases:
        got = clean_policy_title(raw)
        ok = got == want
        bad += 0 if ok else 1
        print(("OK  " if ok else "FAIL"), repr(got), "" if ok else f"≠ {want!r}")
    print("自检通过" if not bad else f"自检失败 {bad} 项")
    _sys.exit(1 if bad else 0)
