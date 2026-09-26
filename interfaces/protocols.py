# -*- coding: utf-8 -*-
"""
interfaces/protocols — 接口层**依赖协议**（v2 §3.1.3 I-4，2026-09-26）

定位
----
`interfaces/*_api.py` 是**实装入口**（可调用具体 `modules/**` 实现）；
本模块是**消费侧类型契约**：声明"横切治理层（`gates/`、`tools/`、`commands/`）
需要什么能力"，从而不必依赖任何 `modules/**` 具体实现模块。

解决的耦合（I-4 的直接动因）：原 `gates/gate_contract.py:18-23` 为了拿主题集合，
自行注入 classifier 目录（sys.path 操作）并 `from rfn import THEME_MAP` ——
治理层因此**直接依赖模块内部实现**（而非接口），是该层唯一的此类依赖。

纪律
----
- 本模块**只声明协议**：不 import 任何 `modules/**`，不做运行期导入，无副作用；
- 协议是"消费方需要什么"的契约：新增方法前须有真实消费方（防再次空壳化）；
- 实现方**无需继承**（`typing.Protocol` 为结构化子类型）；`runtime_checkable` 只用于
  形状断言（`assert_provider`），不用于业务分支判定；
- 治理层应 `import interfaces.protocols`；业务层仍经 `interfaces/<x>_api`。

命名对应关系（协议 ← 实装）
--------------------------
`CleanIndexProvider`     ← `interfaces.clean_index_api`
`RfnProvider`            ← `interfaces.rfn_api`
`InternalPolicyProvider` ← `interfaces.internal_policy_api`
`RelationsProvider`      ← `interfaces.relations_api`
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class CleanIndexProvider(Protocol):
    """clean 快照能力（五源 cleaned 的 latest / 发布件定位）。"""

    def source_ids(self) -> list[str]: ...

    def latest(self, source_id: str) -> dict | None: ...

    def latest_jsonl_path(self, source_id: str) -> str | None: ...

    def published_dir(self) -> str: ...

    def index_path(self) -> str: ...


@runtime_checkable
class RfnProvider(Protocol):
    """RFN / 主题归属能力（编号体系与归属表的事实源）。"""

    def theme_map(self) -> dict[str, str]: ...

    def registry_paths(self) -> dict[str, str]: ...

    def load_attr_rows(self) -> list[dict]: ...

    def get_index(self) -> Any: ...


@runtime_checkable
class InternalPolicyProvider(Protocol):
    """内部制度能力（索引 / 对齐视图 / 产物定位）。"""

    def query(self, ipn: str | None = None, title: str | None = None) -> dict | None: ...

    def load_index(self) -> dict: ...

    def data_dir(self) -> str: ...

    def published_dir(self) -> str: ...


@runtime_checkable
class RelationsProvider(Protocol):
    """依据/废止关系能力（三类关系同一事实源的读取面）。"""

    def load(self, kind: str = "all") -> list[dict]: ...

    def stat(self) -> dict: ...

    def by_src(self, ref: str) -> list[dict]: ...

    def by_dst(self, ref: str) -> list[dict]: ...

    def summary(self) -> dict: ...


def assert_provider(obj: Any, protocol: type) -> None:
    """形状断言（供门禁/单测使用）：`obj` 是否满足给定协议。

    只做**存在性**检查（结构化子类型不校验签名细节），失败时抛出带缺失方法名的
    `TypeError`，便于定位「接口实装与协议漂移」。
    """
    missing = [name for name in getattr(protocol, "__protocol_attrs__", ())
               if not hasattr(obj, name)]
    if missing:
        raise TypeError(
            f"{type(obj).__name__} 不满足 {protocol.__name__}：缺 {sorted(missing)}")
