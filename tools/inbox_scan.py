# -*- coding: utf-8 -*-
"""
tools/inbox_scan.py — 投放区扫描与路由（v2 §3.12.6，P2-3b）

    scan(data/inbox/**) → 逐文件 sha256 → 按 `data/inbox/_registry.yaml` 路由
      sha256 ∈ 归集本体 ∪ originals  → 记 `duplicate`，跳过（**不得**再摄取：防自指环）
      domain=dept_policies 且为新增   → 报 `internal_update` 触发
      domain=regulatory_stats        → 报 `ingest_corpus` 触发
      扩展名不可识别（.db/.png/.zip/.rtf/.htm…）→ `needs_review`（登记 worklist），不静默丢弃
    输出：`data/inbox/_inbox_manifest.json`（sha256 / size / mtime / routed_to / decision / reason）

用法：
    python tools/inbox_scan.py            # 只扫描并打印结论（不落盘）
    python tools/inbox_scan.py --apply    # 落盘 manifest + 登记 needs_review 待办
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import paths  # noqa: E402

INBOX = paths.INBOX_DIR
# ⚠️ 落点修正（2026-09-26，P2-3b 执行中发现 N-26）：方案 §3.12.4 写 `data/inbox/_registry.yaml`，
# 但仓根 `.gitignore:11` 有 `data/`（**整目录**排除）→ 放在 data/ 下的**声明**无法入库，
# 新克隆将缺该文件。声明必须可版本化，故落在 `config/inbox_registry.yaml`（与
# schedule.yaml / triggers.yaml 同类）；`data/inbox/_registry.yaml` 如存在则作为**运行期覆盖**
# （本机特化，不入库），两者取先存在者，避免"同一清单两份"（v2 §3.12.7 F2 的教训）。
REGISTRY_CANDIDATES = (
    os.path.join(paths.CONFIG_DIR, "inbox_registry.yaml"),
    os.path.join(INBOX, "_registry.yaml"),
)
REGISTRY = REGISTRY_CANDIDATES[0]
MANIFEST = os.path.join(INBOX, "_inbox_manifest.json")
CORPUS_MANIFESTS = os.path.join(paths.ROOT, "reports", "corpus")
ORIGINALS = os.path.join(paths.MODULES_DIR, "internal_policy_base", "data", "originals")
CORPUS_ROOT = os.path.join(paths.ROOT, "data", "corpus")
SKIP_NAMES = {"_registry.yaml", "_inbox_manifest.json", ".gitkeep", ".gitignore"}


def registry_path() -> str:
    """注册表实际落点（config 优先，data/inbox 作运行期覆盖）。"""
    for p in REGISTRY_CANDIDATES:
        if os.path.exists(p):
            return p
    return REGISTRY_CANDIDATES[0]


def load_registry() -> dict:
    import yaml  # noqa: PLC0415

    with open(registry_path(), encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def sha256_of(path: str, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for blk in iter(lambda: fh.read(chunk), b""):
            h.update(blk)
    return h.hexdigest()


def _known_hashes() -> dict[str, str]:
    """已知内容哈希 → 来源标签。来源：① `reports/corpus/*.manifest.json`（唯一入库源，
    v2 §3.12.7 F2）；② `data/corpus/**` 与 `originals/**` 的**按大小候选**哈希
    （只对大小能对上的文件算 sha256 —— 全量哈希 2000+ 文件代价不可接受）。"""
    known: dict[str, str] = {}
    sizes: dict[int, list[str]] = {}

    def _walk(base: str, tag: str) -> None:
        if not os.path.isdir(base):
            return
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in ("__pycache__", ".git")]
            for fn in filenames:
                if fn in SKIP_NAMES:
                    continue
                fp = os.path.join(dirpath, fn)
                try:
                    sizes.setdefault(os.path.getsize(fp), []).append(f"{tag}:{fp}")
                except OSError:
                    continue

    if os.path.isdir(CORPUS_MANIFESTS):
        for fn in sorted(os.listdir(CORPUS_MANIFESTS)):
            if not fn.endswith(".manifest.json"):
                continue
            try:
                data = json.load(open(os.path.join(CORPUS_MANIFESTS, fn), encoding="utf-8"))
            except (OSError, ValueError):
                continue
            dom = fn[:-len(".manifest.json")]
            for key in ("files", "records", "entries", "items"):
                for rec in (data.get(key) or []):
                    if isinstance(rec, dict) and rec.get("sha256"):
                        known[rec["sha256"]] = f"corpus:{dom}:{rec.get('rel', '')}"
    _walk(CORPUS_ROOT, "corpus_file")
    _walk(ORIGINALS, "originals")
    return known, sizes  # type: ignore[return-value]


def _hash_candidates(size: int, sizes: dict[int, list[str]]) -> list[str]:
    return sizes.get(size, [])


def scan(apply: bool = False) -> dict:
    reg = load_registry()
    domains = reg.get("domains") or {}
    recognized = set(reg.get("recognized_ext") or [])
    known, sizes = _known_hashes()  # type: ignore[misc]

    rows: list[dict] = []
    counters = {"duplicate": 0, "routed": 0, "needs_review": 0}
    routed_by_domain: dict[str, int] = {}

    for dom, spec in sorted(domains.items()):
        inbox_dir = spec.get("inbox") or ""
        if not os.path.isabs(inbox_dir):
            inbox_dir = os.path.join(paths.ROOT, inbox_dir)
        if not os.path.isdir(inbox_dir):
            continue
        for dirpath, dirnames, filenames in os.walk(inbox_dir):
            dirnames[:] = [d for d in dirnames if d not in ("__pycache__", ".git")]
            for fn in sorted(filenames):
                if fn in SKIP_NAMES:
                    continue
                fp = os.path.join(dirpath, fn)
                rel = os.path.relpath(fp, paths.ROOT).replace(os.sep, "/")
                try:
                    st = os.stat(fp)
                    digest = sha256_of(fp)
                except OSError as e:
                    rows.append({"file": rel, "decision": "needs_review",
                                 "reason": f"读取失败 {type(e).__name__}"})
                    counters["needs_review"] += 1
                    continue
                rec = {"file": rel, "domain": dom, "sha256": digest, "size": st.st_size,
                       "mtime": int(st.st_mtime)}
                if digest in known:
                    rec.update({"decision": "duplicate", "reason": f"内容已归档（{known[digest]}）"})
                else:
                    hit = ""
                    for cand in _hash_candidates(st.st_size, sizes):
                        try:
                            if sha256_of(cand.split(":", 1)[1]) == digest:
                                hit = cand
                                break
                        except OSError:
                            continue
                    if hit:
                        rec.update({"decision": "duplicate",
                                    "reason": f"内容已归档（按大小候选命中 {hit}）"})
                    elif os.path.splitext(fn)[1].lower() not in recognized:
                        rec.update({"decision": "needs_review",
                                    "reason": f"扩展名不可识别（{os.path.splitext(fn)[1] or '无'}）"
                                              "——按 §3.12.6 不静默丢弃"})
                    else:
                        rec.update({"decision": "routed",
                                    "routed_to": (spec.get("pipeline") or [""])[0],
                                    "reason": f"新增内容 → {spec.get('pipeline') or []}"})
                        routed_by_domain[dom] = routed_by_domain.get(dom, 0) + 1
                counters[rec["decision"]] += 1
                rows.append(rec)

    result = {"schema_version": reg.get("schema_version", ""), "inbox": os.path.relpath(INBOX, paths.ROOT),
              "scanned": len(rows), "counters": counters, "by_domain": routed_by_domain,
              "rows": rows}
    if apply:
        os.makedirs(INBOX, exist_ok=True)
        with open(MANIFEST, "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=1)
        # needs_review → worklist（v2 §3.12.6：不静默丢弃；处置入口 = cli.py worklist resolve）
        try:
            from std_lib.common_lib import governance_store as gs  # noqa: PLC0415
            for r in rows:
                if r["decision"] == "needs_review":
                    gs.worklist_add(
                        "corpus_needs_review", r["file"], stage="7.4",
                        artifact_key="inbox_manifest",
                        payload={"file": r["file"], "domain": r.get("domain", ""),
                                 "reason": r.get("reason", ""), "size": r.get("size")},
                        suggestion="判定是否可入管线：可识别格式则移入对应域并核验；否则人工归档或移除")
        except Exception as e:  # noqa: BLE001  旁路设施：登记失败不得中断扫描
            print(f"[inbox] WARN 待办登记失败（不影响扫描结果）: {type(e).__name__}: {e}")
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="投放区扫描与路由（v2 §3.12.6）")
    ap.add_argument("--apply", action="store_true", help="落盘 manifest + 登记 needs_review 待办")
    ap.add_argument("--json", action="store_true", help="打印 JSON")
    ap.add_argument("--new-only", action="store_true",
                    help="只列出需处理的（routed/needs_review 且非 duplicate）")
    args = ap.parse_args(argv)
    res = scan(apply=args.apply)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        print(f"[inbox] 扫描 {res['scanned']} 个文件 → {res['counters']}")
        for r in res["rows"]:
            if args.new_only and r["decision"] == "duplicate":
                continue
            print(f"  [{r['decision']:<12}] {r['file']}  {r.get('reason', '')[:70]}")
        if res["by_domain"]:
            print(f"[inbox] 需路由：{res['by_domain']}"
                  "（dept_policies → triggers 的 inbox_drop；regulatory_stats → ingest_corpus）")
        if args.apply:
            print(f"[inbox] manifest 已落盘：{os.path.relpath(MANIFEST, paths.ROOT)}")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    raise SystemExit(main())
