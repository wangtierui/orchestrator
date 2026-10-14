# -*- coding: utf-8 -*-
"""tests/test_w0_fixes.py — W0 缺陷修复回归（2026-10-13）。

- D-3：`relevance:{src}` 必须被包在 `for src in SOURCES:` 循环内（原实现用 clean 循环的
  **残留变量**，只跑最后一源 supp——台账实证）。AST 结构断言，防回归；
- D-3 文档侧：`gen_flow_map.PURPOSE/STEP_FAILURE` 须登记 `relevance` 头键（README §8
  渲染不得出现「未登记作用」；失败语义须与实装一致=旁路 WARN，不得渲染成「阻断」）；
- W0-3：`_raw_size` 流式双格式计数（见 test_raw_size 节，随 W0-3 落地补充）。

纪律：全部为**源码结构/纯函数**断言，不联网、不跑链（data 标记不需要）。
"""
from __future__ import annotations

import ast
import json
import os

import paths


def _parse(rel: str) -> ast.Module:
    p = os.path.join(paths.ROOT, rel)
    with open(p, encoding="utf-8") as fh:
        return ast.parse(fh.read(), filename=p)


def _call_name(node: ast.Call) -> str:
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return ""


def _iterates_sources(loop: ast.For) -> bool:
    """`for src in SOURCES:`（Name 或 Attribute 形态均可）。"""
    it = loop.iter
    if isinstance(it, ast.Name):
        return it.id == "SOURCES"
    if isinstance(it, ast.Attribute):
        return it.attr == "SOURCES"
    return False


def _has_relevance_call(node: ast.AST) -> bool:
    """子树中是否存在 `_run(f"relevance:{src}", ...)`（f-string 形态）。"""
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call) or _call_name(sub) != "_run":
            continue
        if not sub.args:
            continue
        first = sub.args[0]
        if isinstance(first, ast.JoinedStr):
            txt = "".join(
                v.value if isinstance(v, ast.Constant) else "{}" for v in first.values
            )
            if "relevance:" in txt:
                return True
    return False


def test_relevance_step_inside_sources_loop():
    """D-3 回归：`relevance:{src}` 的 `_run` 调用必须在 `for src in SOURCES:` 内。"""
    tree = _parse(os.path.join("tools", "run_production_refresh.py"))
    in_loop = any(
        _iterates_sources(n) and _has_relevance_call(n) for n in ast.walk(tree)
        if isinstance(n, ast.For)
    )
    assert in_loop, (
        "relevance:{src} 未包入 for src in SOURCES 循环——D-3（残留变量只跑 supp）复发"
    )
    # 且不得再出现**循环外**的 relevance 调用（旧缺陷形态）
    top_calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and _call_name(n) == "_run"
        and n.args and isinstance(n.args[0], ast.JoinedStr)
        and "relevance:" in "".join(
            v.value if isinstance(v, ast.Constant) else "{}" for v in n.args[0].values
        )
    ]
    assert top_calls, "未找到 relevance:{src} 调用（入链被回退？）"


def test_step_order_contains_relevance_template():
    """STEP_ORDER（派生的兼容视图）须含 `relevance:{src}` 模板步骤（链内一步一源）。

    W1 起 `STEP_ORDER` 是 `STEP_LAYERS` 的派生视图（源码无字面量）→ 按**运行时事实**断言。
    """
    rpr = _load_rpr()
    assert "relevance:{src}" in rpr.STEP_ORDER, rpr.STEP_ORDER
    # 派生视图与 plan 一致（plan 段不漏不重）
    plan = [n for _layer, names in rpr.LAYER_EXEC_PLAN for n in names]
    assert plan == list(rpr.STEP_ORDER), "LAYER_EXEC_PLAN 与派生 STEP_ORDER 不一致"


def test_flow_map_relevance_keys_registered():
    """D-3 文档侧：README §8 的作用/语义列派生自 `StepSpec`（W1 起唯一事实源）。

    W1-2 起 `gen_flow_map.PURPOSE`/`STEP_FAILURE` 已删除（第二事实源），渲染改读
    `STEP_LAYERS` 的 `StepSpec.purpose` / `.degrade_note`。
    """
    import gen_flow_map as gfm

    assert not hasattr(gfm, "PURPOSE"), "PURPOSE 第二事实源未删除（漂移风险）"
    assert not hasattr(gfm, "STEP_FAILURE"), "STEP_FAILURE 第二事实源未删除（漂移风险）"
    purpose = gfm._purpose("relevance:gov")
    assert "未登记" not in purpose, purpose
    degr = gfm._failure("relevance:gov")
    assert degr and "旁路" in degr, f"relevance 语义未渲染为旁路: {degr!r}"


def test_flow_map_relevance_not_default_blocking():
    """`relevance:*`（含模板形态）不得落到 kind 级默认「阻断」——语义须来自 StepSpec。"""
    import gen_flow_map as gfm

    for step in ("relevance", "relevance:gov", "relevance:{src}"):
        degr = gfm._failure(step)
        assert "旁路" in degr, f"{step} 渲染为默认阻断语义: {degr!r}"
        assert gfm._KIND_FALLBACK["main"] not in degr, step


# ---- W0-3：_raw_count 流式双格式计数（D-2）----


def _load_rpr():
    """以文件路径加载 tools/run_production_refresh.py（不经包导入，避免执行 main）。"""
    import importlib.util as iu
    import sys as _sys

    p = os.path.join(paths.ROOT, "tools", "run_production_refresh.py")
    spec = iu.spec_from_file_location("_rpr_under_test", p)
    mod = iu.module_from_spec(spec)
    # W1 起链模块含 @dataclass → 必须先登记 sys.modules（否则 dataclasses 回查
    # `sys.modules[cls.__module__].__dict__` 命中 None → AttributeError）
    _sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_raw_count_jsonl_skips_meta_envelope(tmp_path):
    """JSONL：首行 `_meta` 信封不计入；空行跳过。"""
    rpr = _load_rpr()
    p = tmp_path / "x.jsonl"
    p.write_text(
        json.dumps({"_meta": {"source": "gov"}}) + "\n"
        + "\n".join(json.dumps({"r": i}) for i in range(7)) + "\n\n",
        encoding="utf-8",
    )
    assert rpr._raw_count(str(p)) == 7


def test_raw_count_legacy_array_streamed(tmp_path):
    """遗留 JSON 数组：元素数正确（且不经 json.load 全量载入——流式路径）。"""
    rpr = _load_rpr()
    p = tmp_path / "x.json"
    p.write_text(json.dumps([{"a": i} for i in range(1234)], ensure_ascii=False), encoding="utf-8")
    assert rpr._raw_count(str(p)) == 1234


def test_raw_count_legacy_array_chunk_boundary(tmp_path):
    """遗留数组：单个元素**跨读取块**（>1MB 元素）时 raw_decode 增量消化仍计数正确。"""
    rpr = _load_rpr()
    p = tmp_path / "big.json"
    big = "x" * (3 << 20)  # 3MB 正文，必跨 1MB 读块
    p.write_text(
        json.dumps([{"body": big}, {"body": big}, {"n": 3}], ensure_ascii=False), encoding="utf-8"
    )
    assert rpr._raw_count(str(p)) == 3


def test_raw_count_missing_returns_none():
    """缺失 → None（**不伪造 0**——0 会被下游当成「跑过但为空」）。"""
    rpr = _load_rpr()
    assert rpr._raw_count("") is None
    assert rpr._raw_count("/no/such/raw.jsonl") is None


def test_raw_count_real_masters_all_non_none():
    """D-2 回归（生产数据实证）：五源真实主库 records 全部非 None 且 > 0。

    这是修复的核心断言——修复前 `json.load` 对 JSONL 必抛 Extra data → 全部 None。
    源集合经 `config.enums.SOURCE_ORDER`（受控枚举，禁字面量——gate_enum_values v3）。
    """
    from config.enums import SOURCE_ORDER

    rpr = _load_rpr()
    sizes = rpr._raw_size()
    assert set(sizes) >= set(SOURCE_ORDER), sorted(sizes)
    for src in SOURCE_ORDER:
        v = sizes[src]
        if v["bytes"] == 0:
            continue  # 该源主库缺失（克隆首跑）→ 不判
        assert isinstance(v["records"], int) and v["records"] > 0, (
            f"{src}: records={v['records']!r}（D-2 复发：流式计数失效）"
        )


# ---- W0-5：T4/R3 两道新门禁（含负面测试）----


def test_t4_gate_passes_on_real_registry():
    """T4 在真实登记表上 PASS（观察对象闭合/可达/同源/归属）。"""
    from gates import gate_condition_observers as t4

    ok, detail = t4.run()
    assert ok, detail["problems"]
    assert set(detail["conditions"]) >= {
        "token_exists", "days_since", "unindexed_originals", "explicit_arg",
        "publish_manifest_changed", "inbox_has_new",
    }


def test_t4_gate_fails_on_unregistered_condition(monkeypatch):
    """负面：条件无登记（D-1 病灶形态）→ FAIL。"""
    from gates import gate_condition_observers as t4
    from std_lib.common_lib import triggers as trg

    obs = {k: dict(v) for k, v in trg.OBSERVATIONS.items()}
    obs.pop("publish_manifest_changed")
    monkeypatch.setattr(trg, "OBSERVATIONS", obs)
    ok, detail = t4.run()
    assert not ok
    assert any("publish_manifest_changed" in p for p in detail["problems"])


def test_t4_gate_fails_on_missing_required_observer(monkeypatch, tmp_path):
    """负面：required 观察对象缺失（D-1 原病灶：观察不存在的文件）→ FAIL。"""
    from gates import gate_condition_observers as t4
    from std_lib.common_lib import triggers as trg

    obs = {k: dict(v) for k, v in trg.OBSERVATIONS.items()}
    obs["publish_manifest_changed"] = {
        "paths": [str(tmp_path / "gone" / "publish_manifest.json")],
        "required": True,
        "why": "测试注入的缺失路径",
    }
    monkeypatch.setattr(trg, "OBSERVATIONS", obs)
    ok, detail = t4.run()
    assert not ok
    assert any("required 观察对象缺失" in p for p in detail["problems"])


def test_t4_gate_fails_on_out_of_family_path(monkeypatch, tmp_path):
    """负面：观察路径落在 paths 注册目录族外 → FAIL（防再次拼出幽灵目录）。"""
    from gates import gate_condition_observers as t4
    from std_lib.common_lib import triggers as trg

    outside = tmp_path / "elsewhere" / "publish_manifest.json"
    outside.parent.mkdir(parents=True)
    outside.write_text("{}", encoding="utf-8")
    obs = {k: dict(v) for k, v in trg.OBSERVATIONS.items()}
    obs["publish_manifest_changed"] = {
        "paths": [str(outside)], "required": True, "why": "测试注入的族外路径",
    }
    monkeypatch.setattr(trg, "OBSERVATIONS", obs)
    ok, detail = t4.run()
    assert not ok
    assert any("不在 paths 注册目录族内" in p for p in detail["problems"])


def _write_ledger(tmp_path, steps: list[dict]) -> str:
    import json as _json

    p = tmp_path / "last_run_steps.json"
    p.write_text(_json.dumps({"run_at": "2026-10-13 00:00:00", "steps": steps},
                             ensure_ascii=False), encoding="utf-8")
    return str(p)


def test_r3_gate_fails_on_partial_coverage(monkeypatch, tmp_path):
    """负面：模板已演练但缺源（D-3 原病灶：relevance 只跑 supp）→ FAIL。"""
    from config.enums import SOURCE_ORDER
    from gates import gate_dynamic_step_coverage as r3

    ledger = _write_ledger(tmp_path, [
        {"step": "clean:gov", "skipped": False}, {"step": "clean:mof", "skipped": False},
        {"step": "clean:nfra", "skipped": False}, {"step": "clean:pbc", "skipped": False},
        {"step": "clean:supp", "skipped": False},
        {"step": "relevance:supp", "skipped": False},  # 只跑 supp（D-3 实证形态）
    ])
    monkeypatch.setattr(r3, "LEDGER", ledger)
    ok, detail = r3.run()
    assert not ok
    cov = detail["coverage"]["relevance:{src}"]
    assert cov["exercised"] is True
    assert set(cov["missing"]) == set(SOURCE_ORDER) - {"supp"}


def test_r3_gate_passes_on_full_coverage(monkeypatch, tmp_path):
    """正面：全源覆盖（含合法 SKIP）→ PASS。"""
    from config.enums import SOURCE_ORDER
    from gates import gate_dynamic_step_coverage as r3

    steps = [{"step": f"clean:{s}", "skipped": False} for s in SOURCE_ORDER]
    steps += [{"step": f"relevance:{s}", "skipped": s == "gov"} for s in SOURCE_ORDER]
    ledger = _write_ledger(tmp_path, steps)
    monkeypatch.setattr(r3, "LEDGER", ledger)
    ok, detail = r3.run()
    assert ok, detail["problems"]
    assert detail["coverage"]["relevance:{src}"]["missing"] == []


def test_r3_gate_skips_when_template_not_exercised(monkeypatch, tmp_path):
    """`--only` 子集运行：模板全 skipped → 披露不 FAIL（判据不可执行 ≠ 通过）。"""
    from gates import gate_dynamic_step_coverage as r3

    ledger = _write_ledger(tmp_path, [{"step": "relevance:supp", "skipped": True}])
    monkeypatch.setattr(r3, "LEDGER", ledger)
    ok, detail = r3.run()
    assert ok
    assert detail["coverage"]["relevance:{src}"]["exercised"] is False


def test_r3_gate_skips_without_ledger(monkeypatch, tmp_path):
    """无台账（克隆首跑）→ 跳过并披露，不 FAIL。"""
    from gates import gate_dynamic_step_coverage as r3

    monkeypatch.setattr(r3, "LEDGER", str(tmp_path / "nope.json"))
    ok, detail = r3.run()
    assert ok
    assert "skipped" in detail
