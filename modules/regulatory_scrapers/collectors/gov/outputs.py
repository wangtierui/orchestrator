# -*- coding: utf-8 -*-
"""gov.outputs — gov 采集器**落盘层**（E2 拆分，2026-10-16）：主库/产物/统计的写出。

包含三类：
  · 主库视图：`MasterView` / `load_resume` / `master_path` / `_find_master` /
    `_iter_master_records` / `master_index_path`（N-200 起统一走旁路索引 + 流式迭代）；
  · 产物写出：`write_outputs` / `merge_and_write` / `write_master_index` /
    `build_master_index_streaming` / `_meta_line` / `_iter_json_array_records`；
  · 统计写出：`collect_stats_path` / `write_collect_stats` / `_append_stats_history`。

依赖方向：config（类型与列契约）← outputs ← parse。搬移纪律同 extract 层（逐字搬运）。
"""

import os as _os
import sys as _sys
from collections.abc import Iterator
from typing import Any

_HERE = _os.path.dirname(_os.path.abspath(__file__))
# 三条路径都要：仓根（`std_lib`/`config` 等包）、上层 `collectors`、**本目录**
# （兄弟模块裸名回退所需——被 `importlib` 按路径单独加载时本目录不在 sys.path）。
for _p in (_HERE,
           _os.path.abspath(_os.path.join(_HERE, "..", "..", "..", "..")),
           _os.path.dirname(_HERE)):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)
del _p, _os, _sys, _HERE

import csv
import json
import logging
import os
from datetime import datetime

# 依赖导入**双形态**（W4 L1 重组纪律）。
try:  # 包形态
    from .scrape_config import CSV_COLUMNS, ScrapeConfig
except ImportError:  # 脚本形态
    from scrape_config import CSV_COLUMNS, ScrapeConfig  # type: ignore[no-redef]

LOG = logging.getLogger("gov_scraper")


# --------------------------------------------------------------------------- #
# 主库视图
# --------------------------------------------------------------------------- #
MASTER_NAME = "gov_laws.jsonl"

MASTER_NAME_LEGACY = "gov_laws.json"


def master_path(out_dir: str) -> str:
    """gov 主库**写入**路径（JSONL，两子源合并落点、唯一事实源）。"""
    return os.path.join(out_dir, MASTER_NAME)


def _find_master(out_dir: str) -> str:
    """实际存在的主库文件（JSONL 优先，回退旧 `.json`；都不存在 → 返回 JSONL 路径）。"""
    p = master_path(out_dir)
    if os.path.exists(p):
        return p
    legacy = os.path.join(out_dir, MASTER_NAME_LEGACY)
    return legacy if os.path.exists(legacy) else p


def _iter_master_records(out_dir: str) -> Iterator[dict[str, Any]]:
    """**流式**迭代主库记录（JSONL 逐行 / 旧 `.json` 用 `raw_decode` 逐条）——峰值与单条同阶。

    统一入口：`MasterView.records()` 与 `build_master_index_streaming()` 都经此迭代，
    避免"两处各写一套读法"（本仓一贯的 SSOT 纪律）。
    """
    p = _find_master(out_dir)
    if not os.path.exists(p):
        return
    if p.endswith(".jsonl"):
        with open(p, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                rec = json.loads(line)
                if isinstance(rec, dict) and len(rec) == 1 and "_meta" in rec:
                    continue                      # 信封行
                yield rec
        return
    yield from _iter_json_array_records(p)


def _iter_json_array_records(path: str) -> Iterator[dict[str, Any]]:
    """旧 `.json` 主库的**流式**逐条迭代（`raw_decode`；不构造整表副本）。"""
    dec = json.JSONDecoder()
    with open(path, encoding="utf-8") as f:
        buf = ""
        start = -1
        while start < 0:
            chunk = f.read(1 << 20)
            if not chunk:
                return
            buf += chunk
            start = buf.find('"records"')
            if start >= 0:
                b = buf.find("[", start + 9)
                if b >= 0:
                    buf = buf[b + 1:]
                    break
                start = -1
        while True:
            buf = buf.lstrip()
            if buf.startswith(","):
                buf = buf[1:]
                continue
            if buf.startswith("]"):
                return
            if not buf:
                chunk = f.read(1 << 22)
                if not chunk:
                    return
                buf += chunk
                continue
            try:
                rec, end = dec.raw_decode(buf)
            except ValueError:
                chunk = f.read(1 << 22)
                if not chunk:
                    return
                buf += chunk
                continue
            if isinstance(rec, dict):
                yield rec
            buf = buf[end:]


def master_index_path(out_dir: str) -> str:
    """主库**旁路索引**路径（N-200）。"""
    return os.path.join(out_dir, "gov_laws.index.json")


class MasterView:
    """主库只读视图（N-200）：**索引优先**，需要正文时才全量解析，且**整个进程只解析一次**。

    背景（实测）：原实现 `load_resume` 与 `merge_with_master` **各解析一次** 1GB 主库
    ⇒ 同一进程两轮 6GB 级瞬时峰值（`collect:zhengceku` 实测触顶被中止）。
    本类把「跳过集判定」与「合并所需记录」收敛到同一份解析结果，并在索引可用时**完全不解析正文**。
    """

    def __init__(self, out_dir: str):
        self.out_dir = out_dir
        self.from_index = False
        self._records: list[dict[str, Any]] | None = None
        self._seen: set | None = None
        self._detailed: set | None = None

    def _read_index(self) -> dict | None:
        # N-206：主库可能仍是旧 `.json`（迁移期）⇒ 用 `_find_master` 定位实际文件，
        # 保证索引的 size/mtime 校验对准**真实读取对象**（否则恒判失效 → 每次重建，白付开销）。
        ip, mp = master_index_path(self.out_dir), _find_master(self.out_dir)
        if not os.path.exists(mp):
            return None
        if not os.path.exists(ip):
            # N-200b：索引缺失（首次/被删）→ **流式重建**（峰值≈单条记录），避免为判定跳过集
            # 而全量解析 1GB 主库（实测 6.2GB 瞬时峰值）。
            try:
                if not build_master_index_streaming(self.out_dir):
                    return None
            except Exception as e:  # noqa: BLE001  重建失败 → 回退全量解析（安全侧）
                LOG.warning("【resume】旁路索引流式重建失败 → 回退全量解析：%s", e)
                return None
        try:
            with open(ip, encoding="utf-8") as f:
                d = json.load(f)
            st = os.stat(mp)
            if (int(d.get("master_size") or -1) != st.st_size
                    or int(d.get("master_mtime") or -1) != int(st.st_mtime)
                    or int(d.get("count") or -1) != len(d.get("entries") or [])):
                LOG.info("【resume】旁路索引与主库不一致（size/mtime/count）→ 流式重建")
                try:
                    if not build_master_index_streaming(self.out_dir):
                        return None
                except Exception as e:  # noqa: BLE001
                    LOG.warning("【resume】旁路索引流式重建失败 → 回退全量解析：%s", e)
                    return None
                with open(ip, encoding="utf-8") as f:
                    d = json.load(f)
            return d
        except Exception as e:  # noqa: BLE001  索引只是加速器：失败必须回退而非阻断
            LOG.warning("【resume】旁路索引读取失败 → 回退全量解析：%s", e)
            return None

    def keys(self) -> tuple[set, set]:
        """(seen, detailed)——seen=`(title, url)` 全集；detailed=**已有正文**的 url 集。"""
        if self._seen is not None and self._detailed is not None:
            return self._seen, self._detailed
        d = self._read_index()
        if d is not None:
            seen: set = set()
            detailed: set = set()
            for ent in d.get("entries") or []:
                url = ent[0] if len(ent) > 0 else ""
                title = ent[1] if len(ent) > 1 else ""
                has = ent[2] if len(ent) > 2 else 0
                seen.add((title, url))
                if has and url:
                    detailed.add(url)
            self.from_index = True
            LOG.info("【resume】命中旁路索引：%d 条（其中 %d 条已有正文；**未解析主库正文**）",
                     len(seen), len(detailed))
            self._seen, self._detailed = seen, detailed
            return seen, detailed
        seen, detailed = set(), set()
        for r in self.records():
            seen.add((r.get("title", ""), r.get("detail_url", "")))
            if r.get("full_text"):
                detailed.add(r.get("detail_url", ""))
        LOG.info("【resume】已从 %s 加载 %d 条已抓条目（其中 %d 条已有正文）",
                 os.path.basename(master_path(self.out_dir)), len(seen), len(detailed))
        self._seen, self._detailed = seen, detailed
        return seen, detailed

    def records(self) -> list[dict[str, Any]]:
        """全量记录（**流式**读取并缓存复用；主库缺失/损坏 → 空表，由调用方按现状处理）。

        N-206：改用 `_iter_master_records`（JSONL 逐行 / 旧 `.json` 逐条 raw_decode）——
        原实现 `json.load` 对 1GB 主库有 6× 瞬时峰值；现峰值与**单条记录**同阶。
        """
        if self._records is None:
            if not os.path.exists(_find_master(self.out_dir)):
                self._records = []
            else:
                try:
                    self._records = list(_iter_master_records(self.out_dir))
                except Exception as e:  # noqa: BLE001
                    LOG.warning("【merge】读取现主库失败，按空表处理：%s", e)
                    self._records = []
        return self._records


def load_resume(out_dir: str, source: str):
    """读取 out_dir 下最新的同源输出 JSON，返回 (已抓条目key集合, 已有正文的detail_url集合)。
    用于 --resume 增量续抓：跳过已存在的列表条目，且对已含 full_text 的条目不再抓详情。

    N-200（2026-10-09）：**兼容入口**——等价于 `MasterView(out_dir).keys()`（旁路索引优先、
    全量解析至多一次；索引陈旧/缺失时行为与旧实现一致）。
    """
    return MasterView(out_dir).keys()


# --------------------------------------------------------------------------- #
# 产物写出
# --------------------------------------------------------------------------- #
def write_outputs(records: list[dict[str, Any]], cfg: ScrapeConfig,
                  source_label: str | None = None,
                  write_csv: bool = False) -> dict[str, str]:
    """落盘主库（**JSONL**，N-206 / S-A）与可选 CSV。

    格式（`gov_laws.jsonl`）：**首行 `_meta` 信封**（source/category/captured_at/format），
    其后**每行一条记录**（`json.dumps` 会把字符串内换行转义 ⇒ 严格一行一记录）。
    ⚠️ **记录数不入 `_meta`**：单遍流式写入无法先知总数；计数由**旁路索引** `gov_laws.index.json`
    的 `count` 承载（与主库同批原子写），需要时读索引即可（2MB 级，毫秒）。
    """
    os.makedirs(cfg.out_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    jsonl_path = master_path(cfg.out_dir)
    csv_path = os.path.join(cfg.out_dir, "gov_laws.csv")

    _tmp = jsonl_path + ".tmp"
    with open(_tmp, "w", encoding="utf-8") as f:
        f.write(_meta_line(cfg.source, cfg.category, stamp))
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(_tmp, jsonl_path)
    # N-200：与主库**同批**写旁路索引（供下次增量免解析正文）。索引是**加速器**：
    # 写入失败只告警，不影响主库正确性（下次自动回退流式重建）。
    try:
        write_master_index(cfg.out_dir, records)
    except Exception as e:  # noqa: BLE001
        LOG.warning("【resume】旁路索引写入失败（不影响主库；下次将流式重建）：%s", e)

    # CSV：仅 --csv 显式开启时输出表格视图（默认仅 JSONL 主库，2026-09-09 规范）
    if write_csv:
        _tmpc = csv_path + ".tmp"
        with open(_tmpc, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
            writer.writeheader()
            for r in records:
                writer.writerow({k: r.get(k, "") for k in CSV_COLUMNS})
        os.replace(_tmpc, csv_path)

    LOG.info("输出完成：\n  JSONL: %s%s", jsonl_path,
             ("\n  CSV  : %s" % csv_path) if write_csv else "")
    return {"json": jsonl_path, "csv": csv_path if write_csv else ""}


def _meta_line(source: str, category: str, stamp: str) -> str:
    """JSONL 首行信封（单键 `_meta`，读取侧按此跳过）。"""
    return json.dumps(
        {"_meta": {"source": source, "category": category, "captured_at": stamp,
                   "format": "jsonl/1"}},
        ensure_ascii=False,
    ) + "\n"


def merge_and_write(new_records: list[dict[str, Any]], cfg: ScrapeConfig, *,
                    source_label: str | None = None, write_csv: bool = False) -> dict[str, Any]:
    """增量合并**流式**落盘（N-206 / **S-B**）——替代"整表合并 + 整表落盘"。

    为何必需（实测）：原 `merge_with_master` 把旧主库**全量读进内存**再与新记录合并
    （1GB 级 ⇒ 与 clean 同源的 6× 瞬时峰值）。本函数：
      ① 旧主库经 `_iter_master_records` **逐条**读（JSONL 逐行 / 旧 `.json` raw_decode）；
      ② 命中同键（`detail_url` 优先、退化标题）者**用新记录替换**（新优先，语义与原实现一致）；
      ③ 逐条写 `.tmp` ⇒ **峰值 = 新记录 + 键集**，与旧主库体积解耦；④ 原子替换 + 重建索引。
    返回 `{"json", "csv", "merged", "replaced", "csv_path"}`（供调用方日志与统计）。
    """
    os.makedirs(cfg.out_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = master_path(cfg.out_dir)
    csv_path = os.path.join(cfg.out_dir, "gov_laws.csv")

    pending: dict[str, dict[str, Any]] = {}
    for r in new_records:
        pending[str(r.get("detail_url") or r.get("title") or "")] = r
    merged = replaced = 0
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as out:
        out.write(_meta_line(cfg.source, cfg.category, stamp))
        for old in _iter_master_records(cfg.out_dir):
            k = str(old.get("detail_url") or old.get("title") or "")
            if k in pending:
                out.write(json.dumps(pending.pop(k), ensure_ascii=False) + "\n")
                replaced += 1
            else:
                out.write(json.dumps(old, ensure_ascii=False) + "\n")
            merged += 1
        for r in pending.values():                    # 旧库中不存在的新条目
            out.write(json.dumps(r, ensure_ascii=False) + "\n")
            merged += 1
    os.replace(tmp, out_path)
    # 索引：从**刚落盘的主库**流式重建（与主库保证同批一致；失败只告警）
    try:
        build_master_index_streaming(cfg.out_dir)
    except Exception as e:  # noqa: BLE001
        LOG.warning("【resume】旁路索引重建失败（下次将自动重建）：%s", e)
    if write_csv:
        _tmpc = csv_path + ".tmp"
        with open(_tmpc, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
            writer.writeheader()
            for r in _iter_master_records(cfg.out_dir):
                writer.writerow({k: r.get(k, "") for k in CSV_COLUMNS})
        os.replace(_tmpc, csv_path)
    LOG.info("【merge】流式合并完成：%d 条（其中**新覆盖** %d 条，新发现 %d 条）→ %s",
             merged, replaced, len(new_records) - replaced, os.path.basename(out_path))
    return {"json": out_path, "csv": csv_path if write_csv else "", "merged": merged,
            "replaced": replaced}


def write_master_index(out_dir: str, records: list[dict[str, Any]]) -> str:
    """写主库旁路索引（与主库**同批**落盘；供下次增量**免解析 1GB 正文**）。

    为何必需（批 46 实测）：`gov_laws.json` 1132MB，`json.load` 的**峰值 RSS 6.2GB**（≈6× 体积，
    Python 对象膨胀 —— 实测 `collect:zhengceku` 与 `clean:gov` 均因此触顶）；而**周度增量只需**
    ① 哪些 `detail_url` 已抓（跳过）　　② 其中哪些**已有正文**（其余仍需补详情）。
    索引只存 `[detail_url, title, has_full_text]`（13k 条 ≈1MB）⇒ 读取毫秒级、峰值可忽略。

    **失效即回退**：索引记录主库 `size/mtime/count`，任一不符即视为陈旧 → 调用方回退全量解析
    （幂等安全：索引只是加速器，任何异常都不得改变语义）。
    """
    mp = master_path(out_dir)
    st = os.stat(mp) if os.path.exists(mp) else None
    payload = {
        "master": os.path.basename(mp),
        "master_size": st.st_size if st else 0,
        "master_mtime": int(st.st_mtime) if st else 0,
        "count": len(records),
        # 极简三元组（URL/标题/是否有正文）——刻意不存正文，索引体积与语料正文解耦
        "entries": [[r.get("detail_url") or "", r.get("title") or "", 1 if r.get("full_text") else 0]
                    for r in records],
    }
    p = master_index_path(out_dir)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    os.replace(tmp, p)
    return p


def build_master_index_streaming(out_dir: str) -> str:
    """**流式**扫描主库并重建旁路索引（峰值 ≈ 单条记录，与语料体积解耦）。

    为何必需（N-200b，2026-10-09）：索引缺失时若走 `json.load` 全量解析，会再次触发**实测 6.2GB**
    的瞬时峰值（1GB 文件 ≈6× 对象膨胀）。本函数经 `_iter_master_records` 逐条迭代
    （JSONL 逐行 / 旧 `.json` 用 `raw_decode`）⇒ 峰值与单条记录（≤1MB）同阶。
    返回索引路径（主库不存在 → 空串）。
    """
    mp = _find_master(out_dir)
    if not os.path.exists(mp):
        return ""
    entries: list[list] = []
    for rec in _iter_master_records(out_dir):
        entries.append([rec.get("detail_url") or "", rec.get("title") or "",
                        1 if rec.get("full_text") else 0])
    st = os.stat(mp)
    payload = {
        "master": os.path.basename(mp),
        "master_size": st.st_size,
        "master_mtime": int(st.st_mtime),
        "count": len(entries),
        "entries": entries,
    }
    p = master_index_path(out_dir)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    os.replace(tmp, p)
    LOG.info("【resume】旁路索引已**流式重建**：%d 条 → %s", len(entries), os.path.basename(p))
    return p


def merge_with_master(new_records: list[dict[str, Any]], out_dir: str,
                      master_records: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """增量续抓合并：现主库旧记录 ∪ 本次新条目（detail_url 去重，新优先）。

    2026-09-05：定长覆盖更新 + ``--resume`` 使本次仅产出新增条目，
    若直接覆盖写会丢失历史条目；合并后覆盖保证主库 = 历史全量 + 本周新增/更新。

    N-200（2026-10-09）：新增 `master_records` 参数 —— 调用方若已通过 `MasterView.records()`
    解析过主库（如为判定跳过集而必须全量解析时），**直接复用**，避免同一进程重复解析 1GB 主库
    （原实现 `load_resume` 与 `merge_with_master` 各解析一次 ⇒ 两轮 6GB 级瞬时峰值）。
    """
    if master_records is not None:
        old = master_records
    else:
        old = MasterView(out_dir).records()
        if not old and not os.path.exists(master_path(out_dir)):
            return new_records
    merged: dict[str, dict[str, Any]] = {}
    for r in old:
        merged[str(r.get("detail_url") or r.get("title"))] = r
    for r in new_records:
        merged[str(r.get("detail_url") or r.get("title"))] = r
    LOG.info("【merge】现主库 %d 条 + 本次 %d 条 → 合并 %d 条",
             len(old), len(new_records), len(merged))
    return list(merged.values())


# --------------------------------------------------------------------------- #
# 统计写出
# --------------------------------------------------------------------------- #
_COLLECT_STATS_REL = ("data", "run_state", "collect_stats")


def collect_stats_path(label: str) -> str:
    """采集统计文件路径：`<repo>/data/run_state/collect_stats/<label>.json`（N-190）。

    为何放这里（不是 reports/ 也不是 raw/）：
      · `data/run_state/` 是**既有运行状态目录**（`last_run_steps.json` 同处），且**未纳入 git**
        ⇒ 每次采集更新统计**不产生提交噪声**，同时给"无效时长"留下可比的历史基线；
      · **不写进 `data/raw/`**：该目录是**事实源**，统计属旁路观测，不得混入（本仓一贯纪律）。

    根路径用 `os.pardir` 拼（零转义写法）：本文件在 `collectors/`，上溯三级即仓库根。
    """
    here = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.normpath(os.path.join(here, os.pardir, os.pardir, os.pardir, os.pardir))
    return os.path.join(repo_root, *_COLLECT_STATS_REL, "%s.json" % label)


def write_collect_stats(label: str, stats: dict) -> str:
    """原子写采集统计（旁路观测；写失败**不得中断采集**）。返回路径（失败返回空串）。

    指标口径（供"无效时长"评估，字段含义见 `zhengceku.should_stop_paging` 与批 45 报告）：
      `list_pages_fetched` / `list_pages_without_new` → **空转页占比** = 后者/前者；
      `details_skipped_master` → 因"主库已有正文"而**未发请求**的条目（省下的详情请求数）；
      `elapsed_list_s` / `elapsed_detail_s` → 时长按阶段归因。
    """
    p = collect_stats_path(label)
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(stats, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
        # R-E 处置（2026-10-09）：**追加历史样本**（原实现只留最新一份 ⇒ 无法看趋势）。
        # 形状：同目录 `history/<label>.jsonl`，仅保留**紧凑指标子集**，滚动截断到最近 N 条。
        _append_stats_history(label, stats, p)
        return p
    except Exception as e:  # noqa: BLE001  旁路观测：失败只告警
        LOG.warning("【collect-stats】统计落盘失败（不影响采集）：%s", e)
        return ""


STATS_HISTORY_KEEP = 90


_STATS_HISTORY_FIELDS = ("sub_source", "list_pages_fetched", "list_pages_without_new",
                         "new_items", "pending_details", "details_fetched",
                         "details_skipped_master", "elapsed_list_s", "elapsed_detail_s",
                         "stopped_reason")


def _append_stats_history(label: str, stats: dict, latest_path: str) -> None:
    """追加一条紧凑历史样本并**滚动截断**（R-E；失败只告警，不影响采集）。"""
    hp = os.path.join(os.path.dirname(latest_path), "history", label + ".jsonl")
    os.makedirs(os.path.dirname(hp), exist_ok=True)
    st = stats.get("stats") or {}
    row = {"run_at": stats.get("run_at"), "backfill": stats.get("backfill")}
    row.update({k: st.get(k) for k in _STATS_HISTORY_FIELDS})
    lines: list[str] = []
    if os.path.exists(hp):
        with open(hp, encoding="utf-8") as f:
            lines = [x for x in f.read().splitlines() if x.strip()]
    lines.append(json.dumps(row, ensure_ascii=False))
    if len(lines) > STATS_HISTORY_KEEP:
        lines = lines[-STATS_HISTORY_KEEP:]
    tmp = hp + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, hp)
