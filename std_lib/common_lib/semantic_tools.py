# -*- coding: utf-8 -*-
"""std_lib.common_lib.semantic_tools — P1 语义增强工具的**能力探测与指纹**（N-102）

定位（为什么需要它）
--------------------
优化方案 v2 为 P1 语义增强定了三条硬纪律：**零硬依赖 / 降级链 / 指纹**。但"零硬依赖"若只写在
文档里，落地时会退化成两种坏形态：
  ① 某模块顶层 `import hanlp` → 未安装的克隆**导入即崩**（可移植性破坏）；
  ② `try: import x except: pass` 吞掉一切 → 增强能力**静默失效**（无人知道跑的是回退路径）。
本模块把"探测"变成**唯一、显式、可查询**的动作：

```
semantic_tools.probe("hanlp")        # → {"available": False, "version": "", "detail": "..."}
semantic_tools.available("hanlp")    # → bool（调用方据此走增强/回退分支）
semantic_tools.fallback_notice("hanlp", layer="sentence_split_ml")
semantic_tools.fingerprint()         # → 稳定指纹 dict（写入产物 provenance）
```

单一事实源
----------
工具清单（来源仓库 / 用途 / pip 包 / 探测名 / 对应 v2 项 / 体积与离线性 / 许可待核）登记在
`config/schema/semantic_tools.json`；本模块只**读取**它，不重复定义任何工具信息。
消费者：本模块、`gates/gate_config_integrity` 判据 U（清单自洽）、
`tools/gen_benchmark.py` §6.6（每轮全链刷新"当前环境具备哪些增强能力"）。

指纹与可复现性
--------------
ML 输出受 **模型版本 / 权重 / 依赖版本** 影响 → 同一输入可能产出不同产物，使 `input_sha`
断点与 `gate_watermark` 水位判据**失真**。故凡使用本清单任一工具产出的产物，**必须**把
`fingerprint()` 写入其 provenance（既有先例：`relations` 的 `extractor_version`）。
`fingerprint()` 键序稳定（sorted）且只含可比较标量，便于落盘后逐键 diff。
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
from functools import lru_cache


def _manifest_path() -> str:
    """清单路径（经 `paths` 解析，不写相对路径/盘符）。"""
    import paths

    return os.path.join(paths.CONFIG_DIR, "schema", "semantic_tools.json")


@lru_cache(maxsize=1)
def load_manifest() -> dict:
    """读取工具清单（唯一事实源）。

    文件缺失/不可解析/无 `tools` 段 → 抛 `RuntimeError`（**不静默返回空**：空清单会让探测
    把一切判为不可用，从而**掩盖**真正的配置损坏）。
    """
    p = _manifest_path()
    if not os.path.exists(p):
        raise RuntimeError(f"语义工具清单缺失：{p}")
    try:
        with open(p, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as e:
        raise RuntimeError(f"语义工具清单不可解析（{p}）：{type(e).__name__}: {e}") from e
    if not isinstance(data.get("tools"), dict) or not data["tools"]:
        raise RuntimeError(f"语义工具清单无 tools 段：{p}")
    return data


def tool_names() -> list[str]:
    """清单内全部工具名（**稳定排序**）。"""
    return sorted(load_manifest()["tools"])


def policy() -> dict:
    """三条硬纪律（零硬依赖 / 降级链 / 指纹）与违规口径。"""
    return dict(load_manifest().get("policy") or {})


def _dist_version(pypi: str) -> str:
    """pip 发行版版本（无发行包/无元数据 → ""）。"""
    if not pypi:
        return ""
    try:
        from importlib.metadata import PackageNotFoundError, version

        return version(pypi)
    except PackageNotFoundError:
        return ""
    except Exception:  # noqa: BLE001  元数据损坏不应阻断探测（版本降级为空）
        return ""


def _module_version(import_name: str) -> str:
    """模块自述版本（`__version__`；无则 ""）。**惰性导入**，失败即空。"""
    if not import_name:
        return ""
    try:
        mod = __import__(import_name)
    except Exception:  # noqa: BLE001  导入失败 = 未安装/不可用（由 available 统一表达）
        return ""
    return str(getattr(mod, "__version__", "") or "")


def probe(name: str) -> dict:
    """探测单个工具 → 稳定结构（**不抛异常**，清单缺失除外）。

    键：`name / kind / available / version / detail / extra / pipeline_ref / endpoint`。
    `kind` 取自清单：`module`（pip 包）/ `service`（独立服务：客户端库 + 端点）/ `manual`（人工获取）。
    """
    tools = load_manifest()["tools"]
    if name not in tools:
        return {
            "name": name,
            "kind": "unknown",
            "available": False,
            "version": "",
            "detail": f"工具未登记于 semantic_tools.json（已有：{tool_names()}）",
            "extra": "",
            "pipeline_ref": "",
            "endpoint": "",
        }
    spec = tools[name]
    pspec = spec.get("probe") or {}
    kind = str(pspec.get("kind") or "module")
    import_name = str(pspec.get("import_name") or "")
    pypi = str(spec.get("pypi") or "")

    found = False
    detail = ""
    if import_name:
        try:
            found = importlib.util.find_spec(import_name) is not None
        except (ImportError, ValueError) as e:  # 名称异常 / 父包缺失 → 视作未安装
            found = False
            detail = f"find_spec({import_name}) 失败：{type(e).__name__}"
    else:
        detail = "清单未声明探测名"

    ver = (_dist_version(pypi) or _module_version(import_name)) if found else ""
    endpoint = ""
    if kind == "service":
        env = str(pspec.get("endpoint_env") or "")
        endpoint = (
            os.environ.get(env, str(pspec.get("default_endpoint") or "")) if env else ""
        )
        if found and not endpoint:
            detail = f"客户端可用但未配置端点（env {env}）"
    if kind == "manual" and not found:
        detail = "论文/代码库，须人工获取（无发行包）"
    if not found and not detail:
        detail = f"{'pip 包' if pypi else '模块'} {import_name or '?'} 未安装"

    # N-103（2026-09-28）：**离线预置自查**。清单以 `offline` 自由文本描述预置方式，但自由文本
    # 无法被机器校验 → 会出现"装好了依赖、却因未预置模型而在**首次调用时联网下载 GB 级权重**"
    # （既破坏离线可移植性，又使首跑不可预测）。故把预置**收敛为环境变量清单** `offline_env`：
    # 声明了它的工具（会拉取外部权重的）须全部置位，否则 `offline_ready=False` 并给出可读提示。
    # N-113（2026-09-28）：**服务型连通性三态**。此前服务型工具只探测"客户端库是否安装"，
    # 于是 `offline_ready=None`（"无需外部权重"）与"**服务未部署**"混为一谈 —— 用户看到
    # "可用"却连不上服务。此处对 `host:port` 形态的端点做**短超时 TCP 探测**：
    # True 可连通 / False 不可达 / None 非服务型或端点非 `host:port`（如 DSN）。
    service_reachable = None
    if kind == "service" and endpoint:
        # N-134（2026-09-29）：**支持 URI 形态 DSN**。此前只认 `host:port`，于是
        # `postgresql://` 开头的 **URI 连接串**（真实生产形态）落入 `None`（"非 host:port"）
        # → 连通性探测**静默失效**（用户看到 endpoint 已配却不知服务是否可达）。
        # 现先从 URI 析出 host/port，再统一探测。
        # ⚠️ 注：本注释**刻意不写带凭据的 URI 示例** —— 那样会被 `gate_secret_scan` 判为
        #    "数据库连接串明文口令"（实测命中过一次）。示例一律以占位词描述。
        host_port = endpoint
        m = re.match(r"^[a-z][a-z0-9+.\-]*://(?:[^@/]*@)?([^:/?#]+)(?::(\d+))?", endpoint, re.I)
        if m:
            host_port = f"{m.group(1)}:{m.group(2) or ''}"
        host, sep, port_s = host_port.rpartition(":")
        if sep and host and port_s.isdigit():
            import socket

            try:
                with socket.create_connection((host, int(port_s)), timeout=0.3):
                    service_reachable = True
            except OSError:
                service_reachable = False
                detail = f"客户端可用但**服务不可达**（{host_port}）→ 未部署或未启动"
        else:
            detail = f"端点非 host:port 形态，未能探测连通性（{endpoint[:24]}…）"

    # N-120（2026-09-29）：**`hf_model` 探测类型**（新增）。适用"无 pip 包、以 HuggingFace 模型 id
    # 接入"的工具（如 `tencent/Youtu-Embedding`）：可用性 = 加载库（transformers）可导入 **且**
    # 权重已在**本地 HF 缓存**中 —— 后者是离线纪律的硬要求（否则首用会联网拉数百 MB 权重）。
    model_cached = None
    if kind == "hf_model":
        mid = str(pspec.get("hf_model") or "")
        if mid:
            root = os.environ.get("HF_HOME") or os.path.join(
                os.path.expanduser("~"), ".cache", "huggingface"
            )
            model_cached = os.path.isdir(os.path.join(root, "hub", "models--" + mid.replace("/", "--")))
            if found and not model_cached:
                detail = f"加载库可用但**权重未本地缓存**（{mid}）→ 首用将联网下载"

    envs = [str(x) for x in (spec.get("offline_env") or []) if str(x)]
    missing_env = [e for e in envs if not os.environ.get(e)]
    offline_ready = None if not envs else not missing_env
    if found and missing_env:
        detail = f"已安装但**未见离线预置**：缺 env {missing_env}（首用可能联网下载权重）"

    return {
        "name": name,
        "kind": kind,
        "available": bool(found),
        "version": ver,
        "detail": detail,
        "extra": str(spec.get("extra") or ""),
        "pipeline_ref": str(spec.get("pipeline_ref") or ""),
        "endpoint": endpoint,
        # N-103：离线预置状态 —— True 已就绪 / False 未就绪 / None 该工具无需外部权重
        "offline_ready": offline_ready,
        "offline_env_missing": missing_env,
        # N-113：服务型连通性 —— True 可连通 / False 不可达 / None 非服务型（或端点非 host:port）
        "service_reachable": service_reachable,
        # N-120：HF 权重本地缓存 —— True 已缓存 / False 未缓存（首用会联网）/ None 非 hf_model
        "model_cached": model_cached,
    }


def probe_all() -> dict:
    """全部工具探测结果（键序稳定）。"""
    return {n: probe(n) for n in tool_names()}


def available(name: str) -> bool:
    """该工具在当前环境是否可用（调用方据此选增强/回退分支）。"""
    return bool(probe(name)["available"])


def available_any(*names: str) -> bool:
    """任一可用（同一增强层的多后端择优，如 hanlp / ltp 二选一）。"""
    return any(available(n) for n in names)


def fallback_notice(name: str, *, layer: str = "") -> str:
    """**可观测的**回退说明（调用方在走正则回退时应当记录它）。

    纪律：回退**不得静默** —— 否则"增强层已上线但从未生效"会长期无人发现
    （同类历史缺陷：链外产物 N-46 滞后 19 天）。
    """
    p = probe(name)
    where = f"[{layer}] " if layer else ""
    return (
        f"{where}语义增强 {name!r} 不可用（{p['detail']}）→ 回退既有正则实现；"
        f"如需启用：pip install -e .[{p['extra']}]" if p["extra"] else
        f"{where}语义增强 {name!r} 不可用（{p['detail']}）→ 回退既有正则实现"
    )


def fingerprint(names: list[str] | tuple[str, ...] | None = None) -> dict:
    """语义能力**指纹**（供产物 provenance 落盘）。

    → `{"available": [...], "unavailable": [...], "versions": {名: 版本}, "schema_version": ...}`
    **键序稳定**（sorted）、只含标量 → 可逐键 diff。**不含**模型权重 sha（须由增强层在加载
    实际模型后补充 `model_id`/`weights_sha` 至 `versions` 之外的同级字段，见清单 policy）。
    """
    picked = list(names) if names else tool_names()
    vers: dict = {}
    avail: list = []
    unavail: list = []
    for n in sorted(picked):
        p = probe(n)
        if p["available"]:
            avail.append(n)
            vers[n] = p["version"]
        else:
            unavail.append(n)
    return {
        "available": avail,
        "unavailable": unavail,
        "versions": dict(sorted(vers.items())),
        "schema_version": str(load_manifest().get("schema_version") or ""),
    }


def _http_json(url: str, timeout: float = 8.0):
    """GET → JSON（失败返回 None，不抛）。**唯一网络入口**（便于审计与替换镜像）。"""
    import urllib.error
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": "reg-orchestrator/license-check"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, ValueError, OSError):
        return None


def _spdx_from_pypi(pypi: str) -> tuple:
    """PyPI JSON → (SPDX/许可串, 来源)。优先 classifiers 的 `License :: ...` 末段。"""
    if not pypi:
        return "", ""
    d = _http_json(f"https://pypi.org/pypi/{pypi}/json")
    if not d:
        return "", ""
    info = d.get("info") or {}
    for c in info.get("classifiers") or []:
        if c.startswith("License :: OSI Approved ::"):
            name = c.split("::")[-1].strip()
            return name, f"pypi:classifiers/{pypi}"
    lic = str(info.get("license") or "").strip()
    if lic and len(lic) <= 64 and "\n" not in lic:
        return lic, f"pypi:license/{pypi}"
    return "", ""


def _spdx_from_github(repo: str) -> tuple:
    """GitHub repo API → (SPDX ID, 来源)。"""
    m = repo.rstrip("/").split("github.com/")
    if len(m) < 2:
        return "", ""
    slug = m[1]
    d = _http_json(f"https://api.github.com/repos/{slug}")
    if not d:
        return "", ""
    lic = d.get("license") or {}
    sid = str(lic.get("spdx_id") or "").strip()
    if sid and sid != "NOASSERTION":
        return sid, f"github:license/{slug}"
    return "", ""


def verify_licenses(tools: list | None = None) -> dict:
    """许可**自动核验**（W5 后续，2026-09-28）：→ `{工具: {"spdx","source","ok"}}`。

    为什么自动化：许可从"人工待办"变为**可重复执行的核验**——`license_registry.verified`
    若靠人肉登记，会随工具变更而**静默过时**（登记的是旧版本/旧仓库的许可）。
    本函数每次现查 **PyPI / GitHub 的权威元数据**，并把**来源 URL** 一并落盘以可追溯。

    **仅供人工确认后写入**（`apply_license_verification()`）：核验结果不自动改清单 ——
    许可结论必须经人过目（自动化只负责"取证"，不负责"拍板"）。
    """
    names = list(tools) if tools else tool_names()
    raw = load_manifest()["tools"]
    out: dict = {}
    for n in sorted(names):
        spec = raw.get(n) or {}
        spdx, src = "", ""
        # ① GitHub 仓库（最权威：仓库自带 LICENSE）
        spdx, src = _spdx_from_github(str(spec.get("repo") or ""))
        # ② 退回 PyPI —— ⚠️ **仅当该 pypi 就是工具自身**（`kind == "module"`）时可用。
        #    N-121（2026-09-29）**修元数据串线**：`hf_model`/`service` 型工具的 `pypi` 字段表示
        #    **加载库/客户端库**（如 youtu_embedding 的 `transformers`），拿它核验**本工具**的许可
        #    会把库的许可**错误归因**给工具（实测曾把 youtu_embedding 记为 "Apache-2.0 ← transformers"）。
        #    故非 module 型**不得**回退 PyPI；取不到就如实挂 pending。
        if not spdx and str((spec.get("probe") or {}).get("kind") or "module") == "module":
            spdx, src = _spdx_from_pypi(str(spec.get("pypi") or ""))
        out[n] = {"spdx": spdx, "source": src, "ok": bool(spdx)}
    return out


def apply_license_verification(verified_map: dict) -> dict:
    """把核验结果**写入**清单 `license_registry`（verified ← 命中项；其余留在 pending）。

    → 返回新登记（`{"verified": {...}, "pending": [...]}`）。**幂等**：可重复执行。
    只登记 `ok=True` 的项；`pending` = 工具全集 − verified（保持"每个工具都在登记中"的封口）。
    """
    reg = dict(load_manifest().get("license_registry") or {})
    cur_v = dict(reg.get("verified") or {})
    today = __import__("datetime").date.today().isoformat()
    for name, r in (verified_map or {}).items():
        if r.get("ok"):
            cur_v[name] = f"{r['spdx']}（核验日 {today}；来源 {r['source']}）"
    pend = sorted(set(tool_names()) - set(cur_v))
    return {"verified": dict(sorted(cur_v.items())), "pending": pend}


def license_pending() -> list:
    """许可**未核**工具名（W5）：登记在 `license_registry.pending`。入库前置为清空。"""
    reg = load_manifest().get("license_registry") or {}
    return sorted(str(x) for x in (reg.get("pending") or []))


def preflight() -> dict:
    """P1 语义增强**启用前置自检**（W4，2026-09-28）——五道闸，**机器可查**。

    为什么需要：P1 的"未启用"此前只是一个**口头状态**（写在报告里），无人能机器判定
    "现在能不能启用"。五道闸把启用条件**可执行化**，避免两类失败：
      · 抢跑：依赖/离线/许可/v2 取舍尚未就绪就引入，破坏可移植性与合规面；
      · 遗忘：条件已满足却长期未启用（历史同类：链外产物 N-46 滞后 19 天无人察觉）。

    五道闸（**全过才允许启用**）：
      1. `deps`    —— P1-3 分句增强层至少有一个后端可用（`available_any("hanlp","ltp")`）；
      2. `offline` —— 可用后端须**离线就绪**（`offline_ready is not False`），防首用联网拉权重；
      3. `fp`      —— 指纹可计算且结构完整（启用后产物的 provenance 必须带得动它）；
      4. `baseline`—— 存在**评测基线**（v2 四条否决线之一："无度量不得上线"）；
      5. `license` —— 许可 `pending` 已清空（未核许可不得纳入交付面）。

    → 返回 `{"ok": bool, "gates": {名: {"ok": bool, "detail": str}}}`（**不抛异常**：自检本身
    不得成为失败点）。
    """
    import glob

    import paths

    gates: dict = {}

    # ① 依赖（**任一 P1 后端可用**即可开始；逐项后端在各自 pipeline 内按需）
    try:
        backends = ["hanlp", "ltp", "youtu_embedding", "text2vec", "bertopic"]
        ok1 = available_any(*backends)
        gates["deps"] = {
            "ok": bool(ok1),
            "detail": (
                f"P1 后端可用：{[b for b in backends if available(b)]}"
                if ok1
                else f"{backends} 均未安装"
            ),
        }
    except Exception as e:  # noqa: BLE001
        gates["deps"] = {"ok": False, "detail": f"{type(e).__name__}: {e}"}

    # ② 离线就绪（只对"已可用"的后端要求）
    try:
        bad = [
            n
            for n in ("hanlp", "ltp", "youtu_embedding", "text2vec", "bertopic")
            if probe(n)["available"] and probe(n).get("offline_ready") is False
        ]
        gates["offline"] = {
            "ok": not bad,
            "detail": "全部已装后端离线就绪" if not bad else f"已装但未预置离线：{bad}",
        }
    except Exception as e:  # noqa: BLE001
        gates["offline"] = {"ok": False, "detail": f"{type(e).__name__}: {e}"}

    # ③ 指纹可计算
    try:
        fp = fingerprint()
        ok3 = bool(fp.get("schema_version")) and isinstance(fp.get("versions"), dict)
        gates["fp"] = {"ok": ok3, "detail": f"指纹字段完整（schema {fp.get('schema_version')}）"}
    except Exception as e:  # noqa: BLE001
        gates["fp"] = {"ok": False, "detail": f"{type(e).__name__}: {e}"}

    # ④ 评测基线（v2：“无度量不得上线”）
    try:
        files = sorted(glob.glob(os.path.join(paths.ROOT, "reports", "评测基线_*.json")))
        ok4 = bool(files)
        gates["baseline"] = {
            "ok": ok4,
            "detail": (
                f"{os.path.basename(files[-1])} 存在（启用后须在 §6.1 可评样本上报 P/R）"
                if ok4
                else "无评测基线（v2 否决线：无度量不得上线）"
            ),
        }
    except Exception as e:  # noqa: BLE001
        gates["baseline"] = {"ok": False, "detail": f"{type(e).__name__}: {e}"}

    # ⑤ 许可（**策略驱动**：`license_policy.enforced` 决定是否阻断 —— 机制不变，口径可配）
    try:
        pend = license_pending()
        reg = load_manifest().get("license_registry") or {}
        pol = reg.get("license_policy") or {}
        enforced = pol.get("enforced", True)
        risk = sorted(reg.get("risk_notes") or {})
        if not pend:
            gates["license"] = {"ok": True, "detail": "许可全部已核"}
        elif enforced:
            gates["license"] = {"ok": False, "detail": f"未核许可 {len(pend)} 项：{pend}"}
        else:
            # 决策：**不阻断但必须披露**（含风险注记），故 ok=True 而 detail 保留全部事实
            gates["license"] = {
                "ok": True,
                "detail": (
                    f"**按业务决策不阻断**（{pol.get('decided_at')}：{pol.get('scope', '')}）；"
                    f"仍披露：未核 {len(pend)} 项 {pend}"
                    + (f"；风险注记 {risk}" if risk else "")
                ),
            }
    except Exception as e:  # noqa: BLE001
        gates["license"] = {"ok": False, "detail": f"{type(e).__name__}: {e}"}

    return {"ok": all(g["ok"] for g in gates.values()), "gates": gates}


def summary_line() -> str:
    """单行摘要（供日志/基准表引用）。"""
    ps = probe_all()
    ok = [n for n, p in ps.items() if p["available"]]
    return (
        f"语义增强能力：{len(ok)}/{len(ps)} 可用"
        + (f"（{', '.join(ok)}）" if ok else "（全部未安装 → 全链走既有正则路径）")
    )


def _main(argv: list[str]) -> int:
    from config.exitcodes import ExitCode

    cmd = argv[1] if len(argv) > 1 else "--probe"
    if cmd == "--probe":
        print(summary_line())
        for n, p in probe_all().items():
            flag = "可用" if p["available"] else "未装"
            ver = f" v{p['version']}" if p["version"] else ""
            # N-103：已装但未离线预置 → 显式告警（防首用联网下载）
            if p["available"] and p.get("offline_ready") is False:
                flag = "可用·未预置"
            print(f"  [{flag}] {n:20}{ver:14} {p['pipeline_ref']}")
            if not p["available"] or p.get("offline_ready") is False:
                print(f"          {p['detail']}")
        return int(ExitCode.OK)
    if cmd == "--fingerprint":
        print(json.dumps(fingerprint(), ensure_ascii=False, indent=1, sort_keys=True))
        return int(ExitCode.OK)
    if cmd == "--verify-licenses":
        # 核验（只读）+ `--apply` 时才写清单（取证与拍板分离）
        res = verify_licenses()
        for n, r in res.items():
            mark = "已核" if r["ok"] else "未取到"
            print(f"  [{mark}] {n:20} {r['spdx'] or '—':28} {r['source']}")
        if "--apply" in argv:
            new = apply_license_verification(res)
            man = load_manifest()
            man["license_registry"]["verified"] = new["verified"]
            man["license_registry"]["pending"] = new["pending"]
            with open(_manifest_path(), "w", encoding="utf-8") as fh:
                json.dump(man, fh, ensure_ascii=False, indent=1)
                fh.write("\n")
            load_manifest.cache_clear()
            print(f"  已写入清单：verified {len(new['verified'])} / pending {len(new['pending'])}")
        return int(ExitCode.OK)
    if cmd in ("--preflight", "--preflight-report"):
        pf = preflight()
        print(f"P1 启用前置自检：{'可以启用' if pf['ok'] else '**不可启用**'}")
        for name, g in pf["gates"].items():
            print(f"  [{'可' if g['ok'] else '否'}] {name:9} {g['detail']}")
        # N-114：两种用途明确分离 ——
        #   · `--preflight`（判定用）：rc 反映可否启用（供人工/脚本当闸门）；
        #   · `--preflight-report`（披露用）：**恒 rc=0** —— 供**全链披露步骤**调用。
        #     理由：P1 **未启用是合法状态**，若披露步骤因"不可启用"而 FAIL，会把
        #     "尚未启用"误报为"链路故障"（违反"判据不可执行 ≠ 判据不通过"的同款纪律）。
        return int(ExitCode.OK if (cmd == "--preflight-report" or pf["ok"]) else ExitCode.FAIL)
    print(f"用法：{os.path.basename(argv[0])} [--probe|--fingerprint|--preflight]")
    return int(ExitCode.USAGE)


if __name__ == "__main__":  # 离线自检：探测层面**无外部依赖**，未安装亦应正常返回
    import sys as _sys

    _ps = probe_all()
    assert _ps and all({"name", "available", "detail"} <= set(p) for p in _ps.values()), _ps
    _fp = fingerprint()
    assert set(_fp) == {"available", "unavailable", "versions", "schema_version"}, _fp
    assert sorted(_fp["available"] + _fp["unavailable"]) == tool_names(), "指纹须覆盖全部工具"
    raise SystemExit(_main(_sys.argv))
