# -*- coding: utf-8 -*-
"""内部文档采集器：把本地 PDF/Word（公司内部制度文档）读入系统。

与爬虫采集器的差异只在「来源」：爬虫从网页抓取，本采集器从本地目录读取。
产出的记录直接走「统一字段」（content / detail_url / issuing_authority / …）并打
scope="internal"，因此 clean 阶段无需在 _SPECS 为它登记字段映射（自动走默认规格）。

文本抽取复用 collectors.lib.text_utils.extract_document_text（pdf/docx/ole2 全包，
含 .doc 经 LibreOffice 转 docx），不重复造轮子。抽取依赖 pypdf / python-docx /
pdfplumber（已在 requirements 声明，需 pip install 后方可真正抽出正文）。
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Any

# 仓库根入路径，使 collectors 可被导入（与既有采集器同约定）
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from collectors.lib.text_utils import extract_document_text
from collectors.storage import get_store
from collectors.storage.envelope import canonicalize

LOG = logging.getLogger("internal_collector")

# 支持的内部分发格式（ole2 覆盖旧版 .doc，由 doc_convert 转 docx 后抽取）
_DOC_EXT = (".pdf", ".doc", ".docx", ".ceb", ".rtf")


def _build_record(path: str, text: str, *, org: str, category: str) -> dict:
    """构造成「统一字段」记录（clean 的 _DEFAULT_SPEC 直接读这些字段）。"""
    name = os.path.basename(path)
    title = os.path.splitext(name)[0]
    summary = text[:200].replace("\n", " ").strip()
    return {
        "title": title,
        "content": text,
        "detail_url": os.path.abspath(path),  # 本地绝对路径：溯源 + 按文件去重
        "issuing_authority": org,
        "document_number": "",
        "publish_date": "",
        "effective_date": "",
        "category": category,
        "summary": summary,
        "source": "internal",
        "scope": "internal",
        "attachments": [],
        "attachment_content": "",
        "attachment_count": 0,
    }


def collect(
    in_dir: str, *, org: str = "", category: str = "", limit: int = 0
) -> dict:
    """扫描 in_dir 下所有文档，抽取正文并写入 get_store('internal')。

    返回汇总 {in_dir, found, ingested, skipped, locator}。
    """
    in_dir = os.path.abspath(in_dir)
    if not os.path.isdir(in_dir):
        raise NotADirectoryError(in_dir)

    files: list[str] = []
    for root, _, names in os.walk(in_dir):
        for n in names:
            if n.lower().endswith(_DOC_EXT):
                files.append(os.path.join(root, n))
    if limit:
        files = files[:limit]

    records: list[dict[str, Any]] = []
    skipped = 0
    for p in files:
        try:
            with open(p, "rb") as f:
                data = f.read()
        except OSError as e:
            LOG.warning("读文件失败 %s: %s", p, e)
            skipped += 1
            continue
        res = extract_document_text(data, name=os.path.basename(p))
        text = res.get("text") or ""
        if not text.strip():
            LOG.warning("无正文（抽取状态=%s）：%s", res.get("extract_status"), p)
            skipped += 1
            continue
        records.append(_build_record(p, text, org=org, category=category))

    store = get_store("internal")
    ref = store.save(records, on_conflict="update", normalizer=canonicalize)
    return {
        "in_dir": in_dir,
        "found": len(files),
        "ingested": len(records),
        "skipped": skipped,
        "locator": ref,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="采集本地内部制度文档（PDF/Word）入库")
    ap.add_argument("--in-dir", required=True, help="含 PDF/Word 的本地目录")
    ap.add_argument("--org", default="", help="发文机构（可选，默认空）")
    ap.add_argument("--category", default="", help="分类（可选，默认空）")
    ap.add_argument("--limit", type=int, default=0, help="最多处理 N 个文件（0=不限制）")
    args = ap.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    out = collect(args.in_dir, org=args.org, category=args.category, limit=args.limit)
    print("[internal] 采集完成：", out)


if __name__ == "__main__":
    main()
