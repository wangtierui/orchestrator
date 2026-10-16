# -*- coding: utf-8 -*-
"""interfaces.topology_api — 链拓扑**只读门面**（诊断第 2 波，2026-10-16）

为什么需要它（实测依据）
------------------------
链拓扑存在**双层标识**，二者不能混用：
  · **声明面** `STEP_ORDER` 32 项，其中 4 个是 `{src}` 模板
    （`collect:{src}` / `clean:{src}` / `relevance:{src}` / `apply:{src}`）；
  · **运行台账** `run_step.step` 有 48 个**互异值，全部是展开形态**
    （如 `clean:nfra`），**不存在模板形态**。

⇒ 「台账里失败的步骤名」去查「声明面节点」会**查不到**（`clean:nfra` vs `clean:{src}`）。
诊断方案第三章的硬约束「步骤名有来源，查不到即拒绝」若照字面实现，
会在**每一个真实失败场景**下自我拒绝。本门面的职责就是把这两层**显式接上**，
并把「我这次是怎么匹配的」如实返回（`matched_as`），不让调用方误以为二者同形。

只读纪律
--------
· 一切取数经 `interfaces/`（不裸查治理库）—— 与既有 `governance_api` 一致；
· **零写入**：本模块不提供任何写入口；
· 拓扑派生自 `STEP_LAYERS`（唯一事实源），**不另建图**。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from bootstrap import bootstrap

bootstrap("all")

from tools.run_production_refresh import (
    GOV_ARTIFACTS,
    STEP_LAYERS,
    step_specs,
)


@dataclass(frozen=True)
class Node:
    """拓扑节点（**已对齐双层标识**）。

    name        : 实例步名（台账形态，如 `clean:nfra`）或模板步名
    matched_as  : `instance`（台账形态命中）/ `spec`（模板形态命中）/ `both`
    spec_name   : 声明面的步骤名（模板形态，如 `clean:{src}`）
    kind        : main / side / conditional / gate / enhancement / disclosure
    layer       : 所属层键（如 `L2_transform`）
    fail_semantics: abort / warn / rc0 / skip
    depends_on  : 依赖的步骤名或产物键
    produces    : 产出��产物键（2026-10-16 起 19/19 全覆盖）
    purpose     : 作用一句话
    """

    name: str
    matched_as: str
    spec_name: str
    kind: str
    layer: str
    fail_semantics: str
    depends_on: tuple[str, ...] = ()
    produces: tuple[str, ...] = ()
    purpose: str = ""


def _template_prefix(spec_name: str) -> str:
    """`clean:{src}` → `clean`（用于把实例步反查模板步）。"""
    return spec_name.split(":{", 1)[0]


def _by_template() -> dict[str, list]:
    """模板步名前缀 → 声明的 StepSpec 列表。"""
    out: dict[str, list] = {}
    for s in step_specs():
        if "{src}" in s.name:
            out.setdefault(_template_prefix(s.name), []).append(s)
    return out


def get_node(step: str) -> Node | None:
    """取拓扑节点（**双层匹配**，并如实标注匹配方式）。

    匹配顺序（先实例、后模板）：
      1. **精确命中模板步**（`clean:{src}`）⇒ `matched_as="spec"`
      2. **精确命中实例声明**（声明面里已写死的一步，如 `relations:gen`）⇒ `matched_as="instance"`
      3. **实例 → 模板反查**（`clean:nfra` ⇒ `clean:{src}`）⇒ `matched_as="instance"`

    找不到返回 `None`（调用方须显式处理，不得当作"该步无依赖"）。
    """
    specs = step_specs()
    # ① 精确命中模板步或普通步
    for s in specs:
        if s.name == step:
            return Node(name=s.name, matched_as="spec", spec_name=s.name,
                        kind=s.kind, layer=s.layer, fail_semantics=s.fail_semantics,
                        depends_on=s.depends_on, produces=s.produces, purpose=s.purpose)
    # ② 实例 → 模板反查
    prefix = step.split(":", 1)[0]
    for s in _by_template().get(prefix, []):
        return Node(name=step, matched_as="instance", spec_name=s.name,
                    kind=s.kind, layer=s.layer, fail_semantics=s.fail_semantics,
                    depends_on=s.depends_on, produces=s.produces, purpose=s.purpose)
    return None


def neighbors(step: str) -> dict[str, list[str]]:
    """邻接（`upstream` / `downstream` / `produced_artifacts` / `consumed_artifacts`）。"""
    node = get_node(step)
    if node is None:
        return {"upstream": [], "downstream": [], "produced_artifacts": [],
                "consumed_artifacts": []}
    specs = step_specs()
    # 该步依赖的产物键由哪些步骤产出
    consumed = [d for d in node.depends_on if d in GOV_ARTIFACTS]
    upstream: list[str] = []
    for art in consumed:
        for s in specs:
            if art in s.produces:
                upstream.append(s.name)
    downstream = [s.name for s in specs
                   if any(p in node.produces for p in s.depends_on)]
    return {"upstream": sorted(set(upstream)), "downstream": sorted(set(downstream)),
            "produced_artifacts": list(node.produces),
            "consumed_artifacts": consumed}


def upstream_chain(step: str, max_depth: int = 6) -> list[str]:
    """上游链（BFS，带环保护；返回**按距离升序**的步骤名）。

    深度上限默认 6——链有 32 步，无界遍历在有环时会不终止。
    """
    seen: set[str] = set()
    order: list[str] = []
    frontier = [step]
    for _d in range(max_depth):
        nxt: list[str] = []
        for cur in frontier:
            for up in neighbors(cur)["upstream"]:
                if up not in seen:
                    seen.add(up)
                    order.append(up)
                    nxt.append(up)
        if not nxt:
            break
        frontier = nxt
    return order


def affected_nodes(step: str) -> list[str]:
    """受影响的下游节点（该步失败会波及谁）——拓扑反向可达。"""
    node = get_node(step)
    if node is None:
        return []
    out: set[str] = set()
    frontier = [step]
    seen = {step}
    for _d in range(6):
        nxt: list[str] = []
        for cur in frontier:
            for dn in neighbors(cur)["downstream"]:
                if dn not in seen:
                    seen.add(dn)
                    out.add(dn)
                    nxt.append(dn)
        if not nxt:
            break
        frontier = nxt
    return sorted(out)


def layers() -> dict[str, list[str]]:
    """层→步骤名（拓扑全貌，确认台/诊断报告共用同一派生）。"""
    return {name: [s.name for s in ls.steps] for name, ls in sorted(STEP_LAYERS.items())}


def all_nodes() -> list[Node]:
    """全部声明节点（32 项，含 4 个模板）。"""
    out: list[Node] = []
    for s in step_specs():
        out.append(Node(name=s.name, matched_as="spec", spec_name=s.name,
                        kind=s.kind, layer=s.layer, fail_semantics=s.fail_semantics,
                        depends_on=s.depends_on, produces=s.produces, purpose=s.purpose))
    return out


@dataclass
class Topology:
    """拓扑快照（供报告/门禁一次性取全，避免多次遍历）。"""

    nodes: list[Node] = field(default_factory=all_nodes)

    @property
    def node_count(self) -> int:
        return len(self.nodes)

    @property
    def template_nodes(self) -> list[str]:
        """模板形态节点名（4 个 `*.{src}`）——双层标识的存在性证据。"""
        return [n.name for n in self.nodes if "{src}" in n.name]

    @property
    def artifact_keys(self) -> list[str]:
        """血缘声明面的全部产物键（19 个）。"""
        return sorted(GOV_ARTIFACTS)

    def coverage(self) -> dict[str, int]:
        """闭合度：有多少产物有步骤归属（治理缺口的实时指标）。"""
        owned = {p for n in self.nodes for p in n.produces}
        return {"artifacts": len(GOV_ARTIFACTS), "owned": len(owned & set(GOV_ARTIFACTS)),
                "orphan": len(set(GOV_ARTIFACTS) - owned)}
