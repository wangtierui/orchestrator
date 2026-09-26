# -*- coding: utf-8 -*-
"""ingest_corpus.py — 外部语料归集（F-L05：EAST2.0 / 部门制度 / 保单登记等"未归集数据域"）

归集纪律（架构方案 §3.5）：
  ① 先建 manifest（源路径 + 每文件 sha256/大小 + 目标域 + 时间）；
  ② 物理归集（数据不入 git；目标 `modules/regulatory_scrapers/data/corpus/<domain>/`）；
  ③ 幂等增量（已存在且 sha256 相同则跳过）。

用法：
  python tools/ingest_corpus.py --src <源目录> --domain <域标识> [--exclude <相对路径片段>]...
  # 示例（源目录经参数传入，严禁在源码/文档写盘符字面量——gate_hardcoded_paths R4）：
  #   --src "<EAST2.0 语料根>" --domain east2_m20
  #   --src "<公司各部门制度根>" --domain dept_policies
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import shutil
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
import paths  # noqa: E402  路径唯一事实源（P2-3a：语料本体落点统一）
from config.exitcodes import ExitCode  # noqa: E402

# v2 §3.12（决策 D-9 / P2-3a，2026-09-26）：语料归集本体统一到**仓根 `data/corpus/<domain>/`**。
#   改造前落 `modules/regulatory_scrapers/data/corpus/`——归属不当：该层同时服务 classifier 的
#   recall 与 internal_policy_base 的**跨层硬链接去重**，却挂在"采集"模块名下。
#   ⚠️ 迁移必须**同卷 rename**：`originals/` 中 811 个文件与其共享 inode，
#      跨卷复制会断链并复制约 296 MB（退回 2026-09-13 去重前的双份存储）。
CORPUS_DIR = paths.CORPUS_DIR
# 归集清单落 reports/corpus/（reports 入库；语料本体 data/ 不入库——清单为唯一可审计入口）
MANIFEST_DIR = os.path.join(_ROOT, "reports", "corpus")


def _sha256(p: str) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _iter_files(src: str, excludes: list[str], exclude_tops: list[str] | None = None):
    """遍历源目录。

    excludes：**相对路径片段**匹配（历史语义，慎用——片段会误伤深层同名子目录，
      如"需求文档"会命中 `00.关于修订…/2.0需求文档/`，2026-09-12 实证）；
    exclude_tops：**顶层目录名精确**匹配（预期用法：源根一级目录整棵排除）。
    """
    tops = {t for t in (exclude_tops or []) if t}
    for dirpath, dirnames, filenames in os.walk(src):
        rel_dir = os.path.relpath(dirpath, src)
        if rel_dir == "." and tops:
            dirnames[:] = [d for d in dirnames if d not in tops]  # 顶层剪枝（整棵跳过）
        dirnames[:] = [
            d for d in dirnames if not any(e and e in os.path.join(rel_dir, d) for e in excludes)
        ]
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, src)
            if tops and rel.split(os.sep)[0] in tops:
                continue
            if any(e and e in rel for e in excludes):
                continue
            yield full, rel


def _update_index(domain: str, manifest: dict) -> None:
    """同步 reports/corpus/_index.json 的域条目与总计（2026-09-12 落地；原 _index 无生成器）。"""
    idx_path = os.path.join(MANIFEST_DIR, "_index.json")
    idx: dict = {
        "schema_version": "1.0",
        "policy": "语料本体 data/corpus/<domain>/（归集·审计层，只读，不入库；"
        "为 originals/ 的硬链接目标）；清单 reports/corpus/（入库，唯一可审计入口）。"
        "v2 §3.12：投放区 data/inbox/ 与本体分离，域内不再保留清单副本",
        "domains": [],
    }
    if os.path.exists(idx_path):
        try:
            idx = json.load(open(idx_path, encoding="utf-8"))
        except (OSError, ValueError):
            pass
    domains = [d for d in (idx.get("domains") or []) if d.get("domain") != domain]
    domains.append(
        {
            "domain": domain,
            "source_dir": manifest.get("source_dir", ""),
            "file_count": manifest.get("file_count", 0),
            "total_bytes": manifest.get("total_bytes", 0),
            "ingested_at": manifest.get("ingested_at", ""),
            "excludes": manifest.get("excludes", []),
            "exclude_tops": manifest.get("exclude_tops", []),
            "manifest": f"reports/corpus/{domain}.manifest.json",
        }
    )
    idx["domains"] = sorted(domains, key=lambda d: d.get("domain", ""))
    idx["total_files"] = sum(int(d.get("file_count") or 0) for d in idx["domains"])
    idx["total_bytes"] = sum(int(d.get("total_bytes") or 0) for d in idx["domains"])
    idx["generated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    tmp = idx_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(idx, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, idx_path)


def _resync_manifest(domain: str, *, dry_run: bool = False) -> int:
    """由**磁盘本体**重建清单 `files` 段（F1 收口 / 执行中发现 N-14）。

    背景（2026-09-26 实测）：`dept_policies` 的清单 `rel` 与本体实际布局**早已不一致**——
    源侧曾含中间目录（如 `1.营销部/附件二-…/…pdf`）而本体已拍平（`1.营销部/…pdf`），
    顶层部门名亦有改名（`9.健康事业部` vs `9.健康保险事业部`）：1019 条声明 vs 1027 个实际
    文件，仅 433 条可对上（另 586 条为陈旧路径）。

    清单是"语料从哪来、有多少、校验值是什么"的**唯一审计入口**，必须与本体自洽。故以
    **本体为准**重建 `files`，并把差异（dropped/added）记入清单便于追溯；`source_dir`、
    `excludes`、`exclude_tops`、`ingested_at` 等来源侧信息原样保留。
    """
    mp = os.path.join(MANIFEST_DIR, f"{domain}.manifest.json")
    if not os.path.exists(mp):
        print(f"[ingest] 清单不存在：{mp}")
        return ExitCode.FAIL
    with open(mp, encoding="utf-8") as fh:
        man = json.load(fh)
    body = os.path.join(CORPUS_DIR, domain)
    if not os.path.isdir(body):
        print(f"[ingest] 本体不存在：{body}")
        return ExitCode.FAIL

    old_rels = {r.get("rel", "") for r in man.get("files") or []}
    files, nbytes = [], 0
    for dirpath, _dirnames, filenames in os.walk(body):
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            st = os.stat(full)
            rel = os.path.relpath(full, body).replace(os.sep, "/")
            nbytes += st.st_size
            files.append(
                {
                    "rel": rel,
                    "bytes": st.st_size,
                    "sha256": _sha256(full),
                    "mtime": datetime.datetime.fromtimestamp(st.st_mtime).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                }
            )
    files.sort(key=lambda r: r["rel"])
    new_rels = {r["rel"] for r in files}
    dropped = sorted(old_rels - new_rels)
    added = sorted(new_rels - old_rels)

    man["files"] = files
    man["file_count"] = len(files)
    man["total_bytes"] = nbytes
    man["hash_mode"] = "sha256"
    man["resynced_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    man["resync"] = {
        "declared_before": len(old_rels),
        "actual_after": len(files),
        "dropped_total": len(dropped),
        "added_total": len(added),
        "dropped_sample": dropped[:20],
        "added_sample": added[:20],
        "reason": "2026-09-26 执行发现 N-14：清单 rel 与本体布局不一致（源含中间目录、"
        "本体已拍平 + 部门改名）→ 以本体为准重建",
    }
    print(
        f"[ingest] {domain}: 清单重建 {len(old_rels)} → {len(files)} 条"
        f"（陈旧 {len(dropped)} / 新增 {len(added)} / {nbytes / 1048576:.1f} MB）"
        + ("［dry-run］" if dry_run else "")
    )
    if dry_run:
        return ExitCode.OK
    tmp = mp + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(man, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, mp)
    _update_index(man.get("domain") or domain, man)
    return ExitCode.OK


def _rebuild_index() -> int:
    """由 `reports/corpus/*.manifest.json` **重建** `_index.json`（清单为唯一事实源）。

    与 `_update_index` 的区别：后者只按域名"替换或追加"单条，**无法删除已改名的旧条目**
    （P2-3a 的 `east2_m20` → `regulatory_stats` 正属此情形 → 会出现幽灵域）。
    本函数以磁盘清单集合为准全量重建，是域改名/域退役后的收口动作。
    """
    idx_path = os.path.join(MANIFEST_DIR, "_index.json")
    domains = []
    for fn in sorted(os.listdir(MANIFEST_DIR)) if os.path.isdir(MANIFEST_DIR) else []:
        if not fn.endswith(".manifest.json"):
            continue
        try:
            with open(os.path.join(MANIFEST_DIR, fn), encoding="utf-8") as fh:
                man = json.load(fh)
        except (OSError, ValueError) as e:
            print(f"[ingest] 跳过无法解析的清单 {fn}: {type(e).__name__}: {e}")
            continue
        dom = man.get("domain") or fn[: -len(".manifest.json")]
        domains.append(
            {
                "domain": dom,
                "source_dir": man.get("source_dir", ""),
                "file_count": man.get("file_count", 0),
                "total_bytes": man.get("total_bytes", 0),
                "ingested_at": man.get("ingested_at", ""),
                "excludes": man.get("excludes", []),
                "exclude_tops": man.get("exclude_tops", []),
                "hash_mode": man.get("hash_mode", "sha256"),
                "manifest": f"reports/corpus/{fn}",
            }
        )
    idx = {
        "schema_version": "1.0",
        "policy": "语料本体 data/corpus/<domain>/（归集·审计层，只读，不入库；为 originals/ 的"
        "硬链接目标）；清单 reports/corpus/（入库，唯一可审计入口）。"
        "v2 §3.12：投放区 data/inbox/ 与本体分离，域内不再保留清单副本",
        "domains": sorted(domains, key=lambda d: d["domain"]),
        "total_files": sum(int(d["file_count"] or 0) for d in domains),
        "total_bytes": sum(int(d["total_bytes"] or 0) for d in domains),
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    os.makedirs(MANIFEST_DIR, exist_ok=True)
    tmp = idx_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(idx, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, idx_path)
    print(f"[ingest] 索引重建：{len(domains)} 域 / {idx['total_files']} 文件 → {idx_path}")
    return ExitCode.OK


def _backfill_hash(domain: str) -> int:
    """就地回填已归集语料的 sha256（F1）：修正历史 `--no-hash` 造成的空哈希。

    不需要 `--src`：哈希对象是**归集本体中的副本**（后续去重/幂等判定都以本体为准）。
    同时把 `hash_mode` 升为 `sha256`，并记录 `backfilled_at` 与缺失清单（可审计）。
    """
    mp = os.path.join(MANIFEST_DIR, f"{domain}.manifest.json")
    if not os.path.exists(mp):
        print(f"[ingest] 清单不存在：{mp}（先执行一次归集）")
        return ExitCode.FAIL
    with open(mp, encoding="utf-8") as fh:
        man = json.load(fh)
    dst_root = os.path.join(CORPUS_DIR, domain)
    filled, already, missing = 0, 0, []
    for rec in man.get("files") or []:
        tgt = os.path.join(dst_root, (rec.get("rel") or "").replace("/", os.sep))
        if not os.path.exists(tgt):
            missing.append(rec.get("rel", ""))
            continue
        if rec.get("sha256"):
            already += 1
            continue
        rec["sha256"] = _sha256(tgt)
        filled += 1
    man["hash_mode"] = "sha256"
    man["backfilled_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    man["backfill"] = {
        "filled": filled,
        "already_had_hash": already,
        "missing_total": len(missing),
        "missing_sample": missing[:20],
    }
    tmp = mp + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(man, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, mp)
    # 清单是唯一事实源 → `_index.json` 由其派生（含 domain 改名后的一致性）
    _update_index(man.get("domain") or domain, man)
    print(
        f"[ingest] {domain}: 回填 sha256 {filled} 条"
        f"（原有 {already} / 本体缺失 {len(missing)}）→ {mp}"
    )
    return ExitCode.OK


def main() -> int:
    ap = argparse.ArgumentParser(description="外部语料归集（manifest + 幂等物理归集）")
    # `--src/--domain` 不再 argparse 级 required：`--reindex` 无需二者，`--backfill-hash`
    # 只需 `--domain`。改为在各模式下显式校验（否则"就地维护"类子命令无法调用）。
    ap.add_argument("--src", default="", help="源目录（归集模式必填）")
    ap.add_argument("--domain", default="", help="域标识（目标子目录名）")
    ap.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="排除的相对路径片段（可多次；慎用——片段会误伤深层同名子目录）",
    )
    ap.add_argument(
        "--exclude-top",
        action="append",
        default=[],
        dest="exclude_tops",
        help="排除的**顶层目录名**（精确；源根一级整棵排除；可多次）",
    )
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--no-hash",
        action="store_true",
        help="跳过 sha256（大目录快速模式）；清单会记 hash_mode=size_only 使清单可自证",
    )
    ap.add_argument(
        "--reindex",
        action="store_true",
        help="由 reports/corpus/*.manifest.json 重建 _index.json（域改名/退役后收口）",
    )
    ap.add_argument(
        "--resync-manifest",
        action="store_true",
        dest="resync_manifest",
        help="由**磁盘本体**重建清单 files 段（rel/bytes/sha256/mtime；修正路径漂移）",
    )
    ap.add_argument(
        "--backfill-hash",
        action="store_true",
        dest="backfill_hash",
        help="就地回填：对**已归集**语料重算 sha256 并写回清单（无需 --src）；"
        "用于修正历史 --no-hash 造成的空哈希（F1）",
    )
    args = ap.parse_args()

    if args.reindex:
        return _rebuild_index()

    if args.resync_manifest:
        if not args.domain:
            print("[ingest] --resync-manifest 需 --domain <域标识>")
            return ExitCode.FAIL
        return _resync_manifest(args.domain, dry_run=args.dry_run)

    if args.backfill_hash:
        if not args.domain:
            print("[ingest] --backfill-hash 需 --domain <域标识>")
            return ExitCode.FAIL
        return _backfill_hash(args.domain)

    if not args.src or not args.domain:
        print(
            "[ingest] 归集模式需 --src <源目录> 与 --domain <域标识>"
            "（就地维护用 --reindex / --backfill-hash --domain <域标识>）"
        )
        return ExitCode.FAIL
    src = os.path.abspath(args.src)
    if not os.path.isdir(src):
        print(f"[ingest] 源目录不存在: {src}")
        return ExitCode.FAIL
    dst_root = os.path.join(CORPUS_DIR, args.domain)
    os.makedirs(dst_root, exist_ok=True)

    files, copied, skipped, skipped_unverified, nbytes = [], 0, 0, 0, 0
    for full, rel in _iter_files(src, args.exclude, args.exclude_tops):
        st = os.stat(full)
        nbytes += st.st_size
        sha = "" if args.no_hash else _sha256(full)
        target = os.path.join(dst_root, rel)
        rec = {
            "rel": rel.replace("\\", "/"),
            "bytes": st.st_size,
            "sha256": sha,
            "mtime": datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        }
        files.append(rec)
        if args.dry_run:
            continue
        os.makedirs(os.path.dirname(target), exist_ok=True)
        # F1 修复（v2 §3.12.7，2026-09-26）：原实现为
        # `if exists(target) and (args.no_hash or sha256(target) == sha)` —— 在 `--no-hash`
        # 下**只判同名存在**即跳过（实测两域 `skipped_existing: 0`、清单 sha 全空 →
        # "幂等增量"从未真正生效）。现语义：
        #   · 默认：**比对内容 sha256**，一致才跳过（真幂等）；
        #   · `--no-hash`：无法比对 → 仍按"存在即跳过"，但**单独计数**
        #     `skipped_existing_unverified` 并在清单记 `hash_mode=size_only`（可自证弱保证）。
        if os.path.exists(target):
            if args.no_hash:
                skipped_unverified += 1
                continue
            if _sha256(target) == sha:
                skipped += 1
                continue
        shutil.copy2(full, target)
        copied += 1

    manifest = {
        "domain": args.domain,
        "source_dir": src.replace("\\", "/"),
        "excludes": args.exclude,
        "exclude_tops": args.exclude_tops,
        "ingested_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "file_count": len(files),
        "total_bytes": nbytes,
        "copied": copied,
        "skipped_existing": skipped,
        # F1 修复：`hash_mode` 使清单**自证**其校验强度（sha256 = 内容级幂等；
        # size_only = 仅按存在性跳过，不可用于去重判定）
        "hash_mode": "size_only" if args.no_hash else "sha256",
        "skipped_existing_unverified": skipped_unverified,
        "files": files,
    }
    if not args.dry_run:
        # F2 修复（v2 §3.12.7）：清单**唯一源 = reports/corpus/**（入库、可审计）。
        # 原实现把同一份 manifest 同时写到「域内 `_ingest_manifest.json`」与 reports/，
        # 而域内那份**全仓无读方**（唯一命中即写者自身）→ 纯冗余副本，已删除。
        os.makedirs(MANIFEST_DIR, exist_ok=True)
        mp = os.path.join(MANIFEST_DIR, f"{args.domain}.manifest.json")
        tmp = mp + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, mp)
        _update_index(args.domain, manifest)  # 2026-09-12：_index.json 同步（原无生成器）
    print(
        f"[ingest] {args.domain}: {len(files)} 文件 / {nbytes / 1048576:.1f} MB"
        f"（复制 {copied} / 已存在(已校验) {skipped} / 已存在(未校验) {skipped_unverified}）"
        + ("［dry-run］" if args.dry_run else f" → {dst_root}")
    )
    return ExitCode.OK


if __name__ == "__main__":
    sys.exit(main())
