# -*- coding: utf-8 -*-
"""
rfn.bridge — RFN↔clean 溯源桥（唯一读写实现；契约 SSOT = interfaces/contract.RFN_CLEAN_BRIDGE_FIELDS）

定位（Q1=A / R7）：
  - 归属表 8 列保持零变更；RFN↔clean 的稳定技术锚（source_url/dedup_key）独立存于
    modules/regulatory_classifier/data/rfn_clean_bridge.csv。
  - 写者 = reconcile_clean_drift 后处理（source_url 主锚 upsert）；本模块提供唯一读写函数，
    禁止其他代码直接改桥表文件。

桥表 9 列（与 contract 对齐）：
  监管文件编号, 文件来源, source_url, dedup_key,
  登记时标题, 登记时文号, 最近确认日期, 最近状态, relation

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

_PKG_DIR = os.path.dirname(os.path.abspath(__file__))   # modules/regulatory_classifier/rfn/
BRIDGE_PATH = os.environ.get("RFN_BRIDGE_CSV") or os.path.join(
    os.path.dirname(_PKG_DIR), "data", "rfn_clean_bridge.csv")

# 列契约：与 interfaces/contract.RFN_CLEAN_BRIDGE_FIELDS 保持一致（R24 程序可读契约）。
BRIDGE_FIELDS = ["监管文件编号", "文件来源", "source_url", "dedup_key",
                 "登记时标题", "登记时文号", "最近确认日期", "最近状态", "relation"]


def load_bridge(path=None) -> list[dict]:
    """读全部桥记录（utf-8-sig）。文件不存在返回 []。"""
    p = path or BRIDGE_PATH
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


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


def upsert(row: dict, path=None):
    """按「监管文件编号」幂等 upsert（R7 写者入口，供 reconcile 后处理调用）。

    row 需含 BRIDGE_FIELDS 全部列；缺失列补空。
    """
    rows = load_bridge(path)
    idx = {r["监管文件编号"]: i for i, r in enumerate(rows)}
    i = idx.get(row.get("监管文件编号", ""))
    merged = {k: row.get(k, "") for k in BRIDGE_FIELDS}
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
        if rfn and r.get("监管文件编号") != rfn:
            continue
        if source_url and r.get("source_url") != source_url:
            continue
        if dedup_key and r.get("dedup_key") != dedup_key:
            continue
        out.append(r)
    return out
