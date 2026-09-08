# -*- coding: utf-8 -*-
"""
verification_state_mirror.py — verification_state.json 本地只读镜像（N-5）

背景：
    run_retrieval_after_checks.py 的 Gate 2（效力检查）需读取
    regulatory_scrapers/timeliness_review/verification_state.json 的查验缓存。
    但与 rfn 单一事实源纪律一致，classifier 不应在运行时硬依赖 scrapers 的
    内部模块导入；读侧应自包含。本模块在 classifier/recall_audit 下维护一份
    **只读镜像**副本，并按源文件 mtime 自动刷新（源更新即重建镜像），使
    Gate 2 解耦于 scrapers 运行环境；源不可用时回退读取既有镜像。

口径（关键）：
    - 镜像仅用于 Gate 2 的**读**侧；
    - 写侧（mark_checked / sync_to_classifier）仍在 scrapers 侧
      verification_state 模块完成，本镜像**单向同步（源→镜像）**，不回写。
    - MIRROR_PATH 为生成物（每次按需刷新），非手工维护文件。

用法：
    from verification_state_mirror import load_mirror
    state = load_mirror()   # dict，源比镜像新则先刷新；源缺失则用镜像
"""
import json
import os
import shutil

# P4（2026-09-08）：源查验缓存指向新仓同仓 scraper 模块的 timeliness_review（相对解析，无盘符）。
_THIS = os.path.dirname(os.path.abspath(__file__))                 # modules/regulatory_classifier/recall_audit
_MOD_CLASS = os.path.dirname(_THIS)                                 # modules/regulatory_classifier
_SCRAPERS_MOD = os.path.join(os.path.dirname(_MOD_CLASS), "regulatory_scrapers")
SCRAPERS_STATE = os.path.join(_SCRAPERS_MOD, "timeliness_review", "verification_state.json")
MIRROR_PATH = os.path.join(_THIS, "verification_state.mirror.json")


def _src_mtime():
    return os.path.getmtime(SCRAPERS_STATE) if os.path.exists(SCRAPERS_STATE) else 0.0


def _mirror_mtime():
    return os.path.getmtime(MIRROR_PATH) if os.path.exists(MIRROR_PATH) else 0.0


def refresh(force=False):
    """源比镜像新 或 镜像缺失 → 复制源到镜像。返回最终可用路径（均无则返回 None）。"""
    if os.path.exists(SCRAPERS_STATE):
        if force or _mirror_mtime() < _src_mtime():
            try:
                shutil.copy2(SCRAPERS_STATE, MIRROR_PATH)
            except OSError:
                pass  # 复制失败不阻断：下方回退逻辑处理
    # 优先返回镜像；镜像缺失但源在，则临时用源
    if os.path.exists(MIRROR_PATH):
        return MIRROR_PATH
    return SCRAPERS_STATE if os.path.exists(SCRAPERS_STATE) else None


def load_mirror():
    """返回查验状态 dict；优先镜像（按需刷新），源缺失则仅用镜像，均无则返回 {}。"""
    target = refresh()
    if not target or not os.path.exists(target):
        return {}
    try:
        with open(target, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}
