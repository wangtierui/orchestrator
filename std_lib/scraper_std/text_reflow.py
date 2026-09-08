# -*- coding: utf-8 -*-
"""
text_reflow.py —— 中文文档行长硬换行合并（reflow，纯函数，2026-09-08）

背景（⑪ internal 正文换行缺陷）：PDF 文本层按可视行长切行，句子在句中被 ``\\n`` 截断
（如「增员入司、营\\n销组织架构」）。本模块把「同一自然段的续行」重新拼回，消除句内硬换行。

设计：
  - 输入为规范化文本（行以 \\n 分隔，空行=段落分隔）。
  - 强结构行（第X章/条/附件/附表 等）自成一段并断开前段；
  - 段内行：当前行以句读（。！？…；：）或闭引/闭括号收尾则段落结束，否则为续行直接拼接
    （ASCII 字母数字边界自动补一个空格，避免英文单词粘连）；
  - 输出以 ``\\n\\n`` 分隔自然段，段内无句中断行。

适用面：
  - internal_policy_base.extract.normalize_text（已接入，正文抽取后调用）；
  - 五源 clean 侧 future 复用（对 cleaned body_text 做同规整时 import 本函数）。

注意：不做 OCR 版面恢复（扫描件文本质量由其 OCR 保证）；表格/表单类天然短行由上游分类，
本函数仅处理「叙述行续行」，不破坏既有结构化行。
"""
from __future__ import annotations

import re

# 段落终止句读（行尾出现即段末）
_STOP_CHARS = "。！？…；："
# 闭引/闭括号（行尾为这些时，仅当其前方也含句读才算段末，防止『条款名（试行）」』被误断）
_CLOSE_CHARS = "”』」）》"
_STRUCT_RE = re.compile(
    r"^\s*(第\s*[0-9０-９一二三四五六七八九十百千两]+\s*[章节条]"
    r"|附件|附表|附录|附\s*则|目\s*录|目\s*次)"
)
# 表格表头/编号行（不强结构，但独立成段避免与正文粘连）：如 "序号 名称 内容"、"LHQ-D-…"
_TABLEISH_RE = re.compile(r"^\s*(序号|编号|项目|名称|内容|备注|金额|说明)[\s　]")
_ID_LINE_RE = re.compile(r"^\s*[A-Za-z]{1,10}[-—][0-9A-Za-z\-]+\s*$")


def _tail_closes_para(s: str) -> bool:
    if not s:
        return False
    last = s[-1]
    if last in _STOP_CHARS:
        return True
    if last in _CLOSE_CHARS:
        # 闭引号/括号：看其前一个非空白字符是否句读（如 法》 前面逗号/句号）
        pre = s[:-1].rstrip()
        return bool(pre) and pre[-1] in _STOP_CHARS
    return False


def reflow_chinese(text: str) -> str:
    """行长硬换行合并。返回段间以空行（\\n\\n）分隔、段内无句中断行的文本。"""
    paras: list[str] = []
    cur: str | None = None
    for raw in (text or "").split("\n"):
        ln = raw.strip()
        if not ln:
            if cur is not None:
                paras.append(cur)
                cur = None
            continue
        is_struct = bool(_STRUCT_RE.match(ln)) or bool(_TABLEISH_RE.match(ln)) or bool(_ID_LINE_RE.match(ln))
        if cur is None or is_struct:
            if cur is not None:
                paras.append(cur)
            cur = ln
            continue
        # cur 存在且非结构行 → 判断续行/段末
        if _tail_closes_para(cur):
            paras.append(cur)
            cur = ln
            continue
        # 续行拼接：ASCII 字母数字跨界补空格
        a = cur[-1]
        b = ln[0]
        sep = " " if (a.isascii() and b.isascii() and a.isalnum() and b.isalnum()) else ""
        cur = cur + sep + ln
    if cur is not None:
        paras.append(cur)
    return "\n\n".join(paras).strip()


if __name__ == "__main__":  # 离线自检
    demo = (
        "为开拓个人寿险市场，规范个人营销员的工作职责、增员入司、营\n"
        "销组织架构、从业守则、日常管理、品质管理、待遇、业务考核、\n"
        "福利保障等规定，明确营销员职业生涯规划，保证营销业务队伍质\n"
        "量，提高契约品质及经营绩效。\n"
        "第一条 本规定适用于全体营销员。\n"
        "第二条 本办法所称保险销售从业人员，是指符合招募条件获得\n"
        "《执业证书》的人员。\n"
    )
    out = reflow_chinese(demo)
    assert "增员入司、营销组织架构" in out, out
    assert "业务队伍质量，提高契约品质" in out, out
    assert "第一条 本规定适用于全体营销员。" in out, out
    assert "《执业证书》的人员。" in out, out
    assert "\n第一条" in out, "结构行应断段"
    print("[text_reflow] 自检通过")
