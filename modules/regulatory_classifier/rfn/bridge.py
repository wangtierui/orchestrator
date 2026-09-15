# -*- coding: utf-8 -*-
"""
rfn.bridge — RFN↔clean 溯源桥（唯一读写实现；契约 SSOT = interfaces/contract.RFN_CLEAN_BRIDGE_FIELDS）

定位（Q1=A / R7）：
  - 归属表 8 列保持零变更；RFN↔clean 的稳定技术锚（source_url/dedup_key）独立存于
    modules/regulatory_classifier/data/rfn_clean_bridge.csv。
  - 写者 = reconcile_clean_drift 后处理（source_url 主锚 upsert）；本模块提供唯一读写函数，
    禁止其他代码直接改桥表文件。

桥表 11 列（与 contract 对齐；R10 provenance：后两列 generated_by/generated_at）：
  rfn, 文件来源, source_url, dedup_key,
  登记时标题, 登记时文号, 最近确认日期, 最近状态, relation,
  generated_by, generated_at

ⓘ 2026-09-15（首列英文键名 · 键值对格式规范统一）：
  桥表**首列由中文列名「监管文件编号」改为英文键名 `rfn`**——桥表是**技术关联表**（键值对语义，
  RFN↔clean 锚），与「归属表/主题表/明细表」等**业务展示表**不同，后者按项目既定决策保留中文列名。
  为兼容历史快照/备份，读取侧（load_bridge）对旧表头做**归一化映射**（监管文件编号 → rfn），
  写入侧一律按 BRIDGE_FIELDS（rfn）落盘；`upsert` 仍接受旧键名入参。

relation 取值：
  - "self"   锚定同一 clean 记录（source_url 或 dedup_key 命中归属表对应 RFN）
  - "refresh" C1 展示纠错已自动刷新归属表（RFN 不变，R14 联动 needs_rebuild）
  - "supersede" C2 实体变化待人工（新 RFN 承接，旧 RFN 挂 supersede 关系）——由 reconcile --resolve 写入

最近状态（state）：
  - "ok"          最近比对一致
  - "drift_c1"    展示字段漂移（C1 可自动刷新，未 apply）
  - "drift_c2"    实体漂移/无法锚定（待人工）
"""
import csv
import os
import time

_PKG_DIR = os.path.dirname(os.path.abspath(__file__))   # modules/regulatory_classifier/rfn/
BRIDGE_PATH = os.environ.get("RFN_BRIDGE_CSV") or os.path.join(
    os.path.dirname(_PKG_DIR), "data", "rfn_clean_bridge.csv")

# 列契约：与 interfaces/contract.RFN_CLEAN_BRIDGE_FIELDS 保持一致（R24 程序可读契约）。
RFN_KEY = "rfn"
LEGACY_RFN_KEY = "监管文件编号"           # 2026-09-15 前的旧表头（读取侧归一，勿再写入）
BRIDGE_FIELDS = [RFN_KEY, "文件来源", "source_url", "dedup_key",
                 "登记时标题", "登记时文号", "最近确认日期", "最近状态", "relation",
                 "generated_by", "generated_at"]


def rfn_of(row: dict) -> str:
    """从桥记录稳健取 RFN（兼容旧表头「监管文件编号」）。"""
    return (row.get(RFN_KEY) or row.get(LEGACY_RFN_KEY) or "").strip()


def load_bridge(path=None) -> list[dict]:
    """读全部桥记录（utf-8-sig）。文件不存在返回 []。

    首列归一：旧快照/备份若仍为「监管文件编号」，读取时映射为 `rfn`（历史数据不重写，读侧归一）。
    """
    p = path or BRIDGE_PATH
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        if not r.get(RFN_KEY) and r.get(LEGACY_RFN_KEY):
            r[RFN_KEY] = r.pop(LEGACY_RFN_KEY)
    return rows


def _save(rows, path=None):
    p = path or BRIDGE_PATH
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=BRIDGE_FIELDS)
        w.writeheader()
        w.writerows(rows)
    try:
        os.replace(tmp, p)
    except OSError:
        with open(p, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=BRIDGE_FIELDS)
            w.writeheader()
            w.writerows(rows)


def upsert(row: dict, path=None, provenance_by: str = "reconcile_clean_drift"):
    """按 RFN 幂等 upsert（R7 写者入口，供 reconcile 后处理调用）。

    row 需含 BRIDGE_FIELDS 业务列；缺失列补空。**入参键名兼容 `rfn` 与旧名「监管文件编号」**，
    落盘一律为 `rfn`（键值对格式规范统一）。R10 provenance：行未显式带
    generated_by/at 时补写者（默认 reconcile_clean_drift）与本次写入时间。
    """
    rows = load_bridge(path)
    idx = {rfn_of(r): i for i, r in enumerate(rows)}
    i = idx.get(rfn_of(row))
    merged = {k: row.get(k, "") for k in BRIDGE_FIELDS}
    if not merged.get(RFN_KEY):
        merged[RFN_KEY] = rfn_of(row)
    if not merged.get("generated_by"):
        merged["generated_by"] = provenance_by
    if not merged.get("generated_at"):
        merged["generated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    if i is None:
        rows.append(merged)
    else:
        # 保留既有锚（source_url/dedup_key/登记时标题/登记时文号）除非显式覆盖
        old = rows[i]
        for k in BRIDGE_FIELDS:
            if k in ("source_url", "dedup_key", "登记时标题", "登记时文号") and old.get(k) and not row.get(k):
                merged[k] = old.get(k)
        rows[i] = merged
    _save(rows, path)
    return merged


def lookup(rfn=None, source_url=None, dedup_key=None, path=None):
    """按 rfn / source_url / dedup_key 任一精确查询。多条件取交集语义（都给的须全中）。"""
    out = []
    for r in load_bridge(path):
        if rfn and rfn_of(r) != rfn:
            continue
        if source_url and r.get("source_url") != source_url:
            continue
        if dedup_key and r.get("dedup_key") != dedup_key:
            continue
        out.append(r)
    return out
