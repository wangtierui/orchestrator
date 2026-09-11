# -*- coding: utf-8 -*-
"""
modules.regulatory_scrapers.clean.run_clean_pipeline — 五源统一清洗管道入口（P3a 单实例）

取代旧仓 gov/mof/nfra/pbc/supp 各自的 scripts/run_clean_pipeline.py 近似副本
（差异仅 PROJECT/RAW_CANDIDATES 与 supp 的运行时注入 + sanitize 调用）。

用法：
  python -m modules.regulatory_scrapers.clean.run_clean_pipeline --project {gov|mof|nfra|pbc|supp}
      [--raw 路径] [--out-dir 路径] [--clean-version v1.0.0] [--captured-at 时间]
      [--on-alarm log|raise] [--sanitize-jsonl]

设计要点：
  1) raw 主库名映射（与 data_migration_manifest 一致）由 config 派生，禁止硬编码路径；
  2) supp：运行时在 scraper_std.MAPPERS 注册 supp 定制（若存在于 modules 内）——
     新仓 std_lib.scraper_std.unified_schema.MAPPERS 已内置 map_supp（R5 迁移），故默认不覆盖；
  3) N2：落盘后统一 sanitize_csv（错行治理）；JSONL 默认保留换行，--sanitize-jsonl 显式开启；
  4) 输出 data/cleaned/{project}_cleaned_{date}.{csv,jsonl}（共享 pipeline 原子写 + history）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

# ---- 使仓库根可导入（config/ std_lib/ modules/ 顶级包）----
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))  # orchestrator 仓库根
for _p in (_REPO,):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from std_lib.scraper_std.pipeline import run_pipeline  # noqa: E402

# 五源 raw 主库 json 文件名（与 data_migration_manifest / 旧仓 data/raw 一致）
RAW_MASTER_NAMES: dict[str, str] = {
    "gov": "gov_laws.json",
    "mof": "mof_laws.json",
    "nfra": "nfra_regulations.json",
    "pbc": "pbc_laws.json",
    "supp": "supplementary_regulations.json",
}
LEGAL_PROJECTS = set(RAW_MASTER_NAMES)


def _yaml_projects() -> list[str]:
    """clean --project 可选项由 sources.yaml enabled 源派生（R15 clean_project 字段路由）；
    yaml 不可用（PyYAML 未装）时回退 RAW_MASTER_NAMES 键。"""
    try:
        sys.path.insert(0, _REPO)
        from config.loader import active_source_ids  # noqa: PLC0415
        ids = active_source_ids()
        if ids:
            return sorted(ids)
    except Exception:  # noqa: BLE001
        pass
    return sorted(LEGAL_PROJECTS)


def _repo_data_raw() -> str:
    """默认 raw 根：本模块 modules/regulatory_scrapers 对应 data/raw（活跃数据复制后）。"""
    # P3 阶段活跃数据尚未复制到 modules 仓内 data/；后续由 manifest 复制。此函数保持声明式。
    return os.path.join(os.path.dirname(_HERE), "data", "raw")


def _find_latest_raw(project: str, explicit: str = "") -> str:
    if explicit and os.path.exists(explicit):
        return explicit
    candidates = [
        os.path.join(_repo_data_raw(), RAW_MASTER_NAMES[project]),
        # 兼容过渡：P3 起指向 orchestrator 根 data 镜像（若存在）
        os.path.join(_REPO, "data", "raw", RAW_MASTER_NAMES[project]),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    raise FileNotFoundError(
        f"未找到 {project} 原始数据（已尝试 {candidates}）。"
        f"请先运行 collectors 采集，或 --raw 指定路径。"
    )


def _on_alarm_default(over, rates):
    # 第九节：核心字段空值率 >3% 告警（生产环境可替换企业微信/邮件回调）
    print(f"[ALARM] 核心字段空值率超阈值 3%：{over}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="五源统一清洗管道入口")
    ap.add_argument("--project", required=True, choices=_yaml_projects(),
                    help="源标识（sources.yaml enabled 源派生，R15）")
    ap.add_argument("--raw", default="", help="原始数据 JSON 路径（默认自动探测）")
    ap.add_argument("--out-dir", default="", help="输出目录（默认 modules 仓 data/cleaned 或 repo data/cleaned）")
    ap.add_argument("--clean-version", default="v1.0.0")
    ap.add_argument("--captured-at", default="")
    ap.add_argument("--on-alarm", default="log", choices=("log", "raise"))
    ap.add_argument("--sanitize-jsonl", action="store_true",
                    help="JSONL 字段换行一并归一（默认保留原始 \\n）")
    ap.add_argument("--no-clauses", action="store_true",
                    help="跳过 clean→条文 固定节点（默认清洗成功后自动增量构建 clause_index，②）")
    args = ap.parse_args(argv)

    project = args.project
    raw = _find_latest_raw(project, args.raw)
    print(f"[{project}] 原始数据：{raw}")

    from modules.regulatory_scrapers.clean import sanitize as _san

    out_dir = args.out_dir or os.path.join(os.path.dirname(_HERE), "data", "cleaned")
    summary = run_pipeline(
        project,
        raw,
        project_root=None,  # 路径已显式给出，不依赖 project_root 派生
        out_dir=out_dir,
        clean_version=args.clean_version,
        captured_at=args.captured_at,
        on_alarm=None if args.on_alarm == "raise" else _on_alarm_default,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    # N2：CSV 错行治理（统一执行）；JSONL 默认保留换行，显式开启才归一
    outs = summary.get("outputs") or {}
    csv_path = outs.get("csv", "")
    if csv_path and os.path.exists(csv_path):
        n = _san.sanitize_csv(csv_path)
        print(f"[{project}] sanitize: CSV 字段换行归一完成（{n} 行）")
    jsonl_path = outs.get("jsonl", "")
    if args.sanitize_jsonl and jsonl_path and os.path.exists(jsonl_path):
        m = _san.sanitize_jsonl(jsonl_path)
        print(f"[{project}] sanitize: JSONL 已归一（{m} 条）")

    if not summary.get("allow_delivery"):
        print(f"[{project}] WARN 空值率超阈值，按规范不生成交付文件（已告警）。")
        return 2

    # ① clean_index 推进（2026-09-09 修复）：快照落盘后重建五源 clean 索引 index.json。
    #    此前仅写 cleaned 文件不推进索引 → 下游 apply_timeliness/classifier/recall 经
    #    clean_index.latest_* 仍消费旧快照（生产刷新实证：0909 文件生成后索引仍指 0908，
    #    时效回写错写旧快照、clause/recall 全空转）。F-D05 修复（2026-09-12）：原
    #    hash_files=False 使 index.json sha=null → validate 跳过哈希 → "同日改写"全链
    #    隐身；生产重建一律带 sha（成本亚秒级，换取内容级变更可感）。
    try:
        from modules.regulatory_scrapers.clean_index import (  # noqa: PLC0415
            SCRAPER_ROOT as _ci_root,
        )
        from modules.regulatory_scrapers.clean_index import (
            _write_index as _ci_write,
        )
        from modules.regulatory_scrapers.clean_index import (
            build_index_dict as _ci_build,
        )
        _ci_write(_ci_build(_ci_root, hash_files=True))
        print("[clean_index] 已重建（latest 指向最新快照）")
    except Exception as _e:  # noqa: BLE001  不阻断 clean 交付（索引可后续 build_clean_index.py 补）
        print(f"[clean_index] WARN 索引推进失败（不影响 clean 交付）: {_e!r}")

    # ② 固定节点：clean 成功后自动增量构建条文产物（clause_index，跨全源最新快照；
    # 仅对 clean 快照新于既有产物的源抽取，幂等）。
    if not args.no_clauses:
        try:
            from modules.regulatory_scrapers.clause_index import build_clause_index  # noqa: PLC0415
            res = build_clause_index()
            print("[clauses] 条文固定节点: "
                  + "; ".join(f"{k}={v.get('built', v.get('error', 'skip'))}"
                              for k, v in res.items() if not k.startswith("_")))
        except Exception as e:  # noqa: BLE001  不阻断 clean 交付（clause 可后续手工/调度补建）
            print(f"[clauses] WARN 条文节点跳过（不影响 clean 交付）: {e!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
