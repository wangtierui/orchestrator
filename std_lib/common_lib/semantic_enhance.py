# -*- coding: utf-8 -*-
"""std_lib.common_lib.semantic_enhance — P1 语义增强**执行门面**（N-162，2026-09-30）

为什么需要这一层（三层分工，不得混用）
--------------------------------------
| 层 | 模块 | 回答的问题 |
|---|---|---|
| 探测 | `semantic_tools` | **有没有**（依赖 + 权重 + 离线就绪）；唯一事实源 = `config/schema/semantic_tools.json` |
| 加载 | `semantic_models` | **怎么装进内存**（本地路径、强制离线、池化/垫片） |
| **执行** | **本模块** | **用什么（选型）/ 怎么传参 / 出错怎么办（回退与留痕）** |

调用方（`clauses` / `classify` / `relations` 等）**只应依赖本模块**，不得直接调 `semantic_models`
或（更不得）直接 `import transformers/ltp`。

调用入口（唯一）
----------------
```
from std_lib.common_lib import semantic_enhance as se

se.embed(texts, mode="full")                 # 全量：bge_base_zh → 降级 text2vec
se.embed(texts, mode="incremental")          # 增量：youtu_embedding
se.embed(texts, model="text2vec")            # 特别指定
se.split_sentences(text, level="strict")     # 分句：LTP 增强 → 确定性回退
se.describe()                                # 当前生效选型与就绪度（披露用）
```

参数传递（显式、可覆盖、有默认）
--------------------------------
* `mode ∈ {"full","incremental"}` —— 场景；默认 `full`（见清单 `selection_policy`）
* `model: str|None` —— **显式指定**时优先级最高（覆盖 mode）
* `batch: int` —— 分批编码（大模型/大批量时控制内存）
* `normalize: bool` —— 是否 L2 归一（默认 True；余弦检索需归一）
* `strict: bool` —— **异常语义开关**：`False`（默认）回退并留痕；`True` 直接抛（用于"必须用增强"的场景）

异常处理路径（**唯一约定**）
----------------------------
```
调用方 → se.embed()
  ├─ 选型（choose_embedder）：链 = [请求模型] + [降级候选…]
  ├─ 逐个候选尝试 semantic_models.load_embedder(name)
  │    ├─ 成功 → 返回 EmbedResult(ok=True, model=name, vectors=…, fallback=False)
  │    └─ ModelUnavailable/异常 → 记录 → 试下一个候选（**链式降级，不吞错**）
  ├─ 链耗尽
  │    ├─ strict=True  → 抛 EnhanceUnavailable（含每个候选的失败原因）
  │    └─ strict=False → 返回 EmbedResult(ok=False, vectors=None, fallback=True, notice=…)
  │                      **并打印 fallback_notice**（可观测；调用方据此走确定性路径）
  └─ 任何成功路径都带 fingerprint() → 调用方须写入产物 provenance（v2 指纹纪律）
```

`split_sentences` 同理：LTP 增强不可用 → 回退 **`sentence_boundary.split_by`**（受控 SSOT）；
**两级集合差异本身是有意的**（cleaning 用 `strict` 保段、抽取用 `loose` 细分），故 `level` 必须显式传。
"""

from __future__ import annotations

import io
import os
import sys

from std_lib.common_lib import semantic_models as _sm
from std_lib.common_lib import semantic_tools as _st

__all__ = [
    "EnhanceUnavailable",
    "EmbedResult",
    "SplitResult",
    "choose_embedder",
    "embed",
    "split_sentences",
    "describe",
    "fingerprint",
    "MODE_FULL",
    "MODE_INCREMENTAL",
]

MODE_FULL = "full"
MODE_INCREMENTAL = "incremental"

#: 环境开关：分句是否启用 ML 增强（**默认关** → 行为与既有确定性实现对等，零回归）。
ENV_SPLIT_ML = "REG_ORCH_SEMANTIC_SPLIT"


class EnhanceUnavailable(RuntimeError):
    """增强不可用且 `strict=True`（或链耗尽）→ 调用方**必须**回退确定性路径。"""

    def __init__(self, what: str, reasons: list) -> None:
        self.what = what
        self.reasons = list(reasons)
        super().__init__(f"{what} 增强不可用；候选失败原因：{self.reasons}")


class EmbedResult:
    """`embed()` 的返回值（**结构稳定**，便于调用方与 provenance 使用）。"""

    __slots__ = ("ok", "vectors", "model", "requested", "chain_tried", "fallback", "notice")

    def __init__(self, ok, vectors, model, requested, chain_tried, fallback, notice) -> None:
        self.ok = ok
        self.vectors = vectors
        self.model = model
        self.requested = requested
        self.chain_tried = chain_tried
        self.fallback = fallback
        self.notice = notice

    def __repr__(self) -> str:  # pragma: no cover - 诊断用
        return (
            f"<EmbedResult ok={self.ok} model={self.model or '—'} "
            f"n={len(self.vectors) if self.vectors else 0} fallback={self.fallback}>"
        )


class SplitResult:
    """`split_sentences()` 的返回值。`basis` 表明**依据**（`ltp_cws` 或 `deterministic:<level>`）。"""

    __slots__ = ("sentences", "basis", "fallback", "notice")

    def __init__(self, sentences, basis, fallback, notice) -> None:
        self.sentences = sentences
        self.basis = basis
        self.fallback = fallback
        self.notice = notice


# --------------------------------------------------------------------------
# 选型（唯一事实源 = 清单 selection_policy）
# --------------------------------------------------------------------------
def _policy() -> dict:
    """选型策略（**唯一事实源 = 清单 `selection_policy`**，本模块不重复定义任何模型名）。"""
    return _st.load_manifest().get("selection_policy") or {}


def _chain_for(mode: str, requested: str | None) -> list:
    """→ 候选模型**有序链**（先请求模型，再降级候选；去重保序）。"""
    pol = _policy()
    modes = pol.get("modes") or {}
    spec = modes.get(mode) or modes.get(MODE_FULL) or {}
    chain: list = []
    if requested:
        chain.append(str(requested))
    primary = str(spec.get("primary") or "").strip()
    if primary:
        chain.append(primary)
    for f in spec.get("fallback") or []:
        chain.append(str(f))
    seen: set = set()
    out: list = []
    for c in chain:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def choose_embedder(mode: str = MODE_FULL, requested: str | None = None) -> str | None:
    """→ 候选链中**第一个就绪**的模型名；无就绪者 → `None`（调用方走确定性路径）。

    就绪判据 = `semantic_tools.probe(name)["available"] and model_ok is True`（**实检目录**）。
    """
    for name in _chain_for(mode, requested):
        p = _st.probe(name)
        if p["available"] and p.get("model_ok") is True:
            return name
    return None


# --------------------------------------------------------------------------
# 执行：嵌入
# --------------------------------------------------------------------------
def embed(
    texts,
    *,
    mode: str = MODE_FULL,
    model: str | None = None,
    batch: int = 32,
    normalize: bool = True,
    strict: bool = False,
) -> EmbedResult:
    """语义向量（**链式降级**；`strict=False` 时不抛，返回 `ok=False` 供调用方回退）。"""
    if isinstance(texts, str):
        texts = [texts]
    texts = [str(t) for t in texts]
    chain = _chain_for(mode, model)
    tried: list = []
    reasons: list = []
    for name in chain:
        p = _st.probe(name)
        if not (p["available"] and p.get("model_ok") is True):
            reasons.append(f"{name}: 未就绪（{p.get('detail') or '依赖或权重缺失'}）")
            continue
        tried.append(name)
        try:
            emb = _sm.load_embedder(name)
            vecs: list = []
            for i in range(0, len(texts), max(int(batch), 1)):
                vecs.extend(emb.encode(texts[i : i + max(int(batch), 1)], normalize=normalize))
            return EmbedResult(True, vecs, name, model or mode, tried, False, "")
        except Exception as e:  # noqa: BLE001  真实加载/编码失败 → 记因并降级（不吞）
            reasons.append(f"{name}: {type(e).__name__}: {str(e)[:160]}")
    notice = _st.fallback_notice(model or chain[0] if chain else "embedder", layer="semantic_embed")
    if strict:
        raise EnhanceUnavailable("语义嵌入", reasons or ["无可选候选"])
    print(f"[semantic_enhance] WARN 未执行嵌入增强；已回退确定性路径。{notice}", flush=True)
    for r in reasons:
        print(f"[semantic_enhance]   候选失败：{r}", flush=True)
    return EmbedResult(False, None, None, model or mode, tried, True, notice)


# --------------------------------------------------------------------------
# 执行：分句（LTP 增强 → 确定性回退）
# --------------------------------------------------------------------------
def split_sentences(text: str, *, level: str = "strict", prefer: str = "ltp",
                    strict: bool = False, use_ml: bool | None = None) -> SplitResult:
    """分句。`level ∈ {strict, loose}`（**受控 SSOT**：`sentence_boundary`）。

    ML 增强**默认关闭**（env `REG_ORCH_SEMANTIC_SPLIT=1` 打开，或显式 `use_ml=True`）——
    因为"改分句口径"会改变条文/关系产物，须先在**可评样本**上量出 P/R 再启用
    （v2 纪律：无度量不得上线）。关闭时走 `sentence_boundary.split_by`，**与既有实现逐字节对等**。
    """
    from std_lib.common_lib.sentence_boundary import split_by

    if use_ml is None:
        use_ml = os.environ.get(ENV_SPLIT_ML, "") == "1"
    if not use_ml:
        return SplitResult(split_by(text, level), f"deterministic:{level}", False, "")
    try:
        seg = _sm.load_segmenter(prefer)
        return SplitResult(seg.split(text), getattr(seg, "basis_split", "ml"), False, "")
    except Exception as e:
        notice = _st.fallback_notice(prefer, layer="sentence_split_ml")
        if strict:
            raise EnhanceUnavailable("分句增强", [f"{prefer}: {type(e).__name__}: {str(e)[:160]}"]) from e
        print(
            f"[semantic_enhance] WARN 分句增强不可用（{type(e).__name__}）→ 回退确定性切分。{notice}",
            flush=True,
        )
        return SplitResult(split_by(text, level), f"deterministic:{level}", True, notice)


# --------------------------------------------------------------------------
# 披露与指纹
# --------------------------------------------------------------------------
def describe() -> dict:
    """当前**生效选型**与就绪度（供 preflight/基准/README 披露，不加载模型）。"""
    out: dict = {"policy": _policy(), "modes": {}}
    for mode in (MODE_FULL, MODE_INCREMENTAL):
        chain = _chain_for(mode, None)
        out["modes"][mode] = {
            "chain": chain,
            "chosen": choose_embedder(mode),
            "ready": [
                n for n in chain if _st.probe(n)["available"] and _st.probe(n).get("model_ok") is True
            ],
        }
    out["split_ml_enabled"] = os.environ.get(ENV_SPLIT_ML, "") == "1"
    return out


def fingerprint(*, mode: str = MODE_FULL, model: str | None = None) -> dict:
    """本次**实际将使用**的模型指纹（模型名 + 依赖版本 + 权重结构指纹）→ 写产物 provenance。"""
    name = choose_embedder(mode, model)
    if not name:
        return {"model": "", "dep_version": "", "weights": "", "ready": False}
    return _sm.embedder_fingerprint(name)


def main(argv=None) -> int:
    """CLI（**明确调用入口**，供人工/脚本验证）：

        python -m std_lib.common_lib.semantic_enhance --describe
        python -m std_lib.common_lib.semantic_enhance --embed "文本…" [--mode full|incremental] [--model X]
        python -m std_lib.common_lib.semantic_enhance --split "条文…" [--level strict|loose] [--ml]
    """
    import argparse
    import json

    ap = argparse.ArgumentParser(description="P1 语义增强执行门面")
    ap.add_argument("--describe", action="store_true", help="打印生效选型与就绪度")
    ap.add_argument("--embed", default="", help="对给定文本求向量")
    ap.add_argument("--split", default="", help="对给定文本分句")
    ap.add_argument("--mode", default=MODE_FULL, choices=[MODE_FULL, MODE_INCREMENTAL])
    ap.add_argument("--model", default=None)
    ap.add_argument("--level", default="strict", choices=["strict", "loose"])
    ap.add_argument("--ml", action="store_true", help="分句启用 ML 增强（默认关）")
    ap.add_argument("--strict", action="store_true", help="增强不可用时抛错（默认回退）")
    a = ap.parse_args(argv)

    if a.describe or not (a.embed or a.split):
        print(json.dumps(describe(), ensure_ascii=False, indent=1))
        return 0
    if a.embed:
        r = embed([a.embed], mode=a.mode, model=a.model, strict=a.strict)
        print(f"  模型 = {r.model}  fallback = {r.fallback}  维度 = {len(r.vectors[0]) if r.vectors else 0}")
        print(f"  指纹 = {fingerprint(mode=a.mode, model=a.model) if r.ok else '—'}")
        return 0 if r.ok else 1
    s = split_sentences(a.split, level=a.level, strict=a.strict, use_ml=a.ml)
    print(f"  依据 = {s.basis}  fallback = {s.fallback}  句数 = {len(s.sentences)}")
    for x in s.sentences[:5]:
        print(f"   · {x[:60]}")
    return 0


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    raise SystemExit(main())
