# -*- coding: utf-8 -*-
"""只读校验脚本：验证 cache/ 中列表与详情缓存的数据质量。"""

# ---- P3b 仓库引导（migrate_collectors_p3b.py 注入）：使 std_lib/config 可导入 ----
import os as _os
import sys as _sys

_GUIDE_ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "..", ".."))
if _GUIDE_ROOT not in _sys.path:
    _sys.path.insert(0, _GUIDE_ROOT)
del _GUIDE_ROOT, _os, _sys
import glob
import json
import os

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache", "nfra")  # 五源统一缓存根

# 1) 列表总量校验（928 + 927）
files = sorted(glob.glob(os.path.join(CACHE, "SelectDocByItemIdAndChild*.json")))
tot, grand, bad = set(), 0, 0
for f in files:
    try:
        d = json.load(open(f, encoding="utf-8"))
    except Exception as e:
        print("PARSE FAIL", f, e); bad += 1; continue
    data = d.get("data", {})
    if not isinstance(data, dict) or "rows" not in data:
        print("SUSPECT", os.path.basename(f), "keys=", list(d.keys())[:5]); bad += 1; continue
    for r in (data.get("rows") or []):
        did = r.get("docId")
        if did is not None:
            tot.add(did)
    grand += len(data.get("rows") or [])
print("[列表] 缓存文件=%d | 可疑/失败=%d | 去重文档=%d | 行累计=%d"
      % (len(files), bad, len(tot), grand))

# 2) 详情字段校验（抽样 5 个 + 全量 docClob 存在性）
dfiles = sorted(glob.glob(os.path.join(CACHE, "SelectByDocId__docId_*.json")))
print("[详情] 缓存文件=%d" % len(dfiles))
fields_stat = {}
no_clob = 0
for f in dfiles:
    try:
        d = json.load(open(f, encoding="utf-8"))
    except Exception:
        no_clob += 1; continue
    data = d.get("data", d)
    if not isinstance(data, dict):
        no_clob += 1; continue
    clob = data.get("docClob")
    if not clob:
        no_clob += 1
    for k in ["docClob", "documentNo", "docSource", "agencyTypeName",
              "builddate", "indexNo", "interviewTypeName", "title", "docTitle"]:
        if data.get(k) is not None:
            fields_stat[k] = fields_stat.get(k, 0) + 1
print("  含 docClob 正文的缓存数=%d / %d" % (len(dfiles) - no_clob, len(dfiles)))
print("  字段覆盖率(占详情缓存):")
for k, v in sorted(fields_stat.items(), key=lambda x: -x[1]):
    print("    %-14s %d" % (k, v))

# 3) 抽样展示 1 个详情的关键字段
if dfiles:
    d = json.load(open(dfiles[0], encoding="utf-8"))
    data = d.get("data", d)
    print("  抽样(%s):" % os.path.basename(dfiles[0]))
    for k in ["title", "docTitle", "documentNo", "docSource", "agencyTypeName",
              "builddate", "indexNo", "interviewTypeName"]:
        v = data.get(k)
        if v is not None:
            s = str(v).replace("\n", " ")
            print("    %-14s %s" % (k, (s[:60] + "...") if len(s) > 60 else s))
    clob = data.get("docClob") or ""
    txt = clob.replace("\n", " ")
    print("    docClob 长度=%d 前80字=%s" % (len(clob), txt[:80]))
