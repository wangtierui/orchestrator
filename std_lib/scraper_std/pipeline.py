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
import re
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
from .unified_schema import CORE_NULL_FIELDS, CSV_COLUMNS, MAPPERS, UNIFIED_SCHEMA

LOG = logging.getLogger("scraper_std.pipeline")


def load_raw_records(path: str) -> list[dict[str, Any]]:
    """读取原始数据（**.jsonl 与 .json 双格式；均流式解析**）。

    N-206（2026-10-09，S-A）：**为何改流式**——原实现 `json.load(f)` 对 1GB 的 gov 主库实测
    峰值 **RSS 5.24~6.13GB**（≈6× 体积：JSON 文本→对象膨胀 + 内部全量副本）。本函数是
    **五源 clean 的唯一载入点** ⇒ 修在此处，五源（含 mof/nfra 各 117MB）同时降峰值。

    支持形态（与旧实现语义一致）：
      · `.jsonl`（S-A 新形态）：**首行 `_meta` 信封自动跳过**，其余每行一条记录；
      · `.json`：数组，或 `{"records|items|data|results": [...]}` 封装 —— 用 `raw_decode`
        **逐条**解析数组元素，不再构造整表副本。
    """
    if path.endswith(".jsonl"):
        out: list[dict[str, Any]] = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                rec = json.loads(line)
                if isinstance(rec, dict) and len(rec) == 1 and "_meta" in rec:
                    continue                      # 信封行（单键 _meta）不算记录
                out.append(rec)
        return out
    return _load_json_records_streaming(path)


def _find_array_start(buf: str) -> int:
    """在缓冲区中定位**记录数组**的 `[` 下标（-1 = 尚未出现）。

    优先按信封键（records/items/data/results）定位 —— 直接取首个 `[` 会被**前面字符串值里的
    方括号**误导（如 category 文案含 `[`）；裸数组（文件以 `[` 开头）才退化为首字符判定。
    """
    head = buf.lstrip()
    if head.startswith("["):
        return buf.index("[")
    for key in ('"records"', '"items"', '"data"', '"results"'):
        k = buf.find(key)
        if k >= 0:
            b = buf.find("[", k + len(key))
            if b >= 0:
                return b
    return -1


def _load_json_records_streaming(path: str) -> list[dict[str, Any]]:
    """`.json` 原始数据的**流式**载入（数组/封装对象均支持；峰值与单条记录同阶）。"""
    dec = json.JSONDecoder()
    records: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        buf = ""
        start = -1
        while start < 0:
            chunk = f.read(1 << 20)
            if not chunk:
                raise ValueError(f"无法识别的原始数据格式：{path}")
            buf += chunk
            start = _find_array_start(buf)
        buf = buf[start + 1:]
        while True:
            buf = buf.lstrip()
            if buf.startswith(","):
                buf = buf[1:]
                continue
            if buf.startswith("]"):
                break
            if not buf:
                chunk = f.read(1 << 22)
                if not chunk:
                    break
                buf += chunk
                continue
            try:
                rec, end = dec.raw_decode(buf)
            except ValueError:                     # 记录被分块截断 → 续读再解
                chunk = f.read(1 << 22)
                if not chunk:
                    break
                buf += chunk
                continue
            if isinstance(rec, dict):
                records.append(rec)
            buf = buf[end:]
    return records


#: raw 信封键（JSONL 首行）。五源统一：`{"_meta": {...}}` + 一行一记录。
RAW_META_KEY = "_meta"


def resolve_raw_path(path: str) -> str:
    """把"历史 `.json` 名"解析为**实际存在**的 raw 文件（JSONL 优先、旧 `.json` 回退）。

    用途（批 49 / T1：四源 raw 统一 JSONL）：仓库内仍有多处按历史名（`mof_laws.json` 等）拼路径；
    经本函数即可**零语义变更**地指到新文件；未迁移的源（仍 `.json`）亦照常命中 ⇒ 双向兼容。
    """
    if os.path.exists(path):
        return path
    if path.endswith(".json"):
        cand = path[:-5] + ".jsonl"
    elif path.endswith(".jsonl"):
        cand = path[:-6] + ".json"
    else:
        cand = path + ".jsonl"
    return cand if os.path.exists(cand) else path


def read_raw_records(path: str) -> list[dict[str, Any]]:
    """读 raw 记录（**双格式**：`.jsonl` 逐行 / `.json` 逐条 raw_decode；自动解析历史名）。"""
    return load_raw_records(resolve_raw_path(path))


def write_raw_jsonl(path: str, records: list[dict[str, Any]], *, source: str = "",
                    category: str = "", meta: dict[str, Any] | None = None) -> str:
    """把 raw 记录写成 **JSONL**（首行 `_meta` 信封 + 一行一记录；流式 + 原子）。返回落盘路径。

    为何统一 JSONL（批 49，T1）：`.json` 单数组解析 1GB 级主库实测峰值 ≈6× 体积
    （gov 实测 5.24~6.13GB）；JSONL 逐行流式 ⇒ 峰值与语料体积**解耦**（批 47 已在 gov 验证，
    本批把 mof/nfra/pbc/supp 一并统一）。传入的 `.json` 名会被规范化为 `.jsonl`。
    `meta` 中的键（如 nfra 的 `errors` 列表）原样保留在信封里，**不丢信息**。
    """
    p = path
    if p.endswith(".json"):
        p = p[:-5] + ".jsonl"
    elif not p.endswith(".jsonl"):
        p = p + ".jsonl"
    d = os.path.dirname(os.path.abspath(p))
    if d:
        os.makedirs(d, exist_ok=True)
    env: dict[str, Any] = {
        "source": source or "",
        "category": category or "",
        "format": "jsonl/1",
        "captured_at": _dt.datetime.now().strftime("%Y%m%d_%H%M%S"),
    }
    if meta:
        env.update(meta)
    tmp = p + ".tmp"
    n = 0
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps({RAW_META_KEY: env}, ensure_ascii=False) + "\n")
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            n += 1
    os.replace(tmp, p)
    LOG.info("raw JSONL 落盘：%s（%d 条）", os.path.basename(p), n)
    return p


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
    except Exception:  # noqa: BLE001
        return {}
    for rec in records:
        text = " ".join(
            [
                str(rec.get("title") or ""),
                str(rec.get("body_text") or "")[:500],
            ]
        )
        for w in jieba.cut(text):
            w = w.strip()
            if len(w) >= 2 and not w.isdigit():
                counter[w] += 1
    return {w: n for w, n in counter.most_common(max_words) if n >= min_freq}


def _clean_record_body(rec: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    """正文基础清洗（**段落边界保真**）+ 断句诊断（7.2）+ 清洗版本标记。

    ⛔ 2026-09-20（F4/F5b，问题二/三根因）：
      - `clean_text(..., keep_newlines=True)`：**保留源端段落换行**——旧实现把 `\\n` 折叠为
        空格，clauses 层级解析因此丢失"标题行 / 正文行"的唯一判别信号
        （nfra《新监管标准指导意见》`（一）总体目标\\n借鉴…` 被折叠 → 整段进 title）；
      - `repair_text` 的产物**不回写 `body_text`**：它按"断句"目标插入句末 `\\n`
        与标点后空格，回写会污染事实源（同一段被误判为 title+content 两段、出现
        「、 」「100 号」等无效空格）。断句结果只作派生字段（`split_sentences` /
        `raw_uncut_text`）与表格块判定，事实源保持原文形态。
    """
    body = rec.get("body_text") or ""
    if not body:
        return rec
    cleaned = clean_text(body, keep_newlines=True)
    cleaning_cfg = cfg.get("cleaning", {})
    meta = rec.setdefault("_metadata", {})
    meta["clean_version"] = cleaning_cfg.get("clean_version", "v1.0.0")
    rec["body_text"] = cleaned
    if cleaning_cfg.get("sentence_split_on", True):
        repaired = repair_text(cleaned, source="webpage")
        if repaired["is_table"]:
            meta["table_recovery_method"] = (
                meta.get("table_recovery_method") or "pending_table_block"
            )
        if repaired["split_sentences"]:
            rec["split_sentences"] = repaired["split_sentences"]
            rec["raw_uncut_text"] = repaired["raw_uncut_text"]
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


#: T-A（批 48，2026-10-09）：**单条超大记录**的观测阈值（**按字符数**近似，避免 encode 复制——
#: 语料中最大单条为 279M 字符，encode 会再复制一份）。
#: 为何要它：单条 279MB 记录会**按快照/历史/备份多份复制**，并让任何"触碰该源"的步骤付出
#: GB 级瞬时峰值（批 46 实测）。内存侧放大已由 N-198~N-206 的流式化解除；
#: 此处**只观测并登记**（不截断、不改契约），使"数据层治理"成为**可见的产品决策**。
OVERSIZED_RECORD_CHARS = 32 * 1024 * 1024


def write_cleaned(
    out_dir: str,
    project: str,
    records: list[dict[str, Any]],
    *,
    date_tag: str = "",
) -> dict[str, Any]:
    """双轨输出：CSV(UTF-8 BOM) + JSONL。返回 {csv: path, jsonl: path, oversized: {...}}。

    T-A（批 48）：新增 `oversized` 子字典（**只观测不干预**）⇒ 返回类型放宽为 `dict[str, Any]`。
    """
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

    # JSONL（无损保留类型）—— T-A：**顺带度量单条体积**（零额外开销：此处本就要 dumps）
    jsonl_tmp = os.path.join(out_dir, f".tmp_{project}_cleaned_{date_tag}.jsonl")
    oversize: dict[str, Any] = {"threshold_chars": OVERSIZED_RECORD_CHARS, "count": 0,
                                "max_chars": 0, "samples": []}
    with open(jsonl_tmp, "w", encoding="utf-8") as f:
        for rec in records:
            line = json.dumps(rec, ensure_ascii=False)
            f.write(line + "\n")
            n_chars = len(line)                     # 按字符数近似（避免 encode 再复制一份）
            if n_chars > oversize["max_chars"]:
                oversize["max_chars"] = n_chars
            if n_chars > OVERSIZED_RECORD_CHARS:
                oversize["count"] += 1
                oversize["samples"].append({
                    "title": str(rec.get("title") or "")[:60],
                    "dedup_key": str(rec.get("dedup_key") or "")[:40],
                    "chars": n_chars,
                })
                oversize["samples"].sort(key=lambda s: -s["chars"])
                del oversize["samples"][3:]         # 只留最大 3 条（登记用，防自身膨胀）
    try:
        os.replace(jsonl_tmp, jsonl_path)
    except OSError:
        with open(jsonl_path, "w", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return {"csv": csv_path, "jsonl": jsonl_path, "oversized": oversize}


def rotate_history(
    cleaned_dir: str, history_dir: str, project: str, current_date: str, keep: int = 3
) -> int:
    """clean 单版化 + history 归档（2026-09-09 任务三改造）。

    语义：
      - cleaned 目录（cleaned_dir）仅保留 {project}_cleaned_{current_date} 最新一版；
        其它日期的 {project}_cleaned_*.{csv,jsonl} 一律**移入** history 目录；
      - history 每源仅保留最近 keep 个「日期版本」（csv/jsonl 同一日期计一组），超出删除。
    返回本次移入 history 的文件数。
    说明：原「每次 run 复制当前版到 history(带 HHMMSS)」语义废弃——同日多 run 不再产生
    冗余副本；版本演进为每日一版，history 保留最近三版（当前版始终在 cleaned）。
    """
    os.makedirs(history_dir, exist_ok=True)
    current_date = str(current_date or "")
    pat = re.compile(rf"^{re.escape(project)}_cleaned_(\d{{8}})\.(csv|jsonl)$")
    moved = 0
    if cleaned_dir and os.path.isdir(cleaned_dir):
        for fn in list(os.listdir(cleaned_dir)):
            m = pat.match(fn)
            if not m or m.group(1) == current_date:
                continue
            try:
                # 同日期历史版本若已存在则覆盖（keep 裁剪在下方统一执行）
                shutil.move(os.path.join(cleaned_dir, fn), os.path.join(history_dir, fn))
                moved += 1
            except OSError as e:
                LOG.warning("历史归档失败：%s", e)
    # 清理 history：该源仅保留最近 keep 个日期版本（csv/jsonl 成对按日期计）
    dates: set[str] = set()
    if os.path.isdir(history_dir):
        for fn in os.listdir(history_dir):
            m = pat.match(fn)
            if m:
                dates.add(m.group(1))
    drop = sorted(dates)[:-keep] if len(dates) > keep else []
    if drop:
        for fn in list(os.listdir(history_dir)):
            m = pat.match(fn)
            if m and m.group(1) in drop:
                try:
                    os.remove(os.path.join(history_dir, fn))
                except OSError:
                    pass
    return moved


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
    allow_schema_errors: bool = False,
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
    setup_logging(
        log_dir,
        project_name=project,
        task_id=f"{project}-clean",
        json_lines=cfg.get("logging", {}).get("json_lines", True),
    )
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
    # 富内容轨透传（2026-09-09 rich_object）：采集侧在 raw 顶层写 rich_*（图形/公式对象），
    # 经统一映射后按行透传保留到 cleaned JSONL（CSV 39 列不含该行内对象轨）。
    _RICH_KEYS = ("rich_structured", "rich_text", "rich_count")
    for _r, _m in zip(raw, mapped, strict=False):
        for _k in _RICH_KEYS:
            if _k in _r and isinstance(_r[_k], (list, str, int)):
                _m[_k] = _r[_k]
        # rich_text 同时并入 attachment_content（CSV 检索轨，供 recall/主题以文本命中图形文字）
        _rt = _r.get("rich_text")
        if _rt:
            _base = _m.get("attachment_content") or ""
            _m["attachment_content"] = ((_base + "\n") if _base else "") + "[富内容] " + _rt
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
            if project_root
            else cleaning_cfg.get("confusion_map_file", "")
        )
        dict_path = (
            os.path.join(project_root, cleaning_cfg["custom_dict_file"])
            if project_root
            else cleaning_cfg.get("custom_dict_file", "")
        )
        checker = JiebaDict(dict_path, high_freq) if (high_freq or dict_path) else None
        for rec in mapped:
            body = rec.get("body_text") or ""
            if not body:
                continue
            result = correct_ocr_text(
                body, confusion_map=cmap, dict_checker=checker, uncertain_export_dir=log_dir
            )
            meta = rec.setdefault("_metadata", {})
            if result["uncertain"]:
                meta["ocr_uncertain"] = True
                metrics.inc("ocr_uncertain")
            metrics.inc("ocr_corrected", result["corrected"] + result["dict_fixed"])
            # F-S05 修复（2026-09-12）：校正结果写回正文——原批量段仅计数不写回，
            # 唯一写回函数 _ocr_correct_record 零调用 → 全源 cleaned 正文未应用 OCR 校正
            # （_metadata.ocr_corrected 永不产出）。对齐 _ocr_correct_record 行为。
            if result["corrected"] or result["dict_fixed"]:
                rec["body_text"] = result["text"]
                meta["ocr_corrected"] = result["corrected"]
                meta["ocr_dict_fixed"] = result["dict_fixed"]

    # 5) Schema 校验 + 空值阈值监测（第九节）
    #    expected_null_fields：已知源限制字段（如 gov flk 正文 OBS 不可达），
    #    豁免熔断告警但完整计入指标报告（透明可审计）。
    #    源级已知豁免（2026-09-08 演练发现）：pbc 官网发布无公文索引号（结构性）、正文依赖实时
    #    网页 enrich；gov xzfgk 行政法规库多数国务院法规无公文索引号（结构性）。指标仍透明计入。
    _SRC_ALLOWED = {"pbc": {"index_no", "body_text"}, "gov": {"index_no"}}
    allowed_missing = set(cleaning_cfg.get("expected_null_fields") or []) | _SRC_ALLOWED.get(
        project, set()
    )
    hard_fields = [f for f in CORE_NULL_FIELDS if f not in allowed_missing]
    monitor = NullThresholdMonitor(CORE_NULL_FIELDS, alarm_fields=hard_fields, on_alarm=on_alarm)
    valid_records: list[dict[str, Any]] = []
    quarantined: list[dict[str, Any]] = []
    for rec in mapped:
        ok, errors = validate_record(rec, UNIFIED_SCHEMA, allow_missing=allowed_missing)
        if not ok:
            metrics.inc("schema_failed")
            rec["_metadata"]["validation_errors"] = errors
            LOG.warning("[%s] 校验未过 %s：%s", project, rec.get("title", "")[:30], errors[:2])
            if not allow_schema_errors:
                # v2 §3.4 V1（2026-09-26）：校验失败记录**不再进入交付**——原实现把失败记录
                # 照常 append 进 valid_records 并无条件写盘，等于"校验失败仍交付"（假成功）。
                # 此处改为转入隔离序列；空值监测仍计入（保留源端质量问题可见性）。
                monitor.record(rec)
                quarantined.append(rec)
                continue
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
    _ENUM_KEEP_EMPTY = {"source": "", "timeliness_status": "", "body_source": "", "status": ""}
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
        f: round(
            sum(1 for r in cleaned_final if _true_nonempty(r.get(f))) / max(1, len(cleaned_final)),
            4,
        )
        for f in CORE_NULL_FIELDS
    }

    # 8) 输出（双轨）
    out_dir = out_dir or (
        os.path.join(project_root, "data", "cleaned") if project_root else "data/cleaned"
    )
    outputs = write_cleaned(out_dir, project, cleaned_final)

    # 8.1) T-A（批 48）：**超大单条记录的可见性守卫**——只观测与登记，**不截断数据、不改契约**。
    #      动因（批 46/47 实测）：gov 有单条 279MB 记录（占该源语料 24.6%），会被快照/历史/备份
    #      多份复制，并让任何触碰该源的步骤付出 GB 级瞬时峰值；内存侧已由流式化解除，
    #      数据侧治理（阈值+外置）需产品决策 ⇒ 故先让它**每次清洗都可见**。
    _osz: dict[str, Any] = outputs.get("oversized") or {}
    if _osz.get("count"):
        _mb = _osz["max_chars"] / 1048576.0
        _s0 = (_osz["samples"] or [{}])[0]
        LOG.warning("[%s] 超大单条记录 %d 条（最大 %.1fMB 字符）：%s",
                    project, _osz["count"], _mb, str(_s0.get("title") or "")[:50])
        # 机器可读载荷（stdout 契约，见仓内日志纪律：状态行→LOG，载荷→print）
        print("[%s] OVERSIZED_RECORDS=%d MAX_MB=%.1f SAMPLE_KEY=%s"
              % (project, _osz["count"], _mb, str(_s0.get("dedup_key") or "")[:32]))
        # 登记待办（**幂等**：同 (kind, subject) 的 open 项唯一 ⇒ 不会每轮灌爆队列；失败不影响清洗）
        try:
            from std_lib.common_lib import governance_store as _gs

            _gs.worklist_add(
                "trigger_manual_breakpoint",
                "oversized_record/%s/%s" % (project, str(_s0.get("dedup_key") or "?")[:32]),
                stage="阶段1",
                artifact_key=outputs.get("jsonl", ""),
                payload={"source": project, "count": _osz["count"], "max_chars": _osz["max_chars"],
                         "threshold_chars": _osz["threshold_chars"], "samples": _osz["samples"],
                         "note": "内存侧已由流式化解除放大；数据侧（记录级附件聚合字段阈值+外置）待产品决策"},
                suggestion="评估把附件级既有机制（>200K 字 → .txt + path + md5）扩展到记录级聚合字段。",
                confidence=0.75,
            )
        except Exception as _e:  # noqa: BLE001  治理库不可用不得影响清洗
            LOG.debug("[%s] 超大记录待办登记跳过（%s）", project, type(_e).__name__)

    # 8.5) 校验失败记录隔离落盘（v2 §3.4 V1）：不进交付，但**不静默丢弃**——
    #      落 {src}_cleaned_{date}.quarantine.jsonl，供定位与修复后重跑。
    quarantine_path = ""
    if quarantined:
        _qdate = _dt.date.today().strftime("%Y%m%d")
        quarantine_path = os.path.join(out_dir, f"{project}_cleaned_{_qdate}.quarantine.jsonl")
        _tmp = quarantine_path + ".tmp"
        with open(_tmp, "w", encoding="utf-8") as fh:
            for _r in quarantined:
                fh.write(json.dumps(_r, ensure_ascii=False) + "\n")
        os.replace(_tmp, quarantine_path)
        LOG.warning(
            "[%s] 校验未过记录已隔离 %d 条 → %s", project, len(quarantined), quarantine_path
        )

    # 9) 历史版本（每次清洗前将上一版归档——用当前产出做快照基线）
    # history_dir 改为由 out_dir **同级派生**（dirname(out_dir)/history）：
    #   - 默认 out_dir={project_root}/data/cleaned → history={project_root}/data/history，
    #     与改造前**完全等价**（零行为变更）；
    #   - 产物目录统一后 out_dir=repo/data/cleaned → history 自动跟随为 repo/data/history，
    #     无需调用方再传参。
    history_dir = history_dir or os.path.join(os.path.dirname(os.path.abspath(out_dir)), "history")
    # 9) 单版化：cleaned 仅留本次当前日期；旧日期移入 history 且仅保留 keep 个日期版本
    _archived = rotate_history(
        out_dir,
        history_dir,
        project,
        _dt.date.today().strftime("%Y%m%d"),
        keep=cfg.get("output", {}).get("keep_history_versions", 3),
    )
    if _archived:
        LOG.debug("[%s] 历史归档 %d 个旧版文件", project, _archived)

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
        # v2 §3.4 V1（2026-09-26）：隔离量与失败率——供 run_clean_pipeline 判阈值与
        # gate_clean_schema 断言（原实现只有 validation_failed 计数，且失败记录仍交付）
        "quarantined_total": len(quarantined),
        "quarantine_path": quarantine_path,
        "schema_failed_rate": round(len(quarantined) / max(1, len(mapped)), 4),
        "allow_schema_errors": allow_schema_errors,
        "allow_delivery": allow,
        "null_rates": null_rates,
        "true_nonempty_rates": true_nonempty_rates,
        "sentence_acceptance_issues": issues,
        "high_freq_dict_size": len(high_freq),
        "outputs": outputs,
        "metrics_path": metrics_path,
        "elapsed_seconds": round(time.time() - t0, 2),
    }
    LOG.info(
        "[%s] 管道完成：raw=%d → cleaned=%d（去重 %d，耗时 %.1fs）",
        project,
        len(raw),
        len(cleaned_final),
        summary["dedup_removed"],
        summary["elapsed_seconds"],
    )
    return summary


if __name__ == "__main__":  # 离线自检（不执行全量）
    recs = [{"title": "测试", "body_text": "第一条 为规范管理。第二条 施行。"}]
    hf = build_high_freq_dict(recs, min_freq=1)
    assert isinstance(hf, dict)
    print("[scraper_std.pipeline] 离线自检通过")
