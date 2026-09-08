# -*- coding: utf-8 -*-
"""召回复核交付物生成器: 基于 scan_records.csv 产出清单/边界/808提取/无法访问/统计/报告"""
import collections
import csv
import json
import os
import re
import sys

csv.field_size_limit(sys.maxsize)
# P4（2026-09-08）：相对 orchestrator 根注入（R4/Q3），取消盘符。
_THIS = os.path.dirname(os.path.abspath(__file__))          # modules/regulatory_classifier/recall_audit
_MOD_CLASS = os.path.dirname(_THIS)                          # modules/regulatory_classifier
_SCRAPERS_MOD = os.path.join(os.path.dirname(_MOD_CLASS), "regulatory_scrapers")
_ORCH_ROOT = os.path.dirname(os.path.dirname(_MOD_CLASS))
for _p in (_MOD_CLASS, _SCRAPERS_MOD, _ORCH_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)
OUTDIR = os.path.join(_THIS, "output")   # 代码/产物分离（2026-09-08）
CLASS = os.path.join(_MOD_CLASS, "data")
ATTR = os.path.join(CLASS, "人身保险公司-文件归属表.csv")
# 2026-08-31 重构：归属表仅 8 列，已删除「主题」「同文件主编号」列；
# 主题唯一来源＝人身保险公司-主题归属表.csv（枚举 T0上位法锚点 + T1–T10 全称）。
THEME = os.path.join(CLASS, "人身保险公司-主题归属表.csv")
SCAN = os.path.join(OUTDIR, "scan_records.csv")

# 主题单一事实源：一律取 rfn.THEME_MAP（禁止本地副本）。
from rfn import THEME_MAP  # noqa: E402


def _norm_docno(s):
    if not s: return ""
    s=s.strip().replace("〔","[").replace("〕","]").replace("【","[").replace("】","]")
    s=s.replace(" ","").replace("　","").replace("\u3000","").lower()
    return s
def _norm_title(s):
    if not s: return ""
    s=s.strip().replace("\u3000","")
    s=re.sub(r"[\s（）()、《》""''，。：:；;,.!！?？、_/()-]","",s).lower()
    return s

# ---------- 加载 ----------
scan=[]
with open(SCAN,encoding="utf-8-sig",newline="") as f:
    for r in csv.DictReader(f): scan.append(r)

attr={}
with open(ATTR,encoding="utf-8-sig",newline="") as f:
    for r in csv.DictReader(f): attr[r["监管文件编号"]]=r
# 合并「主题」列：归属表自 2026-08-31 重构起不再含该列，须按 RFN 从主题归属表注入。
# 任何直接 attr[...]["主题"] 的写法在未合并时会 KeyError（2026-09-01 06:00 编排即因此崩溃）。
_tmap={}
with open(THEME,encoding="utf-8-sig",newline="") as f:
    for r in csv.DictReader(f): _tmap[r["监管文件编号"]]=r["主题"]
for _rfn,_row in attr.items():
    _row["主题"]=_tmap.get(_rfn,"")

# ---------- 判定理由 / 建议主题 ----------
HARD_LIFE={"人身保险公司","人寿保险公司","寿险公司","健康险公司","养老险公司","养老保险公司","人身险公司"}
def reason_and_theme(r):
    a=set(x for x in r["a_kws"].split("|") if x)
    b=set(x for x in r["b_kws"].split("|") if x)
    x=set(x for x in r["x_kws"].split("|") if x)
    reasons=[]
    if a & HARD_LIFE:
        reasons.append("文件主体/适用对象明确为人身险/寿险/健康险/养老险公司（含其资管子公司）")
    elif a:
        reasons.append("正文明确涉及人身保险/寿险/健康保险/养老险业务，适用主体包含人身险公司")
    if b:
        reasons.append("规范保险销售/代理人行为，直接约束人身险公司销售队伍")
    if "养老保险公司" in a:
        reasons.append("涉及养老保险公司（保险资管子公司）")
    if x:
        reasons.append(f"同时存在排除信号({','.join(sorted(x)[:3])})，已据主体规则判定")
    if not reasons:
        reasons.append("泛化命中'保险公司/保险机构'等，未明确指向人身险公司主体")
    reason="；".join(reasons)
    # 建议主题
    txt=(r["title"]+" "+r["a_kws"]+" "+r["b_kws"]+" "+r["c_kws"]).lower()
    if any(k in txt for k in ["销售","代理","营销","兼业","中介","消费者","理赔","服务","回访"]):
        theme=THEME_MAP["T1"]
    elif any(k in txt for k in ["产品","条款","费率","健康保险","养老保险","寿险","分红","万能","投连","定价","精算"]):
        theme=THEME_MAP["T2"]
    elif any(k in txt for k in ["资金","资管","投资","偿付能力","治理","合规","风险","反洗钱","关联交易"]):
        theme="T3/T4/T5/T7/T9 待人工细分"
    elif any(k in txt for k in ["健康","医疗","养老","税延","个人养老金"]):
        theme="T2/T6 养老与健康保险专项"
    else:
        theme="待定（建议人工归类）"
    return reason,theme

# ---------- 去重(按 文号+标题 复合键, 避免跨源重复且规避同号文异题脏值) ----------
def dedup(rows):
    seen={}; out=[]
    for r in rows:
        k=(_norm_docno(r["document_number"]) or "") + "||" + _norm_title(r["title"])
        if k in seen: continue
        seen[k]=1; out.append(r)
    return out

inc_all=[r for r in scan if r["decision"]=="INCLUDE" and r["in_attr"]=="False"]
bnd_all=[r for r in scan if r["decision"]=="BOUNDARY" and r["in_attr"]=="False"]
inc=dedup(inc_all); bnd=dedup(bnd_all)
print("疑似漏提(INCLUDE非808)去重后:",len(inc),"| 边界(BOUNDARY非808)去重后:",len(bnd))

# ---------- 写 疑似漏提取清单 ----------
with open(os.path.join(OUTDIR,"疑似漏提取文件清单.csv"),"w",encoding="utf-8-sig",newline="") as f:
    w=csv.writer(f)
    w.writerow(["序号","来源","发文字号","文件名","命中关键词","命中段落摘录","判定理由","置信度","建议主题归类","对应已提取ID"])
    for i,r in enumerate(inc,1):
        reason,theme=reason_and_theme(r)
        w.writerow([i,r["source"],r["document_number"],r["title"],r["a_kws"]+" "+r["b_kws"],
                    r["snippet"],reason,r["confidence"],theme,"（漏提，无对应ID）"])

# ---------- 写 边界案例清单 ----------
with open(os.path.join(OUTDIR,"边界案例清单.csv"),"w",encoding="utf-8-sig",newline="") as f:
    w=csv.writer(f)
    w.writerow(["序号","来源","发文字号","文件名","命中关键词(泛化)","命中段落摘录","取舍理由","置信度","建议处理"])
    for i,r in enumerate(bnd,1):
        reason,theme=reason_and_theme(r)
        sug="人工复核：判断是否人身险公司经营相关；若确为财险/再保/中介主体则排除"
        w.writerow([i,r["source"],r["document_number"],r["title"],r["c_kws"]+" "+r["b_kws"],
                    r["snippet"],reason,"低",sug])

# ---------- 808 全文提取记录(要求1) ----------
# 以 RFN 归集 scan 中 in_attr 记录, 取正文最丰富者
rfn_scan=collections.defaultdict(list)
for r in scan:
    if r["in_attr"]=="True" and r["matched_rfn"]:
        for rf in r["matched_rfn"].split(";"):
            if rf: rfn_scan[rf].append(r)

ft_rows=[]
for rfn in sorted(attr.keys()):
    a=attr[rfn]
    recs=rfn_scan.get(rfn,[])
    rec=max(recs,key=lambda x:int(x["avail_text_len"])) if recs else None
    if rec:
        body_ok = rec["has_full_body"]=="True"
        a_kws=rec["a_kws"]; b_kws=rec["b_kws"]; c_kws=rec["c_kws"]; x_kws=rec["x_kws"]
        decision=rec["decision"]; conf=rec["confidence"]; snip=rec["snippet"]
        src=rec["source"]; docno=rec["document_number"]
        avail=rec["avail_text_len"]
    else:
        body_ok=False; a_kws=b_kws=c_kws=x_kws=""; decision="—"; conf="—"; snip=""
        src=""; docno=a["发文字号"]; avail="0"
    status = "已提取-可全文" if body_ok else ("已提取-缺正文" if rec else "未在五源cleaned收录")
    ft_rows.append({"rfn":rfn,"theme":a["主题"],"name":a["文件名称"],"docno":a["发文字号"],
                    "src":src,"decision":decision,"conf":conf,"a":a_kws,"b":b_kws,
                    "c":c_kws,"x":x_kws,"snip":snip,"avail":avail,"status":status})
with open(os.path.join(OUTDIR,"归属表全文提取记录.csv"),"w",encoding="utf-8-sig",newline="") as f:
    w=csv.writer(f)
    w.writerow(["监管文件编号","主题","文件名","发文字号","匹配源","决策","置信度",
                "A强信号关键词","B销售代理关键词","C泛化关键词","X排除关键词","命中摘录","可用正文长度","全文状态"])
    for e in ft_rows:
        w.writerow([e["rfn"],e["theme"],e["name"],e["docno"],e["src"],e["decision"],e["conf"],
                    e["a"],e["b"],e["c"],e["x"],e["snip"],e["avail"],e["status"]])

# 归属表条目中无法访问全文(缺正文或未收录；产出文件名沿用历史命名 808…)
no_body=[e for e in ft_rows if e["status"]!="已提取-可全文"]
with open(os.path.join(OUTDIR,"归属表无全文清单.csv"),"w",encoding="utf-8-sig",newline="") as f:
    w=csv.writer(f)
    w.writerow(["监管文件编号","主题","文件名","发文字号","状态","说明"])
    for e in no_body:
        note = "未在五源cleaned收录（外部/行业自律/文号格式异常）" if e["status"]=="未在五源cleaned收录" else "五源cleaned中对应记录正文缺失(N/A)"
        w.writerow([e["rfn"],e["theme"],e["name"],e["docno"],e["status"],note])

# ---------- 各源快照日期（C-8）：经 clean_index 动态派生，严禁硬编码 ----------
SRC_SNAPSHOT={}
try:
    from clean_index import get_clean_index
    _ci=get_clean_index()
    for _s in ("gov","mof","nfra","pbc","supp"):
        _m=re.search(r"cleaned_(\d{8})",_ci.latest_csv_path(_s) or "")
        if _m: SRC_SNAPSHOT[_s]=_m.group(1)
except Exception as _e:
    print("[warn] 快照日期获取失败（报告中该项将省略）:",_e)

# ---------- 汇总统计 ----------
total=len(scan)
dec_cnt=collections.Counter(r["decision"] for r in scan)
conf_cnt=collections.Counter(r["confidence"] for r in scan)
inc_src=collections.Counter(r["source"] for r in inc)
inc_conf=collections.Counter(r["confidence"] for r in inc)
bnd_src=collections.Counter(r["source"] for r in bnd)
gov_nobody=sum(1 for r in scan if r["source"]=="gov" and r["has_full_body"]=="False")
# 各源记录总数（C-8 来源统计）：供报告按源动态呈现，消除各源条数硬编码
src_total=collections.Counter(r["source"] for r in scan)
stats={
 "total":total,"dec":dict(dec_cnt),"conf":dict(conf_cnt),
 "src_total":dict(src_total),
 "inc":len(inc),"inc_src":dict(inc_src),"inc_conf":dict(inc_conf),
 "bnd":len(bnd),"bnd_src":dict(bnd_src),
 "attr_total":len(attr),"attr_found":len([e for e in ft_rows if e["status"]!="未在五源cleaned收录"]),
 "attr_nobody":len(no_body),"gov_nobody":gov_nobody,
 "src_snapshot":SRC_SNAPSHOT,
 "attr_missed":len(attr)-len([e for e in ft_rows if e["status"]!="未在五源cleaned收录"]),
}
print("统计:",json.dumps(stats,ensure_ascii=False))

# ---------- 关键词扩充建议 ----------
expansion = [
 ("意外险 / 意外伤害保险","Tier A 弱信号","人身意外伤害险兼具寿险与财险属性；部分人身险公司核心业务含意外险，现有库仅在弱信号层覆盖。建议新增为 Tier A 弱信号（与 健康保险 同层），避免漏检以意外险为主体的文件。依据：归属表含《关于加强航空意外保险管理有关问题的通知》等。"),
 ("养老年金 / 商业养老保险 / 个人养老金","Tier A 强信号","现有库以'养老保险'覆盖，但'养老年金''商业养老保险''个人养老金'为商业养老险核心产品表述，建议明确补入 Tier A 强信号，提升对养老金融专项文件的召回。依据：人社部发〔2022〕70号《个人养老金》已命中但属弱信号。"),
 ("人身保险业 / 寿险业 / 健康险业","Tier A 强信号","现有库命中'人身保险/寿险/健康保险'名词，但'人身保险业''寿险业'等集合名词在规划类文件高频出现（如'人身保险业高质量发展'），建议补入 Tier A 强信号。依据：保险业发展规划类文件多以此表述。"),
 ("保险资产管理公司 / 保险资管","Tier A 强信号","现有库以'养老保险公司'覆盖资管子公司，但'保险资产管理公司'为通用资管主体表述（非仅养老），建议补入 Tier A 强信号以防资管规则类文件漏检。依据：保监资金〔2016〕104号等已靠'养老保险公司'命中。"),
 ("费改 / 费率市场化 / 产品费率","GENERIC/AGENT","现有库未含费率监管类表述，部分人身险产品费率文件（如普通型寿险费率厘定）仅靠'寿险'弱命中。建议补入 GENERIC（边界）或 AGENT 相关。依据：归属表 RFN-01f0ad6d44e1fe03（保监发[2010]33号 普通型定期寿险/终身寿险费率厘定）靠'寿险'弱命中。"),
 ("保单 / 保险合同 / 投保人 / 被保险人","GENERIC","现有库含'保单'，建议补充'保险合同''投保人''被保险人'以强化销售/理赔行为类文件召回。依据：保险销售/理赔行为文件高频。"),
]
with open(os.path.join(OUTDIR,"关键词库扩充建议.csv"),"w",encoding="utf-8-sig",newline="") as f:
    w=csv.writer(f); w.writerow(["建议新增词","类别","依据与说明"])
    for term,cat,basis in expansion:
        w.writerow([term,cat,basis])

# ---------- 保存 stats 供报告 ----------
with open(os.path.join(OUTDIR,"_stats.json"),"w",encoding="utf-8") as f:
    json.dump({"stats":stats,"inc":inc,"bnd":bnd,"no_body":no_body,"expansion":expansion,
               "attr_status":[e["status"] for e in ft_rows]},f,ensure_ascii=False)
print("交付物已生成。")
