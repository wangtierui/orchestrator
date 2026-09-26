# -*- coding: utf-8 -*-
"""
verification_state.py — 监管文件效力检查·状态缓存与同步（规范二落地）

符合《监管文件效力检查·标准操作规范》（2026-08-26 用户固化）：
  1) 先查北大法宝查验记录：存在且查验时间 ≤ 90 日 → 直接复用（不重复消耗核验资源）；
  2) 不存在或 > 90 日 → 调用 timeliness_review 执行效力检查；
  3) 执行时先计算当前效力状态与上次记录比对，仅在发生变化时触发后续同步；
  4) 写入时记录 时间戳 + 效力状态，防止死循环；
  5) 效力变更 → 同步五源 data/cleaned（由 timeliness_full_check.py 回写）+ regulatory_classifier 归属表"时效状态"列。

数据文件：
  timeliness_review/verification_state.json —— 按 document_number+title 聚合的查验状态缓存
    { "<唯一键>": {"status": "...", "replacement_document": "...", "verification_source": "...",
                   "last_checked_at": "YYYY-MM-DD HH:MM:SS", "changed_at": "..."} }

用法：
  from verification_state import is_fresh, mark_checked, changed_from_last, sync_to_classifier
"""
import csv
import json
import os
import re
import shutil
import sys
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # regulatory_scrapers/
TASK_DIR = os.path.join(ROOT, "timeliness_review")
STATE_FILE = os.path.join(TASK_DIR, "verification_state.json")

# 北大法宝查验记录默认有效期（规范要求 ≤ 90 日）
DEFAULT_MAX_AGE_DAYS = 90

# regulatory_classifier 归属表（权威交付库）——路径在下方引导之后解析。
# v2 §3.1.3 I-2（2026-09-26）：原「os.path.join(ROOT, "..", "regulatory_classifier", …)」
# 属「自行拼兄弟模块目录」，改经 `interfaces.rfn_api.registry_paths()` 唯一入口（见 _classifier_csv）。

# R4 适配修复（2026-09-14）：原 `sys.path.insert(0, ROOT/"std_lib")` 是**已失效旧路径**
# （std_lib 早前上收 orchestrator 根）→ ①`scraper_std` 导入必失败并被 except 静默降级为硬编码
# 状态集 ②`std_lib.common_lib.norm` 导入**硬失败**，仅当调用方自己注入了仓根才侥幸不报错。
# 实测后果：`classifier_pkulaw_verify.py` 只注入 `ROOT` 与失效的 `ROOT/std_lib` →
# `ModuleNotFoundError: No module named 'std_lib'`，**该脚本自迁移后完全无法运行**。
# 本文件被多脚本 import，故在此**自带同仓引导**（不依赖调用方），保证独立可用。
_ORCH_ROOT = os.path.dirname(os.path.dirname(ROOT))                  # orchestrator 根
for _p in (_ORCH_ROOT, os.path.join(_ORCH_ROOT, "std_lib")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

def classifier_csv() -> str:
    """归属表（权威交付库）路径 —— 经 `interfaces.rfn_api.registry_paths()` 唯一入口。

    v2 §3.1.3 I-2（2026-09-26）：本模块与 `classifier_pkulaw_verify.py`、
    `sync_three_modules.py` 三处「自行拼兄弟模块目录」的写法统一收敛到该接口
    （归属表路径的唯一事实源 = `rfn.registry._csv_path()`，含 `RFN_REGISTRY_CSV` 环境覆盖）。
    回退路径同样经 SSOT（`paths.module_dir`，§3.1.1 结构清单），**不拼兄弟模块名字面量**
    ——`gate_no_cross_module_import` 判据 D 会断言这一点。
    """
    try:
        from interfaces.rfn_api import registry_paths  # noqa: PLC0415
        return registry_paths()["attr_csv"]
    except Exception:  # noqa: BLE001  接口不可用时回退（仓根已在 sys.path，见上方引导）
        from paths import module_dir  # noqa: PLC0415
        return os.path.join(module_dir("regulatory_classifier"), "data",
                            "人身保险公司-文件归属表.csv")


CLASSIFIER_CSV = os.path.abspath(classifier_csv())

# 效力状态合法值（与 unified_schema 共享常量对齐，规范 v3：7 值含 partially_repealed）
try:
    from scraper_std.unified_schema import TIMELINESS_STATUS  # noqa: E402
    STATUS_SET = frozenset(TIMELINESS_STATUS)
except Exception:  # 独立运行兜底（不破坏既有调用）
    STATUS_SET = {"valid", "amended", "repealed", "partially_repealed",
                  "expired", "pending", "uncertain"}


from std_lib.common_lib.norm import norm_docno as _norm_docno  # A-10：SSOT 收敛（标准层）


def state_key(docno=None, title=""):
    """状态缓存唯一键：优先发文字号（归一化）；无文号用标题归一化。"""
    nd = _norm_docno(docno)
    if nd and len(nd) >= 5:
        return "doc:" + nd
    t = re.sub(r"[《》\s]", "", title or "")
    return "title:" + t[:40]


def load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    with open(STATE_FILE, encoding="utf-8") as fh:
        d = json.load(fh)
    # F-D14：读侧剥离版本键（写侧注入 _meta；doc:* 记录消费逻辑零感知）
    if isinstance(d, dict):
        d.pop("_meta", None)
    return d


def read_meta():
    """F-D14：读原始状态文件的版本锚点（结构变更检测/迁移用；正常消费无需调用）。"""
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        return (json.load(open(STATE_FILE, encoding="utf-8")) or {}).get("_meta") or {}
    except (OSError, ValueError):
        return {}


def save_state(state):
    os.makedirs(TASK_DIR, exist_ok=True)
    # F-D14（H-01）：版本锚点（**副本**写入——不污染调用方对象，防"保存后继续遍历"踩 _meta；
    # load 侧剥离，消费方零改动）
    import time as _t  # noqa: PLC0415
    payload = {k: v for k, v in state.items() if k != "_meta"}
    payload["_meta"] = {"schema_version": "1.0", "written_by": "verification_state",
                        "written_at": _t.strftime("%Y-%m-%d %H:%M:%S")}
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    try:
        os.replace(tmp, STATE_FILE)
    except OSError:
        with open(STATE_FILE, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=2)


def is_fresh(docno=None, title="", status="", max_age_days=DEFAULT_MAX_AGE_DAYS, state=None):
    """规范 ①：北大法宝查验记录存在且查验时间 ≤ 90 日 → 直接复用（返回 True）。"""
    state = state if state is not None else load_state()
    rec = state.get(state_key(docno, title))
    if not rec:
        return False
    try:
        last = datetime.strptime(rec.get("last_checked_at", ""), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return False
    if datetime.now() - last > timedelta(days=max_age_days):
        return False
    # 状态一致才可复用；状态变化则需重新核验触发同步
    if status and rec.get("status", "") != status:
        return False
    return rec.get("verification_source", "").startswith("北大法宝")


def changed_from_last(docno=None, title="", new_status="", state=None):
    """规范 ③：计算当前效力状态与上次记录比对，仅发生变化时返回 True。"""
    state = state if state is not None else load_state()
    rec = state.get(state_key(docno, title))
    if not rec:
        return bool(new_status)          # 首次记录视为变化（需同步）
    return rec.get("status", "") != new_status


def mark_checked(docno=None, title="", status="", replacement="", vsource="", state=None):
    """规范 ④：写入时记录 时间戳 + 效力状态（防死循环）。返回 (state, changed, key)。"""
    state = state if state is not None else load_state()
    key = state_key(docno, title)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rec = state.get(key, {})
    changed = rec.get("status", "") != status
    rec.update({
        "status": status,
        "replacement_document": replacement or rec.get("replacement_document", ""),
        "verification_source": vsource or rec.get("verification_source", ""),
        "last_checked_at": now,
    })
    if changed:
        rec["changed_at"] = now
        rec["prev_status"] = rec.get("status") if rec.get("status") != status else rec.get("prev_status")
    state[key] = rec
    return state, changed, key


def sync_to_classifier(changed_records, dry_run=False):
    """规范 ⑤：效力变更 → 同步 regulatory_classifier 归属表"时效状态"列（按文号/标题匹配）。
    changed_records: list of {title, document_number, timeliness_status/new_status, ...}
    返回 (匹配数, 未匹配数, 更新路径)。
    """
    csv_path = os.path.abspath(CLASSIFIER_CSV)
    if not os.path.exists(csv_path):
        return 0, len(changed_records), None

    with open(csv_path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
        fieldnames = list(rows[0].keys()) if rows else []
    # 确保"时效状态"列存在
    if "时效状态" not in fieldnames:
        return 0, len(changed_records), csv_path

    matched = unmatched = 0
    for cr in changed_records:
        nd = _norm_docno(cr.get("document_number") or cr.get("发文字号") or "")
        st = cr.get("timeliness_status") or cr.get("new_status") or cr.get("status") or ""
        if st not in STATUS_SET and st != "":
            # 兼容中文状态
            st_map = {"已废止": "repealed", "已失效": "expired", "现行有效": "valid",
                      "修订": "amended", "待定": "pending", "不确定": "uncertain"}
            st = st_map.get(st, st)
        hit = None
        for r in rows:
            if nd and len(nd) >= 5 and _norm_docno(r.get("发文字号", "")) == nd:
                hit = r; break
        if hit is None:
            nt = re.sub(r"[《》\s]", "", cr.get("title") or "")
            if nt:
                for r in rows:
                    if nt in re.sub(r"[《》\s]", "", r.get("文件名称", "")):
                        hit = r; break
        if hit is None:
            unmatched += 1
            continue
        if st and hit.get("时效状态", "") != st:
            if not dry_run:
                hit["时效状态"] = st
            matched += 1
        elif st:
            matched += 1  # 状态一致也计入匹配（无变更）

    if not dry_run and matched:
        bak = csv_path + ".bak_" + datetime.now().strftime("%Y%m%d_%H%M%S")
        shutil.copy2(csv_path, bak)
        tmp = csv_path + ".tmp"
        with open(tmp, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(rows)
        try:
            os.replace(tmp, csv_path)
        except OSError:
            with open(csv_path, "w", encoding="utf-8-sig", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=fieldnames)
                w.writeheader()
                w.writerows(rows)
    return matched, unmatched, csv_path
