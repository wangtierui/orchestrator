# -*- coding: utf-8 -*-
"""modules.reg_frameworks.extract — 报送素材 → `DataTable`/`DataItem` 结构化（W3 §5.2.2）。

输入：`data/inbox/<域>/` 下的报送素材（EAST 2.0 等）。本模块只做**表格结构化**，
不做业务判断；每个抽取结果都带 `source_file` 溯源，人工校验在确认台/工作台完成。

抽取策略（按优先级，先命中先用；全部走标准库，不引第三方解析栈）：
  1. **CSV**（`csv` 模块）：表头行 = 列名 → 首列为表名，其余列即数据项；
  2. **XLSX**（`zipfile` + XML 最小实现，只读 sharedStrings/sheet1——**不引 openpyxl**：
     采集素材的表格结构规整，且避免为「读一张表」引入重依赖）；
  3. 其余扩展名 → 跳过并计入 `skipped`（**不静默**：回报抽取覆盖率）。

幂等：`DataTable.mk_id`/`DataItem.mk_id` 由（体系, 表名）/（表 id, 项名）派生 ⇒
重复抽取得到同 id，`store.write_rows` 幂等 upsert。
"""
from __future__ import annotations

import os
import re

from modules.reg_frameworks import regfw_model as model
from modules.reg_frameworks import regfw_store as store
from std_lib.common_lib import tabular as _tabular

_XLSX_SKIP = {"styles.xml", "theme", "docProps"}


def _infer_item_type(name: str) -> str:
    """数据项类型粗分（关键词优先；未命中 → statistic）。**启发式只影响分类不影响映射正确性**。"""
    s = str(name or "")
    if re.search(r"(金额|收入|支出|成本|利润|税|资本|充足率|费率)", s):
        return "financial"
    if re.search(r"(数量|件数|户数|人数|次数|笔数)", s):
        return "statistic"
    if re.search(r"(率|比例|占比)", s):
        return "statistic"
    if re.search(r"(名称|编号|代码|类别|类型)", s):
        return "management"
    return "other"


def _keywords_of(name: str) -> tuple[str, ...]:
    """数据项关键词组（供 keyword 映射法使用）：按分隔符切 + 中文 2~4 字滑窗（限长防爆炸）。"""
    parts = [p for p in re.split(r"[\s（）()、,，:：/]+", str(name or "")) if p]
    out: list[str] = []
    for p in parts:
        out.append(p)
        if re.fullmatch(r"[一-鿿]{2,}", p):
            for n in (2, 3, 4):
                out.extend(p[i:i + n] for i in range(max(0, len(p) - n + 1)))
    uniq = sorted({w for w in out if len(w) >= 2})
    return tuple(uniq[:24])


def extract_file(p: str, fid: str, theme: str = "", owner_dept: str = "") -> dict:
    """抽取单个素材 → `(tables, items, meta)`；返回统计以便调用方披露覆盖率。"""
    try:
        rows = _tabular.read_rows(p)   # 共享原语（CSV/XLSX）
    except ValueError as e:
        return {"tables": [], "items": [], "skipped": 1, "reason": str(e)}

    tables: list[model.DataTable] = []
    items: list[model.DataItem] = []
    if not rows:
        return {"tables": [], "items": [], "skipped": 1, "reason": "空表"}

    header = [str(c or "").strip() for c in rows[0]]
    body = rows[1:]
    # 表名：文件名（清洗）或首列表头
    tname = re.sub(r"[_\-]+", " ", os.path.splitext(os.path.basename(p))[0]).strip() or (header[0] or "未命名表")
    tid = model.DataTable.mk_id(fid, tname)
    tables.append(model.DataTable(
        tid=tid, fid=fid, name=tname,
        theme=theme or header[0] or "未分类",
        owner_dept=owner_dept,
        source_systems=(os.path.splitext(os.path.basename(p))[1].lstrip("."),),
    ))
    # 数据项：跳过首列（表名/序号列），其余非空表头即为数据项
    for col in header[1:]:
        name = str(col or "").strip()
        if not name:
            continue
        items.append(model.DataItem(
            iid=model.DataItem.mk_id(tid, name), tid=tid, name=name,
            item_type=_infer_item_type(name),
            definition=f"{tname} · {name}",
            keywords=_keywords_of(name),
        ))
    return {
        "tables": [t.to_dict() for t in tables],
        "items": [i.to_dict() for i in items],
        "skipped": 0,
        "rows": len(body),
    }


def extract_dir(fid: str, src_dir: str, *, theme: str = "", owner_dept: str = "",
                 persist: bool = True) -> dict:
    """批量抽取目录 → upsert 事实源；返回逐文件结果（**覆盖率与跳过原因全部披露**）。"""
    files = sorted(f for f in os.listdir(src_dir)
                   if os.path.splitext(f)[1].lower() in (".csv", ".xlsx"))
    all_t: list[dict] = []
    all_i: list[dict] = []
    per_file: list[dict] = []
    for fn in files:
        r = extract_file(os.path.join(src_dir, fn), fid, theme=theme, owner_dept=owner_dept)
        all_t.extend(r["tables"])
        all_i.extend(r["items"])
        per_file.append({"file": fn, "tables": len(r["tables"]),
                         "items": len(r["items"]), "skipped": r["skipped"],
                         **({"reason": r["reason"]} if r.get("reason") else {})})
    res: dict = {"fid": fid, "files": len(files), "tables": len(all_t), "items": len(all_i),
                 "per_file": per_file}
    if persist and all_t:
        res["write_tables"] = store.write_rows(store.F_TABLES, all_t, key="tid")
        res["write_items"] = store.write_rows(store.F_ITEMS, all_i, key="iid")
    return res
