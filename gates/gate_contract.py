# -*- coding: utf-8 -*-
"""
gates/gate_contract — 数据契约门禁（实装：归属表 8 列 / 主题表 3 列 / 明细契约列 / 底座 40 键集）

数据契约唯一事实 = interfaces/contract.py（程序可读契约）。gate 读 modules 数据与契约比对，
超集判定（允许下游加字段，缺必报）。纯读校验，不写回。
"""

from __future__ import annotations

import json
import os
import re

import paths
from interfaces import contract
from interfaces.theme_api import theme_map as _theme_map

_DATA = os.path.join(paths.MODULES_DIR, "regulatory_classifier", "data")
# R16（二期）：主题集合唯一事实源 = rfn.THEME_MAP，期望数量/命名由遍历派生，禁字面 40/11。
# v2 §3.1.3 I-4（2026-09-26）：原为治理层**直接依赖模块内部实现**
# （自行 sys.path 注入 classifier 目录 + `from rfn import THEME_MAP`），现改经
# `interfaces.theme_api`（协议见 `interfaces/protocols.RfnProvider`）——
# 治理层由此不再持有任何 modules 内部导入，本文件也不再需要 sys.path 注入。
THEME_MAP = _theme_map()

# N-82（2026-09-28）：形状校验纯函数与**派生期望值**一律取自共享层
# `std_lib.common_lib.artifact_shape`（原与 `recall_audit.run_retrieval_after_checks`
# 各持一套逐行等价实现，且该侧期望值**硬编码** → 新增主题即静默分叉）。
# 本门禁保留旧私有名作**别名**，避免改动大量调用点（语义等价、单一实现）。
from std_lib.common_lib.artifact_shape import (
    BASE_RE as _BASE_RE,
)
from std_lib.common_lib.artifact_shape import (
    DET_RE as _DET_RE,
)
from std_lib.common_lib.artifact_shape import (
    dotted as _dotted,
)
from std_lib.common_lib.artifact_shape import (
    expect_base_count as _expect_base_count,
)
from std_lib.common_lib.artifact_shape import (
    expect_base_files as _expect_base_files,
)
from std_lib.common_lib.artifact_shape import (
    expect_detail_table_count as _expect_dets,
)
from std_lib.common_lib.artifact_shape import (
    json_shape as _json_shape,
)
from std_lib.common_lib.artifact_shape import (
    read_csv_header as _header,
)

_EXPECT_DETS = _expect_dets()          # 每主题 ≥1 明细（T0–T10 主题码全覆盖）
_EXPECT_BASE_FILES = _expect_base_files()
_EXPECT_BASE = _expect_base_count()

_MANIFEST = os.path.join(paths.CONFIG_DIR, "schema", "contract_manifest.json")

# N-89（2026-09-28）：契约覆盖判据的**显式豁免**（{符号名: 理由}）。当前为空 ——
# `interfaces/contract.py` 中全部契约类符号（21 个）均已登记在册；若未来确有不属"对外契约"
# 的纯内部容器，在此登记并写明理由（使"漏登"与"有意不登"可区分）。
_COVERAGE_EXEMPT: dict[str, str] = {}


def _manifest_checks() -> tuple[list[str], dict]:
    """M1 修复（v2 §3.4 / P1-4）：让契约清单**有消费方**——file/symbol/count 三元断言。

    背景：`config/schema/contract_manifest.json` 改造前**全仓 0 处代码读取**（仅文档与
    一处注释提及），因此静默漂移两处：`detail_table_fields.count=10`（实为 14）、
    `base_json_keys.file` 指向**不存在**的 `theme_analysis/node2_rebuild/`。
    本判据逐条断言：① `file` 存在；② `symbol`（或 `symbols` 映射）可经点号导入取得；
    ③ `len(符号) == count`。清单新增 `version` 顶层字段以便未来做格式演进判别。
    """
    problems: list[str] = []
    detail: dict = {"checked": {}, "missing": []}
    root = paths.ROOT

    if not os.path.exists(_MANIFEST):
        return [f"契约清单不存在：{_MANIFEST}"], detail
    try:
        man = json.load(open(_MANIFEST, encoding="utf-8"))
    except (OSError, ValueError) as e:
        return [f"契约清单不可解析：{type(e).__name__}: {e}"], detail

    detail["version"] = man.get("version", "")
    if not str(man.get("version") or "").strip():
        problems.append("契约清单缺顶层 version（v2 §3.4：加 version 以便格式演进判别）")

    # N-81（2026-09-28）：**顶层 `consumer` 自述须可解析**。原值写着
    # `gates.gate_contract._check_manifest（file/symbol/count 三元断言）`，而实际函数名是
    # `_manifest_checks` —— 即元数据**指向不存在的符号**（自述失真），却无任何机制发现
    # （本判据此前只校验 contracts 内的 file/symbol/count，不校验顶层 consumer）。
    # 现断言 consumer 形如 `<模块>.<符号>`（可带全角/半角括号说明）时该符号确实可解析。
    cons = str(man.get("consumer") or "").strip()
    detail["consumer"] = cons
    if not cons:
        problems.append("契约清单缺顶层 consumer（元数据须自述其消费方）")
    else:
        sym_path = re.split(r"[（(]", cons, maxsplit=1)[0].strip()
        mod, _, attr = sym_path.rpartition(".")
        if not (mod and attr):
            problems.append(f"顶层 consumer 须为 `<模块>.<符号>` 形态，实为 {cons!r}")
        else:
            import importlib

            try:
                target = importlib.import_module(mod)
                for part in attr.split("."):
                    target = getattr(target, part)
                detail["consumer_resolved"] = True
            except Exception as e:  # noqa: BLE001
                detail["consumer_resolved"] = False
                problems.append(
                    f"顶层 consumer 指向不可解析符号 {sym_path!r}：{type(e).__name__}: {e}"
                )

    # N-89（2026-09-28）：**覆盖完整性判据** —— `interfaces/contract.py` 是"数据契约"的
    # 唯一事实源；凡其中的**契约类符号**（字段集/键集/映射：list/tuple/set/frozenset/dict）
    # 都必须在册（即使 count 断言另有条目覆盖，也须出现在 `symbol` 或某条目的 `symbols` 键里）。
    # 背景：原清单只登记 6 项、而 contract.py 实有 21 个契约类符号（**缺口 16**），
    # 且**无任何机制**发现"新加的字段集没登记" → 契约总册与事实源会持续脱节。
    # 豁免须显式登记（`_COVERAGE_EXEMPT`，附理由），使"漏登"与"有意不登"可区分。
    _registered: set[str] = set()
    _reg_objs: set[int] = set()   # 「同对象别名」：按 `id` 识别（见下）
    import importlib as _il

    for spec in (man.get("contracts") or {}).values():
        if spec.get("symbol"):
            _registered.add(str(spec["symbol"]))
        _registered.update(str(s) for s in (spec.get("symbols") or {}))
        # 某些契约在 contract.py 定义、却被**其它模块 re-export**
        # （如 `registry.py::CSV_FIELDS = contract.REGISTRY_CSV_FIELDS`）→ 清单只登记其中一个名字。
        # 按**对象身份**判定这类别名，无需人工维护等价表（自动随实现变化）。
        rel = str(spec.get("file") or "")
        syms = [str(spec["symbol"])] if spec.get("symbol") else []
        syms += [str(s) for s in (spec.get("symbols") or {})]
        try:
            _mod = _il.import_module(_dotted(rel))
        except Exception:  # noqa: BLE001  file 不可导入由下方三元断言单独报告
            continue
        for s in syms:
            _o = getattr(_mod, s, None)
            if _o is not None:
                _reg_objs.add(id(_o))
    try:
        from interfaces import contract as _ct

        _all_syms = {
            n
            for n in dir(_ct)
            if not n.startswith("_")
            and isinstance(getattr(_ct, n), (list, tuple, set, frozenset, dict))
        }
    except Exception as e:  # noqa: BLE001
        problems.append(f"契约覆盖判据无法执行（interfaces.contract 不可导入）：{type(e).__name__}: {e}")
        _all_syms = set()
    _missing = sorted(
        n
        for n in _all_syms
        if n not in _registered
        and id(getattr(_ct, n, None)) not in _reg_objs
        and n not in _COVERAGE_EXEMPT
    )
    if _missing:
        problems.append(
            f"契约清单**覆盖缺口**：interfaces/contract.py 中未登记的契约类符号 {_missing}"
            "（须补登记 file/symbol/count；确属内部实现细节请登记 `_COVERAGE_EXEMPT` 并写明理由）"
        )
    detail["coverage"] = {
        "contract_symbols": len(_all_syms),
        "registered": len(_all_syms & _registered),
        "missing": _missing,
        "exempt": sorted(_COVERAGE_EXEMPT),
    }

    for name, spec in sorted((man.get("contracts") or {}).items()):
        rel = str(spec.get("file") or "")
        fp = os.path.join(root, rel.replace("/", os.sep))
        if not rel:
            problems.append(f"{name}: 缺 file 字段")
            continue
        if not os.path.exists(fp):
            # 目录型/文件型统一判定：两者都必须存在（历史漂移即此判据抓出）
            problems.append(f"{name}: file 不存在 {rel}")
            detail["missing"].append(rel)
            continue

        pairs: list[tuple[str, int]] = []
        if spec.get("symbol") and spec.get("count") is not None:
            pairs.append((str(spec["symbol"]), int(spec["count"])))
        for sym, cnt in (spec.get("symbols") or {}).items():
            pairs.append((str(sym), int(cnt)))
        if not pairs:
            detail["checked"][name] = {"file": rel, "symbols": "（无 symbol/count，仅登记定位）"}
            continue

        if not rel.endswith(".py"):
            problems.append(f"{name}: 登记了 symbol 但 file 非 .py（{rel}）")
            continue
        mod_name = _dotted(rel)
        try:
            mod = importlib.import_module(mod_name)
        except Exception as e:  # noqa: BLE001  加载失败本身即清单漂移信号
            problems.append(f"{name}: 无法导入 {mod_name}（{type(e).__name__}: {e}）")
            continue
        got: dict = {}
        for sym, cnt in pairs:
            obj = getattr(mod, sym, None)
            if obj is None:
                problems.append(f"{name}: {mod_name} 无符号 {sym}")
                continue
            actual = len(obj)
            got[sym] = {"count": actual, "declared": cnt, "shape": type(obj).__name__}
            if actual != cnt:
                problems.append(
                    f"{name}: {mod_name}::{sym} 实际 {actual} ≠ 清单声明 {cnt}（清单漂移，须同步）"
                )
        detail["checked"][name] = {"file": rel, "module": mod_name, **got}

    return problems, detail


# N-82：`_json_shape` / `_header` / `_dotted` / `_BASE_RE` / `_DET_RE` 及期望值均已上收
# `std_lib.common_lib.artifact_shape`（本文件顶部别名导入）——本处不再保留私有实现。


def run():
    problems, checked = [], {}

    # 0) 契约清单自洽（M1 修复 / P1-4）：与数据面无关，先跑——清单漂移必须独立可报
    _mp, _mdet = _manifest_checks()
    problems += _mp
    checked["manifest"] = _mdet

    # 1) 归属表 8 列
    attr = os.path.join(_DATA, "人身保险公司-文件归属表.csv")
    head = _header(attr)
    ok = head == contract.REGISTRY_CSV_FIELDS
    checked["attr_csv"] = {"ok": ok, "cols": len(head)}
    if not ok:
        problems.append(f"归属表列头 {head} ≠ 契约 {contract.REGISTRY_CSV_FIELDS}")

    # 2) 主题归属表 3 列
    theme = os.path.join(_DATA, "人身保险公司-主题归属表.csv")
    head = _header(theme)
    ok = head == contract.THEME_FIELDS
    checked["theme_csv"] = {"ok": ok, "cols": len(head)}
    if not ok:
        problems.append(f"主题归属表列头 {head} ≠ 契约 {contract.THEME_FIELDS}")

    # 3) 明细表（R16：主题码全覆盖，THEME_MAP 遍历派生）× DETAIL_TABLE_FIELDS 契约列
    dets = sorted(f for f in os.listdir(_DATA) if _DET_RE.match(f))
    det_codes = {m.group(1) for f in dets if (m := _DET_RE.match(f))}   # walrus：一次匹配并收窄
    missing_codes = set(THEME_MAP) - det_codes
    checked["detail_tables"] = {
        "count": len(dets),
        "expected": _EXPECT_DETS,
        "covered_themes": sorted(det_codes),
        "cols": len(contract.DETAIL_TABLE_FIELDS),
    }
    if missing_codes:
        problems.append(f"明细表主题覆盖缺 {sorted(missing_codes)}（THEME_MAP 驱动）: {dets}")
    for f in dets:
        head = _header(os.path.join(_DATA, f))
        if head != contract.DETAIL_TABLE_FIELDS:
            problems.append(
                f"明细表 {f} 列头 ≠ 契约({len(contract.DETAIL_TABLE_FIELDS)}列): {head}"
            )

    # 4) 数据底座（R16：期望文件名集 = THEME_MAP{T1..T10}×4 派生）：结构类型 + 核心键超集
    bfiles = sorted(f for f in os.listdir(_DATA) if _BASE_RE.match(f))
    extra = sorted(set(bfiles) - set(_EXPECT_BASE_FILES))
    miss = sorted(set(_EXPECT_BASE_FILES) - set(bfiles))
    checked["base_files"] = {"count": len(bfiles), "expected": _EXPECT_BASE}
    if miss:
        problems.append(f"数据底座缺 {len(miss)} 个（THEME_MAP 派生期望）: {miss[:5]}")
    if extra:
        problems.append(f"数据底座多余 {len(extra)} 个: {extra[:5]}")
    expect = {
        "base": contract.BASE_KEYS,
        "final": contract.FINAL_KEYS,
        "matched": contract.MATCHED_KEYS,
        "citerefs": contract.CITEREFS_KEYS,
    }
    shape = {"base": "list", "final": "list", "matched": "dict", "citerefs": "dict"}
    for f in bfiles:
        _m = _BASE_RE.match(f)
        assert _m is not None   # bfiles 已由 _BASE_RE 过滤（供 mypy 收窄）
        suf = _m.group(1)
        try:
            st, keys = _json_shape(os.path.join(_DATA, f))
        except Exception as e:  # noqa: BLE001
            problems.append(f"{f} 解析失败: {e!r}")
            continue
        if st != shape[suf]:
            problems.append(f"{f} 顶层结构 {st} ≠ 期望 {shape[suf]}")
        missing = expect[suf] - keys
        if missing:
            problems.append(f"{f} 缺核心键: {sorted(missing)}")

    return (not problems), {"problems": problems[:40], "checked": checked}
