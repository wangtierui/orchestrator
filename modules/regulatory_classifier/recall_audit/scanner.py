# -*- coding: utf-8 -*-
"""
人身险公司范围 召回复核 —— 核心扫描器 (正则合并版, 高效)
对五源 cleaned 全量（条数经 clean_index 动态派生，勿在文档中写死）做宽口径关键词召回
+ 分层判定(INCLUDE/BOUNDARY/EXCLUDE) + 置信度(高/中/低)
并与「已提取集合 E」（= 归属表，现行 1076 份）比对, 标记 in_attr.
输出: scan_records.csv (全量逐条) + scan_hits_attr.jsonl (E 命中明细, 供"全文提取"要求)

命名说明（2026-09-08 语义消歧）：字段 in_attr / scan_hits_attr.jsonl / 归属表全文提取记录.csv /
归属表无全文清单.csv —— 历史 '808' 编号（指归属表全集 E）已按语义改写为 attr；编排器按这些精确文件名校验产物。

数据源纪律（强制）：五源 cleaned 最新快照一律经 clean_index.get_clean_index().latest_csv_path(src)
动态派生，**严禁硬编码快照日期**；索引过期时 scanner 会**自动重建 clean_index**（与编排器 Gate1 一致），
无需手动 build_clean_index.py（见 regulatory_scrapers/clean_index/README.md）。
"""
import collections
import csv
import json
import os
import re
import sys

csv.field_size_limit(sys.maxsize)

# ---- 单一事实源 + 陈旧自愈：五源 clean 最新快照一律从 clean_index 派生，禁止硬编码日期 ----
# P4（2026-09-08）：跨仓 sys.path 盘符取消（R4/Q3）——改为相对 orchestrator 根注入：
#   - modules/regulatory_scrapers（供 import clean_index）
#   - modules/regulatory_classifier（供 import rfn）
#   - orchestrator 根（供 std_lib/config）
_THIS = os.path.dirname(os.path.abspath(__file__))          # modules/regulatory_classifier/recall_audit
_MOD_CLASS = os.path.dirname(_THIS)                          # modules/regulatory_classifier
_SCRAPERS_MOD = os.path.join(os.path.dirname(_MOD_CLASS), "regulatory_scrapers")
_ORCH_ROOT = os.path.dirname(os.path.dirname(_MOD_CLASS))
for _p in (_SCRAPERS_MOD, _MOD_CLASS, _ORCH_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from clean_index import get_clean_index, scan_sources

CLASS = os.path.join(_MOD_CLASS, "data")
ATTR = os.path.join(CLASS, "人身保险公司-文件归属表.csv")
OUTDIR = os.path.join(_THIS, "output")   # 代码/产物分离（2026-09-08）：产物统一落 recall_audit/output/
os.makedirs(OUTDIR, exist_ok=True)

# 从 clean_index 读取五源最新快照 csv（彻底消除硬编码日期）
# 方案C（2026-08-29·与编排器 Gate1「自动重建」语义一致）：scanner 被直接调用时，
# 若 index.json 与磁盘实时扫描（scan_sources, hash_files=False）不一致，先自动重建 clean_index，
# 避免静默读旧快照；重建后仍不一致则 RuntimeError（疑似重建期间磁盘有并发写入）。
def _load_fresh_index():
    idx = get_clean_index()
    def _stale(src_ids, live_snap):
        bad = []
        for sid in src_ids:
            il = idx.latest(sid)
            ls = live_snap.get(sid, {})
            ll = ls.get("latest") if isinstance(ls, dict) else None
            if ((il or {}).get("date") != (ll or {}).get("date")
                    or (il or {}).get("record_count") != (ll or {}).get("record_count")):
                bad.append(sid)
        return bad
    try:
        live = scan_sources(hash_files=False)
    except Exception:
        return idx  # 实时扫描失败时退化为直接使用既有索引，不阻断
    stale = _stale(idx.source_ids(), live)
    if stale:
        idx = get_clean_index(rebuild=True)
        live2 = scan_sources(hash_files=False)
        still = _stale(idx.source_ids(), live2)
        if still:
            raise RuntimeError(
                "clean_index 自动重建后仍与磁盘实时扫描不一致（疑似重建期间磁盘有并发写入）："
                + ", ".join(still))
    return idx

_idx = _load_fresh_index()
CLEANED = {}
for _src in ("gov", "mof", "nfra", "pbc", "supp"):
    _p = _idx.latest_csv_path(_src)
    if not _p or not os.path.exists(_p):
        raise RuntimeError(
            f"clean_index 未找到 {_src} 的 latest csv（或文件不存在），无法构造检索输入。"
            f"请先在 regulatory_scrapers 执行 clean_index 重建。"
        )
    CLEANED[_src] = _p
NULL_TOKENS = {"", "n/a", "na", "无", "-", "none", "null", "未注明", "不详"}

# P4：recall 匹配用归一化保留为本文件私有实现（语义与 common_lib.norm 不同：
# 本版全角→半角括号并转小写，用于 scan 召回去重比对），故下划线命名避免被
# gate_no_duplicate_libs 当作重复公共符号。
# norm-specialization: 扫描引擎内部复合键（半角方括号保留 + lower + NULL 校验）
def _norm_docno(s):
    if not s: return ""
    s = s.strip().replace("〔","[").replace("〕","]").replace("【","[").replace("】","]")
    s = s.replace(" ","").replace("　","").replace("\u3000","").lower()
    return "" if s in NULL_TOKENS else s

# norm-specialization: 扫描引擎内部复合键（去标点 + lower + NULL 校验）
def _norm_title(s):
    if not s: return ""
    s = s.strip().replace("\u3000","")
    # 注：'-' 置于字符类末尾即字面量（原 '\_\-' 写法触发 SyntaxWarning: invalid escape sequence）
    s = re.sub(r"[\s（）()、《》""''，。：:；;,.!！?？、_/-]", "", s).lower()
    return "" if s in NULL_TOKENS else s

# ---------------- 关键词库 ----------------
# Tier A 强信号: 明确冠名人身险/寿险/健康险/养老险含其公司主体
# 加负向环视 (?<!非) 排除"非寿险/非人寿"等; 寿险另加 (?!再保险) 排除"寿险再保险"顺带提及
# 强/弱拆分: 弱信号(养老保险/健康保险)易在税法/社保法中顺带出现, 仅弱信号且非标题命中 -> BOUNDARY
TIER_A_HARD = [
    "(?<!非)人身保险公司","(?<!非)人寿保险公司","(?<!非)寿险公司","(?<!非)健康险公司",
    "(?<!非)养老险公司","(?<!非)养老保险公司","(?<!非)人身险公司","(?<!非)人寿保险",
    "(?<!非)人身保险","(?<!非)寿险(?!再保险)","(?<!非)健康险","(?<!非)养老险",
]
TIER_A_SOFT = ["(?<!非)养老保险","(?<!非)健康保险"]
TIER_A = TIER_A_HARD + TIER_A_SOFT
AGENT = [  # 保险销售/代理人行为监管(与808实践一致) —— 仅保留针对"销售从业人员/代理人"的专词
    "保险销售从业人员","保险营销员","保险代理人","保险兼业代理",
    "个人保险代理人","独立个人保险代理人",
]
GENERIC = [  # 泛化但经营相关 —— 用于 BOUNDARY 人工复核
    "保险公司","保险机构","保险理赔","保险资金","保险资产管理","分红保险","万能保险",
    "投资连结保险","投连险","保险产品","保险消费者","保单","偿付能力","保险公司分支机构",
    "分支机构","保险条款","保险销售","保险营销",  # 裸销售/营销降为边界; 养老险已在A优先匹配
]
EXCL_COMPANY = ["保险经纪公司","保险经纪机构","保险公估","保险中介机构","公估机构","保险经纪"]
EXCL_SECTOR  = ["财产保险公司","财产险","再保险公司","商业银行","银行业金融机构",
                "证券公司","基金管理公司","信托公司","金融资产","银行业"]
# 社保型养老(社会保障法, 非商业养老险公司主体) —— 标题命中即排除
EXCL_SOCIAL  = ["社会养老保险","基本养老保险","社会保险费","社会保障基金",
                "城乡居民基本养老保险","城镇企业职工基本养老保险","职工基本养老保险",
                "企业职工基本养老保险","社会医疗保险","基本医疗保险",
                "补充养老保险","企业职工养老保险","城乡居民养老保险","养老保险条例",
                "社会养老"]
CTX_WORDS = ["各","适用","主送","抄送","发给","发往","对象","范围","通知如下","现将","监管","发送"]

def build_re(kws):
    # 长词优先, 避免子串抢占; 关键词为普通中文或已含合法正则(环视), 不做 re.escape
    kws_sorted = sorted(set(kws), key=len, reverse=True)
    return re.compile("|".join(kws_sorted))

RE_A = build_re(TIER_A)
RE_B = build_re(AGENT)
RE_C = build_re([k for k in GENERIC if k not in TIER_A])
RE_X = build_re(EXCL_COMPANY + EXCL_SECTOR)

def scan_group(rx, text, title_len, meta_end):
    """返回命中列表 [(kw, pos, field)]"""
    hits=[]
    for m in rx.finditer(text):
        kw=m.group(0); pos=m.start()
        if pos < title_len: field="title"
        elif pos < meta_end: field="meta"
        else: field="body"
        hits.append((kw,pos,field))
    return hits

def ctx_around(text, hit):
    kw,pos,field=hit
    window=text[max(0,pos-25):pos+len(kw)+25]
    return any(w in window for w in CTX_WORDS)

def snippet(text, hit, width=55):
    kw,pos,field=hit
    a=max(0,pos-width); b=min(len(text),pos+len(kw)+width)
    return f"[{field}]…{text[a:b].replace(chr(10),' ').replace(chr(13),' ').strip()}…"

def classify(title, meta, body, title_only=False):
    """判定记录与人身险监管的相关性。

    title_only（2026-09-17 新增，默认 False —— 既有 recall_audit 行为完全不变）：
        只在**标题**范围内认定命中，忽略 meta/body 命中。
        动机：本词表（尤其 GENERIC 里的**裸词「分支机构」**）是为"以保险类为主的
        五源语料"调优的；用于成分完全不同的语料（如 gov 政策文件库全量）时，
        几乎所有行政法规的正文都含"分支机构"，导致 BOUNDARY 层大面积误收
        ——实测 gov 13177 条中 BOUNDARY/低 达 752 条，样例为《快递暂行条例》
        《森林病虫害防治条例》等明显无关文件。
        开启后，泛保险词（GENERIC）须出现在**标题**才认定为相关，
        口径与"这份文件的主题是否指向保险"一致。
        排除项（EXCL_*）同样只在标题判定——否则正文里的"商业银行"等
        会把标题明确指向人身险的文件误排除。
    """
    text = title + "\n" + meta + "\n" + body
    title_len = len(title)+1
    meta_end = title_len + len(meta)+1
    a_hits = scan_group(RE_A, text, title_len, meta_end)
    b_hits = scan_group(RE_B, text, title_len, meta_end)
    c_hits = scan_group(RE_C, text, title_len, meta_end) if not a_hits else []
    x_hits = scan_group(RE_X, text, title_len, meta_end)
    if title_only:
        _t = lambda hs: [h for h in hs if h[2] == "title"]      # noqa: E731
        a_hits, b_hits, c_hits, x_hits = _t(a_hits), _t(b_hits), _t(c_hits), _t(x_hits)

    # 强/弱信号拆分
    hard_hits = [h for h in a_hits if h[0] in
                 ("人身保险公司","人寿保险公司","寿险公司","健康险公司","养老险公司",
                  "养老保险公司","人身险公司","人寿保险","人身保险","寿险","健康险","养老险")]
    soft_hits = [h for h in a_hits if h[0] in ("养老保险","健康保险")]

    if hard_hits:
        in_title = any(h[2]=="title" for h in hard_hits)
        ctx = any(ctx_around(text,h) for h in hard_hits)
        if in_title: decision,conf="INCLUDE","高"
        elif ctx: decision,conf="INCLUDE","中"
        else: decision,conf="BOUNDARY","低"
        primary=hard_hits[0]
    elif soft_hits:
        in_title = any(h[2]=="title" for h in soft_hits)
        if in_title: decision,conf="INCLUDE","高"
        else: decision,conf="BOUNDARY","低"   # 弱信号+非标题: 顺带提及嫌疑
        primary=soft_hits[0]
    elif b_hits:
        xcomp=scan_group(RE_X,text,title_len,meta_end)
        if any(h[0] in EXCL_COMPANY for h in xcomp) and not a_hits:
            decision,conf="EXCLUDE","中"
        else:
            decision,conf="INCLUDE","中"
        primary=b_hits[0]
    elif c_hits:
        xcomp=scan_group(RE_X,text,title_len,meta_end)
        strong_x = [h[0] for h in xcomp if h[0] in ("财产保险公司","财产险","再保险公司","保险经纪公司","保险公估","保险中介机构","商业银行","证券公司","信托公司")]
        if strong_x:
            decision,conf="EXCLUDE","中"; primary=xcomp[0]
        else:
            decision,conf="BOUNDARY","低"; primary=c_hits[0]
    elif x_hits:
        decision,conf="EXCLUDE","中"; primary=x_hits[0]
    else:
        decision,conf="EXCLUDE","—"; primary=None

    # ---- 标题级覆盖规则(主体判定优先) ----
    if decision in ("INCLUDE","BOUNDARY"):
        if (("财产保险公司" in title) or ("财险" in title) or ("财产险" in title)
                or ("再保险" in title) or ("机动车辆保险" in title) or ("车险" in title)
                or ("机动车" in title)) and not (("人身保险公司" in title) or ("寿险公司" in title)):
            decision,conf="EXCLUDE","中"; primary=None
        elif any(s in title for s in EXCL_SOCIAL):
            decision,conf="EXCLUDE","中"; primary=None

    # ---- 缺正文降级: 源数据无可用正文(avail<200)且非标题命中 -> BOUNDARY(无法核验全文) ----
    if decision=="INCLUDE" and conf=="中" and (len(title)+len(meta)+len(body))<200:
        decision,conf="BOUNDARY","低"

    def kwset(hs): return sorted(set(h[0] for h in hs))
    snip = snippet(text,primary) if primary else ""
    return decision,conf,kwset(a_hits),kwset(b_hits),kwset(c_hits),kwset(x_hits),snip,a_hits,b_hits

# ---------------- 808 索引 ----------------
docno_index = collections.defaultdict(list)
title_index = collections.defaultdict(list)
with open(ATTR, encoding="utf-8-sig", newline="") as f:
    for row in csv.DictReader(f):
        nd=_norm_docno(row["发文字号"]); nt=_norm_title(row["文件名称"])
        if nd: docno_index[nd].append(row["监管文件编号"])
        if nt: title_index[nt].append(row["监管文件编号"])

def match_attr(docno,title):
    rfns=[]
    if docno and docno in docno_index: rfns+=docno_index[docno]
    if title and title in title_index: rfns+=title_index[title]
    return rfns

# ---------------- 扫描 ----------------
out_csv=os.path.join(OUTDIR,"scan_records.csv")
out_json=os.path.join(OUTDIR,"scan_hits_attr.jsonl")
cols=["source","dedup_key","document_number","title","issue_organ","decision","confidence",
      "a_kws","b_kws","c_kws","x_kws","snippet","avail_text_len","has_full_body","in_attr","matched_rfn"]
total=0; decision_cnt=collections.Counter(); conf_cnt=collections.Counter()
src_cnt=collections.Counter(); miss_cnt=collections.Counter()

with open(out_csv,"w",encoding="utf-8-sig",newline="") as fo, \
     open(out_json,"w",encoding="utf-8",newline="") as fj:
    w=csv.writer(fo); w.writerow(cols)
    for src,path in CLEANED.items():
        with open(path,encoding="utf-8-sig",newline="") as f:
            for row in csv.DictReader(f):
                total+=1
                title=row.get("title","") or ""
                meta=" ".join((row.get(k,"") or "") for k in ("summary","issue_organ","column_name","theme_name","keyword"))
                body=" ".join((row.get(k,"") or "") for k in ("body_text","body_text_webpage","body_text_doc","attachment_content"))
                avail=len(title)+len(meta)+len(body)
                has_full=avail>=200
                docno=_norm_docno(row.get("document_number","")); ntitle=_norm_title(title)
                rfns=match_attr(docno,ntitle); in_attr=bool(rfns)
                decision,conf,a_kws,b_kws,c_kws,x_kws,snip,a_hits,b_hits=classify(title,meta,body)
                decision_cnt[decision]+=1; conf_cnt[conf]+=1; src_cnt[src]+=1
                if decision in ("INCLUDE","BOUNDARY") and not in_attr: miss_cnt[src]+=1
                w.writerow([src,row.get("dedup_key",""),row.get("document_number",""),title,
                            row.get("issue_organ",""),decision,conf,
                            "|".join(a_kws),"|".join(b_kws),"|".join(c_kws),"|".join(x_kws),
                            snip,avail,has_full,in_attr,";".join(rfns)])
                if in_attr and (a_kws or b_kws or c_kws or x_kws):
                    rec={"rfn":rfns,"source":src,"document_number":row.get("document_number",""),
                         "title":title,"issue_organ":row.get("issue_organ",""),
                         "decision":decision,"confidence":conf,
                         "a_hits":[{"kw":h[0],"pos":h[1],"field":h[2]} for h in a_hits],
                         "b_hits":[{"kw":h[0],"pos":h[1],"field":h[2]} for h in b_hits],
                         "c_kws":c_kws,"x_kws":x_kws,"snippet":snip,
                         "avail_text_len":avail,"has_full_body":has_full}
                    fj.write(json.dumps(rec,ensure_ascii=False)+"\n")

print("扫描完成. 总记录:",total)
print("决策分布:",dict(decision_cnt))
print("置信分布:",dict(conf_cnt))
print("各源:",dict(src_cnt))
print("疑似漏提(非808的INCLUDE/BOUNDARY)按源:",dict(miss_cnt))
print("输出:",out_csv,out_json)
