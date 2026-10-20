# -*- coding: utf-8 -*-
"""tests/test_local_path_unified.py — `local_path` **跨源基准统一**的守卫（第一百零五批）。

背景
----
`local_path` 由各采集器 `os.path.relpath(文件, <基准>)` 生成，历史上**基准分裂**：

| 源 | 旧基准 | 条数（迁移前） |
|---|---|---|
| `mof` | 仓根（`modules/…`） | 246（另有 1 条 `data/…` 混用） |
| `pbc` | 仓根 | 13 |
| `gov` | `modules/regulatory_scrapers`（`data/…`） | 1189 + **1626（body_docs）** |
| `supp` | 同上 | 7 + 1（body_docs） |

迁移前取证结论：全仓**没有代码**把 `local_path` 拼成路径打开（`contract.py` 只映射
字段、`build_fts.py` 只存 SQLite、前端不用）⇒ 这是**口径不一致**而非功能故障。
但口径分裂会让任何外部消费者踩坑（按单一规则拼接必有一半找不到文件）。

**第一百零五批统一为「仓根」**（与 mof/pbc 早已在用的口径一致）：
  · 改 `gov/attachments.py` 的 `REPO_ROOT`（第 5 处 `dirname` 少上溯一级的同类缺陷）；
  · 改 `supp/ingest_local_dir.py` 的 `relpath` 基准（`SCRAPERS_ROOT` → `WS_ROOT`）；
  · `tools/migrate_local_path.py` 一次性迁移 **2911 条**历史值（含此前漏掉的
    `body_docs` —— 工具 v1 只扫 `attachments`，实测漏 1627 条）；
  · 保留 1 条**不可验证**（指向不存在的文件）原样不动 —— 保留旧值比改写成一个
    打不开的路径更诚实。

守卫四类
--------
A **统一**：raw 里 `local_path` 不得再出现 `data/docs/…`（SCRAPERS 基准）或 `../`
B **可达**：`resolve_local_path` 对现存值必须能解析到真实文件（≥99%）
C **基准常量**：四个采集器的 `local_path` 基准常量必须都是**仓根**
D **落盘位置不混**：`outdir` / `REPO_ROOT`（数据根）仍应是 `regulatory_scrapers`，
   **不能**因为统一 `local_path` 而把落盘位置也改到仓根（两者刻意分开）
"""
from __future__ import annotations

import glob
import json
import os

import pytest

pytestmark = pytest.mark.contract

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "modules", "regulatory_scrapers", "data", "raw")
SCRAPERS = os.path.join(ROOT, "modules", "regulatory_scrapers")


def _iter_local_paths():
    """遍历 raw 里**所有** local_path（含 `attachments` 与 `body_docs`）。

    ⚠️ 必须含 `body_docs`：正文载体在批 50 起被 `split_record_body_docs` 从
    attachments 分出，**只扫 attachments 会漏掉一半以上**
    （实测 gov 漏 1626 条、supp 漏 1 条 ⇒ 迁移工具 v1 的覆盖缺口即此）。
    """
    for fn in sorted(glob.glob(os.path.join(RAW, "*.jsonl"))):
        src = os.path.basename(fn).split("_")[0]
        for ln in open(fn, encoding="utf-8", errors="replace"):
            if not ln.strip():
                continue
            try:
                d = json.loads(ln)
            except ValueError:
                continue
            if d.get("_meta"):
                continue
            vals = [d.get("local_path")]
            for key in ("attachments", "body_docs"):
                for a in (d.get(key) or []):
                    if isinstance(a, dict):
                        vals.append(a.get("local_path"))
            for v in vals:
                if v:
                    yield src, str(v)


class TestUnifiedToRepoRoot:
    """A. 不得再出现 SCRAPERS 基准或 `../` 脏值（不可验证的已知例外除外）。"""

    def test_no_dirty_paths(self) -> None:
        bad = [(s, p) for s, p in _iter_local_paths() if p.startswith("..")]
        assert not bad, f"仍有 `../` 脏路径 {len(bad)} 条：{bad[:3]}"

    def test_no_scrapers_base(self) -> None:
        """只允许 1 条已知例外（文件不存在的历史记录，迁移工具按设计原样保留）。"""
        bad = [(s, p) for s, p in _iter_local_paths() if p.startswith("data/")]
        assert len(bad) <= 1, (
            f"仍有 {len(bad)} 条 `data/…`（SCRAPERS 基准）——应已统一为仓根："
            f"{bad[:3]}")
        if bad:
            # 例外必须**可解释**：文件确实不存在（否则就是漏迁）
            s, p = bad[0]
            full = os.path.join(ROOT, p.replace("/", os.sep))
            assert not os.path.exists(full), (
                f"唯一允许的 `data/…` 例外指向的文件**存在** ⇒ 属于漏迁：{s}: {p}")

    def test_mostly_repo_base(self) -> None:
        rows = list(_iter_local_paths())
        if not rows:
            pytest.skip("无 raw local_path")
        repo = sum(1 for _, p in rows if p.startswith("modules/"))
        assert repo / len(rows) >= 0.999, (
            f"仓根基准占比 {repo}/{len(rows)} < 99.9% ⇒ 统一不彻底")


class TestReachable:
    """B. `resolve_local_path` 必须能解析到真实文件。"""

    def test_resolve_rate(self) -> None:
        from std_lib.scraper_std.local_paths import resolve_local_path

        rows = list(_iter_local_paths())
        if not rows:
            pytest.skip("无 raw local_path")
        ok = 0
        for _, p in rows:
            r = resolve_local_path(p)
            if r and os.path.exists(r):
                ok += 1
        assert ok / len(rows) >= 0.99, (
            f"解析可达 {ok}/{len(rows)} < 99% ⇒ 基准识别逻辑或数据有问题")


class TestBaseConstants:
    """C. 四个采集器的 `local_path` 基准都必须是**仓根**。"""

    def test_gov_attachments_repo_root(self) -> None:
        from modules.regulatory_scrapers.collectors.gov import attachments as A

        assert os.path.abspath(A.REPO_ROOT) == os.path.abspath(ROOT), (
            f"gov.attachments.REPO_ROOT={A.REPO_ROOT} 不是仓根 ⇒ 新写入会退回旧基准")

    def test_supp_ws_root(self) -> None:
        from modules.regulatory_scrapers.collectors.supp import supp_constants as S

        assert os.path.abspath(S.WS_ROOT) == os.path.abspath(ROOT), (
            f"supp.WS_ROOT={S.WS_ROOT} 不是仓根")

    def test_supp_relpath_uses_ws_root(self) -> None:
        p = os.path.join(ROOT, "modules", "regulatory_scrapers", "collectors",
                         "supp", "ingest_local_dir.py")
        src = open(p, encoding="utf-8").read()
        assert "os.path.relpath(dst, WS_ROOT)" in src, (
            "supp.ingest_local_dir 的 relpath 未用 WS_ROOT（会写回旧基准）")

    def test_mof_pbc_repo_root(self) -> None:
        from modules.regulatory_scrapers.collectors.mof import att_constants as M
        from modules.regulatory_scrapers.collectors.pbc import collector as P

        assert os.path.abspath(M.REPO_ROOT) == os.path.abspath(ROOT)
        assert os.path.abspath(P.REPO_ROOT) == os.path.abspath(ROOT)


class TestOutputDirSeparateFromLocalPathBase:
    """D. **落盘位置**与 **local_path 基准**是两个概念，不能一起改。"""

    def test_outdir_still_points_to_scrapers_data(self) -> None:
        from modules.regulatory_scrapers.collectors.mof import collector as MC

        got = os.path.abspath(os.path.join(MC.SCRAPERS_ROOT, "data", "raw"))
        want = os.path.abspath(os.path.join(SCRAPERS, "data", "raw"))
        assert got == want, (
            f"mof 默认 outdir={got} 应为主库 {want}（统一 local_path 基准不应波及落盘位置）")

    def test_supp_repo_root_still_scrapers(self) -> None:
        from modules.regulatory_scrapers.collectors.supp import supp_constants as S

        assert os.path.abspath(S.REPO_ROOT) == os.path.abspath(SCRAPERS), (
            "supp.REPO_ROOT（落盘数据根）应仍为 regulatory_scrapers，"
            "不能与 local_path 基准（WS_ROOT=仓根）混为一谈")
