# -*- coding: utf-8 -*-
"""
supp_mapper.py —— supplementary_regulations_scraper 统一 Schema 映射器（运行时注册）

背景（2026-08-28 起）：共享库 std_lib/scraper_std/unified_schema.py 的 MAPPERS 注册表
已原生纳入第五源 **"supp"**（map_supp 纯映射实现，见 unified_schema.py）。本目录映射器
仅保留 supp 项目特有的「字段一致化」（utils/supp_normalize.py，I3/I4/I5/I6/I7），
流程为：normalize_raw(rec) 一致化 → 委托 unified_schema.map_supp 完成统一字段映射。
共享库改动经 std_lib/tools/sync_scraper_std.py 同步五项目副本（漂移 0），
scripts/run_clean_pipeline.py 启动时仍以本包装注册 MAPPERS["supp"]，
管道其余环节（清洗/断句/OCR校正/校验/去重/双轨输出）全部复用共享库既有实现。

字段对齐：输出字段完全对齐 unified_schema.UNIFIED_SCHEMA / CSV_COLUMNS /
DATA_DICTIONARY（GB/T 42147-2022 核心元数据集），见 data/cleaned/ 数据字典。

原始记录字段（data/raw/supplementary_regulations.json，每行/每对象）：
  task_index        任务编号（F9/F11/F15/F17，来源：保险销售行为主题纵向深化分析）
  title             标题
  doc_type          文种（通知/便函）
  category          效力级别/分类
  publish_date      公布日期 YYYY-MM-DD
  effective_date    生效日期
  issue_organ       发布机构
  document_number   发文字号
  source_url        来源详情链接（.gov.cn 官网）
  source            数据源标识（固定 "gov.cn补充"）
  body_text         正文全文（官网原文 / 本地 PDF OCR 文本）
  body_source       webpage | downloaded_doc
  downloaded_doc_path 正文文档本地相对路径（6.4 命名，downloaded_docs/ 下）
  summary           摘要
  status            有效性状态（现行有效）
  timeliness_status 时效状态（valid 等）
  verification_source 核验来源（北大法宝/数据源标注/规则判断）
  keyword           关键词
  attachments       附件元数据列表（name/kind/note/file_path/md5/…）
  attachment_content 附件全文文本（页面内嵌表格等）
  table_structured  表格结构化二维数组
  table_raw_text    表格原始文本
  _retrieval_channel 正文获取途径说明（官网全文 / 本地PDF用户提供）
  _raw_fields       溯源字段（检索词/检索途径/检索时间等）
"""

from __future__ import annotations

# ---- P3b 仓库引导（migrate_collectors_p3b.py 注入）：使 std_lib/config 可导入 ----
import os as _os
import sys as _sys

_GUIDE_ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "..", ".."))
if _GUIDE_ROOT not in _sys.path:
    _sys.path.insert(0, _GUIDE_ROOT)
del _GUIDE_ROOT, _os, _sys
from typing import Any

from supp_normalize import normalize_raw  # noqa: E402  字段一致化（I3/I4/I5/I6/I7）

from std_lib.scraper_std.unified_schema import (
    map_supp as _canonical_map_supp,  # noqa: E402  统一映射（五源通用）
)


def map_supp(rec: dict[str, Any], clean_version: str, captured_at: str = "") -> dict[str, Any]:
    """补充法规记录 → 统一 Schema：supp 字段一致化(I3-I7) → 委托 unified_schema.map_supp。

    映射体（纯字段映射）已迁入共享库 unified_schema.py（纳入统一 schema，含附件正文并入），
    本函数仅做 supp 特有的一致化预处理 + 委托，避免映射逻辑双份漂移。
    """
    rec = normalize_raw(rec)
    return _canonical_map_supp(rec, clean_version, captured_at)

if __name__ == "__main__":  # 离线自检
    rec = {
        "task_index": "F9", "title": "中国保监会关于落实《保险销售行为可回溯管理暂行办法》有关事项的通知",
        "doc_type": "通知", "category": "部门规范性文件", "publish_date": "2017-10-23",
        "issue_organ": "中国保险监督管理委员会", "document_number": "保监消保〔2017〕265号",
        "source_url": "https://www.nfra.gov.cn/cn/view/pages/ItemDetail_gdsj.html?docId=21440&docType=2",
        "source": "gov.cn补充", "body_text": "各保监局，各保险公司、保险中介机构：……",
        "body_source": "webpage", "status": "现行有效", "keyword": "可回溯/录音录像",
    }
    out = map_supp(rec, "v1.0.0", "2026-08-25")
    assert out["index_no"] == "保监消保〔2017〕265号"
    assert out["source"] == "gov.cn补充"
    assert out["body_source"] == "webpage"
    assert out["data_format"] == "TXT"
    assert out["dedup_key"]
    assert out["_metadata"]["doc_source"] == "webpage"
    print("[supp_mapper] 离线自检通过")
