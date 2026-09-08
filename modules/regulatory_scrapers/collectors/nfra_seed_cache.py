#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把已成功抓取的真实样本（nfra_children.json 完整列表、nfra_detail.json 单篇详情）
转换为 nfra_collector.py 缓存目录中的标准缓存文件，
使主脚本可在 --offline 下跑通完整解析链路（无需再次联网）。

说明：本脚本仅用于"用已采集的真实数据验证脚本逻辑"。完整实时抓取请直接运行主脚本。
"""

# ---- P3b 仓库引导（migrate_collectors_p3b.py 注入）：使 std_lib/config 可导入 ----
import os as _os
import sys as _sys

_GUIDE_ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "..", ".."))
if _GUIDE_ROOT not in _sys.path:
    _sys.path.insert(0, _GUIDE_ROOT)
del _GUIDE_ROOT, _os, _sys
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import nfra_collector as m

CACHE = os.path.join(HERE, "cache", "nfra")  # 五源统一缓存根
m.set_cache_dir(CACHE)

CHILD_URL = m.API + "/DocInfo/SelectItemAndDocByItemPId"
LIST_URL = m.API + "/DocInfo/SelectDocByItemIdAndChild"
DETAIL_URL = m.API + "/DocInfo/SelectByDocId"

def main():
    # 1) 栏目结构缓存
    src_child = os.path.join(HERE, "tmp", "nfra_children.json")
    with open(src_child, encoding="utf-8") as f:
        child_data = json.load(f)
    cp = m._cache_path(CHILD_URL, {"itemId": 926, "pageSize": 50})
    with open(cp, "w", encoding="utf-8") as f:
        json.dump(child_data, f, ensure_ascii=False)
    print("写入栏目缓存:", os.path.basename(cp))

    children = child_data["data"]
    total_docs = 0
    # 2) 每个子栏目的列表缓存（按 pageSize=18 分页，与官网一致）
    for it in children:
        iid = it["itemId"]
        rows = it.get("docInfoVOList") or []
        total = len(rows)
        pages = [rows[i:i + 18] for i in range(0, max(total, 1), 18)] or [[]]
        for p, chunk in enumerate(pages, 1):
            payload = {"rptCode": 200, "msg": "成功",
                       "data": {"total": total, "rows": chunk}}
            cp = m._cache_path(LIST_URL, {"itemId": iid, "pageSize": 18, "pageIndex": p})
            with open(cp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
        total_docs += total
        print("  列表缓存 栏目 %s(%s): %d 篇 / %d 页" % (it.get("itemName"), iid, total, len(pages)))

    # 父栏目 926 自身列表（样本未单独抓取，置空以避免离线缺失）
    cp = m._cache_path(LIST_URL, {"itemId": 926, "pageSize": 18, "pageIndex": 1})
    with open(cp, "w", encoding="utf-8") as f:
        json.dump({"rptCode": 200, "msg": "成功", "data": {"total": 0, "rows": []}}, f, ensure_ascii=False)

    # 3) 单篇详情缓存
    src_detail = os.path.join(HERE, "tmp", "nfra_detail.json")
    with open(src_detail, encoding="utf-8") as f:
        detail_data = json.load(f)
    did = detail_data["data"].get("docId")
    cp = m._cache_path(DETAIL_URL, {"docId": did})
    with open(cp, "w", encoding="utf-8") as f:
        json.dump(detail_data, f, ensure_ascii=False)
    print("写入详情缓存 docId=%s: %s" % (did, os.path.basename(cp)))

    print("\n缓存就绪：%d 篇文档列表 + 1 篇完整详情，位于 %s" % (total_docs, CACHE))

if __name__ == "__main__":
    main()
