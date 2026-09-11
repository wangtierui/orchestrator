# -*- coding: utf-8 -*-
"""
rfn.registry — 监管文件注册/入库接口（去重键值 ≡ 监管文件编号 + 文件指纹 + sync_status）

符合《监管文件查询调用·标准操作规范》（2026-08-26 用户固化，2026-08-31 重构）：
  1) 唯一键：以**发文字号**为唯一键；无文号/非标文号文件以「文件名+文件来源+发布日期」的 MD5 为唯一键。
     （2026-08-31 重构：去重键值 ≡ 监管文件编号，统一名称「监管文件编号」，不再有独立 seq/唯一键概念）
  2) 编码规则：监管文件编号 = "RFN-" + md5(去重键值).hexdigest()[:16]
     去重键值 = 严格文号正则命中 → "DOC:" + 归一化文号 + "|" + md5(归一化标题)[:8]
               否则 → "MD5:" + md5(归一化标题 | 文件来源 | 发布日期)
  3) 防并发冲突：注册前先按唯一键查重——已存在 → 直接复用现有编号；不存在 → 写入并生成唯一编号。
  4) 文件指纹：登记 sha256 指纹，防同一文件重复摄入。
  5) 来源枚举：文件来源 ∈ {gov, mof, nfra, pbc, supp}，非法值拒绝写入。
  6) sync_status：写入成功后按「数据底座 → 文件归属表、横向整合分析报告、纵向深化分析报告、
     全景分析报告」层级顺序同步；任一写入失败则回滚并记 sync_status="pending" 重试。

用法：
  from rfn.registry import register_doc, unique_key, lookup, sync_status_set, pending_syncs, rebuild_index

  res = register_doc(theme="T1", title="xxx", docno="银保监办发〔2019〕19号",
                     pub_date="2019-02-26", source="nfra", fingerprint="sha256hex")
  # res = {"rfn": "RFN-3f7a9c2e8b1d4056", "action": "reused"|"created", "sync": {...}}

唯一事实源纪律：
  - 权威数据 = regulatory_classifier/data/人身保险公司-文件归属表.csv（只读基准）
    + regulatory_classifier/data/人身保险公司-主题归属表.csv（主题权威）
  - 本模块仅做「查重 + 追加 + 状态记录」，不修改已有记录；追加后索引经 get_index() 重建
  - 防并发：msvcrt 文件锁（Windows）
"""
import csv
import hashlib
import json
import os
import re
import sys

# P4（2026-09-08）：同仓 root 引导——注入 orchestrator 根（取 std_lib/common_lib）与
# classifier 模块根（import rfn 包）。原仓 sys.path 盘符插入已去除（R4/Q3）。
_PKG = os.path.dirname(os.path.abspath(__file__))               # modules/.../rfn/
_MOD_ROOT = os.path.dirname(_PKG)                                # modules/regulatory_classifier/
_ORCH_ROOT = os.path.dirname(os.path.dirname(_MOD_ROOT))          # orchestrator 根
for _p in (_ORCH_ROOT, _MOD_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# 单一事实源：现行主题映射（来自 rfn/__init__.py，禁止本地重复定义）。
from rfn import THEME_MAP  # noqa: E402

# 共享件收口（P4）：原子写 / 指纹 / 归一化 由 std_lib.common_lib 提供（专项三）。
from std_lib.common_lib.fs_lock import WinFileLock  # noqa: E402
from std_lib.common_lib.io_atomic import (
    fingerprint as _fp_fingerprint,  # noqa: E402  (模块尾兼容导出)
)

_PKG_DIR = os.path.dirname(os.path.abspath(__file__))          # regulatory_classifier/rfn/
ROOT = os.path.dirname(_PKG_DIR)                                # regulatory_classifier/
_DEFAULT_CSV = os.path.join(ROOT, "data", "人身保险公司-文件归属表.csv")
_DEFAULT_THEME_CSV = os.path.join(ROOT, "data", "人身保险公司-主题归属表.csv")

LEGAL_SOURCES = {"gov", "mof", "nfra", "pbc", "supp"}
# 严格文号正则：机构名 + (年份) + (第)序号 + 号（2026-08-31 重构，替代 len>=5 宽松判定）
DOC_RE = re.compile(r"^[\u4e00-\u9fa5]{2,20}(?:[〔\[（(]\d{4}[〕\]）)]|年)?\s*第?\d{1,4}\s*号$")


def _csv_path():
    """归属表路径：优先环境变量 RFN_REGISTRY_CSV（测试/临时场景），默认权威表。"""
    return os.environ.get("RFN_REGISTRY_CSV") or _DEFAULT_CSV


def _theme_csv_path():
    return os.environ.get("RFN_REGISTRY_THEME") or _DEFAULT_THEME_CSV


def _fp_path():
    return os.environ.get("RFN_REGISTRY_FP") or os.path.join(_PKG_DIR, "文件指纹.csv")


def _sync_path():
    return os.environ.get("RFN_REGISTRY_SYNC") or os.path.join(_PKG_DIR, "sync_status.json")
LOCK_FILE = os.path.join(_PKG_DIR, ".registry.lock")            # 防并发锁

# 文件归属表 8 列（2026-08-31 重构：无主题/同文件主编号，新增判定日期）
CSV_FIELDS = ["监管文件编号", "文件名称", "发文字号", "发布日期",
              "文件来源", "时效状态", "判定日期", "编号备注"]
# 主题归属表 3 列
THEME_FIELDS = ["监管文件编号", "主题", "判定依据"]

# 层级同步顺序（规范要求）
SYNC_LAYERS = ["数据底座", "文件归属表", "横向整合分析报告", "纵向深化分析报告", "全景分析报告"]


# 归一化收口（P4）：文号归一由 std_lib.common_lib.norm 提供（语义逐字一致：去括号空白 + 去尾号）。
from std_lib.common_lib.norm import norm_docno as _norm_docno_shared  # noqa: E402

norm_docno = _norm_docno_shared  # 保留公共符号（旧 import 兼容）；真实实现单一来源


def _strict_docno(docno):
    """严格文号判定：匹配则返回归一化文号，否则返回 ''（回落 MD5 路径）。"""
    d = (docno or "").strip()
    return norm_docno(d) if DOC_RE.match(d) else ""


# norm-specialization: RFN 事实源内部（去标点+lower 自有语义），防索引漂移
def _norm_title(t):
    # 私有标题归一（保留 registry 原语义——注意与 common_lib.norm.norm_title 的差异：
    # 本函数会去除书名号/全部标点并转小写，用于唯一键标题指纹；勿改，防既有 RFN 漂移）
    return re.sub(r'[\s（）()、《》"\'，。：:；;,.!！?？、_/\-]', "", (t or "")).lower()


def unique_key(docno=None, title="", source="", pub_date=""):
    """去重键值（2026-08-31 重构：与监管文件编号一一对应，统一名称「监管文件编号」）。

    - 有严格文号 → "DOC:" + 归一化文号 + "|" + md5(归一化标题)[:8]（同文号多子文件用标题指纹区分）
    - 无/非标文号 → "MD5:" + md5(归一化标题 | 文件来源 | 发布日期)
    """
    d = _strict_docno(docno)
    if d:
        return "DOC:" + d + "|" + hashlib.md5(_norm_title(title).encode("utf-8")).hexdigest()[:8]
    raw = "|".join([_norm_title(title), (source or "").strip(), (pub_date or "").strip()])
    return "MD5:" + hashlib.md5(raw.encode("utf-8")).hexdigest()


def rfn_of(uk):
    """由去重键值派生监管文件编号：RFN- + md5(去重键值) 前 16 位。"""
    return "RFN-" + hashlib.md5(uk.encode("utf-8")).hexdigest()[:16]


def _lock():
    """获取进程锁（P4：委托 common_lib.WinFileLock，msvcrt 阻塞语义不变）。返回已 acquire 的实例。"""
    lk = WinFileLock(LOCK_FILE)
    lk.acquire()
    return lk


def _unlock(fh):
    fh.release()


def _load_csv_rows(path, required=False):
    """读 CSV（utf-8-sig + DictReader）→ list[dict]；required=True 时文件缺失抛 FileNotFoundError。

    #4 共享 helper（2026-09-01）：统一 _load_rows/_load_theme_rows/_load_fp 三处重复的读取逻辑。
    """
    if not os.path.exists(path):
        if required:
            raise FileNotFoundError(path)
        return []
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def _save_csv_rows(path, rows, fieldnames):
    """原子写 CSV（临时文件 + os.replace；失败回退直接写）。

    #4 共享 helper（2026-09-01）：统一 _save_rows/_save_theme_rows/_save_fp 三处重复的原子写逻辑
    （含 rebuild_index 原 try/except 双分支回退）。
    """
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    try:
        os.replace(tmp, path)
    except OSError:
        with open(path, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(rows)


def _now():
    """当前时间戳（YYYY-MM-DD HH:MM:SS），registry 全部时间戳统一入口。"""
    import datetime
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _load_rows():
    # 归属表为唯一事实源：缺失即异常（required=True，保持原语义）
    return _load_csv_rows(_csv_path(), required=True)


def _load_theme_rows():
    return _load_csv_rows(_theme_csv_path())


def _save_rows(rows):
    _save_csv_rows(_csv_path(), rows, CSV_FIELDS)


def _save_theme_rows(rows):
    _save_csv_rows(_theme_csv_path(), rows, THEME_FIELDS)


def lookup(docno=None, title="", source="", pub_date=""):
    """按去重键值查重（防并发冲突）。命中 → 返回现有记录 dict；未命中 → None。"""
    uk = unique_key(docno, title, source, pub_date)
    rfn = rfn_of(uk)
    for r in _load_rows():
        if r.get("监管文件编号") == rfn:
            return r
    return None


def register_doc(theme, title, docno=None, pub_date="", source="", fingerprint="", source_mark=""):
    """幂等注册监管文件（2026-08-31 重构版）：
    1) 去重键值查重 → 已存在返回现有编号（reused）
    2) 不存在 → 由去重键值派生 RFN，追加文件归属表 + 主题归属表，登记指纹，重建索引，
       按层级写 sync_status
    3) 返回结果 dict（含 sync 层级状态）

    校验：source 必须在五源枚举 {gov,mof,nfra,pbc,supp}，否则抛 ValueError。
    注：organ（发布机构）入参已移除——归属表无该列，无文号唯一键改用「文件来源」。
    """
    if source and source not in LEGAL_SOURCES:
        raise ValueError("文件来源必须 ∈ %s，收到: %r" % (sorted(LEGAL_SOURCES), source))
    src = source or "supp"
    theme_full = THEME_MAP.get(theme, theme)
    uk = unique_key(docno, title, src, pub_date)
    rfn = rfn_of(uk)
    fh = _lock()
    try:
        exist = lookup(docno, title, src, pub_date)
        if exist:
            _sync_status_write(exist["监管文件编号"], "registry", "reused",
                               note="去重键值命中复用（监管文件编号）: %s" % rfn)
            return {"rfn": exist["监管文件编号"], "action": "reused",
                    "sync": _sync_status_read(exist["监管文件编号"])}

        rows = _load_rows()
        rows.append({
            "监管文件编号": rfn, "文件名称": title,
            "发文字号": docno or "", "发布日期": pub_date or "",
            "文件来源": src, "时效状态": "pending", "判定日期": "",   # M-01/D-01：登记即 pending（未核验显式标注），禁止空值致下游默认 valid
            "编号备注": "registry自动登记 %s" % os.path.basename(__file__),
        })
        _save_rows(rows)

        # 主题归属表同步写入
        trows = _load_theme_rows()
        trows.append({"监管文件编号": rfn, "主题": theme_full, "判定依据": "registry自动登记"})
        _save_theme_rows(trows)

        # 指纹登记（防重复摄入；唯一键=去重键 uk，对齐 FP_FIELDS 6 列）
        frows = _load_fp()
        frows.append({"唯一键": uk, "监管文件编号": rfn, "文件名称": title,
                      "发文字号": docno or "", "文件指纹": fingerprint or "",
                      "登记时间": _now()})
        _save_fp(frows)

        # 重建索引（归属表 → 索引；失败不阻断归属表写入）
        try:
            rebuild_index()
        except OSError as _e:
            print(f"[register_doc] WARN 索引重建失败（归属表已写入，可稍后重跑 rebuild_index）: {_e}")

        _sync_status_write(rfn, "文件归属表", "ok", note="归属表已写入（registry）")
        for _layer in ("数据底座", "横向整合分析报告", "纵向深化分析报告", "全景分析报告"):
            _sync_status_write(rfn, _layer, "pending",
                               note="待 build 管线按层级顺序同步（register_doc 仅完成归属表+索引重建）")
        return {"rfn": rfn, "action": "created", "sync": _sync_status_read(rfn)}
    finally:
        _unlock(fh)


def re_theme(rfn: str, new_theme: str, reason: str = "人工改判"):
    """R7 治理入口（2026-09-08）：修改既有 RFN 的主题归属（主题归属表行）并联动重建。

    补审计缺口「registry 仅 append、无改既有 RFN 主题路径」——主题改判后 base/final/明细
    归组迁移由调用方随后触发 classify 底座链重建（本函数写 sync_status 标记 pending）。

    返回 dict：{rfn, from_theme, to_theme, updated}。
    """
    if new_theme not in THEME_MAP:
        raise ValueError("new_theme 必须是主题码 T0..T10，收到 %r" % new_theme)
    fh = _lock()
    try:
        trows = _load_theme_rows()
        hit = None
        for t in trows:
            if t.get("监管文件编号") == rfn:
                hit = t
                break
        if hit is None:
            raise LookupError(f"RFN {rfn} 不在主题归属表（无此记录）")
        old_full = hit.get("主题", "")
        hit["主题"] = THEME_MAP[new_theme]
        hit["判定依据"] = f"{reason}（re_theme {_now()}）"
        _save_theme_rows(trows)
        try:
            rebuild_index()
        except OSError as _e:
            print(f"[re_theme] WARN 索引重建失败（主题已改）: {_e}")
        _sync_status_write(rfn, "数据底座", "pending",
                           note=f"主题改判 {old_full}→{THEME_MAP[new_theme]}，需 classify 底座链重建")
        return {"rfn": rfn, "from_theme": old_full, "to_theme": THEME_MAP[new_theme], "updated": True}
    finally:
        _unlock(fh)


# 文件指纹表 6 列（唯一定义，_save_fp 单处引用；2026-09-08 对齐实际表头含历史「唯一键」列，
# 修 FP_FIELDS(5) 与 文件指纹.csv(6) 契约漂移——register_doc append 同时补唯一键）
FP_FIELDS = ["唯一键", "监管文件编号", "文件名称", "发文字号", "文件指纹", "登记时间"]


def _load_fp():
    return _load_csv_rows(_fp_path())


def _save_fp(rows):
    _save_csv_rows(_fp_path(), rows, FP_FIELDS)


# 索引 CSV 列（2026-08-31 重构：主题由主题归属表合并；2026-09-01 #4 去恒空「同文件主编号」列）
INDEX_COLS = ["监管文件编号", "主题", "文件名称", "发文字号", "发布日期", "时效状态"]


def rebuild_index():
    """由归属表（唯一事实源）+ 主题归属表重建 rfn/监管文件编号索引.csv。

    - 幂等：纯派生，不携带额外信息；register_doc 新建后自动调用；
    - 失败时抛出 OSError，由调用方决定是否中断（register_doc 内已 try/except 降级）。
    """
    rows = _load_rows()
    tmap = {r["监管文件编号"]: r["主题"] for r in _load_theme_rows()}
    idx_path = os.environ.get("RFN_REGISTRY_INDEX") or os.path.join(_PKG_DIR, "监管文件编号索引.csv")
    idx_rows = [{
        "监管文件编号": r.get("监管文件编号", ""),
        "主题": tmap.get(r.get("监管文件编号", ""), ""),
        "文件名称": r.get("文件名称", ""),
        "发文字号": r.get("发文字号", ""),
        "发布日期": r.get("发布日期", ""),
        "时效状态": r.get("时效状态", ""),
    } for r in rows]
    _save_csv_rows(idx_path, idx_rows, INDEX_COLS)
    return len(rows)


def _sync_status_path():
    return _sync_path()


def _sync_status_read(rfn=None):
    data = {}
    sp = _sync_path()
    if os.path.exists(sp):
        with open(sp, encoding="utf-8") as fh:
            data = json.load(fh)
    if rfn:
        return data.get(rfn, {"status": "pending", "layers": {}})
    return data


def _sync_status_write(rfn, layer, status, note=""):
    data = _sync_status_read()
    rec = data.setdefault(rfn, {"status": "pending", "layers": {}})
    rec["layers"][layer] = {"status": status, "note": note, "updated_at": _now()}
    if layer == "registry" and status == "created":
        rec["status"] = "pending"
    elif status == "ok":
        rec["status"] = ("synced" if all(rec["layers"].get(lyr, {}).get("status") == "ok" for lyr in SYNC_LAYERS)
                         else "partial")
    with open(_sync_path(), "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)


def sync_status_set(rfn, layer, status, note=""):
    """外部调用：标记某层级同步完成/失败（layer ∈ SYNC_LAYERS 或 'registry'）。"""
    _sync_status_write(rfn, layer, status, note)
    return _sync_status_read(rfn)


def pending_syncs():
    """返回所有未完成同步（status != synced）的 RFN 及其层级状态。"""
    out = []
    for rfn, rec in _sync_status_read().items():
        if rec.get("status") != "synced":
            out.append({"rfn": rfn, "status": rec.get("status"), "layers": rec.get("layers", {})})
    return out


# 兼容导出：fingerprint 公共符号指向共享库实现（旧 from rfn.registry import fingerprint 可用）
fingerprint = _fp_fingerprint


def main():
    """CLI：python registry.py --rebuild-index  重建 监管文件编号索引.csv（README step 2）。"""
    import argparse
    ap = argparse.ArgumentParser(description="rfn.registry 维护工具")
    ap.add_argument("--rebuild-index", action="store_true", help="由归属表重建 监管文件编号索引.csv")
    ap.add_argument("--pending", action="store_true", help="列出所有未完成层级同步的 RFN")
    args = ap.parse_args()
    if args.rebuild_index:
        n = rebuild_index()
        print(f"[registry] 索引已由归属表重建：{n} 条")
    if args.pending:
        for p in pending_syncs():
            print(f"  {p['rfn']} | {p['status']} | {list(p['layers'].keys())}")
    if not args.rebuild_index and not args.pending:
        ap.print_help()


if __name__ == "__main__":
    main()
