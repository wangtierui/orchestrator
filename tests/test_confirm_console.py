# -*- coding: utf-8 -*-
"""tests/test_confirm_console.py — 本地确认台（方案 §8）契约测试。

覆盖三类（每类都有对应的**失态**用例）：
  1. **路由面**：15 条路由全部 200 且为 JSON；未知路由 404；参数非法 400；
  2. **安全面**：默认绑回环、静态资源路径穿越被拒、处置必填理由、
     响应体不得含密钥形态字段（`web.api._scrub`）；
  3. **分层纪律（AST 静态断言）**：`web/` 不得裸写 SQL INSERT/UPDATE/DELETE，
     不得直接 `sqlite3.connect` 写连接（只允许 `mode=ro` URI），不得 `sys.path.insert`。

设计纪律：测试用**随机端口**（`port=0`）真实起服务，不 mock HTTP（契约要验真实握手）。
"""
from __future__ import annotations

import json
import os
import re
import threading
import urllib.error
import urllib.request

import pytest

pytestmark = pytest.mark.contract

from web import DEFAULT_HOST, api, snapshots
from web.server import build_server

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")


@pytest.fixture(scope="module")
def server():
    """模块级起一次（避免每个用例重复起服务）。"""
    srv = build_server(host="127.0.0.1", port=0)
    th = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True)
    th.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def _get(url: str):
    """GET → (status, body)；**长超时 + 一次重试**。

    为什么不用默认 20s：`/api/worklist`、`/api/governance`、`/api/search?q=` 都要聚合
    sqlite（统计/审计/全文检索），全量测试并发 20+ 进程时 20s 会偶发超时；
    单跑全绿、全量偶红 —— 这种"看心情红"的判据比不判更糟（会训练人忽略红）。
    重试只吃 `URLError/TimeoutError`，服务端 4xx/5xx 仍直接失败。
    """
    import time
    import urllib.error

    last: Exception | None = None
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                return r.status, r.read()
        except urllib.error.HTTPError:
            raise            # 4xx/5xx 是**被测行为**（如 404/400），不重试、不包装
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
            if attempt == 1:
                time.sleep(1.5)
    raise AssertionError(f"请求两次都失败：{url}（{last}）") from last


def _get_json(url: str):
    st, body = _get(url)
    return st, json.loads(body.decode("utf-8"))


# --------------------------------------------------------------------------- #
# 1) 路由面
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("path", ["/api/health", "/api/overview", "/api/flow", "/api/assets",
                                  "/api/metrics", "/api/quality", "/api/slo",
                                  "/api/worklist", "/api/worklist/kinds", "/api/governance",
                                  "/api/publish", "/api/release", "/api/search?q=gov"])
def test_routes_return_json(server, path):
    """正例：每条只读路由 200 + JSON。"""
    st, d = _get_json(server + path)
    assert st == 200, path
    assert isinstance(d, dict), path
    # 旁路纪律：即使某数据源不可用，也必须是 200 + available/reason（**不 500、不白屏**）
    assert "available" in d or "ok" in d, (path, sorted(d)[:8])


def test_index_page_served(server):
    """正例：单页入口返回 HTML。"""
    st, body = _get(server + "/")
    assert st == 200 and b"<!DOCTYPE html>" in body


def test_unknown_route_404(server):
    """负例：未知路由 404（不是 200 空页）。"""
    with pytest.raises(urllib.error.HTTPError) as e:
        _get_json(server + "/api/nope")
    assert e.value.code == 404


def test_invalid_status_400(server):
    """负例：worklist status 非法值 → 400（受控域校验）。"""
    with pytest.raises(urllib.error.HTTPError) as e:
        _get_json(server + "/api/worklist?status=bogus")
    assert e.value.code == 400


# --------------------------------------------------------------------------- #
# 2) 安全面
# --------------------------------------------------------------------------- #
def test_default_host_is_loopback():
    """正例：默认绑定回环（§8.8）。"""
    assert DEFAULT_HOST == "127.0.0.1"


def test_path_traversal_rejected(server):
    """负例：静态资源路径穿越被拒（不得读到仓内任意文件，如 paths.py）。"""
    for evil in ("/../paths.py", "/..%2fpaths.py", "/assets/../../paths.py"):
        with pytest.raises(urllib.error.HTTPError):
            _get(server + evil)


def test_resolve_requires_reason(server):
    """负例：处置缺理由 → 400（无理由的处置不可审计）。"""
    req = urllib.request.Request(
        server + "/api/worklist/resolve",
        data=json.dumps({"item_id": "x"}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=10)
    assert e.value.code == 400


def test_resolve_rejects_bad_status():
    """负例：status 非法 → 400（不静默写库）。"""
    with pytest.raises(api.ApiError) as e:
        api.handle("POST", "/api/worklist/resolve",
                   body={"item_id": "x", "resolution": "理由", "status": "bogus"})
    assert e.value.status == 400


def test_scrub_redacts_secret_fields():
    """正例：密钥形态字段在响应前被脱敏（§8.2.2 密钥 env-only）。"""
    out = api._scrub({"api_key": "SECRET", "nested": {"token": "T", "ok": 1},
                      "list": [{"password": "P"}]})
    assert out["api_key"] == "***redacted***"
    assert out["nested"]["token"] == "***redacted***"
    assert out["nested"]["ok"] == 1
    assert out["list"][0]["password"] == "***redacted***"


# --------------------------------------------------------------------------- #
# 3) 分层纪律（AST 静态断言）
# --------------------------------------------------------------------------- #
def _web_sources() -> list[str]:
    out = []
    for dirpath, dirnames, filenames in os.walk(WEB_DIR):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in filenames:
            if fn.endswith(".py"):
                out.append(os.path.join(dirpath, fn))
    return sorted(out)


def test_no_raw_sql_writes_in_web():
    """负例（静态）：`web/` 不得出现 INSERT/UPDATE/DELETE/REPLACE 语句。"""
    pat = re.compile(r"\b(INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM|DROP\s+TABLE)\b", re.I)
    for p in _web_sources():
        text = open(p, encoding="utf-8", errors="replace").read()
        m = pat.search(text)
        assert not m, f"{os.path.basename(p)} 出现裸 SQL 写：{m.group(0) if m else ''}"


def test_sqlite_connections_are_readonly():
    """负例（静态）：`web/` 若直连 sqlite，只允许只读 URI（mode=ro）。"""
    for p in _web_sources():
        text = open(p, encoding="utf-8", errors="replace").read()
        if "sqlite3.connect" not in text:
            continue
        assert "mode=ro" in text, f"{os.path.basename(p)} 的 sqlite3.connect 非只读（须 mode=ro URI）"


def test_no_sys_path_insert_in_web():
    """负例（静态）：`web/` 不得自写 sys.path（唯一引导点 = bootstrap）。"""
    for p in _web_sources():
        text = open(p, encoding="utf-8", errors="replace").read()
        assert "sys.path.insert" not in text, f"{os.path.basename(p)} 自写 sys.path.insert"


def test_worklist_resolve_is_only_write_path():
    """正例：处置只经 governance_store.worklist_resolve（唯一写路径）。"""
    text = open(os.path.join(WEB_DIR, "snapshots.py"), encoding="utf-8").read()
    assert "worklist_resolve" in text
    # 不得出现其他 governance_store 写函数
    for forbidden in ("record_watermark(", "worklist_add(", "run_start(", "set_offline("):
        assert forbidden not in text, f"web/ 出现额外写口：{forbidden}"


def test_snapshot_degradation_shape():
    """正例：provider 统一降级形状（available=False + reason + source）。"""
    out = snapshots._degrade("src-x", RuntimeError("boom"))
    assert out["available"] is False and out["source"] == "src-x" and "boom" in out["reason"]
