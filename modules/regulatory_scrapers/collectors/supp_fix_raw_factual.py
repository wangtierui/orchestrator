# -*- coding: utf-8 -*-
"""supp_fix_raw_factual.py —— 修正 supplementary_regulations.json 中的事实性错误（I1/I2/I4）。

仅修正「事实性」字段（真实文号 / URL / 正文全文），不触碰推导型字段
（doc_type/category/source/status 由 utils/supp_normalize 在管道内一致化处理）。

修正项：
  I1  电子化回访：doc_no 银保监办发〔2020〕（公布编号）→ 银保监办发〔2020〕11号；
      source_url gov.cn → nfra 官方归档 docId=890223。
  I4  三法律：补「主席令」（个人信息保护法第七十二号 / 广告法第二十二号 /
          数据安全法第八十四号）；中长期资金入市指导意见无文号 → （无发文字号）。
  I2  再保险合同范本：下载 iachina 两份 PDF，抽取正文回填 body_text，
      source_url 置协会官网页，PDF 按 6.4 标准命名落盘 downloaded_docs/。

运行：python scripts/supp_fix_raw_factual.py
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
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HERE)
UTILS = os.path.join(PROJECT_ROOT, "utils")
for _p in (UTILS, PROJECT_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pymupdf as fitz  # noqa: E402

from std_lib.scraper_std.naming import standard_filename  # noqa: E402

RAW = os.path.join(PROJECT_ROOT, "data", "raw", "supplementary_regulations.json")
DOCS = os.path.join(PROJECT_ROOT, "downloaded_docs")
TMP = os.path.join(PROJECT_ROOT, "tmp", "iachina")

def extract_pdf(path: str) -> str:
    d = fitz.open(path)
    txt = "".join(p.get_text() for p in d)
    d.close()
    return txt.strip()

def main() -> int:
    # 备份
    ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = RAW + f".bak_factual_{ts}"
    shutil.copy2(RAW, bak)
    print(f"[fix] 备份 raw → {bak}")

    data = json.load(open(RAW, encoding="utf-8"))

    def find(title_kw):
        for r in data:
            if title_kw in (r.get("title") or ""):
                return r
        raise KeyError(f"未找到含 {title_kw!r} 的记录")

    # ---- I1 电子化回访 ----
    r = find("推广人身保险电子化回访")
    r["document_number"] = "银保监办发〔2020〕11号"
    r["source_url"] = "https://www.nfra.gov.cn/cn/view/pages/ItemDetail.html?docId=890223&itemId=925"
    r.setdefault("_raw_fields", {})["官方核验说明"] = "文号/地址已按 nfra 官方归档页修正（银保监办发〔2020〕11号，docId=890223）"
    print("[fix] I1 电子化回访：doc_no + source_url 已修正")

    # ---- I4 三法律补主席令 ----
    mapping = {
        "个人信息保护法": "中华人民共和国主席令第七十二号",
        "广告法": "中华人民共和国主席令第二十二号",
        "数据安全法": "中华人民共和国主席令第八十四号",
    }
    for kw, chairman_order in mapping.items():
        r = find(kw)
        r["document_number"] = chairman_order
        r.setdefault("_raw_fields", {})["官方核验说明"] = f"法律文号按主席令补全：{chairman_order}"
        print(f"[fix] I4 法律：{kw} → {chairman_order}")

    # 中长期资金入市（无文号）
    r = find("关于推动中长期资金入市的指导意见")
    r["document_number"] = "（无发文字号）"
    r.setdefault("_raw_fields", {})["官方核验说明"] = "中央金融办、中国证监会联合印发指导意见，无正式发文字号"
    print("[fix] I4 中长期资金入市：无文号 → （无发文字号）")

    # ---- I2 再保险合同范本：下载 PDF 抽取正文 ----
    r = find("财产再保险比例及非比例合同范本")
    prop_pdf = os.path.join(TMP, "prop.pdf")
    nonprop_pdf = os.path.join(TMP, "nonprop.pdf")
    if not (os.path.exists(prop_pdf) and os.path.exists(nonprop_pdf)):
        print("[fix] I2 错误：tmp/iachina 下未找到已下载的 PDF，请先下载", file=sys.stderr)
        return 1
    prop_txt = extract_pdf(prop_pdf)
    nonprop_txt = extract_pdf(nonprop_pdf)
    header = ("中国保险行业协会财产再保险比例及非比例合同范本（中文版）\n"
              "（来源：中国保险行业协会官网 iachina.cn，发布日期 2021-11-11；"
              "以下为协会发布的财产再保险比例及非比例合同范本正文）\n")
    combined = (header +
                "\n========== 财产再保险比例合同范本 ==========\n" + prop_txt +
                "\n\n========== 财产再保险非比例合同范本 ==========\n" + nonprop_txt)
    r["body_text"] = combined
    r["source_url"] = "https://www.iachina.cn/art/2021/11/11/art_8616_105619.html"
    r["publish_date"] = r.get("publish_date") or "2021-11-11"
    r["effective_date"] = r.get("effective_date") or "2021-11-11"
    r["body_source"] = "downloaded_doc"
    r["status"] = "现行有效"  # 原 'valid' 同期由 supp_normalize 中文化

    # 6.4 标准命名落盘 PDF
    os.makedirs(DOCS, exist_ok=True)
    idx = r.get("document_number") or r.get("title")
    title_sum = "中国保险行业协会财产再保险比例及非比例合同范本"
    prop_name = standard_filename(index_no=idx, title=title_sum, pub_date="2021-11-11", ext=".pdf", file_type="正文")
    non_name = standard_filename(index_no=idx, title=title_sum, pub_date="2021-11-11", ext=".pdf", file_type="附件1")
    shutil.copy2(prop_pdf, os.path.join(DOCS, prop_name))
    shutil.copy2(nonprop_pdf, os.path.join(DOCS, non_name))
    r["downloaded_doc_path"] = os.path.join("downloaded_docs", prop_name)
    r.setdefault("attachments", []).append({
        "name": "财产再保险非比例合同范本（中文版）",
        "kind": "行业示范文本（PDF）",
        "note": "中国保险行业协会官网 iachina.cn 发布",
        "content_ref": os.path.join("downloaded_docs", non_name),
    })
    r["attachment_content"] = nonprop_txt
    r["attachment_count"] = len(r.get("attachments", []))
    r.setdefault("_raw_fields", {})["官方核验说明"] = "正文由 iachina.cn 两份 PDF 抽取回填（比例/非比例合同范本）"
    print(f"[fix] I2 再保险合同范本：body_text 回填 {len(combined)} 字；PDF 落盘 {prop_name} / {non_name}")

    json.dump(data, open(RAW, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"[fix] 已写回 raw：{RAW}（{len(data)} 条）")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
