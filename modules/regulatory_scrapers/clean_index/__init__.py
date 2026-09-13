# -*- coding: utf-8 -*-
"""
clean_index — 五源 clean 产物结构化索引（单一事实源）

定位：
    本模块是 regulatory_scrapers 五个数据源 cleaned 产物的**唯一索引事实源**。
    所有下游（regulatory_classifier 的分类 / 引用 / 匹配任务）获取五源 clean 数据
    路径、时间戳、记录数、元数据一律经本模块接口，**禁止在下游自行 glob / 硬编码路径**。

设计对标：
    regulatory_classifier/rfn/ 的 get_index() 单例模式——持久化索引 + 稳定访问层。

持久化：
    index.json（UTF-8，可直接被程序读取），由 build_clean_index.py 生成。
    get_clean_index() 优先加载 index.json（零扫描、稳定）；缺失时回退扫描并写入。

可移植性纪律（2026-09-13）：
    index.json 是**派生数据、不入 git**（见 .gitignore），因为其内容内嵌绝对路径
    （scraper_root + 各快照 path）。若入库，异机克隆后会直接复用他机路径，产生两类
    故障：①同机克隆读到**原机数据**（伪造成功）；②异机拿到必然不存在（或意外命中
    同名目录）的死路径。故加载时必须做**归属校验 + 存活性校验**（_index_is_usable），
    任一不过即视为陈旧索引并自动重建（自愈），绝不静默沿用。

稳定接口（下游直接调用，无需转换）：
    from clean_index import get_clean_index
    idx = get_clean_index()
    idx.latest_csv_path("gov")            # -> 最新快照 csv 绝对路径
    idx.latest_jsonl_path("nfra")
    idx.snapshot("pbc", "20260829")       # -> 指定快照 dict
    idx.iter_snapshots()                  # -> 遍历所有 (source_id, date, snap)
    idx.all_active_csv()                  # -> 最新快照 csv 清单（分类任务主入口）
    idx.validate_files()                  # -> 缺失/哈希失配检查
    idx.is_fresh()                        # -> 当前磁盘签名vs持久化是否一致
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone

# --------------------------------------------------------------------------- #
# 配置：五源标识 -> 抓取子目录 / 中文名 / 数据归属
# 注：source_id 与 unified_schema.SOURCE_SET 严格对齐（gov/mof/nfra/pbc/supp）
# --------------------------------------------------------------------------- #
SOURCE_CONFIG: dict[str, dict[str, str]] = {
    "gov":   {"subdir": "gov_regulations_scraper",   "name": "国务院及地方政府规章/法规库（gov.cn 体系）"},
    "mof":   {"subdir": "mof_regulations_scraper",   "name": "财政部（MoF）"},
    "nfra":  {"subdir": "nfra_regulations_scraper",  "name": "国家金融监督管理总局（原银保监会）"},
    "pbc":   {"subdir": "pbc_regulations_scraper",   "name": "中国人民银行（PBoC）"},
    "supp":  {"subdir": "supplementary_regulations_scraper", "name": "补充法规库（多源归集）"},
}

SCHEMA_VERSION = "1.0.0"
CLEANED_FILE_RE = re.compile(r"^(?P<src>[A-Za-z]+)_cleaned_(?P<date>\d{8})\.(?P<ext>csv|jsonl)$")

# 索引文件与包同目录；SCRAPER_ROOT 为包的上一级（regulatory_scrapers）
_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
INDEX_PATH = os.path.join(_PKG_DIR, "index.json")
SCRAPER_ROOT = os.path.dirname(_PKG_DIR)

_TZ = timezone(timedelta(hours=8))  # Asia/Shanghai，与项目时间基准一致

LOG = logging.getLogger("clean_index")


# --------------------------------------------------------------------------- #
# 底层工具
# --------------------------------------------------------------------------- #
def _as_posix(p: str) -> str:
    return os.path.normpath(p).replace("\\", "/")


def _utc8_now() -> str:
    return datetime.now(_TZ).strftime("%Y-%m-%dT%H:%M:%S%z")


def _sha256_file(path: str, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _count_csv(path: str):
    """返回 (记录数, 各元数据列去重集合)。CSV 为 UTF-8 BOM。"""
    csv.field_size_limit(1 << 30)  # body_text 等字段可能远超默认 128KB 上限
    distinct: dict[str, set] = {}
    cols = ["source", "timeliness_status", "doc_type", "category"]
    n = 0
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        idx = {c: header.index(c) for c in cols if header and c in header} if header else {}
        for row in reader:
            if not row or (len(row) == 1 and not row[0].strip()):
                continue
            n += 1
            for c, i in idx.items():
                if i < len(row) and row[i] not in ("", "N/A", None):
                    distinct.setdefault(c, set()).add(row[i])
    return n, {k: sorted(v) for k, v in distinct.items()}


def _count_jsonl(path: str) -> int:
    n = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                n += 1
    return n


def _companion_docs(cleaned_dir: str, date: str) -> list[str]:
    """收集同源同日的配套文档（数据字典/合规记录/测试报告/表格抽取报告/README）。"""
    out = []
    if not os.path.isdir(cleaned_dir):
        return out
    for name in os.listdir(cleaned_dir):
        if name == "README.md":
            out.append(_as_posix(os.path.join(cleaned_dir, name)))
            continue
        if name.lower().endswith((".csv", ".jsonl")):
            continue
        # 形如 gov_数据字典_20260820.md / table_extraction_report_20260820.json
        if f"_{date}." in name:
            out.append(_as_posix(os.path.join(cleaned_dir, name)))
    return sorted(out)


# --------------------------------------------------------------------------- #
# 扫描：构建索引结构（不含信封）
# --------------------------------------------------------------------------- #
def _cleaned_dirs_for(scraper_root: str, subdir: str) -> list[str]:
    """候选 cleaned 目录：仅统一 data/cleaned/（2026-09-04 收敛）。

    收敛前提：gov/mof/nfra/pbc 清洗已于 2026-09-04 重跑并补齐统一
    data/cleaned/ 的 0904 快照（此前 4/5 源 cleaned 仅存于各源 per-source，
    union 扫描掩盖了该缺口）；各源旧 per-source data/cleaned 退出索引。
    """
    return [
        os.path.join(scraper_root, "data", "cleaned"),
    ]


def scan_sources(scraper_root: str = SCRAPER_ROOT, *, hash_files: bool = True) -> dict:
    """扫描五源 cleaned 目录（新旧路径并集），返回 sources 结构字典。"""
    sources: dict[str, dict] = {}
    for src_id, cfg in SOURCE_CONFIG.items():
        cleaned_dirs = _cleaned_dirs_for(scraper_root, cfg["subdir"])
        cleaned_dir = cleaned_dirs[0]
        snapshots: dict[str, dict] = {}
        if not any(os.path.isdir(d) for d in cleaned_dirs):
            sources[src_id] = {
                "source_id": src_id,
                "source_name": cfg["name"],
                "scraper_subdir": cfg["subdir"],
                "cleaned_dir": _as_posix(cleaned_dir),
                "cleaned_dirs": [_as_posix(d) for d in cleaned_dirs],
                "snapshots": {},
                "latest_date": None,
                "latest": None,
                "total_record_count": 0,
            }
            continue

        # 2026-09-04 收敛后仅统一 data/cleaned（各源 0904 快照已补齐）
        _entries: list[tuple] = []
        for _cd in cleaned_dirs:
            if os.path.isdir(_cd):
                _entries.extend((_cd, _n) for _n in os.listdir(_cd))
        for cleaned_dir, name in _entries:
            m = CLEANED_FILE_RE.match(name)
            if not m or m.group("src") != src_id:
                continue
            date = m.group("date")
            ext = m.group("ext")
            full = os.path.join(cleaned_dir, name)
            stat = os.stat(full)
            size = stat.st_size
            sha = _sha256_file(full) if hash_files else None
            rec_count = _count_csv(full) if ext == "csv" else _count_jsonl(full)
            snap = snapshots.setdefault(date, {
                "date": date,
                "files": {},
                "record_count": None,
                "record_source_values": None,
                "companion_docs": _companion_docs(cleaned_dir, date),
            })
            snap["files"][ext] = {
                "path": _as_posix(full),
                "size_bytes": size,
                "sha256": sha,
                "record_count": rec_count,
                "modified_at": datetime.fromtimestamp(stat.st_mtime, _TZ).strftime("%Y-%m-%dT%H:%M:%S%z"),
            }
            if ext == "csv":
                cnt, distinct = rec_count  # _count_csv 返回元组
                snap["files"][ext]["record_count"] = cnt
                snap["record_count"] = cnt
                snap["record_source_values"] = distinct.get("source")
                snap["timeliness_status_values"] = distinct.get("timeliness_status")
                snap["doc_type_values"] = distinct.get("doc_type")
                snap["category_values"] = distinct.get("category")

        # 计算 latest / total（total = 该源「最新快照」记录数，代表当前数据集规模；
        # 历史快照为演进 lineage，不叠加，避免多快照源重复计数）
        latest_date = max(snapshots.keys()) if snapshots else None
        total = snapshots[latest_date].get("record_count") or 0 if latest_date else 0
        latest = None
        if latest_date:
            lf = snapshots[latest_date]["files"]
            latest = {
                "date": latest_date,
                "csv": lf.get("csv", {}).get("path"),
                "jsonl": lf.get("jsonl", {}).get("path"),
                "record_count": snapshots[latest_date].get("record_count"),
            }
        sources[src_id] = {
            "source_id": src_id,
            "source_name": cfg["name"],
            "scraper_subdir": cfg["subdir"],
            "cleaned_dir": _as_posix(cleaned_dirs[0]),
            "cleaned_dirs": [_as_posix(d) for d in cleaned_dirs],
            "snapshots": snapshots,
            "latest_date": latest_date,
            "latest": latest,
            "total_record_count": total,
        }
    return sources


def build_index_dict(scraper_root: str = SCRAPER_ROOT, *, hash_files: bool = True) -> dict:
    sources = scan_sources(scraper_root, hash_files=hash_files)
    snap_count = sum(len(s["snapshots"]) for s in sources.values())
    csv_count = sum(1 for s in sources.values() for sn in s["snapshots"].values() if "csv" in sn["files"])
    jsonl_count = sum(1 for s in sources.values() for sn in s["snapshots"].values() if "jsonl" in sn["files"])
    total_rec = sum(s["total_record_count"] for s in sources.values())
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _utc8_now(),
        "generator": "regulatory_scrapers/clean_index/build_clean_index.py",
        "description": "五源 clean 产物结构化索引（单一事实源），供 regulatory_classifier 直接加载，无需额外转换。",
        "scraper_root": _as_posix(scraper_root),
        "sources": sources,
        "summary": {
            "source_count": len(sources),
            "snapshot_count": snap_count,
            "csv_count": csv_count,
            "jsonl_count": jsonl_count,
            "total_record_count": total_rec,
        },
    }


# --------------------------------------------------------------------------- #
# 访问层：CleanIndex 包装 + 单例
# --------------------------------------------------------------------------- #
class CleanIndex:
    """对持久化索引的结构化只读访问层。"""

    def __init__(self, data: dict):
        self.data = data
        self.schema_version = data.get("schema_version")
        self.generated_at = data.get("generated_at")
        self.scraper_root = data.get("scraper_root")
        self.sources: dict[str, dict] = data.get("sources", {})
        self.summary: dict = data.get("summary", {})

    # ---- 基础查询 ----
    def source_ids(self) -> list[str]:
        return list(self.sources.keys())

    def get_source(self, source_id: str) -> dict | None:
        return self.sources.get(source_id)

    def latest_date(self, source_id: str) -> str | None:
        s = self.sources.get(source_id)
        return s.get("latest_date") if s else None

    def latest(self, source_id: str) -> dict | None:
        s = self.sources.get(source_id)
        return s.get("latest") if s else None

    def latest_csv_path(self, source_id: str) -> str | None:
        l = self.latest(source_id)
        return l.get("csv") if l else None

    def latest_jsonl_path(self, source_id: str) -> str | None:
        l = self.latest(source_id)
        return l.get("jsonl") if l else None

    def snapshot(self, source_id: str, date: str) -> dict | None:
        s = self.sources.get(source_id)
        return s.get("snapshots", {}).get(date) if s else None

    def csv_path(self, source_id: str, date: str) -> str | None:
        sn = self.snapshot(source_id, date)
        return sn.get("files", {}).get("csv", {}).get("path") if sn else None

    def jsonl_path(self, source_id: str, date: str) -> str | None:
        sn = self.snapshot(source_id, date)
        return sn.get("files", {}).get("jsonl", {}).get("path") if sn else None

    def record_count(self, source_id: str, date: str | None = None) -> int | None:
        if date is None:
            s = self.sources.get(source_id)
            return s.get("total_record_count") if s else None
        sn = self.snapshot(source_id, date)
        return sn.get("record_count") if sn else None

    # ---- 遍历 ----
    def iter_snapshots(self):
        """yield (source_id, date, snapshot_dict)。"""
        for src_id, s in self.sources.items():
            for date, sn in sorted(s.get("snapshots", {}).items()):
                yield src_id, date, sn

    def all_active_csv(self) -> list[dict]:
        """分类任务主入口：每源最新快照的 csv 清单。"""
        out = []
        for src_id in self.source_ids():
            l = self.latest(src_id)
            if l and l.get("csv"):
                out.append({
                    "source_id": src_id,
                    "date": l["date"],
                    "path": l["csv"],
                    "record_count": l.get("record_count"),
                })
        return out

    def all_active_jsonl(self) -> list[dict]:
        out = []
        for src_id in self.source_ids():
            l = self.latest(src_id)
            if l and l.get("jsonl"):
                out.append({
                    "source_id": src_id,
                    "date": l["date"],
                    "path": l["jsonl"],
                    "record_count": l.get("record_count"),
                })
        return out

    def total_record_count(self) -> int:
        return self.summary.get("total_record_count", 0)

    # ---- 完整性 ----
    def validate_files(self) -> dict:
        """校验索引引用的文件是否仍存在且 sha256 一致。

        F-D05②：sha 缺失（历史降级索引）不得静默视为通过——降级 size 比对，
        并记入 degraded_no_sha（可见而不阻断；重建带 sha 后自动消除）。
        """
        missing, mismatch, ok, degraded = [], [], [], []
        for src_id, _, sn in self.iter_snapshots():
            for ext, f in sn.get("files", {}).items():
                p = f["path"]
                if not os.path.exists(p):
                    missing.append({"source_id": src_id, "ext": ext, "path": p})
                    continue
                if f.get("sha256"):
                    if _sha256_file(p) != f["sha256"]:
                        mismatch.append({"source_id": src_id, "ext": ext, "path": p})
                    else:
                        ok.append({"source_id": src_id, "ext": ext, "path": p})
                else:
                    if f.get("size_bytes") is not None and os.path.getsize(p) != f["size_bytes"]:
                        mismatch.append({"source_id": src_id, "ext": ext, "path": p,
                                         "reason": "size 失配（无 sha，降级比对）"})
                    else:
                        degraded.append({"source_id": src_id, "ext": ext, "path": p})
        return {"missing": missing, "hash_mismatch": mismatch, "ok": ok,
                "degraded_no_sha": degraded}

    def is_fresh(self) -> bool:
        """磁盘当前文件签名是否与持久化一致（存在 + size 一致）。"""
        for _, _, sn in self.iter_snapshots():
            for _ext, f in sn.get("files", {}).items():
                p = f["path"]
                if not os.path.exists(p):
                    return False
                if os.path.getsize(p) != f.get("size_bytes"):
                    return False
        return True

    # ---- 兼容垫片：替换 classifier 既有 find_cleaned() ----
    def find_cleaned(self, source_id: str) -> str | None:
        """对齐 recall_audit/classify_themes 中 find_cleaned(source) 的返回语义：
        返回该源最新快照 csv 绝对路径（无则 None）。"""
        return self.latest_csv_path(source_id)

    def __repr__(self) -> str:  # noqa: D401
        return f"<CleanIndex sources={self.summary.get('source_count')} snapshots={self.summary.get('snapshot_count')} records={self.summary.get('total_record_count')}>"


# --------------------------------------------------------------------------- #
# 单例加载 / 重建
# --------------------------------------------------------------------------- #
_cache: dict[str, CleanIndex] = {}


def _index_is_usable(idx: CleanIndex) -> tuple[bool, str]:
    """持久化索引可用性判据（2026-09-13 可移植性修复）；不通过即视为陈旧，由调用方自愈重建。

    两道校验（刻意轻量：全量哈希/尺寸比对是 validate_files() 的职责，代价高）：
      1) **归属校验** —— 索引 `scraper_root` 必须等于当前仓库的 SCRAPER_ROOT。
         拦截"索引内嵌绝对路径被别处（克隆副本/其他机器）复用"：此前 index.json 入库，
         同机克隆会直接读到**原仓数据**，把"无数据"伪造成 success。
      2) **存活性校验** —— 登记的 latest csv/jsonl 至少一个在磁盘存在。
         拦截"索引在、数据不在"，避免下游拿到必然 FileNotFoundError 的死路径。
    另：空壳索引（无任何源条目）判为不可用——数据未就绪时应走扫描，而非沿用空壳。
    """
    if os.path.normcase(_as_posix(idx.scraper_root or "")) != \
            os.path.normcase(_as_posix(SCRAPER_ROOT)):
        return False, (f"scraper_root 归属不符（索引={idx.scraper_root!r} "
                       f"当前={_as_posix(SCRAPER_ROOT)!r}）")
    if not idx.sources:
        return False, "索引无任何源条目（空壳）"
    alive = 0
    for sid in idx.source_ids():
        for p in (idx.latest_csv_path(sid), idx.latest_jsonl_path(sid)):
            if p and os.path.exists(p):
                alive += 1
    if alive == 0:
        return False, "索引登记的 latest 快照全部不在磁盘（数据未就绪，或索引来自其他机器）"
    return True, ""


def get_clean_index(*, rebuild: bool = False, scraper_root: str = SCRAPER_ROOT) -> CleanIndex:
    """加载索引单例（2026-09-13 起带**归属/存活性校验 + 自愈重建**）。

    加载顺序：
      1) 显式指定其他 scraper_root → 直接扫描（不读持久化，保持原语义）；
      2) 内存单例命中且未要求重建 → 直接返回；
      3) index.json 存在 → 仅当 _index_is_usable() 通过才采用；
         不通过（归属不符/数据不在/空壳/解析失败）→ 记 warning 并重建；
      4) 其余（文件缺失 / rebuild=True）→ 扫描并持久化。

    自愈的代价是索引不可用时退化为一次目录扫描；收益是**不再把"数据缺失"伪装成
    "数据正常"**（原实现只要 index.json 存在就沿用，失效路径会被静默传递给下游）。
    """
    global _cache
    if scraper_root != SCRAPER_ROOT:
        data = build_index_dict(scraper_root)
        _write_index(data)
        _cache["idx"] = CleanIndex(data)
        return _cache["idx"]
    if "idx" in _cache and not rebuild:
        return _cache["idx"]
    if not rebuild and os.path.exists(INDEX_PATH):
        try:
            with open(INDEX_PATH, encoding="utf-8") as f:
                idx = CleanIndex(json.load(f))
        except Exception as e:  # noqa: BLE001  索引损坏 → 重建（不阻断链路）
            LOG.warning("clean_index: index.json 解析失败，将重建：%r", e)
        else:
            ok, why = _index_is_usable(idx)
            if ok:
                _cache["idx"] = idx
                return idx
            LOG.warning("clean_index: 索引不可用（%s），将重建并覆盖", why)
    data = build_index_dict(scraper_root)
    _write_index(data)
    _cache["idx"] = CleanIndex(data)
    return _cache["idx"]


def _write_index(data: dict) -> None:
    tmp = INDEX_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, INDEX_PATH)  # 原子替换


def rebuild_index(scraper_root: str = SCRAPER_ROOT, *, hash_files: bool = True) -> CleanIndex:
    data = build_index_dict(scraper_root, hash_files=hash_files)
    _write_index(data)
    _cache["idx"] = CleanIndex(data)
    return _cache["idx"]


# 允许 classifier 侧 `from clean_index import find_cleaned` 直接调用
def find_cleaned(source_id: str) -> str | None:
    return get_clean_index().latest_csv_path(source_id)


if __name__ == "__main__":
    idx = get_clean_index()
    print(repr(idx))
    for src_id in idx.source_ids():
        print(f"  {src_id:6s} latest={idx.latest_date(src_id)} records={idx.record_count(src_id)}")
