# -*- coding: utf-8 -*-
"""
tools/install_schedule.py — 由 `config/schedule.yaml` 生成并安装计划任务（v2 §3.13.6，P2-6）

原状（v2 §3.13.6）：手册 L110 教人**手工**建 Windows 计划任务（"参数: tools\\run_production_refresh.py"），
于是"已安装的任务"与"文档/唯一入口"三者长期不一致且无人能验证。本工具把安装也变成**由 yaml 派生**：

    --print      打印 crontab 片段（人类可复制；与手册生成段同源）
    --install    生成 Task Scheduler XML 并 `schtasks /create /xml` 注册（默认 `<prefix>_<id>`）
    --verify     比对**已安装任务**与 yaml（不匹配 → rc=1；供 `cli.py schedule verify` 与 doctor）
    --remove     删除本工具注册的任务（按同一命名规则）
    --list       列出将要操作的 task 名

命名规则：`REG_ORCH_<id>`（固定前缀，使 verify/remove 可枚举、不误伤他人任务）。
纪律：argv 一律来自 yaml，**不在此处再写一遍命令**（否则又出现第二处事实源）。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import xml.sax.saxutils as sx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import paths
from config.exitcodes import ExitCode

PREFIX = "REG_ORCH_"
SCHTASKS = "schtasks"


def _gsd():
    """按文件路径加载 `tools/gen_schedule_doc.py`（tools 非包；避免新增 sys.path 注入）。"""
    import importlib.util

    fp = os.path.join(paths.ROOT, "tools", "gen_schedule_doc.py")
    spec = importlib.util.spec_from_file_location("_gsd_sched", fp)
    assert spec is not None and spec.loader is not None   # 仓内文件必存在（供 mypy 收窄）
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def task_name(job_id: str) -> str:
    return PREFIX + job_id


def _cron_parts(when: str) -> tuple[str, str, str, str, str] | None:
    p = (when or "").split()
    return (p[0], p[1], p[2], p[3], p[4]) if len(p) == 5 else None


# N-44（P9，2026-09-26）：Windows 计划任务 XML 的 <DaysOfWeek> 节点要求**首字母大写**
# （<Monday/> 而非 <MONDAY/>）——原为全大写，`schtasks` 报「task XML contains an unexpected
# node」(10,71):MONDAY:。与下方 _MONTH_ELEM 的 `.capitalize()` 口径对齐。
_WEEK_MAP = {
    "0": "Sunday",
    "1": "Monday",
    "2": "Tuesday",
    "3": "Wednesday",
    "4": "Thursday",
    "5": "Friday",
    "6": "Saturday",
    "7": "Sunday",
}
_MONTH_NUM = {
    m: i + 1
    for i, m in enumerate(
        ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
    )
}
_MONTH_ELEM = {
    i + 1: m.capitalize()
    for i, m in enumerate(
        (
            "JANUARY",
            "FEBRUARY",
            "MARCH",
            "APRIL",
            "MAY",
            "JUNE",
            "JULY",
            "AUGUST",
            "SEPTEMBER",
            "OCTOBER",
            "NOVEMBER",
            "DECEMBER",
        )
    )
}


def _start_boundary(mi: str, hh: str) -> str:
    return f"2000-01-01T{int(hh):02d}:{int(mi):02d}:00"


def build_xml(job: dict) -> str:
    """cron → Task Scheduler XML。**只支持可无损表达的形态**，其余抛 ValueError（不猜）。"""
    parts = _cron_parts(job.get("when", ""))
    if not parts:
        raise ValueError(f"{job.get('id')}: when 非 5 段 cron")
    mi, hh, dom, mon, dow = parts
    if not (mi.isdigit() and hh.isdigit()):
        raise ValueError(f"{job.get('id')}: 仅支持具体分钟/小时（收到 {mi}/{hh}）")
    if mon != "*":
        # 形如 1,4,7,10 → 多 CalendarTrigger
        months = [m.strip() for m in mon.split(",") if m.strip()]
    else:
        months = ["*"]
    sched = []
    for m in months:
        mon_xml = ""
        if m != "*":
            if m not in _MONTH_NUM:
                raise ValueError(f"{job.get('id')}: 月份 {m!r} 不支持（用 JAN..DEC）")
            mon_xml = "\n            <Months><" + _MONTH_ELEM[_MONTH_NUM[m]] + "/></Months>"
        if dow != "*":
            days = "".join(f"<{_WEEK_MAP[d]}/>" for d in dow.split(",") if d in _WEEK_MAP)
            trigger = (
                f"      <CalendarTrigger>\n"
                f"        <StartBoundary>{_start_boundary(mi, hh)}</StartBoundary>\n"
                f"        <ScheduleByWeek><WeeksInterval>1</WeeksInterval>"
                f"<DaysOfWeek>{days}</DaysOfWeek></ScheduleByWeek>\n"
                f"      </CalendarTrigger>"
            )
        elif dom != "*":
            trigger = (
                f"      <CalendarTrigger>\n"
                f"        <StartBoundary>{_start_boundary(mi, hh)}</StartBoundary>\n"
                f"        <ScheduleByMonth><DaysOfMonth><Day>{int(dom)}</Day></DaysOfMonth>"
                f"{mon_xml}</ScheduleByMonth>\n"
                f"      </CalendarTrigger>"
            )
        else:
            trigger = (
                f"      <CalendarTrigger>\n"
                f"        <StartBoundary>{_start_boundary(mi, hh)}</StartBoundary>\n"
                f"        <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>\n"
                f"      </CalendarTrigger>"
            )
        sched.append(trigger)
    argv = list(job.get("argv") or [])
    if not argv:
        raise ValueError(f"{job.get('id')}: argv 为空")
    head, rest = argv[0], argv[1:]
    if head in ("python", "python.exe", "py", "python3"):
        command = sys.executable if head != "py" else "py"
        args = " ".join(rest)
    else:
        # 2026-09-27 修复：脚本（如 `cli.py`）**以当前解释器显式调用**，不依赖 `.py` 文件关联。
        # 原实现把 Command 写成 `<仓根>/cli.py`，Windows 按文件关联选择 Python —— 关联可能指向
        # 另一个缺依赖的解释器，任务静默 rc=3（ENV，doctor deps FAIL）且无输出可查。
        # 显式 `sys.executable`（安装时所用解释器，依赖已就绪）+ 脚本绝对路径。
        command = sys.executable
        args = " ".join([os.path.join(paths.ROOT, head), *rest])
    xml = (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        '<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
        "  <RegistrationInfo>\n"
        f"    <Description>REG_ORCH {sx.escape(job.get('id', ''))}: "
        f"{sx.escape(job.get('desc', ''))}</Description>\n"
        "    <Author>regulatory_compliance_orchestrator</Author>\n"
        "  </RegistrationInfo>\n"
        "  <Triggers>\n" + "\n".join(sched) + "\n  </Triggers>\n"
        '  <Principals><Principal id="Author"><LogonType>InteractiveToken</LogonType>'
        "<RunLevel>LeastPrivilege</RunLevel></Principal></Principals>\n"
        "  <Settings>\n"
        "    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\n"
        # 2026-09-27 修复（计划任务从未执行）：原为 true/true —— 笔记本在电池上时任务
        # **从不启动**（实测 REG_ORCH_refresh Last Run=1999-11-30、Last Result=267011
        # 「尚未运行」，而 Next Run 恒为计划时刻）。数据管道属"每日必跑"任务，不应受
        # 电源状态左右；改用 false（电池上照跑、切电池不中断）。
        "    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>\n"
        "    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>\n"
        "    <StartWhenAvailable>true</StartWhenAvailable>\n"
        # 不因 Windows 网络探测（不可靠，常见误判）跳过；采集类任务缺网时自身有重试/降级。
        "    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>\n"
        # 唤醒睡眠中的计算机执行（06:00 定时任务；对用户影响最小，保证"每日必跑"）。
        "    <WakeToRun>true</WakeToRun>\n"
        "    <ExecutionTimeLimit>PT12H</ExecutionTimeLimit>\n"
        "    <Enabled>true</Enabled>\n"
        "  </Settings>\n"
        '  <Actions Context="Author">\n'
        "    <Exec>\n"
        f"      <Command>{sx.escape(command)}</Command>\n"
        f"      <Arguments>{sx.escape(args)}</Arguments>\n"
        f"      <WorkingDirectory>{sx.escape(paths.ROOT)}</WorkingDirectory>\n"
        "    </Exec>\n"
        "  </Actions>\n"
        "</Task>\n"
    )
    return xml


def cron_jobs() -> list[dict]:
    return [j for j in (_gsd().load_schedule().get("jobs") or []) if j.get("kind") == "cron"]


def _run(argv: list[str]) -> tuple[int, str]:
    try:
        r = subprocess.run(argv, capture_output=True, timeout=60)
        # 批 60：**逐编码严格解码**。原按 `utf-8` 解码而 zh-CN 下 `schtasks` 输出为 **GBK** ⇒ 乱码 ⇒
        # `installed_argv` 的中文标签（"要运行的任务"）匹配失败 ⇒ 参数读成空 ⇒ **verify 全量假 FAIL** ✗
        # （实测：`schtasks /query /tn REG_ORCH_monthly_check /fo LIST /v` 参数实为 `cli.py gates` ✓）。
        raw = (r.stdout or b"") + (r.stderr or b"")
        text = ""
        for _enc in ("utf-8", "gbk", "cp936"):
            try:
                text = raw.decode(_enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            text = raw.decode("utf-8", "replace")
        return r.returncode, text
    except Exception as e:  # noqa: BLE001  schtasks 不可用（非 Windows/权限）→ 明确返回
        return 127, f"{type(e).__name__}: {e}"


def schtasks_usable() -> bool:
    """`schtasks` 是否可用（非 Windows / 无权限 → False；用于"不判定"而非"未安装"）。"""
    import shutil

    return shutil.which(SCHTASKS) is not None


def installed_argv(task: str) -> tuple[str, str] | None:
    """读已安装任务的 (Arguments, StartBoundary)；任务不存在/不可查 → None。"""
    rc, out = _run([SCHTASKS, "/query", "/tn", task, "/fo", "LIST", "/v"])
    if rc != 0:
        return None
    args = ""
    start = ""
    for ln in out.splitlines():
        low = ln.lower()
        if "task to run" in low or "要运行的任务" in low:
            args = ln.split(":", 1)[-1].strip()
        if "start time" in low or "开始时间" in low:
            start = ln.split(":", 1)[-1].strip()
    return args, start


def verify() -> tuple[bool, dict]:
    """比对已安装任务与 yaml（`--verify` 与 `cli.py schedule verify`、doctor、CI 共用）。

    N-61（2026-09-27）：**先探测 `schtasks` 可用性**——不可用（非 Windows / 无权限）时返回
    "一致但未实检"（`skipped=True`）。原实现直接逐任务查询，不可用环境下全部 `None`
    → 全判"未安装" → 在 Linux/CI 产出**假 FAIL**，且真实漂移被淹没（本批实测：
    `schedule.yaml` 改过 argv 而系统仍是旧版，因 verify **未进 CI** 而无人发现）。
    """
    if not schtasks_usable():
        return True, {
            "problems": [],
            "rows": [],
            "skipped": True,
            "note": "schtasks 不可用（非 Windows / 无权限）→ 跳过系统侧比对",
        }
    problems: list[str] = []
    rows: list[dict] = []
    for j in cron_jobs():
        tn = task_name(j["id"])
        got = installed_argv(tn)
        want_args = " ".join((j.get("argv") or [])[1:])
        rows.append(
            {
                "id": j["id"],
                "task": tn,
                "installed": got is not None,
                "want_args": want_args,
                "got_args": (got[0] if got else ""),
                "start": (got[1] if got else ""),
            }
        )
        if got is None:
            problems.append(f"{tn}: 未安装（yaml 期望 {want_args!r}）")
        elif want_args and want_args not in got[0]:
            problems.append(f"{tn}: 参数不匹配（yaml={want_args!r} / 已装={got[0]!r}）")
    return (not problems), {"problems": problems, "rows": rows, "skipped": False}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="由 config/schedule.yaml 生成/安装/校验计划任务")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--print", dest="do_print", action="store_true", help="打印 crontab 片段")
    g.add_argument("--install", action="store_true", help="生成 XML 并 schtasks 注册")
    g.add_argument("--verify", action="store_true", help="比对已安装任务与 yaml")
    g.add_argument("--remove", action="store_true", help="删除本工具注册的任务")
    g.add_argument("--list", action="store_true", help="列出任务名")
    ap.add_argument(
        "--xml-dir",
        default=os.path.join(paths.ROOT, "reports", "_tmp", "schedule"),
        help="XML 落盘目录（默认 reports/_tmp/schedule）",
    )
    args = ap.parse_args(argv)

    jobs = cron_jobs()
    if args.do_print:
        print(_gsd().render_cron(jobs))
        return ExitCode.OK
    if args.list:
        for j in jobs:
            print(f"{task_name(j['id']):<28} {j['when']:<12} {' '.join(j['argv'])}")
        return ExitCode.OK
    if args.verify:
        ok, det = verify()
        for r in det["rows"]:
            print(
                f"  {'[OK]  ' if r['installed'] else '[缺失]'} {r['task']:<28} {r['got_args'][:60]}"
            )
        for p in det["problems"]:
            print(f"  [FAIL] {p}")
        print("[schedule] " + ("已安装任务与 schedule.yaml 一致" if ok else "存在不一致"))
        return ExitCode.OK if ok else ExitCode.FAIL
    if args.remove:
        rc_all = 0
        for j in jobs:
            rc, out = _run([SCHTASKS, "/delete", "/tn", task_name(j["id"]), "/f"])
            print(
                f"  {'[OK]  ' if rc == 0 else '[FAIL]'} delete {task_name(j['id'])} {out.strip()[:60]}"
            )
            rc_all |= rc
        return ExitCode.OK if rc_all == 0 else ExitCode.FAIL

    # ---- --install ----
    os.makedirs(args.xml_dir, exist_ok=True)
    rc_all = 0
    for j in jobs:
        tn = task_name(j["id"])
        try:
            xml = build_xml(j)
        except ValueError as e:
            print(f"  [FAIL] {tn}: 无法生成 XML —— {e}（请在 yaml 中用可无损表达的 cron）")
            rc_all = 1
            continue
        fp = os.path.join(args.xml_dir, f"{tn}.xml")
        with open(fp, "w", encoding="utf-16", newline="") as fh:
            fh.write(xml)
        rc, out = _run([SCHTASKS, "/create", "/tn", tn, "/xml", fp, "/f"])
        print(f"  {'[OK]  ' if rc == 0 else '[FAIL]'} create {tn} （{fp}）{out.strip()[:80]}")
        rc_all |= rc
    print("[schedule] 安装完成" if rc_all == 0 else "[schedule] 安装存在失败项")
    return ExitCode.OK if rc_all == 0 else ExitCode.FAIL


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        pass
    raise SystemExit(main())
