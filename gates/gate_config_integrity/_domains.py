# -*- coding: utf-8 -*-
"""gates.gate_config_integrity._domains — V/U 族判据（W4-c 五拆）。

agent 状态受管块（U）/ domains 登记一致性 / theme 覆盖 / semantic manifest。
公共基础设施经 `._common`。
"""

from __future__ import annotations

import os

from ._common import _SCAN_BASE_SKIP, ROOT

_AGENT_DIR = ".codebuddy"
_AGENT_README = os.path.join(ROOT, _AGENT_DIR, "README.md")
_CODEBUDDY_MARK = "." + "codebuddy"  # 拼接写法：避免本文件自身被"引用扫描"误伤
_INPUT_MARKS = (
    "os.path.join(",
    "os.path.abspath(",
    "open(",
    "Path(",
    "listdir(",
    "glob.glob(",
    "read_text(",
    "json.load(",
)
_U_SCAN_SKIP = set(_SCAN_BASE_SKIP) | {
    "data",
    "reports",
    "archive",
    "external",  # 含 tessdata（W4 §7.10 案例二已迁入 external/tessdata）
    "node_modules",
    ".venv",
    "venv",
}
_U_EXT = (".py", ".yaml", ".yml", ".json", ".toml", ".cfg", ".ini", ".bat")


def _check_agent_state() -> tuple[list[str], dict]:
    problems: list[str] = []
    detail: dict = {}

    # ---- U1：.codebuddy 作为输入（引用扫描）----
    refs: list[str] = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in _U_SCAN_SKIP and d != _AGENT_DIR]
        for fn in filenames:
            if not fn.endswith(_U_EXT):
                continue
            fp = os.path.join(dirpath, fn)
            rel = os.path.relpath(fp, ROOT).replace(os.sep, "/")
            if rel in ("gates/gate_config_integrity.py", "CODEBUDDY.md"):
                continue  # 判据自身与仓级说明（口径声明处）
            try:
                lines = open(fp, encoding="utf-8", errors="replace").read().splitlines()
            except OSError:
                continue
            for i, ln in enumerate(lines, 1):
                if _CODEBUDDY_MARK not in ln:
                    continue
                s = ln.strip()
                if s.startswith("#") or s.startswith("//"):
                    continue
                if any(m in ln for m in _INPUT_MARKS):
                    refs.append(f"{rel}:{i}")
    if refs:
        problems.append(
            f"U1: 有 {len(refs)} 处把 `.{_AGENT_DIR}/` 当输入引用 {refs[:5]}"
            "（它只是历史归档；把目录写进**扫描排除集**的字符串条目不算）"
        )
    detail["agent_refs"] = refs[:10]

    # ---- U2：权威声明存在且写明"仓外为权威" ----
    if not os.path.exists(_AGENT_README):
        problems.append(f"U2: 缺 {_AGENT_DIR}/README.md（记忆权威口径无落点 → 会再次二义）")
    else:
        txt = open(_AGENT_README, encoding="utf-8", errors="replace").read()
        if ("权威" not in txt) or ("仓外" not in txt and "用户级" not in txt):
            problems.append(f"U2: {_AGENT_DIR}/README.md 未声明'仓外（用户级）为权威'")

    # ---- U3：调度/触发事实源不得回指 .codebuddy ----
    for name in ("schedule.yaml", "triggers.yaml"):
        fp = os.path.join(ROOT, "config", name)
        if os.path.exists(fp):
            txt = open(fp, encoding="utf-8", errors="replace").read()
            if _CODEBUDDY_MARK in txt:
                problems.append(
                    f"U3: config/{name} 引用了 `.{_AGENT_DIR}/`（调度/触发事实源不得回指助手记忆）"
                )

    # ---- U4：.codebuddy/** 不得新增 yaml 声明 ----
    yamls: list[str] = []
    for dirpath, _dirnames, filenames in os.walk(os.path.join(ROOT, _AGENT_DIR)):
        for fn in filenames:
            if fn.endswith((".yaml", ".yml")):
                yamls.append(os.path.relpath(os.path.join(dirpath, fn), ROOT).replace(os.sep, "/"))
    if yamls:
        problems.append(
            f"U4: {_AGENT_DIR}/ 出现声明类 yaml {yamls}（防记忆再次成为流程输入；声明应落 config/）"
        )

    # 披露：仓内归档规模（信息项，不阻断）
    n_hist = 0
    for _dirpath, _dirnames, filenames in os.walk(os.path.join(ROOT, _AGENT_DIR)):
        n_hist += sum(1 for fn in filenames if fn.endswith(".md"))
    detail["agent_state"] = {
        "repo_archived_md": n_hist,
        "refs": len(refs),
        "yaml": yamls,
        "authority": "仓外（用户级）记忆为权威；仓内为历史归档",
    }
    return problems, detail


# --------------------------------------------------------------------------- #
# V. 投放区域消费者已登记（v2 §3.12.4 / R11「有产出无消费」，P3-3）
#    每个域的 `consumer` 必须非空，且 ∈ 模块包名 ∪ {pending_consumer}；
#    `pending_consumer` 允许但**必须被披露**（不假装已消费）。
# --------------------------------------------------------------------------- #
_PENDING_CONSUMER = "pending_consumer"


def _check_domains() -> tuple[list[str], dict]:
    problems: list[str] = []
    detail: dict = {}
    fp = os.path.join(ROOT, "config", "inbox_registry.yaml")
    if not os.path.exists(fp):
        return [f"V1: 缺 {os.path.relpath(fp, ROOT)}"], {}
    try:
        import yaml

        data = yaml.safe_load(open(fp, encoding="utf-8")) or {}
    except Exception as e:  # noqa: BLE001
        return [f"V1: inbox_registry.yaml 解析失败：{type(e).__name__}: {e}"], {}
    from config.constants import MODULE_PKG_SET

    rows: dict[str, dict] = {}
    pending: list[str] = []
    for dom, spec in (data.get("domains") or {}).items():
        consumer = (spec.get("consumer") or "").strip()
        rows[dom] = {
            "consumer": consumer,
            "layer": spec.get("layer", ""),
            "pipeline": spec.get("pipeline") or [],
        }
        if not consumer:
            problems.append(f"V2: 域 {dom} 未声明 consumer（有产出无消费）")
        elif consumer == _PENDING_CONSUMER:
            pending.append(dom)
        elif consumer == dom:
            problems.append(f"V2: 域 {dom} 的 consumer 指向自身（自指环 = 事实上的无消费）")
        elif consumer not in MODULE_PKG_SET:
            problems.append(
                f"V2: 域 {dom} 的 consumer={consumer!r} 不在已登记模块"
                f"（{sorted(MODULE_PKG_SET)}）或 {_PENDING_CONSUMER}"
            )
        if not rows[dom]["pipeline"]:
            problems.append(f"V2: 域 {dom} 未声明 pipeline（投放后无处理路径）")
    # ⚠️ 键名刻意不叫 `domains`：该键已被 P2-3a 的 `_check_corpus()`（语料域本体/清单一致性）
    # 占用，且其在 run() 中**在本判据之后**执行 → 同名会静默互相覆盖（本批实测踩到）。
    detail["inbox_domains"] = {"count": len(rows), "pending_consumer": pending, "rows": rows}
    return problems, detail


def _check_theme_coverage() -> tuple[list[str], dict]:
    """判据 T（N-76，2026-09-28）：**主题关键词表须覆盖 THEME_MAP 全部主题**。

    背景：`internal_policy_base.align.THEME_TITLE_KW` 是"内部制度 → 主题"对齐的**唯一**关键词表
    （`score_text` / `align_one` 均遍历它）。原表覆盖 T1/T2/T4–T10 而**独缺 T3**（资本与偿付能力
    监管）→ 该类内部制度**永远无法**对齐到 T3（静默落到其他主题或 UNALIGNED），且**无任何门禁**
    可发现（属"覆盖缺口"类缺陷：不报错、不缺失、只是永远不命中）。

    判据：`THEME_TITLE_KW` 的键集 ⊇ `THEME_MAP` − `THEME_TITLE_KW_EXEMPT`（豁免须显式登记，
    使"漏写"与"有意不写"可区分）；且不得出现不在 THEME_MAP 的主题码（防拼写漂移）。
    """
    problems: list[str] = []
    detail: dict = {}
    try:
        from interfaces.theme_api import THEME_MAP_P0 as _themes
        from modules.internal_policy_base.align import (
            THEME_TITLE_KW,
            THEME_TITLE_KW_EXEMPT,
        )
    except Exception as e:  # noqa: BLE001
        return [f"T1: 主题覆盖判据依赖导入失败：{type(e).__name__}: {e}"], {}
    covered = set(THEME_TITLE_KW)
    known = set(_themes)
    missing = sorted((known - set(THEME_TITLE_KW_EXEMPT)) - covered)
    unknown = sorted(covered - known)
    if missing:
        problems.append(
            f"T1: THEME_TITLE_KW 未覆盖主题 {missing}"
            "（须补关键词；确属不应覆盖者登记 `THEME_TITLE_KW_EXEMPT` 并注明理由）"
        )
    if unknown:
        problems.append(f"T1: THEME_TITLE_KW 含未登记主题码 {unknown}（不在 THEME_MAP，疑拼写漂移）")
    detail["theme_coverage"] = {
        "themes": len(known),
        "covered": sorted(covered),
        "exempt": sorted(THEME_TITLE_KW_EXEMPT),
        "missing": missing,
        "unknown": unknown,
    }
    return problems, detail


def _check_semantic_manifest() -> tuple[list[str], dict]:
    """判据 U（N-102，2026-09-28）：**P1 语义工具清单自洽**。

    背景：P1 语义增强的"零硬依赖 / 降级链 / 指纹"三条纪律，若清单本身可缺字段、可指向不存在的
    extra，纪律就只是文档。本判据把清单变成**机器可校验**的元数据：

      ① 清单可加载，且 `policy` 明确声明 `hard_dependency=False` + `degradation/fingerprint=required`
         （三条纪律**不得被静默放宽**）；
      ② 每条工具必备：`repo`（http 链接）、`purpose`、`probe.import_name`、`pipeline_ref`
         （指向 v2 方案项 → 保证"每个工具都对应一个已论证的落点"，防"先把依赖装上再说"）；
      ③ `probe()` 对每个工具**不抛异常**且返回结构完整（探测层自身不得成为新失败点）；
      ④ 清单声明的 `extra`（若为非空）必须真的存在于 `pyproject [project.optional-dependencies]`
         ——防"清单说有 extra、用户按文档装了却没有"（同 CP-A01 那类"装了等于没装"缺陷）。
    """
    problems: list[str] = []
    detail: dict = {}
    try:
        from std_lib.common_lib import semantic_tools as st
    except Exception as e:  # noqa: BLE001
        return [f"U1: 语义工具清单判据依赖导入失败：{type(e).__name__}: {e}"], {}

    try:
        man = st.load_manifest()
    except RuntimeError as e:
        return [f"U1: {e}"], {}

    pol = man.get("policy") or {}
    if pol.get("hard_dependency") is not False:
        problems.append("U1: policy.hard_dependency 必须显式为 false（零硬依赖是纪律，不得放宽）")
    for k in ("degradation", "fingerprint"):
        if pol.get(k) != "required":
            problems.append(f"U1: policy.{k} 必须为 \"required\"（实为 {pol.get(k)!r}）")

    tools = man.get("tools") or {}
    need = ("repo", "purpose", "pipeline_ref")
    for name, spec in sorted(tools.items()):
        for k in need:
            if not str(spec.get(k) or "").strip():
                problems.append(f"U1: 工具 {name!r} 缺 {k}")
        if not str(spec.get("repo") or "").startswith("http"):
            problems.append(f"U1: 工具 {name!r} 的 repo 须为 http(s) 链接")
        # N-179（2026-09-30）：**`source_tree`/`local_dir` 型工具不要求 `import_name`** ——
        # 它们的可用性由**本地产物实检**判定（见 `semantic_tools.probe()` 的 N-176 `source_tree`
        # 与 N-155 `local_model` 分支），而非某个 pip 模块。原判据一律要求 `import_name`，
        # 会把"源码树部署的框架"（如 DeepKE）判为登记不合规 —— 属**假阳性**（会逼人用
        # `import_name=torch` 之类**借位**把"运行时在"混当"工具在"，反而更失真）。
        _pspec = spec.get("probe") or {}
        _self_describing = (
            str(_pspec.get("kind") or "") in ("source_tree", "local_model")
            and bool(_pspec.get("local_dir"))
        )
        if not _self_describing and not str(_pspec.get("import_name") or "").strip():
            problems.append(f"U1: 工具 {name!r} 缺 probe.import_name（探测层无法判定可用性）")

    # ③ 探测层健壮性 + 离线预置声明合法性（N-103）
    bad_probe: list[str] = []
    for n in sorted(tools):
        try:
            p = st.probe(n)
            if not {"name", "available", "detail", "pipeline_ref"} <= set(p):
                bad_probe.append(f"{n}(结构缺键)")
            # N-103：`offline_env` 须为**非空字符串**列表 —— 空串/空列表会让"离线自查"
            # 变成永真（`all(...)` 为空集合恒 True），即静默失效。
            oe = tools[n].get("offline_env")
            if oe is not None and (
                not isinstance(oe, list) or not all(isinstance(x, str) and x.strip() for x in oe)
            ):
                problems.append(f"U1: 工具 {n!r} 的 offline_env 须为非空字符串列表（实为 {oe!r}）")
        except Exception as e:  # noqa: BLE001
            bad_probe.append(f"{n}({type(e).__name__})")
    if bad_probe:
        problems.append(f"U1: 工具探测异常/结构不完整 {bad_probe}（探测层不得成为新失败点）")

    # ④b 许可登记完整性（W5，2026-09-28）：**每个工具都须在许可登记中**（verified 或 pending），
    #     使"还有哪些未核"机器可查（原文只有各条目的 `license_to_verify: true` 自由标记，
    #     既无法回答"还剩几项"，也无法作为启用闸门）。
    reg = man.get("license_registry") or {}
    ver = set(reg.get("verified") or {})
    pend = set(reg.get("pending") or [])
    all_tools = set(tools)
    unreg = sorted(all_tools - ver - pend)
    ghost = sorted((ver | pend) - all_tools)
    if unreg:
        problems.append(f"U1: 工具未登记许可状态 {unreg}（须入 verified 或 pending）")
    if ghost:
        problems.append(f"U1: 许可登记含未定义工具 {ghost}（疑拼写漂移）")
    if ver & pend:
        problems.append(f"U1: 许可状态冲突（同时 verified 与 pending）：{sorted(ver & pend)}")
    # ⑤ pending 须带**原因**（否则"待核"退化为无人能判的长期悬挂）；risk_notes 须指向已定义工具
    reason = reg.get("pending_reason") or {}
    no_reason = sorted(pend - set(reason))
    if no_reason:
        problems.append(f"U1: pending 工具缺 pending_reason {no_reason}（须写明为何未核）")
    stray_reason = sorted(set(reason) - pend)
    if stray_reason:
        problems.append(f"U1: pending_reason 指向非 pending 项 {stray_reason}（状态已变须清理）")
    risk = reg.get("risk_notes") or {}
    stray_risk = sorted(set(risk) - all_tools)
    if stray_risk:
        problems.append(f"U1: risk_notes 含未定义工具 {stray_risk}")

    # ④ extra 交叉核对
    extras: set = set()
    try:
        import tomllib

        with open(os.path.join(ROOT, "pyproject.toml"), "rb") as fh:
            extras = set((tomllib.load(fh).get("project") or {}).get("optional-dependencies") or {})
    except Exception as e:  # noqa: BLE001
        problems.append(f"U1: 无法读取 pyproject extra 段：{type(e).__name__}: {e}")
    missing_extras = sorted(
        {str(s.get("extra")) for s in tools.values() if s.get("extra")} - extras
    )
    if missing_extras:
        problems.append(f"U1: 清单声明的 extra 在 pyproject 中不存在 {missing_extras}")

    try:
        pf = st.preflight()
        pf_ok, pf_gates = bool(pf.get("ok")), {k: v["ok"] for k, v in (pf.get("gates") or {}).items()}
    except Exception as e:  # noqa: BLE001  自检失败须可见，但不阻断清单判据
        pf_ok, pf_gates = False, {"error": f"{type(e).__name__}: {e}"}
    detail["semantic_manifest"] = {
        "tools": len(tools),
        "extras": sorted(extras & {str(s.get("extra")) for s in tools.values()}),
        "policy": {k: pol.get(k) for k in ("hard_dependency", "degradation", "fingerprint")},
        "available": [n for n in sorted(tools) if st.available(n)],
        # W5：许可未核数（入库前置为 0）；W4：启用前置自检（**披露**，不阻断——未启用是合法状态）
        "license_pending": len(pend),
        "preflight_ok": pf_ok,
        "preflight_gates": pf_gates,
    }
    return problems, detail
