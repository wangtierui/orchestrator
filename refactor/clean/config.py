# -*- coding: utf-8 -*-
"""clean 阶段配置（CleanSettings）。

极简版只保留"是否清洗"相关的开关，不再包含交付闸门/阈值/历史等治理项：
  - ocr_correction_on : OCR 误读校正（四级流程）开关；
  - sentence_split_on : 断句修复（写入 split_sentences / raw_uncut_text）开关；
  - dedup_on          : 同批重复抓取剔除开关；
  - clean_version     : 清洗版本标记（写入 _metadata，便于溯源）；
  - ocr_*_file        : OCR 资产文件名（混淆映射 / 自定义词典）；
  - ocr_asset_dir     : OCR 资产目录（空串=相对当前工作目录）。
"""
from dataclasses import dataclass


@dataclass
class CleanSettings:
    ocr_correction_on: bool = True
    sentence_split_on: bool = True
    dedup_on: bool = True
    clean_version: str = "v1.0.0"
    ocr_confusion_file: str = "ocr_confusion.json"
    custom_dict_file: str = "custom_dict.txt"
    ocr_asset_dir: str = ""


def get_clean_settings() -> CleanSettings:
    """返回默认配置（后续可由 yaml/环境变量覆盖）。"""
    return CleanSettings()
