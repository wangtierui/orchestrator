# -*- coding: utf-8 -*-
"""
tools/gen_schedule_doc.py — 由 `config/schedule.yaml` **反向生成**运行手册定时表（v2 §3.9，P2-2）

背景（v2 §3.13.1 M3）：手册 `reports/运行手册_编排与定时_*.md` 的定时表是**手抄**的，
与实测调度记录二义（01:00 vs 01:30、06:30 vs 06:00–06:37）。本节把定时表改为**生成物**：
`config/schedule.yaml` 是唯一事实源，本工具渲染手册的自动段与 crontab 片段，
`gate_config_integrity` 判据 S 断言「手册自动段 == 本工具渲染结果」（手改手册即 FAIL）。

用法：
    python tools/gen_schedule_doc.py            # 写出手册自动段（默认）
    python tools/gen_schedule_doc.py --check    # 只比对，漂移则 rc=1（供门禁/CI）
    python tools/gen_schedule_doc.py --print    # 打到 stdout（不写文件）
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import paths
from config.exitcodes import ExitCode

SCHEDULE_YAML = os.path.join(paths.CONFIG_DIR, "schedule.yaml")
MANUAL = os.path.join(paths.ROOT, "reports", "运行手册_编排与定时_20260912.md")
# N-186（2026-10-08）：README §5.2 是**同一张定时表**，但此前**无人渲染**（本工具只写手册）
#   ⇒ 手工副本必然漂移（实测：缺新作业，且仍是 N-51 之前的 `--only 6.9 / --only 21.5` 旧形态），
#   而它**没有门禁守护**（判据 S4 只守手册）→ 读者据 README 会误判"哪个源没有采集"。
#   现把 README 的该段纳入**同一渲染源**（行由 `render_table` 生成、表头静态固定），
#   并把 S4 扩展到 README ⇒ 漂移即 CI FAIL。
README = os.path.join(paths.ROOT, "README.md")
TABLE_START, TABLE_END = "<!-- SCHED:AUTO -->", "<!-- SCHED:END -->"
CRON_START, CRON_END = "<!-- CRON:AUTO -->", "<!-- CRON:END -->"

_WEEK = {"0": "日", "1": "一", "2": "二", "3": "三", "4": "四", "5": "五", "6": "六", "7": "日"}


def load_schedule() -> dict:
    """读 `config/schedule.yaml`（无 PyYAML 或文件缺失 → 抛错，不做静默兜底）。"""
    import yaml

    with open(SCHEDULE_YAML, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def cron_label(when: str) -> str:
    """cron 五段式 → 人读频率标签（如 `0 6 * * *` → 每日 06:00；无法识别则原样返回）。"""
    parts = (when or "").split()
    if len(parts) != 5:
        return when or "—"
    mi, hh, dom, _mon, dow = parts
    if not (mi.isdigit() and hh.isdigit()):
        return when
    clock = f"{int(hh):02d}:{int(mi):02d}"
    if dow != "*":
        return f"每周{_WEEK.get(dow, dow)} {clock}"
    if dom != "*":
        return f"每月 {dom} 日 {clock}"
    return f"每日 {clock}"


def _clock_key(job: dict) -> tuple:
    """排序键：cron 类按「一天内的钟点」升序，事件驱动类排在最后（再按 id 稳定）。"""
    if job.get("kind") != "cron":
        return (1, 99, 99, job.get("id", ""))
    parts = (job.get("when") or "").split()
    mi = int(parts[0]) if len(parts) >= 2 and parts[0].isdigit() else 99
    hh = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else 99
    return (0, hh, mi, job.get("id", ""))


def render_table(jobs: list[dict]) -> str:
    """渲染定时表（markdown 表体，不含表头分隔行——表头由手册模板固定）。"""
    rows = []
    for j in sorted(jobs, key=_clock_key):
        if j.get("kind") == "cron":
            label = cron_label(j.get("when", ""))
            when = "`" + j.get("when", "") + "`"
        else:
            label = f"事件驱动（after: {j.get('after', '—')}）"
            when = "—"
        cmd = "`" + " ".join(j.get("argv") or []) + "`"
        desc = (j.get("desc") or "").strip()
        if j.get("on_miss") and j.get("on_miss") != "skip":
            desc += f"（漏跑策略：{j['on_miss']}）"
        rows.append(f"| **{label}** | {when} | {cmd} | {desc} |")
    return "\n".join(rows)


def render_cron(jobs: list[dict], *, repo_placeholder: str = "/path/to/repo") -> str:
    """渲染 crontab 片段（仅 cron 类作业；统一走 `cli.py` 唯一入口）。"""
    lines = ["```cron"]
    for j in jobs:
        if j.get("kind") != "cron":
            continue
        argv = " ".join(a for a in (j.get("argv") or []) if a != "cli.py")
        lines.append(
            f"{j['when']}   cd {repo_placeholder} && python cli.py{(' ' + argv) if argv else ''}"
            f" >> logs/cron_{j['id']}.log 2>&1"
        )
    lines.append("```")
    return "\n".join(lines)


def _replace_block(text: str, start: str, end: str, body: str) -> str:
    """替换 `start` … `end` 之间的内容（保留标记本身）。"""
    i, k = text.find(start), text.find(end)
    if i < 0 or k < 0 or k < i:
        raise LookupError(f"手册缺少标记对：{start} … {end}")
    return text[: i + len(start)] + "\n" + body + "\n" + text[k:]


def build_blocks() -> tuple[str, str, dict]:
    """返回 (定时表 body, crontab body, schedule dict)。"""
    data = load_schedule()
    jobs = data.get("jobs") or []
    return render_table(jobs), render_cron(jobs), data


def write_manual(check: bool = False) -> tuple[bool, str]:
    """写/比对手册自动段。返回 (ok, 说明)。"""
    table, cron, _data = build_blocks()
    with open(MANUAL, encoding="utf-8") as fh:
        text = fh.read()
    new = _replace_block(text, TABLE_START, TABLE_END, table)
    new = _replace_block(new, CRON_START, CRON_END, cron)
    if new == text:
        return True, "手册自动段与 schedule.yaml 一致（无漂移）"
    if check:
        return False, (
            "手册自动段与 schedule.yaml **不一致**：请运行 "
            "`python tools/gen_schedule_doc.py` 重写（勿手改手册）"
        )
    with open(MANUAL, "w", encoding="utf-8", newline="") as fh:
        fh.write(new)
    return True, f"手册自动段已重写：{os.path.relpath(MANUAL, paths.ROOT)}"


def write_readme(check: bool = False) -> tuple[bool, str]:
    """写/比对 README 定时表自动段（N-186：与手册**同一渲染源**，消灭手工副本）。"""
    table, _cron, _data = build_blocks()
    if not os.path.exists(README):
        return True, "（缺 README：跳过）"
    with open(README, encoding="utf-8") as fh:
        text = fh.read()
    try:
        new = _replace_block(text, TABLE_START, TABLE_END, table)
    except LookupError as e:
        return False, f"README 定时表缺自动段标记对：{e}"
    if new == text:
        return True, "README 定时表与 schedule.yaml 一致（无漂移）"
    if check:
        return False, (
            "README 定时表与 config/schedule.yaml **不一致**：请运行 "
            "`python tools/gen_schedule_doc.py` 重写（勿手改 README 该段）"
        )
    with open(README, "w", encoding="utf-8", newline="") as fh:
        fh.write(new)
    return True, f"README 定时表已重写：{os.path.relpath(README, paths.ROOT)}"


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--print" in argv:
        table, cron, data = build_blocks()
        print(render_table(data.get("jobs") or []).replace("| **", "\n| **").lstrip())
        print()
        print(cron)
        return ExitCode.OK
    ok1, msg1 = write_manual(check="--check" in argv)
    ok2, msg2 = write_readme(check="--check" in argv)
    ok = ok1 and ok2
    print(f"[schedule-doc] {'OK' if ok else 'FAIL'} {msg1}；{msg2}")
    return ExitCode.OK if ok else ExitCode.FAIL


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        pass
    raise SystemExit(main())
