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

TESTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "tests")
CASSETTES_DIR = os.path.join(TESTS_DIR, "cassettes")
MANIFEST = os.path.join(CASSETTES_DIR, "manifest.json")

#: 明文凭据特征（J13.4）。与 `http_cassette._SECRET_HEADERS` 互补：那边拦「头」，
#: 这边拦「正文里出现的凭据形态」（录制时误把 token 写进 body 的场景）。
_SECRET_BODY_HINTS = ("Bearer ", "Basic ", "api_key=", "access_token=", "password=")


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

    # 导入夹具层。**不自写模块路径引导**——门禁经 `gates` 包导入时仓根已在路径上
    # （唯一引导点是 bootstrap.py），自写注入会顶破 gate_import_bootstrap 的
    # 「注入数只减不增」基线：本判据自身不得违反它守护的纪律。
    try:
        from tests import http_cassette as HC
    except Exception as e:  # noqa: BLE001
        problems.append(f"J13.3: http_cassette 不可导入（{type(e).__name__}: {str(e)[:100]}）")
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
