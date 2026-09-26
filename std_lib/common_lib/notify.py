# -*- coding: utf-8 -*-
"""
common_lib.notify — 告警通道（v2 §3.13.6，P2-6）

问题（v2 §3.13.6）：现状编排器**只有 `run_log` 与 stdout** —— 无人值守下失败无人知晓
（凌晨跑挂 → 早上才发现）。本模块把"该告诉人的事"落成**可审计的文件告警**：

    reports/_alerts/<ts>_<kind>.json      结构化（机器可读，供外部watcher/IDE 任务消费）
    reports/_alerts/<ts>_<kind>.md        人类摘要（含证据行，便于快速判断）

设计纪律
--------
① **零外部依赖**：默认 `kind=file`（写文件）；`webhook` 为可选，URL **只从环境变量
   `REG_ORCH_NOTIFY_WEBHOOK` 读取**（禁止入库、禁止硬编码）；
② **旁路设施**：任何异常一律降级（返回 None），**绝不因告警失败中断主链**
   （同 worklist / 水位登记的既有纪律）；
③ 触发项与 `config/schedule.yaml` 的 `notify.on` **同源**（受控值 `NOTIFY_EVENTS`），
   `gate_config_integrity` 判据 S3 断言 yaml 里的 on 不含未登记项；
④ `reports/` 已在门禁排除集内 → 告警文件不构成新的门禁盲区（v2 §3.10 明确不新增顶层 `tmp/`）。
"""

from __future__ import annotations

import datetime
import json
import os
import sys

import paths

# 受控事件值（与 config/schedule.yaml 的 notify.on 对齐）
NOTIFY_EVENTS = frozenset({"failed_step", "gate_fail", "worklist_aged", "doctor_fail"})
NOTIFY_KINDS = ("none", "file", "webhook")
DEFAULT_DIR = os.path.join(paths.ROOT, "reports", "_alerts")
WEBHOOK_ENV = "REG_ORCH_NOTIFY_WEBHOOK"


def _load_cfg() -> dict:
    """从 `config/schedule.yaml` 的 `notify` 段读取配置（缺文件/缺段 → 默认 file）。"""
    path = os.path.join(paths.CONFIG_DIR, "schedule.yaml")
    try:
        import yaml  # noqa: PLC0415

        with open(path, encoding="utf-8") as fh:
            return ((yaml.safe_load(fh) or {}).get("notify") or {}) or {"kind": "file"}
    except Exception:  # noqa: BLE001  配置不可读 → 保守默认（file，落 reports/_alerts）
        return {"kind": "file", "file_dir": DEFAULT_DIR}


def enabled_for(event: str) -> bool:
    """该事件是否被声明为需要告警（`notify.on` 列表）。"""
    cfg = _load_cfg()
    if cfg.get("kind", "file") == "none":
        return False
    return event in set(cfg.get("on") or [])


def notify(
    event: str, title: str, payload: dict | None = None, *, force: bool = False
) -> str | None:
    """发一次告警。返回落盘路径（或 webhook 结果）；`notify.kind=none` 或事件未声明 → None。

    `force=True` 绕过 `notify.on` 声明（供 `cli.py doctor` 直调等显式场景）。
    """
    if event not in NOTIFY_EVENTS:
        print(f"[notify] WARN 未登记的告警事件 {event!r}（应加入 NOTIFY_EVENTS）")
    cfg = _load_cfg()
    kind = cfg.get("kind", "file")
    if kind == "none" or (not force and not enabled_for(event)):
        return None
    if kind == "webhook":
        return _webhook(event, title, payload or {})
    return _file(cfg, event, title, payload or {})


def _file(cfg: dict, event: str, title: str, payload: dict) -> str | None:
    try:
        out_dir = cfg.get("file_dir") or DEFAULT_DIR
        if not os.path.isabs(out_dir):
            out_dir = os.path.join(paths.ROOT, out_dir)
        os.makedirs(out_dir, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        base = os.path.join(out_dir, f"{ts}_{event}")
        rec = {
            "event": event,
            "title": title,
            "at": datetime.datetime.now().isoformat(timespec="seconds"),
            "run_id": os.environ.get("REG_ORCH_RUN_ID", ""),
            "payload": payload,
        }
        with open(base + ".json", "w", encoding="utf-8") as fh:
            json.dump(rec, fh, ensure_ascii=False, indent=1)
        ev = payload.get("evidence") or []
        with open(base + ".md", "w", encoding="utf-8", newline="") as fh:
            fh.write(
                f"# {title}\n\n- 事件: `{event}`\n- 时间: {rec['at']}\n"
                f"- run: `{rec['run_id'] or '(无)'}`\n\n"
            )
            if ev:
                fh.write("## 证据\n\n" + "\n".join(f"- {e}" for e in ev) + "\n")
            rest = {k: v for k, v in payload.items() if k != "evidence"}
            if rest:
                fh.write(
                    "\n## 载荷\n\n```json\n"
                    + json.dumps(rest, ensure_ascii=False, indent=1)
                    + "\n```\n"
                )
        return base + ".md"
    except Exception as e:  # noqa: BLE001  旁路设施：告警失败不得中断主链
        print(f"[notify] WARN 文件告警写入失败（不影响主链）: {type(e).__name__}: {e}")
        return None


def _webhook(event: str, title: str, payload: dict) -> str | None:
    url = os.environ.get(WEBHOOK_ENV, "").strip()
    if not url:
        print(f"[notify] WARN notify.kind=webhook 但 {WEBHOOK_ENV} 未设置 → 降级为不发送")
        return None
    try:
        import urllib.request  # noqa: PLC0415

        body = json.dumps(
            {"event": event, "title": title, "payload": payload}, ensure_ascii=False
        ).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310  受控 URL（env 提供）
            return f"webhook rc={getattr(r, 'status', '?')}"
    except Exception as e:  # noqa: BLE001  发送失败不得中断主链
        print(f"[notify] WARN webhook 发送失败: {type(e).__name__}: {e}")
        return None


def recent(limit: int = 10) -> list[dict]:
    """最近告警（供 `cli.py status` 披露）。读不到目录 → 空列表。"""
    out: list[dict] = []
    try:
        cfg = _load_cfg()
        out_dir = cfg.get("file_dir") or DEFAULT_DIR
        if not os.path.isabs(out_dir):
            out_dir = os.path.join(paths.ROOT, out_dir)
        if not os.path.isdir(out_dir):
            return []
        files = sorted((f for f in os.listdir(out_dir) if f.endswith(".json")), reverse=True)
        for fn in files[:limit]:
            try:
                out.append(json.load(open(os.path.join(out_dir, fn), encoding="utf-8")))
            except (OSError, ValueError):
                continue
    except Exception:  # noqa: BLE001
        return out
    return out


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("events:", sorted(NOTIFY_EVENTS))
    print("cfg:", _load_cfg())
    print("recent:", len(recent()))
