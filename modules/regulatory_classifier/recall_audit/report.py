# -*- coding: utf-8 -*-
"""生成 召回复核报告.md (含关联已提取ID同主题链接)

口径纪律（C-6，2026-08-29）：报告内**全部统计数字一律由 _stats.json 与实际数据动态推导**，
禁止硬编码（历史遗留的 808/56/484/33,693/30,271/762/46/12/98% 等已全部改为动态）。
注：产出文件名 `归属表全文提取记录.csv` / `归属表无全文清单.csv` 中的 "808" 为**历史命名**，
为保持既有引用稳定而保留文件名，但其内容条数已在报告中按归属表实际份数动态呈现。
"""
import csv
import json
import os
import re
import sys

csv.field_size_limit(sys.maxsize)
# P4（2026-09-08）：相对路径（R4/Q3）
_THIS = os.path.dirname(os.path.abspath(__file__))          # modules/regulatory_classifier/recall_audit
_MOD_CLASS = os.path.dirname(_THIS)                          # modules/regulatory_classifier
OUTDIR = os.path.join(_THIS, "output")   # 代码/产物分离（2026-09-08）
ATTR = os.path.join(_MOD_CLASS, "data", "人身保险公司-文件归属表.csv")
# 2026-08-31 重构：归属表已删除「主题」列，主题唯一来源＝主题归属表（T0–T10）
THEME = os.path.join(_MOD_CLASS, "data", "人身保险公司-主题归属表.csv")

def norm(s): return re.sub(r"[\s（）()、《》""''，。：:；;,.!！?？、_/()-]","",(s or "").lower())

stats=json.load(open(os.path.join(OUTDIR,"_stats.json"),encoding="utf-8"))
inc=[dict(r) for r in csv.DictReader(open(os.path.join(OUTDIR,"疑似漏提取文件清单.csv"),encoding="utf-8-sig",newline=""))]
bnd=[dict(r) for r in csv.DictReader(open(os.path.join(OUTDIR,"边界案例清单.csv"),encoding="utf-8-sig",newline=""))]
e808=[dict(r) for r in csv.DictReader(open(os.path.join(OUTDIR,"归属表全文提取记录.csv"),encoding="utf-8-sig",newline=""))]
nobody=[dict(r) for r in csv.DictReader(open(os.path.join(OUTDIR,"归属表无全文清单.csv"),encoding="utf-8-sig",newline=""))]
expan=[dict(r) for r in csv.DictReader(open(os.path.join(OUTDIR,"关键词库扩充建议.csv"),encoding="utf-8-sig",newline=""))]
attr={r["监管文件编号"]:r for r in csv.DictReader(open(ATTR,encoding="utf-8-sig",newline=""))}
# 合并「主题」列（归属表已无该列；未合并则下方 a["主题"] 会 KeyError）
_tmap={r["监管文件编号"]:r["主题"] for r in csv.DictReader(open(THEME,encoding="utf-8-sig",newline=""))}
for _rfn,_row in attr.items():
    _row["主题"]=_tmap.get(_rfn,"")

# 关联已提取ID: 同主题(共享关键词)的808 RFN
LIFE_KW=["人身保险","寿险","健康保险","养老保险","养老险","人寿保险","保险销售","保险代理","保险营销","保险资金","保险资管","保险资产管理","保单","理赔","产品","条款","费率"]
def related_808(rec):
    text=(rec["文件名"]+" "+rec["命中关键词"]).lower()
    hits=[k for k in LIFE_KW if k in text]
    res=[]
    for rfn,a in attr.items():
        tn=norm(a["文件名称"])
        if any(k in tn for k in hits):
            res.append((rfn,a["文件名称"],a["主题"]))
        if len(res)>=3: break
    return res[:3]

# 分层: 高置信 vs 需谨慎确认
def tier(rec):
    t=rec["文件名"]
    cautious_kw=["行政许可","行业协会","反腐","惩治","经济普查","统计","系统重要性","派出机构","现场检查","法律文书","党内","成立"]
    if rec["置信度"]=="高": return "A高置信"
    if any(k in t for k in cautious_kw): return "C需谨慎确认"
    return "B中置信"

L=[]
for rec in inc:
    rec["_tier"]=tier(rec)
    rec["_rel"]=related_808(rec)
inc_sorted=sorted(inc,key=lambda r:({"A高置信":0,"B中置信":1,"C需谨慎确认":2}[r["_tier"]],r["序号"]))

S=stats["stats"]
# ---------- 动态口径（C-6：消除硬编码，一律由 _stats.json / 实际数据推导） ----------
SRC_T=S["src_total"]                      # 各源记录数 {gov:30271,...}
SRC_SNAP=S.get("src_snapshot",{})         # 各源 cleaned 快照日期（经 clean_index 动态派生）
GOV_T=SRC_T.get("gov",0)
GOV_RATE=(S["gov_nobody"]/GOV_T*100) if GOV_T else 0.0
SRC_FMT=" / ".join(f"{k} {v:,}" for k,v in sorted(SRC_T.items(),key=lambda x:-x[1]))
SNAP_FMT="、".join(f"{k} 取 {v}" for k,v in sorted(SRC_SNAP.items()))
INC_SRC_FMT="、".join(f"{k} {v}" for k,v in sorted(S["inc_src"].items())) or "—"
BND_TOP=sorted(S["bnd_src"].items(),key=lambda x:-x[1])[0] if S["bnd_src"] else ("—",0)
md=[]
md.append("# 监管 classifier 提取结果·召回复核报告")
md.append("")
md.append(f"> 生成时间：2026-08-29 ｜ 复核宇宙：五源 cleaned 共 **{S['total']:,}** 条 ｜ 已提取基准 E：**{S['attr_total']:,}** 份归属表")
md.append("")
md.append("## 一、核心结论")
md.append("")
md.append(f"- **复核总数**：{S['total']:,} 条（{SRC_FMT}）。")
md.append(f"- **全量分层判定**：INCLUDE（明确命中人身险公司范围）**{S['dec']['INCLUDE']}** 条；BOUNDARY（泛化/模糊，需人工）**{S['dec']['BOUNDARY']}** 条；EXCLUDE（排除）**{S['dec']['EXCLUDE']}** 条。")
md.append(f"- **疑似漏提取（命中且未纳入 {S['attr_total']:,} 份归属表）去重后：{S['inc']} 条**（高置信 {S['inc_conf'].get('高',0)}、中置信 {S['inc_conf'].get('中',0)}）；按源：{INC_SRC_FMT}。")
md.append(f"- **边界案例（去重）：{S['bnd']} 条**，单列待人工取舍。")
md.append(f"- **无法访问全文**：gov 源 {S['gov_nobody']:,} 条缺正文（仅元数据可查，召回受限）；归属表 {S['attr_total']:,} 份中 {S['attr_nobody']} 份无法从 cleaned 获取全文（详见第七节）。")
md.append("")
md.append("## 二、数据基础与判定口径")
md.append("")
md.append("### 2.1 数据基础")
md.append("")
md.append("| 项目 | 说明 |")
md.append("|---|---|")
md.append(f"| 复核宇宙 | 五源 cleaned（gov/mof/nfra/pbc/supp）合计 {S['total']:,} 条{(f'（{SNAP_FMT}）' if SNAP_FMT else '')} |")
md.append(f"| 已提取基准 E | `人身保险公司-文件归属表.csv` **{S['attr_total']:,}** 份；经发文字号/标题归一关联，**{S['attr_found']:,}** 份命中 cleaned、**{S['attr_missed']}** 份未命中（外部/行业自律/文号格式异常） |")
md.append("| 全文提取覆盖字段 | title / summary / issue_organ / column_name / theme_name / body_text / body_text_webpage / body_text_doc / attachment_content / keyword（覆盖正文、附表、落款发文机构、发文对象上下文） |")
md.append(f"| 关键约束 | **gov 源 {GOV_RATE:.0f}% 记录正文为 N/A**（{S['gov_nobody']:,}/{GOV_T:,}；仅标题/摘要/栏目元数据），无法做真正全文提取，其召回仅依赖元数据，存在漏检风险 |")
md.append("")
md.append("### 2.2 判定口径（人身险公司范围）")
md.append("")
md.append("依据任务定义：**文件主体针对人身险/寿险/健康险/养老险公司（含其分支机构、资管子公司），或发文对象、适用主体明确包含上述机构**；排除仅顺带提及、或主体为财险/再保/保险中介（行业主体）/其他金融机构的情形。")
md.append("")
md.append("### 2.3 关键词库与分层决策规则")
md.append("")
md.append("| 层级 | 关键词（示例） | 决策 | 置信度 |")
md.append("|---|---|---|---|")
md.append("| **Tier A 强信号** | 人身保险公司/人寿保险公司/寿险公司/健康险公司/养老险公司/养老保险公司/人身险公司/人寿保险/人身保险/寿险/健康险/养老险（均加 `(?<!非)` 负向环视排除「非寿险」；寿险再加 `(?!再保险)`） | 标题命中→INCLUDE·高；适用/发文对象上下文命中→INCLUDE·中；正文孤立命中→BOUNDARY·低（顺带提及嫌疑） | 高/中/低 |")
md.append("| *Tier A 弱信号* | 养老保险、健康保险（易在税法/社保法中顺带出现） | 仅弱信号且非标题命中→BOUNDARY·低 | 低 |")
md.append("| **AGENT 销售代理** | 保险销售从业人员/保险营销员/保险代理人/保险兼业代理/个人保险代理人（与归属表既有实践一致，约束保险公司销售队伍） | INCLUDE·中（若为经纪/公估等行业主体则 EXCLUDE） | 中 |")
md.append("| **GENERIC 泛化** | 保险公司/保险机构/保险理赔/保险资金/保险资产管理/分红保险/万能保险/投连险/保险产品/保单/偿付能力/保险销售/保险营销 等 | BOUNDARY·低（需人工判断是否人身险经营相关） | 低 |")
md.append("| **EXCL 排除** | 财产保险公司/财险/财产险/再保险/保险经纪公司/保险公估/保险中介机构/商业银行/证券/信托/机动车辆保险/车险 + 社保型养老（社会养老保险/基本养老保险/养老保险条例 等） | EXCLUDE·中 | 中 |")
md.append("")
md.append("**标题级覆盖规则（主体判定优先）**：标题含财险/再保/机动车辆保险等且未同时冠名人身险公司→直接 EXCLUDE；标题含社保型养老→EXCLUDE（社会保障法，非商业养老险公司主体）。")
md.append("")
md.append("## 三、方法论")
md.append("")
md.append(f"1. **全文提取**：对 {S['total']:,} 条逐条合并多字段文本，正则合并扫描（4 次/条，避免逐词顺序匹配的性能问题），记录命中关键词、字段位置与上下文摘录。")
md.append(f"2. **比对去重**：以发文字号（格式化归一）+ 标题归一为主键，将归属表 {S['attr_total']:,} 份映射至 cleaned 得已提取集合 E；「命中但未被纳入」= INCLUDE/BOUNDARY 且不在 E 的记录。")
md.append("3. **分层判定**：按 2.3 规则输出 INCLUDE/BOUNDARY/EXCLUDE 与置信度；跨源同文档按「文号+标题」复合键去重。")
md.append("4. **约束遵循**：关键词库仅做建议扩充（见第八节），未改动判定逻辑。")
md.append("")
md.append("## 四、汇总统计")
md.append("")
md.append("| 指标 | 数值 |")
md.append("|---|---|")
md.append(f"| 复核总数 | {S['total']:,} |")
md.append(f"| INCLUDE（明确命中） | {S['dec']['INCLUDE']} |")
md.append(f"| BOUNDARY（边界待核） | {S['dec']['BOUNDARY']} |")
md.append(f"| EXCLUDE（排除） | {S['dec']['EXCLUDE']} |")
md.append(f"| **疑似漏提取（去重）** | **{S['inc']}**（高 {S['inc_conf'].get('高',0)} / 中 {S['inc_conf'].get('中',0)}） |")
md.append(f"| 边界案例（去重） | {S['bnd']} |")
md.append("")
md.append("**疑似漏提按来源**："+ " ｜ ".join(f"{k} {v}" for k,v in sorted(S['inc_src'].items()))+"  ")
md.append("")
md.append("**边界案例按来源**："+ " ｜ ".join(f"{k} {v}" for k,v in sorted(S['bnd_src'].items()))+"  ")
md.append("")
md.append("## 五、疑似漏提取文件清单（去重 "+str(S['inc'])+" 条）")
md.append("")
md.append("> 置信度说明：**高**＝标题明确冠名人身险公司/业务；**中**＝正文适用/发文对象上下文命中，或销售代理行为规则。")
md.append(f"> 「关联已提取ID」＝同主题已纳入归属表（{S['attr_total']:,} 份）的 RFN（供归位参考；漏提文件本身无对应 ID）。")
md.append("")
for grp,label in [("A高置信","### 5.1 高置信疑似漏提（明确针对人身险公司/业务）"),
                  ("B中置信","### 5.2 中置信疑似漏提（人身险业务相关，文件具综合性）"),
                  ("C需谨慎确认","### 5.3 需谨慎确认（泛化/顺带提及嫌疑，建议人工复核是否纳入）")]:
    sub=[r for r in inc_sorted if r["_tier"]==grp]
    md.append("")
    md.append(label+f"（{len(sub)} 条）")
    md.append("")
    md.append("| 序号 | 来源 | 发文字号 | 文件名 | 命中关键词 | 置信度 | 建议主题 | 关联已提取ID |")
    md.append("|---|---|---|---|---|---|---|---|")
    for r in sub:
        rel="；".join(f"{x[0]}({x[2].split('（')[0]})" for x in r["_rel"]) or "—"
        md.append(f"| {r['序号']} | {r['来源']} | {r['发文字号'][:16]} | {r['文件名'][:30]} | {r['命中关键词'][:28]} | {r['置信度']} | {r['建议主题归类'].split('（')[0]} | {rel} |")
md.append("")
md.append(f"> 完整 {S['inc']} 条（含命中段落摘录、判定理由）见附件 `疑似漏提取文件清单.csv`。")
md.append("")
md.append("## 六、边界案例（去重 "+str(S['bnd'])+" 条，单列）")
md.append("")
md.append("边界案例指仅泛化命中「保险公司/保险机构」等、未明确指向人身险公司主体，或弱信号孤命中、源缺正文无法核验者。**取舍原则**：")
md.append("- 若实为财险/再保/保险经纪·公估（行业主体）/银行证券类文件 → **排除**（不纳入人身险库）；")
md.append("- 若确为人身险公司经营相关（偿付能力、保险消费者、保险条款、保险理赔等普适规则）→ 由人工判断是否纳入；")
md.append("- gov 源因缺正文，仅元数据命中者优先人工核验全文后再定。")
md.append("")
md.append("**边界案例按来源分布**："+ " ｜ ".join(f"{k} {v}" for k,v in sorted(S['bnd_src'].items()))+"  ")
md.append("")
md.append("代表性抽样（前 25 条）：")
md.append("")
md.append("| 序号 | 来源 | 发文字号 | 文件名 | 命中关键词(泛化) |")
md.append("|---|---|---|---|---|")
for r in bnd[:25]:
    md.append(f"| {r['序号']} | {r['来源']} | {r['发文字号'][:16]} | {r['文件名'][:30]} | {r['命中关键词(泛化)'][:24]} |")
md.append("")
md.append(f"> 完整 {S['bnd']} 条见附件 `边界案例清单.csv`（含取舍理由与建议处理）。")
md.append("")
md.append("## 七、无法访问全文 / 乱码文件（单列，不做猜测）")
md.append("")
md.append(f"- **gov 源系统性缺正文**：{S['gov_nobody']:,} 条（占 gov {GOV_T:,} 的 {GOV_RATE:.0f}%）`body_text/body_text_webpage/body_text_doc/attachment_content` 均为 N/A，仅余标题/摘要/发文机构/栏目。此类记录仅能依元数据判定，**凡需正文佐证的人身险文件可能未被召回**，属方法局限而非漏检结论。")
md.append(f"- **归属表 {S['attr_total']:,} 份中无法从 cleaned 获取全文：{S['attr_nobody']} 份**（详见 `归属表无全文清单.csv`）：")
md.append(f"  - {S['attr_missed']} 份未在五源 cleaned 收录（外部文件/行业自律/文号格式异常，如人社部发〔2022〕70号、中保协发〔2009〕161号等）；")
md.append("  - 其余为 cleaned 中对应记录正文缺失（N/A）。")
md.append("- **个别乱码/异常**：未发现整批乱码；gov 前段法律法规类（xzfg.moj.gov.cn）有完整正文，已正常参与判定。")
md.append("")
md.append("## 八、关键词库扩充建议（仅列建议，未改动判定逻辑）")
md.append("")
md.append("| 建议新增词 | 类别 | 依据与说明 |")
md.append("|---|---|---|")
for e in expan:
    md.append(f"| {e['建议新增词']} | {e['类别']} | {e['依据与说明']} |")
md.append("")
md.append("## 九、局限与下一步")
md.append("")
md.append(f"1. **gov 召回上限**：因 {GOV_RATE:.0f}% 缺正文，gov 源潜在的人身险文件仅靠标题/摘要，可能低估；建议补充 gov 正文抓取或接入北大法宝核验后再复核 gov 部分。")
md.append(f"2. **弱信号/泛化边界**：{S['bnd']} 条边界案例需人工逐条取舍；建议优先复核 {BND_TOP[0]} {BND_TOP[1]} 条（占多数）。")
md.append("3. **置信度校准**：「中」置信多依赖上下文词探测，偶有顺带提及误判（已在 5.3 单列供复核）。")
md.append(f"4. **闭环动作**：对 5.1/5.2 确认漏提的 {S['inc']} 条，建议按「数据底座→归属表→纵向/横向/全景报告」顺序经 `register_doc` 登记 RFN 并同步，遵循监管文件查询调用标准操作规范。")
md.append("")
md.append("---")
md.append("")
md.append("### 附件清单（均位于 `regulatory_classifier/recall_audit/`）")
md.append("")
md.append("| 文件 | 内容 |")
md.append("|---|---|")
md.append(f"| 疑似漏提取文件清单.csv | {S['inc']} 条：来源/文号/文件名/命中关键词/摘录/判定理由/置信度/建议主题 |")
md.append(f"| 边界案例清单.csv | {S['bnd']} 条：泛化命中/取舍理由/建议处理 |")
md.append(f"| 归属表全文提取记录.csv | 归属表 {S['attr_total']:,} 份逐条关键词提取（命中关键词/位置/置信度/全文状态） |")
md.append(f"| 归属表无全文清单.csv | {S['attr_nobody']} 份缺正文/未收录说明 |")
md.append(f"| 关键词库扩充建议.csv | {len(expan)} 条建议新增词及依据 |")
md.append(f"| scan_records.csv | {S['total']:,} 条全量逐条判定原始结果（可审计） |")

open(os.path.join(OUTDIR,"召回复核报告.md"),"w",encoding="utf-8").write("\n".join(md))
print("报告已生成，行数:",len(md))
print("高置信",sum(1 for r in inc if r['_tier']=='A高置信'),"中置信",sum(1 for r in inc if r['_tier']=='B中置信'),"需谨慎",sum(1 for r in inc if r['_tier']=='C需谨慎确认'))
