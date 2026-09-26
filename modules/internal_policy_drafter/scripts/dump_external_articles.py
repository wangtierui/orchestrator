# -*- coding: utf-8 -*-
"""
internal_policy_drafter.scripts.dump_external_articles — 外部监管文件条款正文查询（② 条款级对照）

消费 scrapers clause_index 固定节点产物（clean 后条文抽取），按 发文字号 或 《标题》 检索外部
监管文件（人身险监管体系 T0-T10）条文正文，供 drafter 六件套「条款级对应」对照使用。

用法：
  python dump_external_articles.py --docno "银保监发〔2019〕19号" [--article 第X条|N]
  python dump_external_articles.py --title "保险销售行为管理办法" [--article N] [--src nfra]
说明：
  - --article 可省略，省略则输出全部条文编号+正文截断列表；
  - 多源命中均输出（src 标识）；未命中提示可先运行 clause_index 构建（clean 固定节点）。
"""
from __future__ import annotations

import argparse
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_MOD_DR = os.path.dirname(_HERE)                          # modules/internal_policy_drafter
_MODS = os.path.dirname(_MOD_DR)                          # modules
_ORCH = os.path.dirname(_MODS)                            # orchestrator 根
for _p in (_ORCH, _MODS, os.path.join(_ORCH, "std_lib")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# 阶段 3（2026-09-18）：条文产物经 interfaces 唯一入口（原插 scrapers 目录已移除）
from config.exitcodes import ExitCode  # noqa: E402
from interfaces import clause_index_api as clause_index  # noqa: E402


def _strip_art_head(body: str) -> str:
    """剥 body 行首「第X条」前缀（extract body 含起行条号，展示去重）。"""
    s = (body or "").lstrip()
    m = re.match(r"第\s*[0-9一二三四五六七八九十百千零〇两]+\s*条[、．. ]?", s)
    return s[m.end():].strip() if m else s


def _match_article_no(article: str):
    m = re.search(r"\d+|[一二三四五六七八九十百千零〇两]+", article or "")
    if not m:
        return ""
    raw = m.group(0)
    if raw.isdigit():
        return int(raw)
    cn = "零一二三四五六七八九十"
    v = 0
    for ch in raw:
        if ch in cn:
            v = v * 10 + cn.index(ch)
    return v


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001  容错降级
        pass
    ap = argparse.ArgumentParser(description="外部监管文件条款正文查询（clause_index ②）")
    ap.add_argument("--docno", default="", help="发文字号（归一匹配）")
    ap.add_argument("--title", default="", help="《标题》（书名号内文本）")
    ap.add_argument("--src", default="", help="限定源（gov/mof/nfra/pbc/supp）")
    ap.add_argument("--article", default="", help="限定第 N 条（数字或中文）")
    args = ap.parse_args()
    if not (args.docno or args.title):
        print("[usage] 至少给 --docno 或 --title")
        return ExitCode.USAGE
    want = _match_article_no(args.article)
    hits = 0
    for cl in clause_index.find_clauses(docno=args.docno, title=args.title, src=args.src):
        hits += 1
        print(f"=== [{cl.get('src')}] {cl.get('title', '')} | {cl.get('document_number', '')} "
              f"| {cl.get('article_count', 0)} 条 ===")
        if not want:
            for a in (cl.get("articles") or [])[:80]:
                print(f"  {a.get('number', '')} {_strip_art_head(a.get('body')).replace(chr(10), ' ')[:160]}")
            continue
        for a in (cl.get("articles") or []):
            if a.get("no") == want or a.get("number", "").rstrip("条").lstrip("第") == str(want):
                print(f"  {a.get('number', '')} {_strip_art_head(a.get('body')).replace(chr(10), ' ')}")
                break
        else:
            print("  (未找到该条)")
    if not hits:
        print("[未命中] 请确认 clean 已跑且 clause_index 已构建（clean 管道固定节点自动构建；"
              "或 python -m modules.regulatory_scrapers.clause_index 触发）")
        return ExitCode.DATA
    return ExitCode.OK


if __name__ == "__main__":
    raise SystemExit(main())
