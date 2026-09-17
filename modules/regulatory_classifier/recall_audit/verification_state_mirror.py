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

阶段 3（2026-09-18）：源路径改由 `interfaces.timeliness_api.state_path()` 提供，
本模块不再自行拼兄弟模块目录（跨模块直连收口，方案 §6 阶段 3）。
"""
import json
import os
import shutil

_THIS = os.path.dirname(os.path.abspath(__file__))      # modules/regulatory_classifier/recall_audit
_MIRROR_PATH_NAME = "verification_state.mirror.json"
MIRROR_PATH = os.path.join(_THIS, "output", _MIRROR_PATH_NAME)   # 兼容导出（生成物路径）


def _state_path() -> str:
    """时效 SSOT 路径（经 interfaces 唯一入口）。"""
    from interfaces.timeliness_api import state_path  # noqa: PLC0415
    return state_path()


def _src_mtime():
    p = _state_path()
    return os.path.getmtime(p) if p and os.path.exists(p) else 0.0


def _mirror_mtime():
    return os.path.getmtime(MIRROR_PATH) if os.path.exists(MIRROR_PATH) else 0.0


def refresh(force=False):
    """源比镜像新 或 镜像缺失 → 复制源到镜像。返回最终可用路径（均无则返回 None）。"""
    src = _state_path()
    if src and os.path.exists(src):
        if force or _mirror_mtime() < _src_mtime():
            try:
                os.makedirs(os.path.dirname(MIRROR_PATH), exist_ok=True)
                shutil.copy2(src, MIRROR_PATH)
            except OSError:
                pass  # 复制失败不阻断：下方回退逻辑处理
    # 优先返回镜像；镜像缺失但源在，则临时用源
    if os.path.exists(MIRROR_PATH):
        return MIRROR_PATH
    return src if src and os.path.exists(src) else None


def load_mirror():
    """返回查验状态 dict；优先镜像（按需刷新），源缺失则仅用镜像，均无则返回 {}。"""
    target = refresh()
    if not target or not os.path.exists(target):
        return {}
    try:
        with open(target, encoding="utf-8") as fh:
            d = json.load(fh)
        # F-D14：镜像同源剥离版本键（与 verification_state.load_state 同款）
        if isinstance(d, dict):
            d.pop("_meta", None)
        return d
    except (OSError, ValueError):
        return {}
