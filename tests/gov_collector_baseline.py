# -*- coding: utf-8 -*-
"""tests/gov_collector_baseline.py — `gov/collector.py` 拆分前的**等价性基线夹具**。

为什么需要（而不是只靠 cassette）
--------------------------------
拆分（841 行 → 常量/会话/抓取/编排四层）的真实风险**不是 HTTP 行为变化**
（那是 cassette 的职责，且现有 6 个夹具已覆盖 list/detail 解析），而是：

  · **漏搬**：某个模块级常量/函数没搬到新位置 ⇒ 运行时 `NameError`（只在脚本形态暴露）；
  · **搬错**：常量值被改（如 selector 少了一个、header 少一项）；
  · **签名漂移**：`RobustSession`/`XzfgkScraper` 的方法参数变了但调用点没改；
  · **CLI 漂移**：`main()` 的 argparse 选项少了/改名了（调度会传 `--source`/`--max-seconds`）。

**快照三类**
  1. `MODULE_NAMES` —— 模块级名字全集（36 个，按名排序）；
  2. `CONST_DIGEST` —— 常量的**结构摘要**（容器取长度 + 排序键，字符串取长度 + 前 12 字）；
  3. `CLASS_METHODS` —— 两个类的方法 → 参数名元组；
  4. `CLI_OPTIONS` —— `main()` 的 argparse 选项与默认值。

**不用 `hash()`**：Python 3 的 str hash 每次进程随机（PYTHONHASHSEED），
会把"未变化"误判为"变化"。摘要一律用**稳定的结构表示**。

用法
----
    python -m tests.gov_collector_baseline --write   # 生成/更新基线 JSON
    python -m tests.gov_collector_baseline           # 与基线比对（差异则非零退出）
"""
from __future__ import annotations

import inspect
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
#: ⚠️ 放 `tests/baselines/` 而**不是** `tests/cassettes/`——后者是 **HTTP 录制夹具**
#: 目录，J13 判据会按夹具 schema（`interactions`/`origin`/`sha256`）校验其中每个文件；
#: 结构快照不具备回放语义，混进去必然判 "schema 不支持"（实测）。
BASELINE = os.path.join(ROOT, "tests", "baselines",
                        "gov_collector_baseline.json")

#: 拆分前后都必须可达的模块级名字（由 AST 实测得出，见 CHANGELOG 第一百零四批）
#: ⚠️ **刻意不列**两个名字（否则基线永远报"缺失"，噪声掩盖真问题）：
#:   · `_GUIDE_ROOT` —— 引导块用完后被 `del`（设计如此）；
#:   · `parse` —— 实际是 `_parse`（`gov/parse` 的导入别名），对外暴露的是
#:     `SOURCES` 等具体符号，模块本身不作为名字导出。
MODULE_NAMES = (
    "_OfflineMiss", "_ORGAN_KW", "_NOISE_CLASS_HINTS",
    "_RESP_TEXT", "_SCHEME_UPGRADE_RE", "_SCRAPERS_ROOT", "_STD_LIB_ROOT",
    "_init_cache", "_is_https_scheme_upgrade", "_longest_text_block",
    "build_config", "clean_text", "decode_html", "extract_date",
    "extract_doc_number", "extract_issue_organ", "fetch_gov_attachments",
    "load_resume", "LOG", "main", "make_summary", "MasterView",
    "merge_and_write", "merge_with_master", "RobustSession", "ScrapeConfig",
    "set_cache_dir", "set_offline", "SOURCES", "TITLE_SELECTORS",
    "write_collect_stats", "write_outputs", "XzfgkScraper",
    "CONTENT_SELECTORS", "CSV_COLUMNS", "DEFAULT_HEADERS",
)

#: 结构型常量（**按实测类型分类**，不按名字猜——2026-10-20 实测：
#: `CONTENT_SELECTORS`/`_NOISE_CLASS_HINTS`/`TITLE_SELECTORS`/`CSV_COLUMNS` 都是
#: **list**（不是 dict/set），`_ORGAN_KW` 是 **str**，`DEFAULT_HEADERS` 是 dict）。
#: 容器取「长度 + 排序键/元素」，能抓住"少一项/改名/换序"。
_DICT_CONSTS = ("DEFAULT_HEADERS",)
_SEQ_CONSTS = ("CONTENT_SELECTORS", "TITLE_SELECTORS", "CSV_COLUMNS",
               "_NOISE_CLASS_HINTS")
_STR_CONSTS = ("_ORGAN_KW",)


def _const_digest(mod) -> dict:
    out: dict[str, object] = {}
    for n in _DICT_CONSTS:
        v = getattr(mod, n, None)
        if isinstance(v, dict):
            out[n] = {"len": len(v), "keys": sorted(map(str, v.keys()))}
    for n in _SEQ_CONSTS:
        v = getattr(mod, n, None)
        if isinstance(v, (list, tuple)):
            out[n] = {"len": len(v), "items": [str(x)[:60] for x in v]}
    for n in _STR_CONSTS:
        v = getattr(mod, n, None)
        if isinstance(v, str):
            out[n] = {"len": len(v), "head": v[:12]}
    for n in ("_SCHEME_UPGRADE_RE",):
        v = getattr(mod, n, None)
        if v is not None and hasattr(v, "pattern"):
            out[n] = {"pattern": v.pattern, "flags": int(v.flags)}
    return out


def _class_methods(mod) -> dict:
    out: dict[str, dict] = {}
    for cname in ("RobustSession", "XzfgkScraper"):
        cls = getattr(mod, cname, None)
        if cls is None:
            continue
        methods: dict[str, list[str]] = {}
        for mname, fn in inspect.getmembers(cls, predicate=inspect.isfunction):
            if mname.startswith("__") and mname not in ("__init__",):
                continue
            try:
                sig = inspect.signature(fn)
            except (TypeError, ValueError):
                methods[mname] = ["<unavailable>"]
                continue
            methods[mname] = [p for p in sig.parameters]
        out[cname] = methods
    return out


def _normalize_default(v: str) -> str:
    """把默认值里的**仓根前缀**归一为 `<ROOT>`（防"盘符字面量"门禁）。

    `--out-dir` 的默认值是 `paths.ROOT/modules/.../data/raw`（**绝对路径**）⇒
    直接写进基线就含盘符。归一化后既保留"默认值指向哪"的语义，又不泄漏本机路径。
    """
    if not v:
        return v
    s = str(v)
    for cand in (ROOT, ROOT.replace("\\", "/")):
        s = s.replace(cand, "<ROOT>")
    return s


def _cli_options(mod) -> dict:
    """抓 `main()` 的 argparse 选项（不改行为：用 monkeypatch 截获 add_argument）。"""
    captured: list[dict] = []

    import argparse

    orig = argparse.ArgumentParser.add_argument

    def spy(self, *a, **kw):
        captured.append({
            "flags": [str(x) for x in a if isinstance(x, str)],
            "default": (_normalize_default(str(kw["default"]))
                        if "default" in kw and kw["default"] is not None else None),
            "action": str(kw.get("action") or "store"),
            "required": bool(kw.get("required", False)),
        })
        return orig(self, *a, **kw)

    argv_backup = sys.argv[:]
    argparse.ArgumentParser.add_argument = spy  # type: ignore[method-assign]
    try:
        # 只解析、不执行：`--help` 会在 parse_args 后 raise SystemExit
        sys.argv = ["collector.py", "--help"]
        try:
            mod.main()
        except SystemExit:
            pass
        except Exception:  # noqa: BLE001  --help 之外的失败与"选项集合"无关
            pass
    finally:
        argparse.ArgumentParser.add_argument = orig  # type: ignore[method-assign]
        sys.argv = argv_backup
    return {"count": len(captured), "options": captured}


def build() -> dict:
    import importlib

    m = importlib.import_module(
        "modules.regulatory_scrapers.collectors.gov.collector")
    present = [n for n in MODULE_NAMES if hasattr(m, n)]
    return {
        "_note": "gov/collector.py 拆分等价性基线（结构快照，非 hash）",
        "module_names": {"expected": list(MODULE_NAMES),
                         "present": sorted(present),
                         "missing": sorted(set(MODULE_NAMES) - set(present))},
        "const_digest": _const_digest(m),
        "class_methods": _class_methods(m),
        "cli": _cli_options(m),
    }


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    # ⚠️ 引导放在**函数内**：模块级 `sys.path.insert` 会让
    # `gate_import_bootstrap` 的 `tests/` 计数 +1（该基线"只减不增"）。
    # pytest 下 `tests/conftest.py` 已 bootstrap，这里只是 `python -m` 直跑时的兜底。
    # 无需 `sys.path.insert`：`pytest` 下 `tests/conftest.py` 已 bootstrap；
    # `python -m tests.gov_collector_baseline` 时 `sys.path[0]` 即 cwd（仓根）
    # ⇒ `from bootstrap import bootstrap` 天然可用。
    # （`gate_import_bootstrap` 的 `tests/` 基线"只减不增"，多一行 insert 就顶破。）
    from bootstrap import bootstrap

    bootstrap("all")

    cur = build()
    if "--write" in argv:
        os.makedirs(os.path.dirname(BASELINE), exist_ok=True)
        with open(BASELINE, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(cur, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")
        print(f"[baseline] 已写入 {BASELINE}")
        print(f"  模块名 {len(cur['module_names']['present'])}/"
              f"{len(MODULE_NAMES)}  CLI 选项 {cur['cli']['count']}")
        return 0

    if not os.path.exists(BASELINE):
        print("[baseline] 缺基线文件，先跑 --write")
        return 2
    base = json.load(open(BASELINE, encoding="utf-8"))
    diffs: list[str] = []
    for k in ("module_names", "const_digest", "class_methods", "cli"):
        if base.get(k) != cur.get(k):
            diffs.append(k)
    if diffs:
        print("[baseline] **不一致** 段落:", diffs)
        for k in diffs:
            print(f"--- {k}")
            print("  基线:", json.dumps(base.get(k), ensure_ascii=False)[:600])
            print("  当前:", json.dumps(cur.get(k), ensure_ascii=False)[:600])
        return 1
    print("[baseline] 一致 ✓（模块名/常量/方法签名/CLI 选项）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
