"""J13. 采集器**录制-重放夹具**完整性 —— TD-1 的门禁化（方案 §7.8 D-遗留的前置条件）。

背景：TD-1（`docs/tech_debt.md`）处置后，采集器的抓取/解析回归依赖 `tests/cassettes/`
夹具。夹具入库即成为**事实源**，若任其腐化，回放测试会「绿着说谎」——这是比缺测试
更坏的状态（假绿）。故把夹具纳入门禁：

  J13.1 **manifest 双向闭合**：磁盘夹具全部登记、登记项全部存在（防漏登记 + 幽灵登记）；
  J13.2 **摘要一致**：manifest 的 sha256 == 文件实际摘要（防「手改夹具不更新摘要」）；
  J13.3 **结构与摘要自校验**：schema/origin 必填、逐条 `body_sha256` 与正文一致
        （**直接复用运行期加载器**，避免门禁与运行期两套校验漂移）；
  J13.4 **无凭据残留**：Authorization/Cookie/token 类明文不得出现（对齐 gate_secret_scan
        ——夹具入库也不得携带密钥）；
  J13.5 **origin 必填且受控** ∈ {recorded, synthetic}（不把合成样本冒充真实抓取）。

可测性设计：判据把自己的 `CASSETTES_DIR` 注入 `http_cassette.CASSETTES_ROOT`
（夹具层的根目录参数，运行期默认 `tests/cassettes`）。于是——
  · 运行期：注入值==默认值，行为不变；
  · 测试期：注入临时目录，负例可现场构造失态；
  · 校验逻辑**只有一份**（`http_cassette.load_cassette`），门禁不复制实现。
「无数据环境」友好：本判据只读仓内夹具，不触网、不依赖生产数据。
"""
from __future__ import annotations

import json
import os
import sys

TESTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "tests")
CASSETTES_DIR = os.path.join(TESTS_DIR, "cassettes")
MANIFEST = os.path.join(CASSETTES_DIR, "manifest.json")

#: 明文凭据特征（J13.4）。与 `http_cassette._SECRET_HEADERS` 互补：那边拦「头」，
#: 这边拦「正文里出现的凭据形态」（录制时误把 token 写进 body 的场景）。
_SECRET_BODY_HINTS = ("Bearer ", "Basic ", "api_key=", "access_token=", "password=")

_CASSETTE_MODULE = "tests_http_cassette"   #: 加载用的模块名（避开同名 `tests` 歧义）
_cassette_cache: object | None = None      #: 模块级缓存（门禁一次运行只加载一次）


def _load_cassette_module():
    """按**文件路径**加载 `tests/http_cassette.py`（绕开同名包歧义）。

    为什么不用 `from tests import http_cassette`：
    本仓 `tests/` 是**命名空间包**（无 `__init__.py`），而 `refactor/collectors/tests/`
    是**真包**（有 `__init__.py`）。Python 导入规则**真包优先** ⇒ 一旦
    `refactor/collectors` 进 sys.path，`import tests` 会解析到那个目录，
    报 `cannot import name 'http_cassette' from 'tests'`（2026-10-17/18/19
    三天生产门禁连续因此 FAIL）。

    ⚠️ 本函数**不做 sys.path 注入**（不违反 gate_import_bootstrap 的注入纪律），
    只用 `spec_from_file_location` 显式按文件加载。

    **两级策略**（2026-10-19 定，兼顾两个真实场景）：
      1. 先按 `TESTS_DIR/http_cassette.py` 路径加载 —— 这是**主路径**，
         绕开同名包歧义（`refactor/collectors/tests` 真包遮蔽，见上方说明）；
      2. 路径不存在时**回退到包导入** `from tests import http_cassette`
         —— 测试用 `monkeypatch.setattr(C, "TESTS_DIR", tmp)` 注入临时树，
         而临时树里只有 `cassettes/` 子目录、没有 `http_cassette.py`
         （它仍从真仓导入）⇒ 此时回退才能保持既有测试语义。
    ⚠️ `TESTS_DIR` 在**调用时**读取（而非导入期绑定），否则 monkeypatch 不生效。
    """
    global _cassette_cache
    tests_dir = TESTS_DIR                      # 读时取（容许 monkeypatch）
    path = os.path.join(tests_dir, "http_cassette.py")
    if os.path.exists(path):
        import importlib.util

        abspath = os.path.abspath(path)
        if _cassette_cache is not None and getattr(
                _cassette_cache, "__file__", "") == abspath:
            return _cassette_cache
        try:
            spec = importlib.util.spec_from_file_location(_CASSETTE_MODULE, abspath)
            if spec is None or spec.loader is None:
                return None
            mod = importlib.util.module_from_spec(spec)
            sys.modules[_CASSETTE_MODULE] = mod
            spec.loader.exec_module(mod)
        except Exception:  # noqa: BLE001  加载失败交由调用方报 J13.3
            return None
        _cassette_cache = mod
        return mod
    # 路径不存在（典型：测试注入的临时树）⇒ 回退包导入
    try:
        from tests import http_cassette as HC

        _cassette_cache = HC
        return HC
    except Exception:  # noqa: BLE001
        return None
    try:
        spec = importlib.util.spec_from_file_location(_CASSETTE_MODULE, path)
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        sys.modules[_CASSETTE_MODULE] = mod
        spec.loader.exec_module(mod)
    except Exception:  # noqa: BLE001  加载失败交由调用方报 J13.3
        return None
    _cassette_cache = mod
    return mod


def _check_cassette_manifest(root: str) -> tuple[list[str], dict]:
    """返回 `(problems, detail)`。夹具目录不存在 ⇒ 不适用（不判 FAIL）。"""
    problems: list[str] = []
    detail: dict = {}
    if not os.path.isdir(CASSETTES_DIR):
        return problems, detail

    if not os.path.exists(MANIFEST):
        problems.append("J13.1: tests/cassettes/ 存在但缺 manifest.json（跑 "
                        "python -m tools.record_cassettes --rebuild-manifest）")
        return problems, detail
    try:
        man = json.load(open(MANIFEST, encoding="utf-8"))
    except (OSError, ValueError):
        problems.append("J13.1: cassette manifest 不可解析")
        return problems, detail

    entries = {e.get("name"): e for e in (man.get("cassettes") or [])}
    on_disk = {fn[:-5] for fn in os.listdir(CASSETTES_DIR)
               if fn.endswith(".json") and fn != "manifest.json"}
    detail["cassette_count"] = len(on_disk)

    # J13.1 双向闭合
    missing = sorted(on_disk - set(entries))
    ghost = sorted(set(entries) - on_disk)
    if missing:
        problems.append(f"J13.1: 夹具未登记 manifest {missing}（重跑 --rebuild-manifest）")
    if ghost:
        problems.append(f"J13.1: manifest 登记了不存在的夹具 {ghost}")

    # 导入夹具层。**不自写 sys.path 引导**——自写注入会顶破 gate_import_bootstrap 的
    # 「注入数只减不增」基线：本判据自身不得违反它守护的纪律。
    #
    # ⚠️ 但也不能用 `from tests import http_cassette`（2026-10-19 修，此前连续三天
    # 生产门禁因此 FAIL）：本仓 `tests/` 是**命名空间包**（无 `__init__.py`），
    # 而 `refactor/collectors/tests/` 是**真包**（有 `__init__.py`）——
    # Python 的导入规则是**真包优先于命名空间包** ⇒ 一旦 `refactor/collectors`
    # 进入 sys.path（生产跑的 bootstrap 会扫到它），`import tests` 就解析到
    # 那个没有 `http_cassette` 的目录，报
    # `cannot import name 'http_cassette' from 'tests'`。
    # 这与 J14 判据（同名模块遮蔽）是同一类问题。
    #
    # 根治：**按文件路径显式加载**（`spec_from_file_location`）——它**不是**
    # sys.path 注入（不违反上述纪律），也完全绕开同名歧义。
    HC = _load_cassette_module()
    if HC is None:
        problems.append("J13.3: http_cassette 不可导入（按路径加载失败，"
                        "见 tests/http_cassette.py 是否存在）")
        return problems, detail

    # 注入根目录（可测性：判据的 CASSETTES_DIR 优先于夹具层默认的 tests/）
    prev_root = HC.CASSETTES_ROOT
    HC.CASSETTES_ROOT = CASSETTES_DIR
    secret_hits: list[str] = []
    try:
        for name in sorted(on_disk & set(entries)):
            try:
                HC.load_cassette(name)      # J13.3 + J13.5（schema/origin/body_sha256）
            except Exception as e:  # noqa: BLE001
                problems.append(
                    f"J13.3: 夹具 {name} 自校验失败（{type(e).__name__}: {str(e)[:110]}）")
                continue
            # J13.2 文件摘要
            want = entries[name].get("sha256") or ""
            got = HC.cassette_file_sha256(name)
            if want != got:
                problems.append(
                    f"J13.2: 夹具 {name} 摘要不符（manifest {want[:12]}… != 实际 "
                    f"{got[:12]}…）——夹具被手改后须重跑 --rebuild-manifest")
            # J13.4 凭据残留（明文）
            raw = open(HC.cassette_path(name), encoding="utf-8", errors="replace").read()
            for hint in _SECRET_BODY_HINTS:
                if hint in raw:
                    secret_hits.append(f"{name}:{hint.strip()}")
            # J13.5 origin 受控（load_cassette 已校验，此处复核 manifest 侧登记值）
            if entries[name].get("origin") not in ("recorded", "synthetic"):
                problems.append(f"J13.5: manifest 中 {name} 的 origin 非法 "
                                f"{entries[name].get('origin')!r}（须 recorded|synthetic）")
    finally:
        HC.CASSETTES_ROOT = prev_root

    if secret_hits:
        problems.append(f"J13.4: 夹具含疑似凭据明文 {secret_hits}")
    detail["cassette_origins"] = sorted({str(e.get("origin")) for e in entries.values()})
    return problems, detail
