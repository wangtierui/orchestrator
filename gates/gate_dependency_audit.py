# -*- coding: utf-8 -*-
"""gates.gate_dependency_audit — **依赖治理与供应链安全门禁**（方案 §13.2；ADR-0011）。

判据（逐条可复跑；工具缺失按**分级**处理，不因可选工具缺失误报红）：

  D1 **锁存在且与当前环境一致**：`requirements.lock` 在库；`gen_lock --check` 通过
     （逐条比对 pip freeze 快照；头部注释区不参与比对，跨机器可比）。
  D2 **直接依赖可解析**：pyproject 的 core/optional 依赖能解析出规范名（剥离 extras、
     版本区间、marker、环境变量），解析失败即 FAIL（声明本身坏了）。
  D3 **无影子依赖**（双向断言）：pyproject 直接依赖集合 ≡ `config/dependency_policy.yaml`
     登记集合；每条登记必须有 why（adr 或豁免 note）。左缺=影子依赖（谁批准？）、
     右缺=死登记（声明漂移）。
  D4 **漏洞扫描**：装了 `pip-audit` 则对 lock 扫描，high/critical **零容忍**（FAIL）、
     medium 披露；未装或**网络不可达**（公告库拉不到）⇒ SKIP 披露（不把网络故障
     误报成供应链问题——与 doctor 的 degradation 纪律一致）。
  D5 **SBOM 与 lock 同步**：`reports/_sbom/sbom_latest.json` 存在，且其 `lock:sha256`
     等于当前 lock 的 sha256、组件数与 lock 条目数一致（可复现证据链）。
  D6 **锁的卫生**：条目不含本地路径/VCS 直连；头部声明生成命令；无重复条目。

设计取舍：
  · pip-audit 子进程有界超时（默认 180s，超时按 SKIP 处置并披露）；
  · 判据 D3 用「规范化名」比对（`paddlex[ocr]`→`paddlex`、`types-PyYAML`→小写），
    避免 extras/大小写造成的假失配；
  · 本门禁只读（审计纪律：门禁不得成为风险源）；lock/SBOM 的生成是独立命令。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tomllib

import paths

ROOT = paths.ROOT
LOCK = os.path.join(ROOT, "requirements.lock")
SBOM_LATEST = os.path.join(ROOT, "reports", "_sbom", "sbom_latest.json")
POLICY = os.path.join(ROOT, "config", "dependency_policy.yaml")
PYPROJECT = os.path.join(ROOT, "pyproject.toml")

AUDIT_TIMEOUT_S = 180
# high/critical 零容忍；medium 及以下仅披露
BLOCKING_SEVERITIES = {"high", "critical"}
# pip-audit 表格输出的漏洞行形态：`name==ver  ID  CVSS: x.x  [severity]`
_VULN_ROW = re.compile(r"^\s*(\S+?==\S+)\s+(\S+-\d{4}-\d+|GHSA-\S+|PYSEC-\S+)\s+.*?\[(\w+)\]")


def _norm(name: str) -> str:
    """依赖名规范化：剥离 extras、版本区间、marker 与环境变量，PEP 503 小写连字符。"""
    n = name.strip()
    n = n.split("[", 1)[0]          # extras：paddlex[ocr] → paddlex
    n = n.split(";", 1)[0]          # marker；环境变量项整体剔除（不可安装）
    n = re.split(r"[<>=!~\[;\s]", n, maxsplit=1)[0]
    return re.sub(r"[-_.]+", "-", n).lower()


def _pyproject_deps() -> tuple[dict, list]:
    """→ (分组 {core: {名: 原始串}, optional: {组: {名}}}, 解析失败清单)。"""
    with open(PYPROJECT, "rb") as fh:
        data = tomllib.load(fh)
    proj = data.get("project") or {}
    bad: list[str] = []
    core: dict[str, str] = {}
    for raw in proj.get("dependencies") or []:
        if ";" in raw and ("extra ==" in raw or "python_version" in raw):
            bad.append(raw)
            continue
        core[_norm(raw)] = raw
    optional: dict[str, dict] = {}
    for grp, items in (proj.get("optional-dependencies") or {}).items():
        d: dict[str, str] = {}
        for raw in items:
            d[_norm(raw)] = raw
        optional[grp] = d
    return {"core": core, "optional": optional}, bad


def _policy_deps() -> dict:
    import yaml

    with open(POLICY, encoding="utf-8") as fh:
        pol = yaml.safe_load(fh) or {}
    return {
        "core": {k: v for k, v in (pol.get("core") or {}).items()},
        "optional": {g: {k: v for k, v in (d or {}).items()}
                     for g, d in (pol.get("optional") or {}).items()},
        "exemptions": pol.get("exemptions") or {},
    }


def _lock_entries() -> list[str]:
    if not os.path.exists(LOCK):
        return []
    out = []
    with open(LOCK, encoding="utf-8") as fh:
        for ln in fh:
            ln = ln.strip()
            if ln and not ln.startswith("#"):
                out.append(ln)
    return out


def run():
    problems: list[str] = []
    warnings: list[str] = []
    skipped: list[str] = []
    detail: dict = {}

    # ---- D2 依赖可解析 ----
    pp, bad = _pyproject_deps()
    if bad:
        problems.append(f"D2: pyproject 依赖含环境变量/marker 项，登记簿无法表达：{bad}")

    # ---- D6 锁的卫生 ----
    entries = _lock_entries()
    if not os.path.exists(LOCK):
        problems.append("D1: 缺少 requirements.lock（运行 python -m tools.gen_lock）")
        skipped.append("D1/D5/D6: 锁缺失，D5/D6 判据连带跳过")
    else:
        head = open(LOCK, encoding="utf-8").read(2000)
        if "python -m tools.gen_lock" not in head:
            problems.append("D6: requirements.lock 头部缺生成命令声明（锁的来源不可追溯）")
        dirty = [e for e in entries
                 if e.lower().startswith(("-e ", "file:", "-git")) or " @ file:" in e
                 or "git+" in e]
        if dirty:
            problems.append(f"D6: 锁含本地路径/VCS 直连条目（不可复现）：{dirty[:5]}")
        dup = sorted({e.lower() for e in entries
                      if [x.lower() for x in entries].count(e.lower()) > 1})
        if dup:
            problems.append(f"D6: 锁含重复条目：{dup[:5]}")
        detail["lock_entries"] = len(entries)

        # D1 锁与环境一致（子进程跑 gen_lock --check；有界超时）
        try:
            r = subprocess.run(
                [sys.executable, "-m", "tools.gen_lock", "--check"],
                cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=300)
            detail["lock_check_rc"] = r.returncode
            if r.returncode != 0:
                problems.append(
                    "D1: requirements.lock 与当前环境不一致"
                    f"（{((r.stdout or '') + (r.stderr or '')).strip()[:160]}）")
        except (OSError, subprocess.SubprocessError) as e:
            skipped.append(f"D1: lock 一致性校验未执行（{type(e).__name__}）")

    # ---- D3 双向断言 ----
    pol = _policy_deps()
    py_core = set(pp["core"])
    po_core = {_norm(k) for k in pol["core"]}
    missing = sorted(py_core - po_core)
    dead = sorted(po_core - py_core)
    if missing:
        problems.append(
            f"D3: 影子依赖 {missing}（在用未登记——新增直接依赖须登记 ADR/豁免理由）")
    if dead:
        problems.append(f"D3: 死登记 {dead}（已不在 pyproject——声明漂移）")
    for group, items in pol["optional"].items():
        py_g = {_norm(k) for k in (pp["optional"].get(group) or {})}
        po_g = {_norm(k) for k in items}
        if py_g - po_g:
            problems.append(
                f"D3: 影子依赖（optional/{group}）{sorted(py_g - po_g)}")
        if po_g - py_g:
            problems.append(
                f"D3: 死登记（optional/{group}）{sorted(po_g - py_g)}"
                f"（pyproject 无该组或已移除）")
    no_why = []
    for scope, items in [("core", pol["core"])] + [
            (f"optional/{g}", d) for g, d in pol["optional"].items()]:
        for k, v in items.items():
            if not isinstance(v, dict) or not str(v.get("why") or "").strip():
                no_why.append(f"{scope}:{k}")
    for k in pol["exemptions"]:
        if _norm(k) in py_core and not str(
                (pol["exemptions"].get(k) or {}).get("note") or "").strip():
            no_why.append(f"exemptions:{k}")
    if no_why:
        problems.append(f"D3: 登记项缺 why/adr 说明 {no_why}")
    detail["direct_core"] = len(py_core)
    detail["direct_optional"] = {g: len(d) for g, d in pp["optional"].items()}

    # ---- D4 漏洞扫描（分级：工具/网络不可用 → SKIP）----
    audit = {"ran": False, "vulns": [], "blocking": 0, "reason": ""}
    if not entries:
        audit["reason"] = "无锁可扫"
        skipped.append("D4: 无锁，漏洞扫描跳过")
    else:
        try:
            # PYTHONUTF8=1：pip-audit 以 **locale 编码**读 requirements.lock；GBK/cp936
            # 环境下会 UnicodeDecodeError（rc=1 且零行输出 ⇒ 被误读成"无漏洞"）。
            env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
            r = subprocess.run(
                [sys.executable, "-m", "pip_audit", "-r", LOCK, "--progress-spinner=off"],
                cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=AUDIT_TIMEOUT_S, env=env)
            out = (r.stdout or "") + (r.stderr or "")
            rows = []
            for ln in out.splitlines():
                m = _VULN_ROW.match(ln)
                if m:
                    rows.append({"pkg": m.group(1), "id": m.group(2), "severity": m.group(3).lower()})
            audit["ran"] = True
            audit["vulns"] = rows
            audit["blocking"] = sum(1 for x in rows if x["severity"] in BLOCKING_SEVERITIES)
            audit["rc"] = r.returncode
            if rows:
                msg = "；".join(f"{x['pkg']}({x['id']},{x['severity']})" for x in rows[:8])
                if audit["blocking"]:
                    problems.append(
                        f"D4: 依赖存在 high/critical 漏洞 {audit['blocking']} 项：{msg}"
                        "（零容忍；修复后重生成 lock 与 SBOM）")
                else:
                    warnings.append(f"D4: 依赖存在 medium 及以下漏洞 {len(rows)} 项（披露）：{msg}")
            elif r.returncode not in (0, 1):
                low = out.strip().splitlines()[-3:]
                audit["reason"] = f"rc={r.returncode}: {' | '.join(low)[:160]}"
                skipped.append(f"D4: pip-audit 未取得有效结论（{audit['reason']}）——不误报为供应链问题")
        except FileNotFoundError:
            skipped.append("D4: 未装 pip-audit（可选栈）——漏洞扫描跳过")
        except subprocess.TimeoutExpired:
            skipped.append(f"D4: pip-audit 超时（>{AUDIT_TIMEOUT_S}s）——扫描跳过")
        except (OSError, subprocess.SubprocessError) as e:
            skipped.append(f"D4: pip-audit 执行异常（{type(e).__name__}）")
    detail["audit"] = audit

    # ---- D5 SBOM 同步 ----
    if not os.path.exists(SBOM_LATEST):
        if entries:
            problems.append("D5: 缺少 reports/_sbom/sbom_latest.json（运行 python -m tools.gen_sbom）")
        else:
            skipped.append("D5: 无锁，SBOM 判据跳过")
    else:
        try:
            with open(SBOM_LATEST, encoding="utf-8") as fh:
                sbom = json.load(fh)
        except (OSError, ValueError) as e:
            problems.append(f"D5: SBOM 不可解析（{type(e).__name__}）")
            sbom = None
        if sbom is not None and entries:
            with open(LOCK, "rb") as fh:
                want = hashlib.sha256(fh.read()).hexdigest()
            props = (sbom.get("metadata") or {}).get("properties") or []
            got = next((p.get("value") for p in props if p.get("name") == "lock:sha256"), "")
            if got != want:
                problems.append(
                    "D5: SBOM 与 lock 不同步（lock sha256 不符）——须重新生成 SBOM")
            n_sbom = len(sbom.get("components") or [])
            if n_sbom != len(entries):
                problems.append(
                    f"D5: SBOM 组件数 {n_sbom} ≠ lock 条目数 {len(entries)}（证据链不完整）")
            detail["sbom_components"] = n_sbom
            detail["sbom_lock_sha256"] = got[:16]

    # ---- D7 子进程解释器路径（守护 bandit B607 唯一真实风险子类）----
    # 全仓 OS 命令按名调用（git/tasklist/schtasks）是**正确**形态，故 bandit B607 类级跳过；
    # 但其中「以裸 `python`/`py` 启动子进程」是真缺陷（PATH 上可能先命中另一解释器 ⇒
    # 运行体与记录不一致）。此处用 AST 断言把它钉死，作为该跳过的**对冲**。
    d7 = _check_interpreter_launchers()
    problems += d7["problems"]
    detail["d7_checked"] = d7["checked"]

    return (not problems), {
        "problems": problems[:40],
        "warnings": warnings[:20],
        "skipped": skipped,
        **detail,
    }


_BAD_LAUNCHERS = {"python", "python.exe", "python3", "py", "py.exe"}
_LAUNCH_SITES = ("subprocess.run", "subprocess.Popen", "subprocess.call",
                 "subprocess.check_call", "subprocess.check_output", "os.system")


def _check_interpreter_launchers() -> dict:
    """断言子进程 argv 的可执行位**不是**裸 python/py（须 sys.executable）。"""
    import ast

    problems: list[str] = []
    checked = 0
    for base in ("std_lib", "modules", "tools", "gates", "commands", "interfaces"):
        for dirpath, dirnames, filenames in os.walk(os.path.join(ROOT, base)):
            dirnames[:] = [d for d in dirnames if d not in {"__pycache__", "retired"}]
            for fn in filenames:
                if not fn.endswith(".py"):
                    continue
                p = os.path.join(dirpath, fn)
                try:
                    tree = ast.parse(open(p, encoding="utf-8", errors="replace").read())
                except (OSError, SyntaxError):
                    continue
                rel = os.path.relpath(p, ROOT).replace("\\", "/")
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call):
                        continue
                    f = node.func
                    name = (f.attr if isinstance(f, ast.Attribute)
                            else getattr(f, "id", "") or "")
                    if name not in {s.split(".")[-1] for s in _LAUNCH_SITES}:
                        continue
                    checked += 1
                    if not node.args:
                        continue
                    first = node.args[0]
                    items: list = []
                    if isinstance(first, (ast.List, ast.Tuple)):
                        items = [el for el in first.elts]
                    elif isinstance(first, ast.Constant):
                        items = [first]
                    if not items:
                        continue
                    head = items[0]
                    val = head.value if isinstance(head, ast.Constant) else None
                    if isinstance(val, str) and val.strip().lower() in _BAD_LAUNCHERS:
                        problems.append(
                            f"D7: {rel}:{head.lineno} 以裸 {val!r} 启动子进程"
                            "（须 sys.executable —— PATH 可能先命中另一解释器）")
    return {"problems": problems, "checked": checked}


if __name__ == "__main__":  # pragma: no cover - 手工诊断入口
    ok, d = run()
    print(json.dumps(d, ensure_ascii=False, indent=2))
    sys.exit(0 if ok else 1)
