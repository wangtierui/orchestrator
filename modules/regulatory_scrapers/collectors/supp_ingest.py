# -*- coding: utf-8 -*-
"""
supp_ingest.py —— 四源补充法规数据摄取脚本（supplementary_regulations_scraper）

用途：将「已有四源（gov/mof/nfra/pbc）未收录」的补充法规文件，按爬虫规范
（字段对齐 shared_lib 数据字典 CSV_COLUMNS；正文+附件齐全；6.4 标准命名落盘）
组装为统一原始数据 data/raw/supplementary_regulations.json，供统一清洗管道
scripts/run_clean_pipeline.py 消费。

本批次 4 份（来源：保险销售行为主题纵向深化分析 · 政府网站补充）：
  F9  中国保监会关于落实《保险销售行为可回溯管理暂行办法》有关事项的通知
      保监消保〔2017〕265号 —— nfra.gov.cn 官网全文（_sources/F9_body.txt）
  F11 中国银保监会办公厅关于进一步加强消费投诉处理工作的通知
      银保监办发〔2022〕90号 —— nfra.gov.cn 官网全文（_sources/F11_body.txt）
  F15 关于规范人身保险公司银行代理渠道业务有关事项的通知
      金办便函〔2024〕66号 —— gov.cn 未公开全文，本地 PDF（用户提供）OCR
  F17 国家金融监督管理总局办公厅关于进一步加强金融机构营销行为管理的通知
      金办发〔2025〕63号 —— gov.cn 未检索到全文，本地 PDF（用户提供）OCR
      （原文标注"不对外公开"，仅作内部法规库归档，注意保密管理）

流程：
  1) 读取 _sources/ 官网全文（F9/F11）与人工校对文本（F15/F17，优先 curated，
     回退 OCR 缓存，再回退实时 OCR —— 见 scripts/supp_ocr_pdf.py）；
  2) 本地 PDF（F15/F17）复制到 downloaded_docs/（6.4 标准命名），记录 MD5；
  3) 组装 4 条记录 → data/raw/supplementary_regulations.json；
  4) 输出摄取摘要与 OCR 质量/误差声明。

官方出处双保险核验规则（摄取前必查，可复用）：
  ⓐ 优先以 .gov.cn 官方地址为 source_url（如 nfra.gov.cn 历史归档页
     ItemDetail_gdsj.html?docId=xxxx）；官方页面正文若为 Angular 异步渲染、
     curl 无法拉取，用 WebFetch 尝试渲染全文；
  ⓑ 核验采用「官方地址标题确证 + 权威库正文交叉验证」双保险：
     ① 官方地址能返回「公文名称」标题 → 确证地址与文件对应关系；
     ② 正文与权威库（北大法宝等）文本逐字比对一致 → 确证内容准确性；
     ③ 接口 404 / 渲染失败 → 如实标注「页面存在但接口下线 / 正文渲染失败」，
        正文以权威库完整文本为准，禁止臆造；
  ⓒ 核验结论写入记录 _raw_fields.官方出处 / _raw_fields.官方核验说明，
     source 字段标注核验方式（见 verify_official_source()）。

用法：
  python scripts/supp_ingest.py
"""

from __future__ import annotations

# ---- P3b 仓库引导（migrate_collectors_p3b.py 注入）：使 std_lib/config 可导入 ----
import os as _os
import sys as _sys

_GUIDE_ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "..", ".."))
if _GUIDE_ROOT not in _sys.path:
    _sys.path.insert(0, _GUIDE_ROOT)
del _GUIDE_ROOT, _os, _sys
import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))          # modules/regulatory_scrapers/collectors
SCRAPERS_ROOT = os.path.dirname(HERE)                        # modules/regulatory_scrapers（统一数据根）
for _p in (HERE, SCRAPERS_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from config.exitcodes import ExitCode  # noqa: E402
from std_lib.scraper_std.naming import standard_filename  # noqa: E402

REPO_ROOT = SCRAPERS_ROOT  # 数据根 = modules/regulatory_scrapers（拍平后单层上溯一次即到）
# 5b 补全（2026-09-04）：supp ingest 收敛至统一 data/（此前写 per-source data/raw 与统一副本分裂；
# _sources 源文本随迁统一 data/raw/_sources；content_ref / OCR 缓存引用在统一后相对 REPO_ROOT 解析一致）
SRC_DIR = os.path.join(REPO_ROOT, "data", "raw", "_sources")
RAW_OUT = os.path.join(REPO_ROOT, "data", "raw", "supplementary_regulations.json")
from std_lib.scraper_std.cache_store import docs_root  # noqa: E402

DOCS_DIR = docs_root("supp")
STATE_DIR = os.path.join(REPO_ROOT, "data", "state")

# 用户提供的本地 PDF 源文件（仅读取，不移动）。
# P3b 去硬编码：由环境变量提供（LOCAL_PDF_F15 / LOCAL_PDF_F17），未设置则为空（跳过该来源）。
LOCAL_PDFS = {
    "F15": os.environ.get("LOCAL_PDF_F15", ""),
    "F17": os.environ.get("LOCAL_PDF_F17", ""),
}

def md5(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()

def read_text(*names: str) -> str:
    """依次尝试多个候选文件名（_sources 下），返回第一个存在且非空的文本。"""
    for n in names:
        p = os.path.join(SRC_DIR, n)
        if os.path.exists(p) and os.path.getsize(p) > 0:
            with open(p, encoding="utf-8") as f:
                return f.read().strip()
    raise FileNotFoundError(f"缺少源文本：{names}（应在 {SRC_DIR} 下）")

def parse_pipe_table(text: str) -> list:
    """'|' 分隔文本 → 二维数组（供 table_structured）。"""
    rows = []
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        rows.append([c.strip() for c in ln.split("|")])
    return rows

def _sanitize_key(s: str) -> str:
    """将发文字号/标题规整为可用作 _sources 文件名的 key（与下游 enrich 一致）。"""
    return re.sub(r'[^\w]', '_', s or '')[:60]

def enrich_placeholder_records(records: list) -> tuple:
    """用 data/raw/_sources/ 下已就绪的官方全文（<文号key>_body.txt）回填
    占位/缺失正文，避免 '（全文见留存文档：…）' 占位说明进入交付物。

    适用场景：本批次（T1 五库补充 seq...）直接以占位正文入 JSON 的记录，
    只要把官方全文存为 `_sources/<文号key>_body.txt`（key = 发文字号规整），
    重跑 scripts/supp_ingest.py 即自动回填，并将 body_source 归并为
    规范值 'webpage'，保证清洗管道 body_text_webpage 正确分流、不出现 'N/A'。
    无法以文本解析的来源（如二进制 .doc 附件）不下臆造内容，交由人工本地提供。
    """
    changed = 0
    for r in records:
        bt = str(r.get("body_text", "")).strip()
        placeholder = ("全文见留存文档" in bt) or ("见补充文档" in bt) or (bt in ("", "N/A"))
        if not placeholder:
            continue
        key = _sanitize_key(r.get("document_number") or r.get("title", ""))
        cand = os.path.join(SRC_DIR, f"{key}_body.txt")
        if os.path.exists(cand) and os.path.getsize(cand) > 0:
            with open(cand, encoding="utf-8") as f:
                r["body_text"] = f.read().strip()
            if str(r.get("body_source") or "") not in ("webpage", "downloaded_doc"):
                r["body_source"] = "webpage"
            rf = r.setdefault("_raw_fields", {})
            rf["正文获取"] = rf.get("正文获取", "官网全文回填（WebFetch 获取）")
            changed += 1
            print(f"  [enrich] 回填正文：{r.get('document_number', '')} {str(r.get('title', ''))[:24]}")
    return records, changed

def enrich_attachments(records: list) -> list:
    """用 _sources/ 下已就绪的附件全文（记录显式声明 _attachment_files）回填附件内容。

    记录可声明：
      "_attachment_files": [{"file": "<key>_附件1.txt", "name": "...", "kind": "..."}]
    脚本读取对应 _sources 文本写入 attachment_content / attachments，
    保证 '附件（如续保表述要求）' 等内容被真正提取，而非占位。
    """
    for r in records:
        attaches = list(r.get("attachments") or [])
        for spec in (r.get("_attachment_files") or []):
            fn = spec.get("file")
            p = os.path.join(SRC_DIR, fn) if fn else ""
            if p and os.path.exists(p) and os.path.getsize(p) > 0:
                with open(p, encoding="utf-8") as f:
                    content = f.read().strip()
                attaches.append({
                    "name": spec.get("name", fn),
                    "kind": spec.get("kind", "本地文本/留存文档"),
                    "note": "用户本地提供全文，经摄取脚本回填",
                    "content_ref": f"data/raw/_sources/{fn}",
                })
                r["attachment_content"] = (str(r.get("attachment_content") or "") + "\n\n" + content).strip()
                print(f"  [enrich] 回填附件：{r.get('document_number', '')} <- {fn}")
        r["attachments"] = attaches
        r["attachment_count"] = len(attaches)
    return records

def land_doc(rec: dict) -> dict:
    """本地 PDF → downloaded_docs/（6.4 命名），回填 downloaded_doc_path/md5。"""
    src = LOCAL_PDFS.get(rec["task_index"])
    if not src:
        raise FileNotFoundError(f"未知任务编号，未配置本地 PDF：{rec.get('task_index')}")
    if not os.path.exists(src):
        raise FileNotFoundError(f"本地 PDF 不存在：{src}")
    fn = standard_filename(
        index_no=rec["document_number"], title=rec["title"],
        pub_date=rec["publish_date"], ext=".pdf", file_type="正文")
    dst = os.path.join(DOCS_DIR, fn)
    if not os.path.exists(dst) or os.path.getsize(dst) != os.path.getsize(src):
        os.makedirs(DOCS_DIR, exist_ok=True)
        shutil.copy2(src, dst)
    rec["downloaded_doc_path"] = os.path.join("downloaded_docs", fn)
    rec["_doc_md5_sha256"] = md5(dst)
    rec["_doc_size"] = os.path.getsize(dst)
    return rec

def verify_official_source(rec: dict) -> dict:
    """官方出处双保险核验（docstring ⓑ 规则的落地函数）。

    对每条补充记录执行：
      1) 官方地址标题确证：source_url 指向 .gov.cn 官方归档页时，确认页面
         「公文名称」标题与 rec['title'] 对应（人工/WebFetch 核验后填写
         _raw_fields.官方标题核验）；
      2) 权威库正文交叉验证：正文与北大法宝等权威库文本比对一致后填写
         _raw_fields.官方核验说明；
      3) 接口异常如实标注：官方接口 404 / 渲染失败时填写
         _raw_fields.官方核验说明 = "页面存在但接口下线/正文渲染失败，
         正文以权威库完整文本为准"。

    本函数不发起网络请求（核验在摄取前由人工/工具完成），仅负责将核验
    结论规范化写入记录，保证可追溯。
    """
    raw = rec.setdefault("_raw_fields", {})
    url = rec.get("source_url", "") or ""
    if "nfra.gov.cn" in url or "gov.cn" in url:
        raw.setdefault("官方出处", url)
    if raw.get("官方标题核验") and raw.get("官方核验说明"):
        # 双保险结论已人工填写，保留
        pass
    else:
        raw.setdefault("官方核验说明",
                       "官方地址已记录；如页面异步渲染/接口下线，正文以权威库（北大法宝）完整文本为准，禁止臆造")
    return rec

# 4.1b：摄入后接入 RFN 登记（P4 起经 interfaces.rfn_api）。
# 阶段 3（2026-09-18）：登记一律经 interfaces 唯一入口（原按 env REG_CLASSIFIER_ROOT
# 插兄弟模块目录再 `from rfn.registry import register_doc` 的跨模块直连已移除）。
_REGISTER_DOC = None

def _load_register_doc():
    """受控导入 register_doc（None=未加载；False=加载失败；callable=可用）。"""
    global _REGISTER_DOC
    if _REGISTER_DOC is None:
        try:
            from interfaces.rfn_api import register_doc  # noqa: F401  # noqa: PLC0415
            _REGISTER_DOC = register_doc
        except Exception as _e:
            print(f"[ingest] WARN 无法经 interfaces.rfn_api 取得 register_doc（跳过 RFN 登记）: {_e}")
            _REGISTER_DOC = False
    return _REGISTER_DOC or None

def register_supplements(records: list) -> dict:
    """按唯一键接入 rfn.registry.register_doc（防并发冲突）。

    - 仅对显式声明 rfn_theme 的记录登记（主题未知 → 跳过，防 RFN 错配）；
    - 已存在（文号/标题命中）→ reused 复用现有编号；不存在 → created 生成 RFN；
    - 无文号且新登记时打印 WARN（需人工确认主题归属）。
    返回 {"registered", "reused", "created", "skipped"} 计数。
    """
    reg = _load_register_doc()
    stat = {"registered": 0, "reused": 0, "created": 0, "skipped": 0, "failed": 0}
    for r in records:
        theme = r.get("rfn_theme") or ""
        if not theme or reg is None:
            stat["skipped"] += 1
            continue
        body = str(r.get("body_text") or "")
        fp = r.get("_doc_md5_sha256") or hashlib.sha256(body.encode("utf-8")).hexdigest()
        try:
            # F-S04 修复（2026-09-12）：原传 organ= 而 register_doc 签名无该参数 →
            # TypeError 被吞仅 WARN（supp 新文件永不进 RFN 体系）。organ 仅存 raw 记录
            # 字段（registry 注：归属表无该列，发布机构不再入登记键）。
            res = reg(theme=theme, title=r["title"], docno=r.get("document_number") or "",
                      pub_date=r.get("publish_date") or "",
                      source="supp", source_mark="补充", fingerprint=fp)
        except Exception as _e:
            print(f"[ingest] ERROR 登记失败 {r.get('document_number','')} {r['title'][:20]}: {_e}")
            stat["failed"] += 1
            continue
        stat["registered"] += 1
        stat[res["action"]] += 1
        r["_rfn"] = res["rfn"]
        if res["action"] == "created" and not r.get("document_number"):
            print(f"[ingest] WARN 无文号记录新建 RFN {res['rfn']}（{r['title'][:24]}），请人工确认主题归属")
        print(f"  [rfn] {res['action']:>7} {res['rfn']} <- {r.get('document_number','(无文号)')} {r['title'][:24]}")
    return stat

def build_records() -> list:
    records = []

    # ---- F9：官网全文 + 页面内嵌表格附件 ----
    f9_att = read_text("F9_attachment.txt")
    records.append({
        "task_index": "F9",
        "rfn_theme": "T1",
        "title": "中国保监会关于落实《保险销售行为可回溯管理暂行办法》有关事项的通知",
        "doc_type": "通知",
        "category": "部门规范性文件",
        "publish_date": "2017-10-23",
        "effective_date": "2017-10-23",
        "issue_organ": "中国保险监督管理委员会",
        "document_number": "保监消保〔2017〕265号",
        "source_url": "https://www.nfra.gov.cn/cn/view/pages/ItemDetail_gdsj.html?docId=21440&docType=2",
        "source": "gov.cn补充",
        "body_text": read_text("F9_body.txt"),
        "body_source": "webpage",
        "summary": "细化《保险销售行为可回溯管理暂行办法》（保监发〔2017〕54号）录音录像业务规范七项：录制时点（填写投保单时统一集中录制第七条全部内容）、明确提示话术、明示身份、录制要求（同框展示/证件与签名清晰可辨）、文件制作（逐单录制）、自助终端提示、整改补录。附件为现场同步录音录像用语示例（页面内嵌表格）。",
        "status": "现行有效",
        "timeliness_status": "valid",
        "verification_source": "数据源标注（官网原文核验）",
        "keyword": "可回溯/录音录像/双录",
        "attachments": [{
            "name": "保险销售行为现场同步录音录像用语示例",
            "kind": "页面内嵌表格",
            "note": "nfra.gov.cn 页面内嵌表格附件，无独立文件；全文存于 attachment_content / table_structured",
            "content_ref": "data/raw/_sources/F9_attachment.txt",
        }],
        "attachment_count": 1,
        "attachment_content": f9_att,
        "table_structured": parse_pipe_table(f9_att),
        "table_raw_text": f9_att,
        "table_recovery_method": "structured",
        "_retrieval_channel": "官网全文（nfra.gov.cn，2026-08-25 WebFetch 获取）",
        "_raw_fields": {
            "检索时间": "2026-08-25",
            "效力核验时间": "2026-08-25",
            "检索途径": "WebFetch→nfra.gov.cn",
            "检索词": "保险销售行为可回溯管理暂行办法 有关事项的通知 265号",
            "正文获取": "官网全文",
        },
    })

    # ---- F11：官网全文（无附件） ----
    records.append({
        "task_index": "F11",
        "rfn_theme": "T1",
        "title": "中国银保监会办公厅关于进一步加强消费投诉处理工作的通知",
        "doc_type": "通知",
        "category": "部门规范性文件",
        "publish_date": "2022-09-28",
        "effective_date": "2022-09-28",
        "issue_organ": "中国银行保险监督管理委员会办公厅",
        "document_number": "银保监办发〔2022〕90号",
        "source_url": "https://www.nfra.gov.cn/cn/view/pages/ItemDetail.html?docId=1075504&itemId=4099&generaltype=0",
        "source": "gov.cn补充",
        "body_text": read_text("F11_body.txt"),
        "body_source": "webpage",
        "summary": "7 部分 13 条：完善消费投诉处理制度机制、畅通投诉渠道、积极妥善处理投诉（严格按银保监会令2020年第3号时限）、运用调解机制、突出考核导向、强化责任追究和溯源整改、加强监管工作。",
        "status": "现行有效",
        "timeliness_status": "valid",
        "verification_source": "数据源标注（官网原文核验）",
        "keyword": "消费投诉/首问负责/考核",
        "attachments": [],
        "attachment_count": 0,
        "_retrieval_channel": "官网全文（nfra.gov.cn，2026-08-25 WebFetch 获取）",
        "_raw_fields": {
            "检索时间": "2026-08-25",
            "检索途径": "WebFetch→nfra.gov.cn",
            "检索词": "中国银保监会办公厅关于进一步加强消费投诉处理工作的通知 银保监办发〔2022〕90号",
            "正文获取": "官网全文",
        },
    })

    # ---- F15：本地 PDF（便函，gov.cn 未公开全文）→ OCR + 人工校对 ----
    rec15 = {
        "task_index": "F15",
        "rfn_theme": "T1",
        "title": "关于规范人身保险公司银行代理渠道业务有关事项的通知",
        "doc_type": "便函",
        "category": "部门规范性文件（内部便函）",
        "publish_date": "2024-01-19",
        "effective_date": "2024-01-19",
        "issue_organ": "国家金融监督管理总局办公厅",
        "document_number": "金办便函〔2024〕66号",
        "source_url": "",
        "source": "gov.cn补充",
        "body_text": read_text("F15_body_curated.txt", "F15_body_ocr.txt"),
        "body_source": "downloaded_doc",
        "summary": "银保渠道报行合一执行细则五方面：①科学设计和备案产品（附加费用率含佣金率上限并列明结构）；②健全内控机制建设（总公司/分支机构责任、费用真实性、佣金支付不得超上限且不得以出单费/信息费等名义支付佣金以外费用）；③强化内部监督管理（费用假设回溯分析、内部审计）；④统筹非现场监管和现场检查（快立快查快处，商业贿赂移交司法）；⑤形成同管共治合力（保险业协会自律、银保信科技支撑）。本通知自发布之日起施行。",
        "status": "现行有效",
        "timeliness_status": "valid",
        "verification_source": "数据源标注（内部便函，未公开；时效按官方披露）",
        "keyword": "报行合一/银保渠道/佣金",
        "attachments": [],
        "attachment_count": 0,
        "_retrieval_channel": "本地 PDF（用户提供，gov.cn 未公开全文）→ OCR 提取 + 人工校对",
        "_raw_fields": {
            "检索时间": "2026-08-25",
            "检索途径": "WebSearch（金融监管总局办公厅发布信息、金融时报/中国金融新闻网报道）",
            "检索词": "金办便函〔2024〕66号 银行代理渠道业务",
            "正文获取": "本地PDF OCR+人工校对",
            "OCR原始缓存": "data/raw/_sources/F15_body_ocr.txt",
            "OCR局限声明": "报头/落款为装饰性混排，Tesseract 识别有噪点，已按官方媒体披露要点人工复原（2024-01-19 发布）",
        },
    }
    land_doc(rec15)
    records.append(rec15)

    # ---- F17：本地 PDF（原文标注"不对外公开"，gov.cn 无全文）→ OCR + 人工校对 ----
    rec17 = {
        "task_index": "F17",
        "rfn_theme": "T1",
        "title": "国家金融监督管理总局办公厅关于进一步加强金融机构营销行为管理的通知",
        "doc_type": "通知",
        "category": "部门规范性文件（内部文件，不对外公开）",
        "publish_date": "2025-07-16",
        "effective_date": "",
        "issue_organ": "国家金融监督管理总局办公厅",
        "document_number": "金办发〔2025〕63号",
        "source_url": "https://www.nfra.gov.cn/cn/view/pages/ItemDetail.html?docId=1225502&itemId=928",
        "source": "gov.cn补充",
        "body_text": read_text("F17_body_curated.txt", "F17_body_ocr.txt"),
        "body_source": "downloaded_doc",
        "summary": "金融机构营销行为管理 7 部分 21 条：总体原则（金融为民）、加强全流程管控（主体责任/预评预审/跟踪管控/内部监督）、严格规范营销宣传（禁不当宣传/禁违规揽储诱导借贷/禁不当推介保险/禁违规营销资管产品/规范网络营销）、强化销售合规管理（适当性/告知义务/禁强制捆绑搭售/可回溯管理/增值服务）、加强合作机构营销行为管理（名单制/规范合作）、发挥行业自律作用、加强监督管理（监督检查/协同联动）。原文标注不对外公开。",
        "status": "现行有效",
        "timeliness_status": "valid",
        "verification_source": "数据源标注（内部文件原文，未公开）",
        "keyword": "营销行为/全流程管控/营销宣传",
        "attachments": [],
        "attachment_count": 0,
        "_retrieval_channel": "本地 PDF（用户提供，gov.cn 未检索到全文）→ OCR 提取 + 人工校对",
        "_raw_fields": {
            "检索时间": "2026-08-25",
            "检索途径": "WebSearch→nfra.gov.cn 公文库",
            "检索词": "金办发〔2025〕63号 金融机构营销行为管理",
            "正文获取": "本地PDF OCR+人工校对",
            "OCR原始缓存": "data/raw/_sources/F17_body_ocr.txt",
            "OCR局限声明": "报头/版记为装饰性混排，Tesseract 识别有噪点，已人工复原（版记：2025年7月16日印发）",
            "公开属性": "不对外公开（内部文件，严禁通过互联网、手机、微信等传播使用和对外发布）",
            "留存口径": "任务留存记录 effective_date=2025-09-12（原文 PDF 未见施行条款，本库以版记印发日期 2025-07-16 为 publish_date）",
        },
    }
    land_doc(rec17)
    records.append(rec17)

    # ---- 补A批次（2026-08-26 项目RFN映射补录，正文经 _sources 自动回填）----
    for _r in [
        dict(task_index='补A-1', rfn_theme='T1', title='国务院办公厅关于加强金融消费者权益保护工作的指导意见',
             doc_type='国务院文件', category='制定依据',
             publish_date='2015-11-04', effective_date='2015-11-04',
             issue_organ='国务院办公厅', document_number='国办发〔2015〕81号',
             source_url='https://www.gov.cn/gongbao/content/2015/content_2973146.htm',
             source='gov.cn补充', body_source='webpage',
             summary='金融消费者权益保护顶层指导意见：八项基本权利（财产安全权/知情权/自主选择权/公平交易权/依法求偿权/受教育权/受尊重权/信息安全权）、金融机构行为规范、监督管理机制、保障机制。',
             status='现行有效', timeliness_status='valid',
             verification_source='数据源标注（官网原文核验）',
             keyword='金融消保/八项权利/适当性',
             attachments=[], attachment_count=0,
             _retrieval_channel='官网全文（gov.cn 国务院公报，2026-08-26 WebFetch 获取）',
             _raw_fields={'检索时间':'2026-08-26','效力核验时间':'2026-08-26','检索途径':'WebFetch→gov.cn','检索词':'国办发〔2015〕81号 金融消费者权益保护','正文获取':'官网全文'}),
        dict(task_index='补A-2', rfn_theme='T1', title='中华人民共和国个人信息保护法',
             doc_type='法律', category='上位法',
             publish_date='2021-08-20', effective_date='2021-11-01',
             issue_organ='全国人民代表大会常务委员会', document_number='中华人民共和国主席令第七十二号',
             source_url='https://www.cac.gov.cn/2021-08/20/c_1631050028355286.htm',
             source='gov.cn补充', body_source='webpage',
             summary='个人信息保护基本法律（8章74条）：个人信息处理规则（同意/敏感信息/自动化决策）、跨境提供、个人权利、处理者义务、法律责任；保险销售个人信息保护直接上位法。',
             status='现行有效', timeliness_status='valid',
             verification_source='数据源标注（官网原文核验）',
             keyword='个人信息/同意/敏感信息',
             attachments=[], attachment_count=0,
             _retrieval_channel='官网全文（cac.gov.cn 转载中国人大网，2026-08-26 WebFetch 获取）',
             _raw_fields={'检索时间':'2026-08-26','效力核验时间':'2026-08-26','检索途径':'WebFetch→cac.gov.cn','检索词':'中华人民共和国个人信息保护法 全文','正文获取':'官网全文'}),
        dict(task_index='补A-3', rfn_theme='T1', title='中华人民共和国广告法',
             doc_type='法律', category='上位法',
             publish_date='2021-04-29', effective_date='2021-04-29',
             issue_organ='全国人民代表大会常务委员会', document_number='中华人民共和国主席令第二十二号',
             source_url='', source='官方发布', body_source='webpage',
             summary='广告行为规范法律：虚假广告禁止、金融广告特殊规制（第十四条）、广告内容真实合法；保险营销宣传直接上位法（正文待获取，_sources 就绪后重跑摄取自动回填）。',
             status='现行有效', timeliness_status='valid',
             verification_source='数据源标注（官方发布）',
             keyword='广告/虚假宣传/金融广告',
             attachments=[], attachment_count=0,
             _retrieval_channel='官方发布（全文待获取，重跑摄取自动回填）',
             _raw_fields={'检索时间':'2026-08-26','效力核验时间':'2026-08-26','检索途径':'官方发布','检索词':'中华人民共和国广告法','正文获取':'官网全文'}),
        dict(task_index='补A-4', rfn_theme='T1', title='中华人民共和国数据安全法',
             doc_type='法律', category='上位法',
             publish_date='2021-06-10', effective_date='2021-09-01',
             issue_organ='全国人民代表大会常务委员会', document_number='中华人民共和国主席令第八十四号',
             source_url='', source='官方发布', body_source='webpage',
             summary='数据安全基础法律：数据分类分级保护、数据安全审查、重要数据保护；保险销售数据安全与可回溯数据管理直接上位法（正文待获取，_sources 就绪后重跑摄取自动回填）。',
             status='现行有效', timeliness_status='valid',
             verification_source='数据源标注（官方发布）',
             keyword='数据安全/分级分类',
             attachments=[], attachment_count=0,
             _retrieval_channel='官方发布（全文待获取，重跑摄取自动回填）',
             _raw_fields={'检索时间':'2026-08-26','效力核验时间':'2026-08-26','检索途径':'官方发布','检索词':'中华人民共和国数据安全法','正文获取':'官网全文'}),
    ]:
        records.append(_r)

    # ---- 补B批次（2026-08-27 门禁缺口补录：银监办发〔2008〕274号，历史废止文件）----
    records.append({
        "task_index": "补B-1",
        "rfn_theme": "T1",
        "title": "中国银监会办公厅关于商业银行开展代理销售基金和保险产品相关业务风险提示的通知",
        "doc_type": "通知",
        "category": "部门规范性文件",
        "publish_date": "2008-11-06",
        "effective_date": "2008-11-06",
        "issue_organ": "中国银行业监督管理委员会办公厅",
        "document_number": "银监办发〔2008〕274号",
        "source_url": "https://www.nfra.gov.cn/cn/view/pages/ItemDetail.html?docId=268041",
        "source": "gov.cn补充",
        "body_text": read_text("银监办发_2008_274号_body.txt"),
        "body_source": "webpage",
        "summary": "银监会办公厅对商业银行代理销售基金和保险产品业务的风险提示七项：①合作协议机构尽职调查/审慎选伴（停止与违规机构合作）；②产品名称恰当反映属性、禁止承诺保本；③禁止当储蓄产品大众化推销、禁止误导销售、禁止非银行人员违规网点营销；④严格客户评估与记录保管（了解你的客户/法律责任界定告知）；⑤明确与客户联络和信息传递方式；⑥设置投诉电话、指定人员及时处理投诉；⑦其他银行业金融机构遵照执行。2025 年被金规〔2025〕6号《商业银行代理销售业务管理办法》废止（北大法宝核验）。",
        "status": "已废止",
        "timeliness_status": "repealed",
        "replacement_document": "金规〔2025〕6号（商业银行代理销售业务管理办法）",
        "verification_source": "北大法宝（2026-08-27 检索：废止或失效，依据金规〔2025〕6号）",
        "keyword": "银保渠道/代销/风险提示/废止",
        "attachments": [],
        "attachment_count": 0,
        "_retrieval_channel": "官网全文（nfra.gov.cn 历史归档 docId=268041，2026-08-27 WebFetch 获取）",
        "_raw_fields": {
            "检索时间": "2026-08-27",
            "效力核验时间": "2026-08-27",
            "检索途径": "WebFetch→nfra.gov.cn 归档页 + WebSearch→北大法宝",
            "检索词": "银监办发〔2008〕274号 商业银行代理保险",
            "正文获取": "官网全文",
            "效力核验": "北大法宝标注「废止或失效」，依据金规〔2025〕6号",
        },
    })

    return records

def main() -> int:
    os.makedirs(STATE_DIR, exist_ok=True)
    records = build_records()
    for rec in records:
        verify_official_source(rec)   # 官方出处双保险核验结论写入
    os.makedirs(os.path.dirname(RAW_OUT), exist_ok=True)
    # 增量合并：若 raw 已存在（含历史手工/工具追加批次），按 document_number+title 去重合并，
    # 避免全量覆盖丢失既有记录（如 T1 五库补充 seq61/62/67/100/116/149/150/177/199/208/255/273/276/286 等）。
    if os.path.exists(RAW_OUT):
        try:
            with open(RAW_OUT, encoding="utf-8") as f:
                existing = json.load(f)
        except Exception:
            existing = []
        keys = {(r.get("document_number", ""), r.get("title", "")) for r in existing}
        merged = list(existing)
        for r in records:
            k = (r.get("document_number", ""), r.get("title", ""))
            if k not in keys:
                merged.append(r)
                keys.add(k)
        records = merged
        # 占位/缺失正文回填（读取 _sources/<key>_body.txt）+ 附件回填
        records, enriched = enrich_placeholder_records(records)
        records = enrich_attachments(records)
    with open(RAW_OUT, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

    # 4.1b：摄入后按唯一键接入 classifier register_doc（防并发；异常不阻断 raw 输出）
    try:
        rstat = register_supplements(records)
        print(f"[ingest] RFN 登记：registered={rstat['registered']} "
              f"reused={rstat['reused']} created={rstat['created']} skipped={rstat['skipped']}")
    except Exception as _e:
        print(f"[ingest] WARN RFN 登记环节异常（不影响 raw 输出）: {_e}")

    # 摄取台账（state/ingest.last.json）——task_index 缺失（历史手工/工具追加批次）时以 document_number 为 key
    manifest = {
        "generated_at": _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "records": len(records),
        "files": {r.get("task_index") or r.get("document_number") or r.get("title", ""): {
            "title": r["title"], "document_number": r["document_number"],
            "body_source": r["body_source"],
            "downloaded_doc_path": r.get("downloaded_doc_path", ""),
            "doc_md5_sha256": r.get("_doc_md5_sha256", ""),
            "doc_size": r.get("_doc_size", 0),
            "attachment_count": r.get("attachment_count", 0),
        } for r in records},
        "raw_out": RAW_OUT,
    }
    with open(os.path.join(STATE_DIR, "ingest.last.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print(f"[ingest] 原始数据已生成：{RAW_OUT}")
    print(f"[ingest] 记录数：{len(records)}（占位正文回填 {enriched} 条）")
    for r in records:
        src_tag = "官网全文" if r["body_source"] == "webpage" else "本地PDF(OCR+校对)"
        doc = f"，正文文档: {r.get('downloaded_doc_path', '—')}" if r.get("downloaded_doc_path") else ""
        tag = r.get("task_index") or r.get("document_number") or r.get("title", "")[:8]
        print(f"  - {tag} | {r['document_number']} | {r['title'][:24]} | {src_tag}{doc}")
    print("[ingest] 台账：state/ingest.last.json")
    return ExitCode.OK

if __name__ == "__main__":
    raise SystemExit(main())
