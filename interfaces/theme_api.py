# -*- coding: utf-8 -*-
"""
interfaces/theme_api — 监管主题体系唯一访问/写接口（R2：收敛 3 个写入者为单接口）

- 读取：主题映射 THEME_MAP（唯一源 modules/regulatory_classifier/rfn/__init__.py）、按主题查记录。
- 写入：唯一写接口 set_theme(rfn, theme, basis)——委派 `rfn.registry.set_theme`（幂等 upsert +
  索引重建）；`registry.register_doc` 首次登记亦复用该实现（同函数 `insert=True` 路径）。
- 对齐：align_record() 供 internal_policy_base.align 打标（D-03：仅对齐，不登记 RFN）。

v2 §3.1.3 I-1（2026-09-26）：
  - 三个方法由 `NotImplementedError` 空壳转为实装（`gate_runtime_hygiene` 判据③据此转严）；
  - `THEME_MAP_P0` 由**硬编码副本**（第二处需同步维护的清单）改为经 `rfn_api` 的 re-export。
本模块**不自带 sys.path 引导**——引导统一由 `bootstrap.bootstrap("all")` 承担（v2 §3.1.2）；
`rfn_api` 的模块级引导在先，故 `rfn` 命名空间在本模块导入时已可用。
"""
from __future__ import annotations

from interfaces import rfn_api as _rfn_api

# 主题码 → 全名（R16：扩展主题唯一入口）
# 保留 `THEME_MAP_P0` 名称以免破坏既有调用方；内容取自唯一事实源（浅拷贝，防误改事实源）。
THEME_MAP_P0: dict[str, str] = _rfn_api.theme_map()


class ThemeAPI:
    def get_theme_map(self) -> dict[str, str]:
        """主题码 → 全名（每次现取，反映运行期变更）。"""
        return _rfn_api.theme_map()

    # ---- 写：唯一接口（R2）----
    def set_theme(self, rfn, theme, basis):
        """设定/改判某 RFN 的主题归属（幂等 upsert）。

        - theme 接受主题码（T0..T10）或完整主题名；
        - RFN 未登记 → `LookupError`（防静默建档；首次建档走 `rfn.registry.register_doc`）；
        - 联动：完成后重建 rfn 索引，并写 `sync_status=数据底座 pending`
          （提示 classify 底座链需重建）。
        返回 {rfn, from_theme, to_theme, changed, inserted}。
        """
        from rfn.registry import set_theme as _set  # noqa: PLC0415
        return _set(rfn, theme, basis, sync_pending=True)

    # ---- 读：按主题查询 ----
    def by_theme(self, theme):
        """按主题查询全部监管文件记录（theme 接受主题码或完整主题名）。

        唯一实现 = `rfn` 索引 `by_theme`（未知主题抛 `KeyError`）。
        """
        return _rfn_api.get_index().by_theme(theme)

    # ---- 对齐（内部制度 → 监管主题；D-03 仅对齐、不登记 RFN）----
    def align_record(self, ipn, theme_hint=None):
        """单条内部制度 → 监管主题对齐（**只读计算，不落盘、不登记 RFN**）。

        委托 `internal_policy_base.align.align_one`（对齐算法唯一实现）；标题与正文分别经
        `internal_policy_api` 的 `query()` / `load_processed()` 取得（不直读兄弟模块 data）。
        `theme_hint` 给定时只做**一致性回报**（`hint_match`），不改变算法结果。
        返回 {ipn, title, primary, secondary[], method, hint?, hint_match?}。
        """
        from interfaces.internal_policy_api import get_internal_policy_api  # noqa: PLC0415
        try:
            from internal_policy_base.align import align_one  # noqa: PLC0415
        except ModuleNotFoundError as e:  # pragma: no cover - 引导缺失时的明确提示
            raise ModuleNotFoundError(
                "internal_policy_base 不可导入：请先 `from bootstrap import bootstrap; "
                "bootstrap(\"all\")`（v2 §3.1.2 唯一引导点）") from e

        api = get_internal_policy_api()
        rec = api.query(ipn=ipn) or {}
        fulltext = api.load_processed(ipn, "_fulltext.json") or {}
        text = fulltext.get("text", "") if isinstance(fulltext, dict) else ""
        res = align_one(rec.get("title", ""), text)
        out = {"ipn": ipn, "title": rec.get("title", ""),
               "primary": res.get("primary", ""),
               "secondary": list(res.get("secondary") or []),
               "method": res.get("method", "")}
        if theme_hint:
            out["hint"] = theme_hint
            out["hint_match"] = theme_hint in ([out["primary"]] + out["secondary"])
        return out


_api: ThemeAPI | None = None


def get_theme_api() -> ThemeAPI:
    global _api
    if _api is None:
        _api = ThemeAPI()
    return _api


# ---- 模块级便捷函数（与 rfn_api 同风格，供 `from interfaces.theme_api import X` 直接消费）----
def theme_map() -> dict[str, str]:
    return get_theme_api().get_theme_map()


def set_theme(rfn, theme, basis=""):
    return get_theme_api().set_theme(rfn, theme, basis)


def by_theme(theme):
    return get_theme_api().by_theme(theme)


def align_record(ipn, theme_hint=None):
    return get_theme_api().align_record(ipn, theme_hint=theme_hint)
