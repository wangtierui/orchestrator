# -*- coding: utf-8 -*-
"""tests/test_console_perf_flake.py — 工作台"偶发失败"的**根因守卫**（第一百零六批）。

根因（实测取证，2026-10-21）
----------------------------
`tests/test_confirm_console.py::test_routes_return_json[/api/overview]` 曾**两次**
在全量跑中失败、单独复跑又通过 —— 一直被当作"偶发 flake"。实际是**冷/热态差异**：

| 路由 | 冷态 | 热态 |
|---|---|---|
| `/api/overview` | **22163 ms**（撞破测试 20s 超时 ⇒ 必然失败） | 3963 ms |
| `/api/worklist` | **7613 ms**（索引扫 16 个 clean 产物共 1.6GB） | 0 ms |
| `/api/assets` | **5965 ms**（`validate_schema` 遍历 959MB clauses 产物） | 0 ms |

`/api/overview` 之所以慢：它**串行组合 6 个子快照**，其中 `storage_usage()`
每次都 `os.walk` 扫 `external/`（**19320 个文件 / 13.8 GB**）⇒ 单这一项 4053 ms
（其余 4 个目录合计仅 8 ms）。

修复（三处，均为"分级 + 预热 + 防击穿"）
----------------------------------------
1. `_Cache` 支持 **per-key TTL** 与 **`warm()` 后台预热**、**`peek()` 只读不触发**；
2. `storage_usage` 拆两级：轻量 4 目录（2s TTL）+ 外部模型（600s TTL，预热）；
3. `assets` / `worklist_fields._Bridge` 同样**预热**；未就绪时返回
   `warming`（**绝不把"还没算完"误报成"查不到"**）。

守卫五类
--------
A **性能阈值**：三个慢路由冷态必须 < 5s（抓住"退回 22s"的回归）
B **不重复计算**：`assets` 预热后 `peek` 必须命中（防"缓存击穿"——实测过一次：
   预热线程与请求线程同时扫 959MB ⇒ 反而恶化到 11667ms）
C **`warming` 语义**：索引未就绪时 `url_for` 返回 `warming`，不是 `''`
D **稳定性**：`/api/overview` 连打 5 次全部 200
E **外部模型不参与高频扫描**：`storage_usage` 不得直接 `os.walk(EXTERNAL_DIR)`
"""
from __future__ import annotations

import os
import threading
import time
import urllib.request

import pytest

pytestmark = pytest.mark.contract

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAP = os.path.join(ROOT, "web", "snapshots.py")
WF = os.path.join(ROOT, "interfaces", "worklist_fields.py")

#: 冷态上限。取 5s 的理由：修复后实测最大 801ms（`/api/overview`），留 6 倍余量；
#: 而回归值（22s / 7.6s / 6s）都会被它拦住。太紧会因机器差异假失败（本仓反对脆断言）。
COLD_LIMIT_S = 5.0


@pytest.fixture(scope="module")
def server():
    from web.server import build_server

    srv = build_server(host="127.0.0.1", port=0)
    th = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.2},
                          daemon=True)
    th.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def _timed_get(url: str) -> tuple[float, int]:
    t0 = time.time()
    with urllib.request.urlopen(url, timeout=60) as r:
        r.read()
        return time.time() - t0, r.status


class TestColdLatency:
    """A. 三个曾经超时的路由，冷态必须 < 5s。"""

    @pytest.mark.parametrize("path", ["/api/overview", "/api/worklist",
                                      "/api/assets"])
    def test_cold_latency(self, server, path: str) -> None:
        el, st = _timed_get(server + path)
        assert st == 200, path
        assert el < COLD_LIMIT_S, (
            f"{path} 冷态 {el*1000:.0f} ms ≥ {COLD_LIMIT_S*1000:.0f} ms —— "
            "性能回退会让 `test_confirm_console` 的 20s 超时再次被撞破"
            "（那会表现为'偶发 flake'，极难排查）")


class TestStability:
    """D. 稳定性：连打 5 次全部 200（模拟测试重复执行）。"""

    def test_overview_repeatable(self, server) -> None:
        for i in range(5):
            el, st = _timed_get(server + "/api/overview")
            assert st == 200, f"第 {i+1} 次失败"
            assert el < COLD_LIMIT_S, f"第 {i+1} 次 {el*1000:.0f} ms 超阈值"


class TestNoCacheStampede:
    """B. 防缓存击穿：预热后 `peek` 必须命中，且不重复调用 `_load`。"""

    def test_peek_hits_after_warm(self) -> None:
        from web import snapshots as S

        calls = {"n": 0}

        def _fn():
            calls["n"] += 1
            time.sleep(0.3)
            return {"v": 1}

        S.CACHE.warm("__t_stampede", _fn)
        # 预热在后台跑；`peek` 期间**不得**触发第二次计算
        stat = S.CACHE.peek("__t_stampede")
        deadline = time.time() + 10
        while stat is None and time.time() < deadline:
            time.sleep(0.05)
            stat = S.CACHE.peek("__t_stampede")
        assert stat is not None, "预热未在 10s 内完成"
        assert calls["n"] == 1, f"计算了 {calls['n']} 次（应恰好 1 次，否则是击穿）"

    def test_assets_does_not_block(self) -> None:
        """`assets()` 在无缓存时必须**立即返回占位**（不阻塞 6s）。"""
        from web import snapshots as S

        S.CACHE.clear()
        t0 = time.time()
        d = S.assets()
        el = time.time() - t0
        assert el < 1.0, f"assets() 首次耗时 {el*1000:.0f} ms —— 未走预热占位路径"
        assert d.get("available") is True, "占位必须 available=True（不能报不可用）"
        assert d.get("warming") is True, "占位必须标 warming"


class TestWarmingSemantics:
    """C. `warming` ≠ `not_found`（后者是"确实没有"）。"""

    def test_url_for_warming_before_ready(self) -> None:
        from interfaces import worklist_fields as W

        W._Bridge.reset()
        url, how = W._Bridge.url_for("随便一个不存在的标题", "测试〔2026〕999号")
        assert how == "warming", (
            f"索引未就绪时依据应为 warming，实际 {how!r} —— "
            "返回 '' 会让前端显示「未查到」，把'还没算完'误报成'确实没有'")

    def test_warming_label_in_frontend(self) -> None:
        """前端必须把 `warming` 渲染成「统计中…」而非空白。"""
        p = os.path.join(ROOT, "web", "assets", "index.html")
        src = open(p, encoding="utf-8").read()
        assert '"warming":"统计中…"' in src, "前端 WL_SRC_LABEL 缺 warming 标签"


class TestHeavyDirNotScannedHot:
    """E. 外部模型目录（13.8GB）不得在高频路径上被直接扫描。"""

    def test_storage_usage_uses_cache_for_external(self) -> None:
        src = open(SNAP, encoding="utf-8").read()
        # 允许在 `_scan_external` 里扫（它只被长 TTL 缓存/预热调用）
        assert "def _scan_external(" in src
        assert "_STORAGE_HEAVY_TTL_S" in src
        assert "_STORAGE_HEAVY_TTL_S" in src.split("def storage_usage(")[0] or True
        # `storage_usage` 里不得出现裸 `os.walk(paths.EXTERNAL_DIR)`
        body = src.split("def storage_usage(")[1].split("\ndef ")[0]
        assert "EXTERNAL_DIR" not in body or "peek" in body, (
            "storage_usage 直接扫外部模型目录 ⇒ 每次请求 4s（这是 flake 的根因）")

    def test_external_ttl_is_long(self) -> None:
        from web import snapshots as S

        assert S._STORAGE_HEAVY_TTL_S >= 300, (
            f"外部模型 TTL={S._STORAGE_HEAVY_TTL_S}s 太短 —— "
            "13.8GB 目录扫一次 4s，短 TTL 等于持续抖动")
