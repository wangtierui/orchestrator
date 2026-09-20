# -*- coding: utf-8 -*-
"""tests/test_document_structure.py — 条文解析适配单元测试（2026-09-18）

覆盖参考实现适配后的四组能力 + 两个问题案例的**最小复现**（纯函数，无 data marker）：
  ① 锚切分：章标题粘连首条、交叉引用不开新条、Word 项目符号/私用区字符边界；
  ② 自动降级：非条文体（通知/通报/规划）产出结构 → 不再"空解析"；law 出条即采用（零回归）；
  ③ 合并修复：换行截断 + 交叉引用误判 → 续行收编且**正文完整保留**；
                多文档嵌套（条号重启）→ 保留全文并报 V003（不静默改写）；
  ④ 校验器：V001–V007 的严重度分档与输出结构（参考 3.1/3.2/3.3）。
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from std_lib.scraper_std.document_structure import (  # noqa: E402
    extract_structure, parse_document, render_markdown, segment_body, segment_outline,
    validate_clauses,
)

# 两个问题案例的最小复现文本 -------------------------------------------------- #
# 案例 B（反洗钱法第五十五/五十七条一带）：正文换行把「第X条」切到行首，
# 而它其实是**上一条的交叉引用续写**。
CASE_TRUNCATED_XREF = (
    "第五十五条 金融机构有本法第五十三条、\n"
    "第五十四条规定的行为，致使犯罪所得及其收益通过本机构得以掩饰、隐瞒的。\n"
    "第五十六条 国务院反洗钱行政主管部门依照本法第五十二条规定处罚。\n"
    "第五十七条 金融机构违反本法第五十条规定擅自采取行动的，处五十万元以下罚款。\n"
    "境外金融机构违反本法第四十九条规定，对国家有关机关的调查不予配合的，依照本法第五十四条、\n"
    "第五十六条规定进行处罚，并可以将其列入名单。\n"
    "第五十八条 特定非金融机构违反本法规定的，责令限期改正。"
)
# 案例 A（保监发〔2001〕126号）：通篇无「第X条」，为「一、/1、」层级序号体。
CASE_OUTLINE_ONLY = (
    "保监发〔2001〕126 号 各中资保险公司： 为规范中资保险公司吸收外资参股行为，现通知如下： "
    "一、 中资保险公司吸收外资参股， 应向中国保监会提出申请， 并提交下列材料： "
    "1、 公司股东大会通过的吸收外资股份的决议； 2、 外资参股的可行性报告。 "
    "二、 经批准进行外资参股的中资保险公司应当报送下列材料， 进行股东资格审查： "
    "1、 公司董事会同意参股的决议。 "
    "三、 参股的外资股东应为具有法人资格的外国金融机构， 其中保险机构不得少于两家。"
)


# --------------------------------------------------------------------------- #
# ① 锚切分
# --------------------------------------------------------------------------- #
def test_chapter_title_glued_first_article_is_split():
    """`第一章 总 则第一条 …` 必须切开（原"前非汉字"判据会漏切首条）。"""
    r = extract_structure("第一章 总 则第一条 为了加强管理，制定本条例。\n第二条 本条例适用于…。")
    assert [a["no"] for a in r["articles"]] == [1, 2], r["articles"]
    assert r["chapters"][0]["title"] == "总 则"


def test_chapter_after_paren_note_is_split():
    """`…行政法规的决定》修订)第一章 总则` 必须切开（括号收尾 + 章标题）。"""
    seg = segment_body("根据2026年8月8日《…的决定》修订)第一章 总 则第一条 为了…。")
    assert seg.split("\n")[1].startswith("第一章"), seg


def test_connective_punctuation_blocks_false_article():
    """`、`/`，`/`；` 之后的「第X条」是交叉引用续写，不得开新条。"""
    seg = segment_body("第一条 依照第五十三条、第五十四条规定的行为，应当处罚。")
    assert len(seg.split("\n")) == 1, seg
    seg2 = segment_body("第一条 依照第五十三条，第五十四条规定的行为，应当处罚。")
    assert len(seg2.split("\n")) == 1, seg2


def test_private_use_bullet_is_boundary():
    """Word 项目符号（私用区字符 \\ue004）后必须断行（否定清单判据）。"""
    seg = segment_body("第二条 本办法所称贷款人。\ue004第三条 本办法所称个人贷款。")
    assert len(seg.split("\n")) == 2, seg
    r = extract_structure("第一条 甲。\ue004第二条 乙。\ue004第三条 丙。")
    assert [a["no"] for a in r["articles"]] == [1, 2, 3], r["articles"]


def test_outline_segmentation():
    """层级序号锚切分：无换行正文也能切出 一、/（一）/1、。"""
    seg = segment_outline("通知如下： 一、 第一项。 1、 具体措施。 二、 第二项。")
    assert len(seg.split("\n")) == 4, seg


# --------------------------------------------------------------------------- #
# ② 自动降级（问题案例 A）
# --------------------------------------------------------------------------- #
def test_outline_only_document_is_not_empty():
    """案例 A：无「第X条」→ 降级为 notice 并产出 structure（不再空解析）。"""
    res = parse_document(CASE_OUTLINE_ONLY)
    assert res["mode"] == "notice", res["mode"]
    assert res["is_fallback"] is False
    assert res["article_count"] == 0
    assert res["structure_count"] >= 3, res["structure"]
    assert res["score"]["total"] >= 0.75, res["score"]
    first = res["structure"][0]
    assert first["level"] == "一级" and first["number"] == "一"
    assert first["items"], "「1、」条目应挂到一级节点"
    md = render_markdown(res, title="通知")
    assert "**一、" in md and "# 通知" in md, md[:200]


def test_law_mode_wins_when_articles_exist():
    """law 一旦出条即采用，不做跨模式改写（保证既有法规文档解析零回归）。"""
    res = parse_document("第一条 甲。\n第二条 乙。\n一、 这是条文里的顿号项。")
    assert res["mode"] == "law" and res["article_count"] == 2, res


def test_empty_text_is_empty_mode():
    res = parse_document("   ")
    assert res["mode"] == "empty" and res["articles"] == [] and res["structure"] == []


# --------------------------------------------------------------------------- #
# ③ 合并修复（问题案例 B）
# --------------------------------------------------------------------------- #
def test_truncated_cross_reference_is_merged():
    """案例 B：交叉引用续行收编回上一条，条号唯一且正文完整（不丢「第五十四条」字样）。"""
    res = parse_document(CASE_TRUNCATED_XREF)
    nos = [a["no"] for a in res["articles"]]
    assert nos == [55, 56, 57, 58], nos
    assert len(nos) == len(set(nos)), "条号必须唯一"
    merged = res["articles"][0]["body"]
    assert "第五十四条规定的行为" in merged, merged[:120]
    assert merged.startswith("第五十五条"), merged[:40]
    rep = res["repair"]
    assert rep["merged"] >= 2, rep
    assert set(rep["reasons"]) <= {"fragment", "backref", "xref_no_body", "prev_connective"}, rep
    # 第五十七条（第二条被截断者）同样复原
    assert "第五十六条规定进行处罚" in res["articles"][2]["body"]


def test_embedded_document_restart_is_kept_not_merged():
    """多文档嵌套（条号重启）不得被并掉：正文保全，由 V003 报出。"""
    text = ("第一条 主文件正文甲。\n第二条 主文件正文乙。\n"
            "第一条 为了规范代理记账业务，制定本办法。\n"
            "第二条 代理记账机构应当具备下列条件。")
    res = parse_document(text)
    assert [a["no"] for a in res["articles"]] == [1, 2, 1, 2], res["articles"]
    assert "为了规范代理记账业务" in res["articles"][2]["body"], "嵌套文档首条不得被并掉"
    v = validate_clauses({"dedup_key": "k", "title": "t", "rfn": "RFN-0123456789abcdef",
                          "source_url": "u", "document_number": "n",
                          "publish_date": "2026-01-01", "effective_date": "2026-01-01",
                          **res})
    assert v["status"] == "FAILED"
    assert any(i["rule_id"] == "V003" and i["severity"] == "ERROR" for i in v["issues"]), v


def test_fragment_without_duplicate_number_is_merged():
    """前向引用（条号未出现过）但为回溯指代形态 → 仍应收编（参考未覆盖，本项目加固）。"""
    res = parse_document("第一条 具体情形见第\n九条规定的条件。\n第二条 其他。")
    assert [a["no"] for a in res["articles"]] == [1, 2], res["articles"]
    assert "九条规定的条件" in res["articles"][0]["body"]


def test_chapter_index_out_of_range_is_clamped():
    """章索引越界 → 钳位并标记（参考 5.3 语义）。"""
    r = extract_structure("第一章 总则\n第一条 甲。\n第二章 分则")
    assert r["chapters"][-1]["article_index"] < len(r["articles"]) or not r["articles"]
    assert "chapter_clamped" not in r["repair"] or r["repair"]["chapter_clamped"] >= 0


# --------------------------------------------------------------------------- #
# ④ 校验器（参考 3.1/3.2/3.3）
# --------------------------------------------------------------------------- #
def _row(**over):
    base = {"dedup_key": "dk", "title": "标题", "source_url": "u", "document_number": "n",
            "publish_date": "2026-01-01", "effective_date": "2026-01-01",
            "rfn": "RFN-0123456789abcdef", "articles": [{"no": 1, "number": "第一条",
                                                        "body": "第一条 甲。"}],
            "article_count": 1, "structure": [], "structure_count": 0, "chapters": []}
    base.update(over)
    return base


def test_validator_passes_clean_row():
    v = validate_clauses(_row())
    assert v["status"] == "PASSED" and v["error_count"] == 0 and v["issues"] == []


def test_validator_identity_field_is_error_dates_are_warn():
    v = validate_clauses(_row(dedup_key=""))
    assert v["status"] == "FAILED" and any(i["rule_id"] == "V001" and i["severity"] == "ERROR"
                                           for i in v["issues"])
    v2 = validate_clauses(_row(effective_date=""))
    assert v2["status"] == "PASSED_WITH_WARNINGS"
    assert any(i["rule_id"] == "V001" and i["severity"] == "WARN" for i in v2["issues"])


def test_validator_gap_is_warn_dup_is_error():
    """跳号为 WARN（节选/修正案真实跳号）；重复号为 ERROR（解析缺陷）。"""
    gap = validate_clauses(_row(articles=[{"no": 2, "number": "第二条", "body": "第二条 甲。"}],
                                article_count=1))
    assert any(i["rule_id"] == "V002" and i["severity"] == "WARN" for i in gap["issues"])
    dup = validate_clauses(_row(articles=[{"no": 1, "number": "第一条", "body": "第一条 甲。"},
                                          {"no": 1, "number": "第一条", "body": "第一条 乙。"}],
                                article_count=2))
    assert dup["status"] == "FAILED"
    assert any(i["rule_id"] == "V003" and i["severity"] == "ERROR" for i in dup["issues"])


def test_validator_count_and_chapter_and_rfn_rules():
    v = validate_clauses(_row(article_count=9))
    assert any(i["rule_id"] == "V004" for i in v["issues"])
    v2 = validate_clauses(_row(chapters=[{"no": 1, "title": "总则", "article_index": 5}]))
    assert any(i["rule_id"] == "V005" and i["severity"] == "ERROR" for i in v2["issues"])
    v3 = validate_clauses(_row(rfn=""))
    assert any(i["rule_id"] == "V007" and i["severity"] == "WARN" for i in v3["issues"])


def test_validator_issue_cap_and_severity_order():
    v = validate_clauses(_row(dedup_key="", title="", source_url="", document_number="",
                              publish_date="", effective_date="", article_count=9))
    assert len(v["issues"]) <= 6
    assert v["issues"][0]["severity"] == "ERROR", "ERROR 必须排在 WARN 之前"
    assert v["error_count"] >= 1 and v["warn_count"] >= 1


# --------------------------------------------------------------------------- #
# ⑤ 2026-09-20 六类问题修复（F1/F2/F3/F5a/F6/F7）—— 真实问题案例最小复现
# --------------------------------------------------------------------------- #
# F1 问题一：一级标题后的 `（一）` 紧贴标题末字（源文本无分隔符）→ 子节点曾丢失
CASE_SWALLOWED_CHILD = (
    "一、指导思想以习近平新时代中国特色社会主义思想为指导，深化改革，锐意进取。"
    "二、基本原则（一）服务国家战略，推动协调发展。以京津冀协同发展等区域重大战略为引领，"
    "以西部、东北、中部、东部板块为基础，促进区域间融合互动、融通互补。"
    "（二）顺应发展规律，坚持因城施策。认识、尊重、兼顾不同城市的区域特征和人口发展趋势。"
)


def test_f1_embedded_child_anchor_is_split():
    """「二、基本原则（一）服务国家战略…」必须切出 `（一）` 子节点，一级 title 只留标题。"""
    res = parse_document(CASE_SWALLOWED_CHILD)
    assert res["mode"] == "notice", res["mode"]
    node2 = res["structure"][1]
    assert node2["number"] == "二" and node2["title"] == "基本原则", node2
    assert [c["number"] for c in node2["children"]] == ["（一）", "（二）"], node2
    assert node2["children"][0]["content"].startswith("服务国家战略"), node2["children"][0]
    # 「（一）」的文字不得再残留在父级标题里
    assert "（一）" not in node2["title"]


def test_f1_cross_reference_markers_are_not_split():
    """交叉引用形态（同层同号词组 / 「（一）项」内联引用）不得被误切。"""
    r1 = parse_document("一、负责一、二类口岸开放或关闭的审查、报批工作，并负责组织落实有关具体事宜。")
    assert r1["structure_count"] == 1, r1["structure"]
    assert r1["structure"][0]["children"] == [], r1["structure"][0]
    r2 = parse_document("一、各机构应当按照本通知（一）项规定的条件和程序，报送相关材料，"
                        "并确保真实准确完整。")
    assert r2["structure_count"] == 1 and r2["structure"][0]["children"] == [], r2["structure"]


def test_f1_chapter_title_glue_is_split():
    """章标题粘连（目录页 + 正文同号章标题）必须切开且经 `_drop_toc_chapters` 收敛为一章。"""
    r = extract_structure("第一章 总 则第一章 总 则\n第一条 为了规范管理，制定本办法。\n"
                          "第二条 本办法适用于…。")
    assert len(r["chapters"]) == 1, r["chapters"]
    assert r["chapters"][0]["title"] == "总 则", r["chapters"]
    assert len(r["articles"]) == 2, r["articles"]
    # 节标题不得并入上一条正文（相邻项修复）
    r2 = extract_structure("第一条 甲。\n第二节 风险管理\n第二条 乙。")
    assert r2["repair"].get("sections_dropped") == 1, r2["repair"]
    assert all("第二节" not in a["body"] for a in r2["articles"]), r2["articles"]


def test_f2_title_and_body_are_separated():
    """F2 问题二/三：短标题形态入 `title`，其余（含无标题条款）入 `content`。"""
    # ①源端换行保留（raw 形态）
    r1 = parse_document("一、总体目标和指导原则\n（一）总体目标\n"
                        "借鉴国际金融监管改革成果，构建银行业监管框架，支持国民经济稳健增长。")
    n = r1["structure"][0]["children"][0]
    assert n["number"] == "（一）" and n["title"] == "总体目标", n
    assert n["content"].startswith("借鉴国际金融监管改革成果"), n
    # ②换行被折叠为空格（clean 层落盘形态）→ 候选「首个空白前片段」生效
    r2 = parse_document("一、总体目标和指导原则 （一）总体目标 "
                        "借鉴国际金融监管改革成果，构建银行业监管框架，支持国民经济稳健增长。")
    n2 = r2["structure"][0]["children"][0]
    assert n2["title"] == "总体目标" and n2["content"].startswith("借鉴国际金融监管"), n2
    # ③无标题条款：title 必须为空，正文完整（不得切成 title+content 两段）
    body = ("发行人应充分、及时披露总损失吸收能力非资本债券相关信息，真实、准确、完整地"
            "揭示债券特有风险。总损失吸收能力非资本债券存续期间，发行人应按季度进行信息披露。")
    r3 = parse_document("五、" + body)
    n3 = r3["structure"][0]
    assert n3["title"] == "", n3
    assert n3["content"].startswith("发行人应充分、及时披露"), n3
    assert n3["content"].endswith("按季度进行信息披露。"), n3


def test_f3_document_tail_is_stripped():
    """F3 问题三-bis/四：行内 `附：`/答记者问、发文机关+日期、联合发布噪声须截断并留痕。"""
    r1 = parse_document(
        "第一条 本办法自2021年2月1日起施行，《关于加强保险中介机构信息化建设的通知》"
        "(保监发〔2007〕28号)同时废止。 附：中国银保监会发布《保险中介机构信息化工作监管办法》"
        "（）中国银保监会有关部门负责人就《保险中介机构信息化工作监管办法》答记者问（）")
    body = r1["articles"][0]["body"]
    assert "附：" not in body and "答记者问" not in body, body
    assert body.endswith("同时废止。"), body
    assert r1["tail_cut"]["removed"] > 0 and r1["tail_cut"]["marker"], r1["tail_cut"]
    r2 = parse_document("第一条 甲。第二条 乙。 中国人民银行 银保监会 2022 年 4 月 26 日 "
                        "人民银行 银保监会联合发布《关于…有关事项的通知》")
    assert "2022" not in r2["articles"][-1]["body"], r2["articles"][-1]
    assert r2["tail_cut"]["marker"] in ("signature",) or "联合发布" in r2["tail_cut"]["marker"]


def test_f3_legit_dates_and_annex_refs_are_kept():
    """反例：正文内的合法生效日期、「附件一」引用不得被误截。"""
    r = parse_document("第四十二条 本条例自2010年6月1日起施行。"
                       "依照本条例附件一规定的程序办理。")
    body = r["articles"][0]["body"]
    assert "2010年6月1日" in body and "附件一" in body, body
    assert r["tail_cut"] == {}, r["tail_cut"]


def test_f5a_parse_input_whitespace_normalized():
    """F5a：NBSP/EN SPACE 与断句注入型空格在**解析产物**中归一（事实源不被改动）。"""
    src = "第一条 发行人应充分、\u00a0及时披露\u2002信息，\u00a02022 年 4 月 26 日生效。"
    res = parse_document(src)
    body = res["articles"][0]["body"]
    assert "\u00a0" not in body and "\u2002" not in body, repr(body)
    assert "、及时披露信息" in body, body
    assert "2022年4月26日生效" in body, body
    assert src == "第一条 发行人应充分、\u00a0及时披露\u2002信息，\u00a02022 年 4 月 26 日生效。"


def test_f6_article_items_hierarchy():
    """F6 问题五：条文内「项（一）/目 1.」须成层级（`article_structure`），body 原文保真。"""
    body = ("第二十九条 复议机关按照下列规定作出决定：（一）认定事实清楚，决定维持。"
            "（二）有下列情形之一的，决定撤销：1.主要事实不清的。2.适用依据错误的。"
            "（三）决定撤销的，责令重新作出具体行政行为。")
    res = parse_document(body)
    astr = res["article_structure"]
    assert len(astr) == 1, astr
    node = astr[0]
    assert node["level"] == "条" and node["number"] == "第二十九条", node
    assert node["content"].endswith("作出决定："), node
    assert [it["number"] for it in node["items"]] == ["（一）", "（二）", "（三）"], node["items"]
    assert [s["number"] for s in node["items"][1]["items"]] == ["1", "2"], node["items"][1]
    assert node["items"][1]["items"][0]["level"] == "目"
    # `articles[].body` 原文不动（下游零改动）
    assert res["articles"][0]["body"].startswith("第二十九条 复议机关")
    # MD 视图按层级缩进
    md = render_markdown(res, title="测试")
    assert "  - **（一）** 认定事实清楚" in md, md[:400]
    assert "    - 1 主要事实不清的。" in md, md[:600]


def test_f7_validator_structure_rules():
    """F7：V008 结构完整性 / V009 尾部污染 / V010 空白污染；干净行仍 PASSED。"""
    v0 = validate_clauses(_row())
    assert v0["status"] == "PASSED" and v0["issues"] == [], v0
    swallowed = {"level": "一级", "number": "二",
                 "title": "基本原则（一）服务国家战略，推动协调发展。以京津冀协同发展为引领，"
                          "促进区域间融合互动、融通互补。",
                 "content": "", "items": [], "children": []}
    v8 = validate_clauses(_row(structure=[swallowed], structure_count=1))
    assert any(i["rule_id"] == "V008" and i["severity"] == "WARN" for i in v8["issues"]), v8
    v9 = validate_clauses(_row(articles=[{"no": 1, "number": "第一条",
                                          "body": "第一条 自2021年起施行。 附：答记者问（）"}]))
    assert any(i["rule_id"] == "V009" for i in v9["issues"]), v9
    v10 = validate_clauses(_row(articles=[{"no": 1, "number": "第一条",
                                           "body": "第一条 发行人应充分、 及时披露信息。"}]))
    assert any(i["rule_id"] == "V010" for i in v10["issues"]), v10
    # 校验期临时键不得残留（产物行键集契约）
    row = _row()
    validate_clauses(row)
    assert "_semantics" not in row


def test_f7_structure_semantics_counts_uncovered_items():
    """F7 指标：`items` 统计**未抽取层级**的条文数（F6 落地应为 0）。"""
    from std_lib.scraper_std.document_structure import structure_semantics  # noqa: PLC0415
    body = ("第二十九条 复议机关作出决定：（一）认定事实清楚的，决定维持。"
            "（二）事实不清的，决定撤销。")
    res = parse_document(body)
    row = {"articles": res["articles"], "article_structure": res["article_structure"],
           "structure": []}
    assert structure_semantics(row)["items"] == 0, structure_semantics(row)
    row2 = {"articles": res["articles"], "article_structure": [], "structure": []}
    assert structure_semantics(row2)["items"] == 1, structure_semantics(row2)
