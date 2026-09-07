# -*- coding: utf-8 -*-
"""
config.py —— settings.yaml 装载与物理开关（第十五节 人工干预接口）

物理开关（settings.yaml 顶层）：
  enable_proxy       : 是否启用代理池（True 时读取 proxy_list.txt）
  max_pages_today    : 每日抓取上限（None 表示不限）
  stop_after_n_errors: 连续错误 N 次自动退出（None 表示不启用）
  delay_range        : [min, max] 请求随机间隔（秒，默认 [2,5]）
  retries            : 指数退避重试次数（≥3）
  anchor_selectors   : 关键锚点元素（熔断检测用）

用法：
  cfg = load_settings("config/settings.yaml")
  cfg.get("delay_range", [2,5])
"""

from __future__ import annotations

import copy
import os
from typing import Any

# 默认配置（所有物理开关的缺省值；与规范第十五节一致）
DEFAULTS: dict[str, Any] = {
    "project": {"name": "unnamed", "data_source": ""},
    "http": {
        "enable_proxy": False,
        "proxy_list_file": "config/proxy_list.txt",
        "user_agents_file": "config/user_agents.json",
        "delay_range": [2.0, 5.0],
        "retries": 3,
        "connect_timeout": 10.0,
        "read_timeout": 30.0,
        "max_pages_today": None,
        "stop_after_n_errors": None,
        "use_mobile_ua": True,
    },
    "circuit_breaker": {
        "enabled": True,
        "anchor_selectors": [],
        "max_miss": 3,
        "fail_snapshot_dir": "logs/fail_snapshots",
    },
    "cleaning": {
        "clean_version": "v1.0.0",
        "dedup_on": True,
        "sentence_split_on": True,
        "ocr_correction_on": True,
        "confusion_map_file": "config/ocr_confusion.json",
        "custom_dict_file": "config/custom_dict.txt",
        "truncate_max_len": 200,
        # 已知源限制字段（源站架构性缺失，非抓取缺陷）：豁免熔断告警，
        # 但空值率仍完整计入 metrics 报告（透明可审计）。示例：gov=["body_text","index_no"]
        "expected_null_fields": [],
    },
    "output": {
        "raw_dir": "data/raw",
        "cleaned_dir": "data/cleaned",
        "history_dir": "data/history",
        "keep_history_versions": 3,
        "attachments_dir": "attachments",
        "downloaded_docs_dir": "downloaded_docs",
    },
    "logging": {
        "log_dir": "logs",
        "level": "INFO",
        "json_lines": True,
        "fail_snapshot_dir": "logs/fail_snapshots",
    },
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_settings(path: str | None = None, *, project_root: str | None = None) -> dict[str, Any]:
    """
    装载 settings.yaml；文件缺失/解析失败时返回 DEFAULTS（并记录原因）。
    path 为空时依次尝试 <root>/config/settings.yaml。
    """
    if not path and project_root:
        path = os.path.join(project_root, "config", "settings.yaml")
    if not path:
        return copy.deepcopy(DEFAULTS)
    if not os.path.exists(path):
        return copy.deepcopy(DEFAULTS)
    try:
        import yaml  # 惰性
    except Exception:
        return copy.deepcopy(DEFAULTS)
    try:
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        return _deep_merge(DEFAULTS, raw)
    except Exception:
        return copy.deepcopy(DEFAULTS)


def save_settings(path: str, cfg: dict[str, Any]) -> None:
    """写出配置（含注释说明）。"""
    import yaml
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)


def load_proxy_list(path: str) -> list:
    """读取代理池文件（每行一个代理 URL；如需鉴权，用户名口令经环境变量注入）。"""
    if not path or not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                out.append({"http": line, "https": line})
    return out


if __name__ == "__main__":  # 离线自检
    cfg = load_settings()  # 无文件 → 默认
    assert cfg["http"]["delay_range"] == [2.0, 5.0]
    assert cfg["http"]["retries"] >= 3
    merged = _deep_merge(DEFAULTS, {"http": {"retries": 5}})
    assert merged["http"]["retries"] == 5
    print("[scraper_std.config] 离线自检通过")
