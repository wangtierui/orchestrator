# -*- coding: utf-8 -*-
"""
pipeline.py —— 统一清洗管道（第七/九/十一/十三节 集成）

流程：
  load_raw → map_unified → clean_body → ocr_correct → validate+schema →
  dedup → fill_missing → history → write CSV(UTF-8 BOM)+JSONL → metrics

输出（7.4 双轨制）：
  data/cleaned/{project}_cleaned_{YYYYMMDD}.csv     （UTF-8 with BOM）
  data/cleaned/{project}_cleaned_{YYYYMMDD}.jsonl   （每行一个完整 JSON 对象）

辅助：
  - build_high_freq_dict(records, min_freq=10)：来源②动态新词发现（标题+正文
    分词统计，频次≥10 且长度≥2）
  - run_pipeline(project, raw_path, out_dir, ...) ：主入口
"""

from __future__ import annotations

import csv
import datetime as _dt
import json
import logging
import os
import shutil
import time
from collections import Counter
from typing import Any

from .cleaner import clean_text, dedup_records, fill_missing, normalize_date
from .config import load_settings
from .logging_setup import setup_logging
from .metrics import MetricsCollector
from .ocr_correction import JiebaDict, correct_ocr_text, load_confusion_map
from .schema_validation import NullThresholdMonitor, validate_record
from .sentence_split import acceptance_check, repair_text
from .unified_schema import (CORE_NULL_FIELDS, CSV_COLUMNS, MAPPERS,
                             UNIFIED_SCHEMA)

LOG = logging.getLogger("scraper_std.pipeline")


def load_raw_records(path: str) -> list[dict[str, Any]]:
    """读取原始 JSON（支持 list 或 {records/items: [...]} 封装）。"""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in ("records", "items", "data", "results"):
            if isinstance(data.get(k), list):
                return data[k]
    raise ValueError(f"无法识别的原始数据格式：{path}")


def build_high_freq_dict(
    records: list[dict[str, Any]],
    *,
    min_freq: int = 10,
    max_words: int = 2000,
) -> dict[str, int]:
    """
    来源② 动态抽取——新词发现：对标题+正文做 jieba 分词统计，
    提取出现频次≥min_freq 且长度≥2 的词（含整体长词），供 OCR 词典校验与
    后续 custom_dict.txt 追加。属 7.5 执行路径。
    """
    counter: Counter = Counter()
    try:
        import jieba
    except Exception:
        return {}
    for rec in records:
        text = " ".join([
            str(rec.get("title") or ""),
            str(rec.get("body_text") or "")[:500],
        ])
        for w in jieba.cut(text):
            w = w.strip()
            if len(w) >= 2 and not w.isdigit():
                counter[w] += 1
    return {w: n for w, n in counter.most_common(max_words) if n >= min_freq}


def _clean_record_body(rec: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    """正文基础清洗 + 断句修复（7.2）+ 清洗版本标记。"""
    body = rec.get("body_text") or ""
    if not body:
        return rec
    cleaned = clean_text(body)
    cleaning_cfg = cfg.get("cleaning", {})
    if cleaning_cfg.get("sentence_split_on", True):
        repaired = repair_text(cleaned, source="webpage")
        rec["body_text"] = repaired["text"]
        meta = rec.setdefault("_metadata", {})
        meta["clean_version"] = cleaning_cfg.get("clean_version", "v1.0.0")
        if repaired["is_table"]:
            meta["table_recovery_method"] = meta.get("table_recovery_method") or "pending_table_block"
        if repaired["split_sentences"]:
            rec["split_sentences"] = repaired["split_sentences"]
            rec["raw_uncut_text"] = repaired["raw_uncut_text"]
    else:
        rec["body_text"] = cleaned
        rec.setdefault("_metadata", {})["clean_version"] = cleaning_cfg.get("clean_version", "v1.0.0")
    return rec


def _populate_renamed_filename(rec: dict[str, Any]) -> None:
    """7.4：主文档/附件标准重命名文件名（6.4）回填到顶层字段，便于核查与溯源。
    始终保证字段存在（无值时留空字符串，满足 7.4 字段齐全要求）。"""
    rec.setdefault("renamed_filename", "")
    if rec.get("renamed_filename"):
        return
    atts = rec.get("attachments") or []
    if atts and isinstance(atts[0], dict) and atts[0].get("file_name"):
        rec["renamed_filename"] = atts[0]["file_name"]
        return
    dp = rec.get("downloaded_doc_path") or ""
    if dp:
        rec["renamed_filename"] = os.path.basename(dp)


def _ocr_correct_record(rec: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    """OCR 校正（7.3）：仅对疑似 OCR 文本执行（正文含扫描件标记或纠正后存疑）。"""
    body = rec.get("body_text") or ""
    if not body:
        return rec
    cleaning_cfg = cfg.get("cleaning", {})
    cmap = load_confusion_map(cleaning_cfg.get("confusion_map_file", ""))
    dict_path = cleaning_cfg.get("custom_dict_file", "")
    result = correct_ocr_text(
        body,
        confusion_map=cmap,
        dict_path=dict_path,
        high_freq=None,  # 高词频词典由管道构建后传入（见 run_pipeline）
        uncertain_export_dir=os.path.join(cfg.get("logging", {}).get("log_dir", "logs")),
    )
    meta = rec.setdefault("_metadata", {})
    if result["uncertain"]:
        meta["ocr_uncertain"] = True
    if result["corrected"] or result["dict_fixed"]:
        rec["body_text"] = result["text"]
        meta["ocr_corrected"] = result["corrected"]
        meta["ocr_dict_fixed"] = result["dict_fixed"]
    return rec


def _serialize_for_csv(value: Any) -> str:
    """数组/对象字段 → 字符串（分号；分隔，数据字典注明）。"""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return "；".join(_serialize_for_csv(v) for v in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def write_cleaned(
    out_dir: str,
    project: str,
    records: list[dict[str, Any]],
    *,
    date_tag: str = "",
) -> dict[str, str]:
    """双轨输出：CSV(UTF-8 BOM) + JSONL。返回 {csv: path, jsonl: path}。"""
    os.makedirs(out_dir, exist_ok=True)
    date_tag = date_tag or _dt.date.today().strftime("%Y%m%d")
    csv_path = os.path.join(out_dir, f"{project}_cleaned_{date_tag}.csv")
    jsonl_path = os.path.join(out_dir, f"{project}_cleaned_{date_tag}.jsonl")

    # CSV（UTF-8 with BOM：encoding='utf-8-sig' 自动写入 BOM，兼容 Excel 中文）
    # 原子写（N-8 纪律）：同目录临时文件 + os.replace，避免中断/崩溃留半截文件；
    # 五源统一经此入口落盘，故此处原子化即覆盖全部源 cleaned 写入。
    csv_tmp = os.path.join(out_dir, f".tmp_{project}_cleaned_{date_tag}.csv")
    with open(csv_tmp, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for rec in records:
            row = {k: _serialize_for_csv(rec.get(k)) for k in CSV_COLUMNS}
            writer.writerow(row)
    try:
        os.replace(csv_tmp, csv_path)
    except OSError:
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
            writer.writeheader()
            for rec in records:
                row = {k: _serialize_for_csv(rec.get(k)) for k in CSV_COLUMNS}
                writer.writerow(row)

    # JSONL（无损保留类型）
    jsonl_tmp = os.path.join(out_dir, f".tmp_{project}_cleaned_{date_tag}.jsonl")
    with open(jsonl_tmp, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    try:
        os.replace(jsonl_tmp, jsonl_path)
    except OSError:
        with open(jsonl_path, "w", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return {"csv": csv_path, "jsonl": jsonl_path}


def rotate_history(history_dir: str, project: str, cleaned_paths: dict[str, str],
                   keep: int = 3) -> list[str]:
    """历史版本管理（第十一节）：每次新清洗前将上一版移入 data/history/，
    仅保留最近 keep 个版本。返回保留的历史文件列表。"""
    os.makedirs(history_dir, exist_ok=True)
    ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    archived: list[str] = []
    for kind in ("csv", "jsonl"):
        src = cleaned_paths.get(kind)
        if not src or not os.path.exists(src):
            continue
        ext = "csv" if kind == "csv" else "jsonl"
        dst = os.path.join(history_dir, f"{project}_cleaned_{ts}.{ext}")
        try:
            shutil.copy2(src, dst)
            archived.append(dst)
        except OSError as e:
            LOG.warning("历史归档失败：%s", e)
    # 清理旧版本（仅保留最近 keep 个）
    for ext in ("csv", "jsonl"):
        files = sorted(
            f for f in os.listdir(history_dir)
            if f.startswith(f"{project}_cleaned_") and f.endswith(f".{ext}")
        )
        for old in files[:-keep]:
            try:
                os.remove(os.path.join(history_dir, old))
            except OSError:
                pass
    return archived


def run_pipeline(
    project: str,
    raw_path: str,
    *,
    project_root: str | None = None,
    out_dir: str | None = None,
    clean_version: str = "v1.0.0",
    captured_at: str = "",
    on_alarm: Any | None = None,
    cfg_path: str | None = None,
    history_dir: str | None = None,
) -> dict[str, Any]:
    """
    统一清洗管道主入口。返回汇总：
      {
        "project", "raw_total", "mapped_total", "cleaned_total",
        "dedup_removed", "validation_failed", "allow_delivery",
        "null_rates", "outputs": {csv, jsonl}, "metrics_path", "elapsed_seconds"
      }
    """
    t0 = time.time()
    cfg = load_settings(cfg_path, project_root=project_root)
    log_dir = cfg.get("logging", {}).get("log_dir", "logs")
    if project_root:
        log_dir = os.path.join(project_root, log_dir)
    setup_logging(log_dir, project_name=project, task_id=f"{project}-clean",
                  json_lines=cfg.get("logging", {}).get("json_lines", True))
    metrics = MetricsCollector(project, f"{project}-clean")

    # 1) 加载原始数据
    raw = load_raw_records(raw_path)
    metrics.inc("total_targets", len(raw))
    LOG.info("[%s] 加载原始记录 %d 条：%s", project, len(raw), raw_path)

    # 2) 统一 Schema 映射
    mapper = MAPPERS.get(project)
    if not mapper:
        raise ValueError(f"未知项目：{project}（可用：{list(MAPPERS)}）")
    mapped = [mapper(r, clean_version, captured_at) for r in raw]
    metrics.inc("success", len(mapped))

    # 2.5) 日期标准化（7.1）+ 主文档标准重命名文件名（7.4）
    for rec in mapped:
        for fld in ("publish_date", "effective_date"):
            if rec.get(fld):
                rec[fld] = normalize_date(rec[fld])
        _populate_renamed_filename(rec)

    # 3) 正文清洗 + 断句修复
    for rec in mapped:
        _clean_record_body(rec, cfg)

    # 4) OCR 校正（混淆映射与词典一次性构建，批量复用；7.3）
    cleaning_cfg = cfg.get("cleaning", {})
    high_freq: dict[str, int] = {}
    if cleaning_cfg.get("ocr_correction_on", True):
        high_freq = build_high_freq_dict(mapped, min_freq=10)
        LOG.info("[%s] 高频词典构建：%d 词", project, len(high_freq))
        cmap = load_confusion_map(
            os.path.join(project_root, cleaning_cfg["confusion_map_file"])
            if project_root else cleaning_cfg.get("confusion_map_file", ""))
        dict_path = (
            os.path.join(project_root, cleaning_cfg["custom_dict_file"])
            if project_root else cleaning_cfg.get("custom_dict_file", ""))
        checker = JiebaDict(dict_path, high_freq) if (high_freq or dict_path) else None
        for rec in mapped:
            body = rec.get("body_text") or ""
            if not body:
                continue
            result = correct_ocr_text(
                body, confusion_map=cmap, dict_checker=checker,
                uncertain_export_dir=log_dir)
            meta = rec.setdefault("_metadata", {})
            if result["uncertain"]:
                meta["ocr_uncertain"] = True
                metrics.inc("ocr_uncertain")
            metrics.inc("ocr_corrected", result["corrected"] + result["dict_fixed"])

    # 5) Schema 校验 + 空值阈值监测（第九节）
    #    expected_null_fields：已知源限制字段（如 gov flk 正文 OBS 不可达），
    #    豁免熔断告警但完整计入指标报告（透明可审计）。
    allowed_missing = set(cleaning_cfg.get("expected_null_fields") or [])
    hard_fields = [f for f in CORE_NULL_FIELDS if f not in allowed_missing]
    monitor = NullThresholdMonitor(CORE_NULL_FIELDS, alarm_fields=hard_fields,
                                   on_alarm=on_alarm)
    valid_records: list[dict[str, Any]] = []
    for rec in mapped:
        ok, errors = validate_record(rec, UNIFIED_SCHEMA, allow_missing=allowed_missing)
        if not ok:
            metrics.inc("schema_failed")
            rec["_metadata"]["validation_errors"] = errors
            LOG.warning("[%s] 校验未过 %s：%s", project, rec.get("title", "")[:30], errors[:2])
        monitor.record(rec)
        valid_records.append(rec)
    allow, null_rates = monitor.alarm()
    # 空值统计同步至指标报告（含豁免字段的真实空值率）
    for f, (n, total) in monitor.counts().items():
        metrics.field_total[f] = total
        metrics.field_null[f] = n

    # 6) 去重（唯一业务主键 + 内容哈希）
    before = len(valid_records)
    deduped = dedup_records(valid_records, key_fields=["dedup_key"], on_content=True)
    metrics.inc("dedup_removed", before - len(deduped))

    # 7) 缺失值填充（S-3 纪律）：枚举/受控字段保持空串（空=未填/未核验），
    #    不得用 'N/A' 填充——'N/A' 非合法枚举值，会触发 check_enum_values 违规。
    _ENUM_KEEP_EMPTY = {"source": "", "timeliness_status": "",
                        "body_source": "", "status": ""}
    cleaned_final = [fill_missing(r, _ENUM_KEEP_EMPTY) for r in deduped]

    # 7.5) 真实非空率（2026-09-04 口径修正）：对交付终态统计「排除 N/A/空占位后的真实非空率」，
    #      与前置空值率（null_rates，映射后统计）互为印证——null_rates 反映源端缺失，
    #      true_nonempty_rates 反映交付文件里 body_text 等是否被 'N/A' 占位（如 gov 曾全 N/A
    #      被人工误判为 100% 非空）。两者任一异常都应视为质量问题。
    def _true_nonempty(v: Any) -> bool:
        if v is None:
            return False
        if isinstance(v, str):
            s = v.strip()
            if not s or s.upper() in ("N/A", "NA", "NULL", "NONE"):
                return False
        return True

    true_nonempty_rates = {
        f: round(sum(1 for r in cleaned_final if _true_nonempty(r.get(f)))
                 / max(1, len(cleaned_final)), 4)
        for f in CORE_NULL_FIELDS
    }

    # 8) 输出（双轨）
    out_dir = out_dir or (os.path.join(project_root, "data", "cleaned")
                          if project_root else "data/cleaned")
    outputs = write_cleaned(out_dir, project, cleaned_final)

    # 9) 历史版本（每次清洗前将上一版归档——用当前产出做快照基线）
    # history_dir 改为由 out_dir **同级派生**（dirname(out_dir)/history）：
    #   - 默认 out_dir={project_root}/data/cleaned → history={project_root}/data/history，
    #     与改造前**完全等价**（零行为变更）；
    #   - 产物目录统一后 out_dir=repo/data/cleaned → history 自动跟随为 repo/data/history，
    #     无需调用方再传参。
    history_dir = history_dir or os.path.join(
        os.path.dirname(os.path.abspath(out_dir)), "history")
    rotate_history(history_dir, project, outputs,
                   keep=cfg.get("output", {}).get("keep_history_versions", 3))

    # 10) 断句验收 + 指标落盘
    issues = sum(len(acceptance_check(r.get("body_text") or "")) for r in cleaned_final)
    if issues:
        LOG.warning("[%s] 断句验收违规片段合计 %d 处", project, issues)
    metrics_path = metrics.write_report(log_dir)

    summary = {
        "project": project,
        "raw_total": len(raw),
        "mapped_total": len(mapped),
        "cleaned_total": len(cleaned_final),
        "dedup_removed": before - len(deduped),
        "validation_failed": metrics.counts["schema_failed"],
        "allow_delivery": allow,
        "null_rates": null_rates,
        "true_nonempty_rates": true_nonempty_rates,
        "sentence_acceptance_issues": issues,
        "high_freq_dict_size": len(high_freq),
        "outputs": outputs,
        "metrics_path": metrics_path,
        "elapsed_seconds": round(time.time() - t0, 2),
    }
    LOG.info("[%s] 管道完成：raw=%d → cleaned=%d（去重 %d，耗时 %.1fs）",
             project, len(raw), len(cleaned_final),
             summary["dedup_removed"], summary["elapsed_seconds"])
    return summary


if __name__ == "__main__":  # 离线自检（不执行全量）
    recs = [{"title": "测试", "body_text": "第一条 为规范管理。第二条 施行。"}]
    hf = build_high_freq_dict(recs, min_freq=1)
    assert isinstance(hf, dict)
    print("[scraper_std.pipeline] 离线自检通过")
