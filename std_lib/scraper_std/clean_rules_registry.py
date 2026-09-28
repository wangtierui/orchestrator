# -*- coding: utf-8 -*-
"""std_lib.scraper_std.clean_rules_registry — 清洗规则**注册表**（N-92 / 优化方案 v2 · P0-4）

为什么需要它
------------
清洗规则原分散在 7 个模块（`cleaner` / `sentence_split` / `ocr_correction` / `table_recovery` /
`text_reflow` / `rich_object` / `schema_validation`），**没有任何地方能回答**：
"这一条数据被哪些规则改过？某规则改动会影响哪些字段？规则是否幂等？"

本模块把规则**声明化**（名称/所属模块/可调用对象/阶段/作用字段/幂等性/是否默认启用），
并提供 `verify()` 做**可导入性自检** —— 例如某规则被改名/删除而注册表未同步时立即暴露。

**分工（避免第三套注册）**
--------------------------
| 关注点 | 唯一事实源 |
|---|---|
| **字段/键集契约**（有哪些列、键叫什名） | `interfaces/contract.py` + `config/schema/contract_manifest.json` |
| **受控取值域**（值合法范围） | `config/enums.py` |
| **清洗规则的执行面**（规则→函数→字段） | **本模块** |
三者**互不重叠**：本模块**不**定义字段集合，只引用其名称（字符串），因此不与契约层冲突。

纪律
----
本模块是**声明 + 自检**用，**不承载执行逻辑**（执行仍在各模块）；`verify()` 只做可导入性断言，
**不修改任何行为**（零回归面）。
"""

from __future__ import annotations

import importlib
import importlib.util
import os


def _load_module(short: str):
    """加载同包内 `std_lib.scraper_std.<short>`。

    **优先**按包导入（正常运行环境）；若本模块被**直接当脚本**执行（`python clean_rules_registry.py`，
    此时 `__package__` 为空、`std_lib` 不在 `sys.path`），则**按文件路径**加载同目录兄弟模块
    —— 与 `tools/install_schedule.py::_gsd()` 同款手法：**不新增任何 `sys.path` 注入**
    （`gate_import_bootstrap` 的"每层只减不增"基线因此无需调整）。
    """
    pkg = __package__ or ""
    if pkg:
        return importlib.import_module(f"{pkg}.{short}")
    here = os.path.dirname(os.path.abspath(__file__))
    fp = os.path.join(here, f"{short}.py")
    spec = importlib.util.spec_from_file_location(f"_crr_{short}", fp)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法按路径加载 {fp}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

# 规则登记：`module` 为 `std_lib.scraper_std.<x>` 的短名；`role` ∈ entry/step/helper；
# `affects` 为**被该规则改写或产出的字段/概念**（仅供追溯，不构成契约）。
RULES: tuple[dict, ...] = (
    # ---- cleaner：字符级清洗（最先跑） ----
    {"id": "CLEAN-01", "module": "cleaner", "fn": "clean_text", "role": "entry",
     "stage": "char", "affects": ("body_text",), "idempotent": True, "enabled": True,
     "purpose": "字符级总清洗（不可见字符/异常空白/控制符）"},
    {"id": "CLEAN-02", "module": "cleaner", "fn": "normalize_ws", "role": "step",
     "stage": "char", "affects": ("body_text", "title"), "idempotent": True, "enabled": True,
     "purpose": "空白归一（可保留换行）"},
    {"id": "CLEAN-03", "module": "cleaner", "fn": "normalize_date", "role": "step",
     "stage": "field", "affects": ("发布日期",), "idempotent": True, "enabled": True,
     "purpose": "日期归一为统一形态"},
    {"id": "CLEAN-04", "module": "cleaner", "fn": "denoise", "role": "step",
     "stage": "char", "affects": ("body_text",), "idempotent": True, "enabled": True,
     "purpose": "噪声去除（页眉页脚/水印类残留）"},
    {"id": "CLEAN-05", "module": "cleaner", "fn": "is_table_block", "role": "helper",
     "stage": "guard", "affects": (), "idempotent": True, "enabled": True,
     "purpose": "表格污染判定（命中则跳过断句，交由 table_recovery）"},
    {"id": "CLEAN-06", "module": "cleaner", "fn": "dedup_records", "role": "step",
     "stage": "record", "affects": ("去重键",), "idempotent": True, "enabled": True,
     "purpose": "记录级去重"},
    {"id": "CLEAN-07", "module": "cleaner", "fn": "fill_missing", "role": "step",
     "stage": "record", "affects": ("缺失字段",), "idempotent": True, "enabled": True,
     "purpose": "缺失字段填充（**不得臆造**：仅限可判定项）"},
    {"id": "CLEAN-08", "module": "cleaner", "fn": "content_hash", "role": "helper",
     "stage": "record", "affects": ("去重键",), "idempotent": True, "enabled": True,
     "purpose": "内容指纹（去重键派生）"},
    # ---- sentence_split：断句与粘连修复（四级） ----
    {"id": "SENT-01", "module": "sentence_split", "fn": "repair_text", "role": "entry",
     "stage": "sentence", "affects": ("body_text", "split_sentences", "raw_uncut_text"),
     "idempotent": True, "enabled": True,
     "purpose": "断句修复总入口（①标签语义 ②标点 ③中英粘连 ④无标点兜底）"},
    {"id": "SENT-02", "module": "sentence_split", "fn": "split_long_uncut", "role": "step",
     "stage": "sentence", "affects": ("split_sentences", "raw_uncut_text"),
     "idempotent": True, "enabled": True, "purpose": "④ 无标点长文兜底切分（原文保留）"},
    {"id": "SENT-03", "module": "sentence_split", "fn": "acceptance_check", "role": "helper",
     "stage": "guard", "affects": (), "idempotent": True, "enabled": True,
     "purpose": "断句验收口径（超长无标点串检测）"},
    # ---- ocr_correction：OCR 纠错（仅 OCR 来源记录） ----
    {"id": "OCR-01", "module": "ocr_correction", "fn": "correct_ocr_text", "role": "entry",
     "stage": "ocr", "affects": ("body_text",), "idempotent": True, "enabled": True,
     "purpose": "OCR 文本纠错（混淆字表 + 噪声过滤）"},
    {"id": "OCR-02", "module": "ocr_correction", "fn": "load_confusion_map", "role": "helper",
     "stage": "ocr", "affects": (), "idempotent": True, "enabled": True,
     "purpose": "混淆字表加载"},
    {"id": "OCR-03", "module": "ocr_correction", "fn": "replace_confusions", "role": "step",
     "stage": "ocr", "affects": ("body_text",), "idempotent": True, "enabled": True,
     "purpose": "混淆字替换"},
    {"id": "OCR-04", "module": "ocr_correction", "fn": "filter_noise", "role": "step",
     "stage": "ocr", "affects": ("body_text",), "idempotent": True, "enabled": True,
     "purpose": "OCR 噪声过滤"},
    # ---- table_recovery：表格恢复 ----
    {"id": "TAB-01", "module": "table_recovery", "fn": "normalize_table", "role": "entry",
     "stage": "table", "affects": ("结构化表格",), "idempotent": True, "enabled": True,
     "purpose": "表格结构化归一"},
    {"id": "TAB-02", "module": "table_recovery", "fn": "fill_merged_headers", "role": "step",
     "stage": "table", "affects": ("表头",), "idempotent": True, "enabled": True,
     "purpose": "合并单元格表头回填"},
    {"id": "TAB-03", "module": "table_recovery", "fn": "flatten_header_chain", "role": "step",
     "stage": "table", "affects": ("表头",), "idempotent": True, "enabled": True,
     "purpose": "多级表头扁平化"},
    {"id": "TAB-04", "module": "table_recovery", "fn": "remove_dup_headers", "role": "step",
     "stage": "table", "affects": ("表头",), "idempotent": True, "enabled": True,
     "purpose": "重复表头去除"},
    {"id": "TAB-05", "module": "table_recovery", "fn": "structured_table_fields", "role": "entry",
     "stage": "table", "affects": ("结构化表格",), "idempotent": True, "enabled": True,
     "purpose": "结构化表格字段产出"},
    # ---- text_reflow：中文回排 ----
    {"id": "REF-01", "module": "text_reflow", "fn": "reflow_chinese", "role": "entry",
     "stage": "reflow", "affects": ("body_text",), "idempotent": True, "enabled": True,
     "purpose": "中文硬换行回排（还原段落）"},
    # ---- rich_object：富内容 ----
    {"id": "RICH-01", "module": "rich_object", "fn": "extract_rich_objects", "role": "entry",
     "stage": "rich", "affects": ("rich_structured", "rich_text", "rich_count"),
     "idempotent": True, "enabled": True, "purpose": "富对象（图形/公式）抽取"},
    {"id": "RICH-02", "module": "rich_object", "fn": "rich_object_fields", "role": "helper",
     "stage": "rich", "affects": ("rich_structured", "rich_text", "rich_count"),
     "idempotent": True, "enabled": True, "purpose": "富对象字段拼装"},
    # ---- schema_validation：字段校验（收口） ----
    {"id": "SCH-01", "module": "schema_validation", "fn": "validate_record", "role": "entry",
     "stage": "validate", "affects": ("校验结论",), "idempotent": True, "enabled": True,
     "purpose": "记录级字段校验（不合格 → 隔离）"},
    {"id": "SCH-02", "module": "schema_validation", "fn": "normalize_datetime", "role": "step",
     "stage": "validate", "affects": ("时间字段",), "idempotent": True, "enabled": True,
     "purpose": "时间字段归一"},
    {"id": "SCH-03", "module": "schema_validation", "fn": "check_unique_dedup_keys", "role": "helper",
     "stage": "validate", "affects": ("去重键",), "idempotent": True, "enabled": True,
     "purpose": "去重键唯一性检查"},
)

# 清洗流水线**阶段顺序**（唯一事实源；与 `pipeline.run_pipeline` 的执行序一致）
STAGE_ORDER: tuple[str, ...] = (
    "guard", "char", "field", "record", "sentence", "ocr", "table", "reflow", "rich", "validate",
)


def rules_of_module(short: str) -> list[dict]:
    """某模块登记的规则。"""
    return [r for r in RULES if r["module"] == short]


def entry_rules() -> list[dict]:
    """`role == "entry"` 的规则（流水线入口）。"""
    return [r for r in RULES if r["role"] == "entry"]


def verify() -> list[str]:
    """自检：每条规则声明的 `(module, fn)` 必须**可导入**；stage 必须已登记。

    返回问题清单（空 = 通过）。**纯只读**，不改任何行为。
    """
    problems: list[str] = []
    for r in RULES:
        mod_name = f"std_lib.scraper_std.{r['module']}"
        try:
            mod = _load_module(r["module"])
        except Exception as e:  # noqa: BLE001
            problems.append(f"{r['id']}: 模块不可导入 {mod_name}：{type(e).__name__}: {e}")
            continue
        if not callable(getattr(mod, r["fn"], None)):
            problems.append(f"{r['id']}: {mod_name}.{r['fn']} 不存在或不可调用（注册表漂移）")
        if r["stage"] not in STAGE_ORDER:
            problems.append(f"{r['id']}: 未登记的 stage {r['stage']!r}（须加入 STAGE_ORDER）")
    return problems


def brief() -> str:
    """人读摘要（供文档/报告引用）。"""
    lines = [f"清洗规则登记 {len(RULES)} 条（{len({r['module'] for r in RULES})} 个模块）："]
    for m in sorted({r["module"] for r in RULES}):
        rs = rules_of_module(m)
        lines.append(f"  - {m}: {len(rs)} 条（入口 {[r['fn'] for r in rs if r['role'] == 'entry']}）")
    return "\n".join(lines)


if __name__ == "__main__":  # 离线自检
    # 脚本态（`python std_lib/scraper_std/clean_rules_registry.py`）下 `__package__` 为空 →
    # 兄弟模块只能按路径加载，而**自身带包内绝对导入**的模块（如 `sentence_split` 引
    # `std_lib.common_lib.sentence_boundary`）在无仓根 `sys.path` 时不可加载。
    # 此处**如实报告**而不误判为"注册表漂移"；权威校验请以包上下文运行：
    #   python -m std_lib.scraper_std.clean_rules_registry
    _p = verify()
    _pkg = bool(__package__)
    if _pkg:
        assert not _p, f"清洗规则注册表自检失败：{_p}"
    else:
        print("（脚本态：包内绝对导入的模块无法按路径加载，以下问题**不计为注册表漂移**）")
    print(brief())
    print("[scraper_std.clean_rules_registry] 自检通过" if _pkg else "（包上下文校验请用 -m 方式）")
