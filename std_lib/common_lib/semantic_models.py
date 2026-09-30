# -*- coding: utf-8 -*-
"""std_lib.common_lib.semantic_models — P1 语义增强**统一加载层**（N-156，2026-09-30）

定位
----
`semantic_tools` 回答"**有没有**（依赖 + 权重）"；本模块回答"**怎么装进内存并调用**"。
两者分工固定：**探测归 `semantic_tools`（唯一事实源 = `config/schema/semantic_tools.json`），
加载归本模块** —— 调用方一律先 `semantic_tools.probe(name)["available"]` 再进来，不得直接
`import sentence_transformers/ltp/...`（`policy.gate` 明令：增强层禁止顶层硬导入）。

三条纪律的落地
--------------
  · **零硬依赖**：本模块顶层**不导入**任何 ML 库；一切 `import` 都在函数体内惰性发生。
    未装依赖时 `load_*` 抛 `ModelUnavailable`（带可读原因），调用方据此**回退确定性路径**。
  · **降级链可观测**：加载失败**不吞**。`ModelUnavailable` 携带 `name/reason/how_to_fix`。
  · **指纹**：`embedder_fingerprint(name)` 把"模型名 + 依赖版本 + 权重结构指纹"三者合一，
    供产物 provenance（否则 `input_sha` 断点与水位判据会因模型差异而失真）。

离线**硬保证**（本模块存在的主要理由）
--------------------------------------
此前"离线"靠"设了某个 env"来保证 —— 那是**假绿灯**（env 名猜错就完全无效，
`MTL_HANLP_OFFLINE` 即实例）。本模块在**加载前**强制：
  `HF_HUB_OFFLINE=1` / `TRANSFORMERS_OFFLINE=1` / `HF_HUB_DISABLE_TELEMETRY=1`，
  并把模型路径**以显式本地路径**传入（`local_files_only=True`）。
即使权重缺失也**只会报错，不会联网下载**。

用法
----
```
from std_lib.common_lib import semantic_models as sm

emb = sm.load_embedder("bge_base_zh")     # → 有 .encode(list[str]) -> list[list[float]]
vecs = emb.encode(["第一条", "第二条"])

seg = sm.load_segmenter("ltp")            # → 有 .split(str) -> list[str]
sents = seg.split("本法自公布之日起施行。违反本条的，处以罚款。")
```
"""

from __future__ import annotations

import os
from typing import Any

from std_lib.common_lib import semantic_tools as _st

__all__ = [
    "ModelUnavailable",
    "load_embedder",
    "load_segmenter",
    "embedder_fingerprint",
    "is_ready",
]


class ModelUnavailable(RuntimeError):
    """模型不可用（依赖未装 / 权重未预置 / 加载失败）——**调用方据此回退**。"""

    def __init__(self, name: str, reason: str, how_to_fix: str = "") -> None:
        self.name = name
        self.reason = reason
        self.how_to_fix = how_to_fix
        super().__init__(f"{name} 不可用：{reason}" + (f"｜处置：{how_to_fix}" if how_to_fix else ""))


def _force_offline() -> None:
    """置离线开关（进程内生效，幂等）。**这是"防首用联网"的真正保证**。"""
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("HF_HUB_DISABLE_IMPLICIT_TOKEN", "1")


def _resolve(name: str) -> tuple:
    """→ (模型本地目录, 探测结果)。不可用时抛 `ModelUnavailable`。"""
    p = _st.probe(name)
    if not p["available"]:
        raise ModelUnavailable(
            name,
            p.get("detail") or "依赖未安装",
            "按 config/schema/semantic_tools.json 的 pypi/extra 安装；`pip install -e .[semantic]`",
        )
    path = str(p.get("model_path") or "")
    if not path:
        raise ModelUnavailable(
            name,
            f"权重未预置（{p.get('detail') or '清单未声明 local_dir 或目录不完整'}）",
            f"把权重按 HF local_dir 形态放到清单声明的 local_dir（{name}）；"
            "或按 offline 字段的说明预置后重跑 `--probe` 复核",
        )
    return path, p


def is_ready(name: str) -> bool:
    """该模型是否**可加载**（依赖 + 权重均就绪）——不抛异常，供调用方分支。"""
    try:
        path, _ = _resolve(name)
        return bool(path)
    except ModelUnavailable:
        return False


# --------------------------------------------------------------------------
# 嵌入（embedder）
# --------------------------------------------------------------------------
class _Embedder:
    """统一嵌入封装：`encode(texts, normalize=True) -> list[list[float]]`。"""

    def __init__(self, name: str, model: Any, path: str, dim: int) -> None:
        self.name = name
        self.model = model
        self.path = path
        self.dim = dim

    def encode(self, texts, normalize: bool = True) -> list:
        import numpy as np

        if isinstance(texts, str):
            texts = [texts]
        v = self.model.encode(list(texts), normalize_embeddings=normalize)
        arr = np.asarray(v, dtype="float32")
        return arr.tolist()

    def __repr__(self) -> str:  # pragma: no cover - 诊断用
        return f"<Embedder {self.name} dim={self.dim} path={os.path.basename(self.path)}>"


def load_embedder(name: str) -> _Embedder:
    """加载嵌入模型（**本地路径 + 强制离线**）。支持 `bge_base_zh`/`text2vec`/`youtu_embedding`。

    `sentence-transformers` 路径优先（三者均为 ST 格式：含 `modules.json`/`1_Pooling`）；
    **带自定义建模代码者例外**（`youtu_embedding` 的 `configuration_youtu.py`）→ 直走
    "裸 transformers + **按 `1_Pooling` 声明池化**"（见 `_needs_remote_code` 的实测依据）。
    """
    path, p = _resolve(name)
    _force_offline()
    # ⚠️ 实测（2026-09-30，Youtu-Embedding + ST 6.1.0）：仓库带**自定义建模代码**时，
    #   `SentenceTransformer(..., trust_remote_code=True)` 仍会在其**模块加载链**里丢掉该参数
    #   → `ValueError: contains custom code which must be executed`；而同路径上
    #   `AutoConfig` / `AutoTokenizer(trust_remote_code=True)` **均正常**（UTUConfig/2048 维）。
    #   故先判自定义代码，需要时**直接走 transformers 路径**（按声明池化，语义不偏移），
    #   省掉三次注定失败且耗时的 ST 尝试。
    if _needs_remote_code(path):
        # 自定义建模代码按 4.x 写的 → 先补兼容垫片（实测仅缺 LossKwargs），再走 transformers 路径
        _inj = _patch_transformers_compat()
        if _inj:
            print(f"[semantic_models] 已注入 transformers 兼容垫片：{_inj}")
        return _load_embedder_transformers(name, path)
    try:
        from sentence_transformers import SentenceTransformer
    except Exception as e:
        raise ModelUnavailable(
            name, f"sentence-transformers 不可用：{type(e).__name__}: {e}", "pip install sentence-transformers"
        ) from e

    last: Exception | None = None
    for kwargs in (
        {"local_files_only": True, "trust_remote_code": True, "device": "cpu"},
        {"local_files_only": True, "device": "cpu"},
        {"local_files_only": True},
    ):
        try:
            m = SentenceTransformer(path, **kwargs)
            # 维度取法随 ST 版本改名（`get_sentence_embedding_dimension` → `get_embedding_dimension`）
            # → 两者都试，避免版本升级后打 FutureWarning 或 AttributeError。
            _dimf = getattr(m, "get_embedding_dimension", None) or getattr(
                m, "get_sentence_embedding_dimension", None
            )
            dim = int(_dimf() or 0) if _dimf else 0
            return _Embedder(name, m, path, dim)
        except TypeError:
            continue  # 该版本不接受某 kwargs → 换下一档
        except Exception as e:  # noqa: BLE001  真实加载错误：记录并继续降级
            last = e
    # 末级回退：裸 transformers（youtu 自定义代码 / ST 版本不兼容时）
    try:
        return _load_embedder_transformers(name, path)
    except Exception as e:
        raise ModelUnavailable(
            name,
            f"加载失败（ST：{type(last).__name__ if last else '—'}: {last}；"
            f"transformers：{type(e).__name__}: {e}）",
            "核对权重完整性（config.json/权重/tokenizer 齐备）与 sentence-transformers 版本",
        ) from e


def _needs_remote_code(path: str) -> bool:
    """该模型是否**需要执行自定义代码**（`config.json` 有 `auto_map`，或目录内有 `configuration_*.py`）。

    → True 表示必须经 `trust_remote_code=True` 的 **transformers 路径**加载。
    ⚠️ 安全提示：这会**执行模型目录内的 Python 代码**。本仓权重由使用方自行下载并预置，
    属可信来源；仍显式标注，避免"静默执行远端代码"。
    """
    try:
        import json as _json

        if "auto_map" in (_json.load(open(os.path.join(path, "config.json"), encoding="utf-8")) or {}):
            return True
    except Exception:  # noqa: BLE001  配置不可读 → 退回按文件名判断
        pass
    try:
        return any(f.startswith("configuration_") and f.endswith(".py") for f in os.listdir(path))
    except OSError:
        return False


def _pooling_mode(path: str) -> dict:
    """读 `1_Pooling/config.json` 的**池化声明**（缺省 = 均值池化，与 ST 默认一致）。

    为何必须读它：SP 与"裸 transformers"的差别**几乎全在池化**。bge 系列用 **CLS**、
    text2vec/youtu 用 **均值**——若一律用均值，bge 的向量语义会偏移（且不报错，静默劣化）。
    """
    p = os.path.join(path, "1_Pooling", "config.json")
    try:
        import json as _json

        c = _json.load(open(p, encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001  无该文件 → ST 默认（均值）
        return {"cls": False, "mean": True}
    return {
        "cls": bool(c.get("pooling_mode_cls_token")),
        "mean": bool(c.get("pooling_mode_mean_tokens", not c.get("pooling_mode_cls_token"))),
        "max": bool(c.get("pooling_mode_max_tokens")),
        "lasttoken": bool(c.get("pooling_mode_lasttoken")),
    }


def _load_embedder_transformers(name: str, path: str) -> _Embedder:
    """裸 transformers + **按声明池化**（自定义建模代码模型必走此路，如 Youtu-Embedding）。"""
    import torch
    from transformers import AutoModel, AutoTokenizer

    tok = AutoTokenizer.from_pretrained(path, local_files_only=True, trust_remote_code=True)
    mdl = AutoModel.from_pretrained(path, local_files_only=True, trust_remote_code=True)
    mdl.eval()
    pool = _pooling_mode(path)

    class _TfEmbedder(_Embedder):
        def encode(self, texts, normalize: bool = True) -> list:
            if isinstance(texts, str):
                texts = [texts]
            out: list = []
            with torch.no_grad():
                for t in texts:
                    enc = tok([t], padding=True, truncation=True, max_length=512, return_tensors="pt")
                    h = mdl(**enc).last_hidden_state          # [1, L, H]
                    mask = enc["attention_mask"].unsqueeze(-1).float()
                    if pool.get("cls"):
                        v = h[:, 0]                            # CLS（bge 系列）
                    else:
                        v = (h * mask).sum(1) / mask.sum(1).clamp(min=1e-9)  # 均值（text2vec/youtu）
                    if normalize:
                        v = torch.nn.functional.normalize(v, p=2, dim=1)
                    out.append(v[0].tolist())
            return out

    dim = int(getattr(mdl.config, "hidden_size", 0) or 0)
    return _TfEmbedder(name, mdl, path, dim)


# --------------------------------------------------------------------------
# 分句（segmenter）
# --------------------------------------------------------------------------
class _LTPSegmenter:
    """LTP 后端封装（**如实标注能力边界**）。

    ⚠️ 实测结论（2026-09-30，ltp 4.2.14）：**LTP 4.x 不提供分句 API** ——
    全包（含 `legacy.py`）无 `sent_split`/`split_sentence`；`supported_tasks` 仅
    `{cws,pos,ner,dep,sdp,srl,sdpg}`。故本类：
      · `cws()` / `pos()` —— **真实 LTP 模型调用**（分词 / 词性）；
      · `split()` —— **以 LTP 分词为锚切句**（标点归前句），并以 `basis_split` 显式标注
        **不是** LTP 原生分句能力；LTP 不可用时调用方须回退仓内确定性切分。
    """

    #: 切句依据（供调用方与审计判断"这是不是原生 ML 分句"）
    basis_split = "ltp_cws（分词+标点聚合；LTP 4.x 无原生分句 API）"

    def __init__(self, name: str, model: Any, path: str) -> None:
        self.name = name
        self.model = model
        self.path = path

    def _pipeline(self, texts, tasks):
        return self.model.pipeline(list(texts), tasks=list(tasks))

    def cws(self, texts) -> list:
        """分词（**真实 LTP 调用**）→ `list[list[str]]`。"""
        if isinstance(texts, str):
            texts = [texts]
        out = self._pipeline(texts, ["cws"])
        return [list(x) for x in (getattr(out, "cws", None) or [])]

    def pos(self, texts) -> list:
        """词性（**真实 LTP 调用**）→ `list[list[str]]`（与 `cws` 对齐）。"""
        if isinstance(texts, str):
            texts = [texts]
        out = self._pipeline(texts, ["cws", "pos"])
        return [list(x) for x in (getattr(out, "pos", None) or [])]

    def split(self, text: str) -> list:
        """切句：以 LTP 分词为锚，按句末标点（。；！？及换行）聚合 → `list[str]`。

        实现要点：**不丢字符**（标点归属其所在句）。LTP 分词抛错则**原样抛出**，
        由调用方回退（不静默降级）。
        """
        if not text or not text.strip():
            return []
        toks = self.cws([text])
        if not toks or not toks[0]:
            return [text.strip()]
        ends = "。；！？\n"
        sents: list = []
        buf: list = []
        for t in toks[0]:
            buf.append(t)
            if t and t[-1] in ends:
                s = "".join(buf).strip()
                if s:
                    sents.append(s)
                buf = []
        tail = "".join(buf).strip()
        if tail:
            sents.append(tail)
        return sents


def _patch_transformers_compat() -> list:
    """transformers ≥5 的**向后兼容垫片**（缺失时才注入）→ 返回本次注入的项名列表。

    实测依据（2026-09-30，transformers 5.17.0）——**只补"确缺且语义等价"的**：
      ① `BertTokenizer.batch_encode_plus`：v5 移除该方法，而 **ltp 4.2.x** 仍在调
         （`ltp/nerual.py`）→ 委托 `__call__`（语义等价）。
      ② `transformers.utils.LossKwargs`：v5 移除该**类型标记**，而 **Youtu-Embedding 的
         `modeling_youtu.py`** 仍 `from transformers.utils import (… LossKwargs …)`
         → 补为 `total=False` 的 `TypedDict`（它本就只是 typing 标记，无运行时行为）。
    ⚠️ 纪律：**先实测**再补 —— 曾逐项核对 `modeling_youtu.py` 依赖的 **21** 个符号，
    5.17 中**仅 `LossKwargs` 缺失**（`StaticCache`/`AttentionMaskConverter`/`flex_attention`
    等均在）→ 故补 1 项即可，**不做无差别的"补一堆"**（那会掩盖真实不兼容）。
    """
    injected: list = []
    try:
        import transformers
        from transformers import BertTokenizer
    except Exception:  # noqa: BLE001  无 transformers 时交给上层报错
        return injected

    if not hasattr(BertTokenizer, "batch_encode_plus"):

        def _batch_encode_plus(self, *args, **kwargs):
            return self(*args, **kwargs)

        BertTokenizer.batch_encode_plus = _batch_encode_plus  # type: ignore[attr-defined]
        injected.append("BertTokenizer.batch_encode_plus")

    if not hasattr(transformers.utils, "LossKwargs"):
        import typing

        class LossKwargs(typing.TypedDict, total=False):
            """transformers 4.x 的损失 kwargs 类型标记（v5 移除）。"""

            num_items_in_batch: typing.Any

        transformers.utils.LossKwargs = LossKwargs  # type: ignore[attr-defined]
        injected.append("transformers.utils.LossKwargs")

    # ③ `ROPE_INIT_FUNCTIONS["default"]`：v5 的键集为 {dynamic,linear,llama3,longrope,
    #    proportional,yarn} —— **不含 "default"**；而 4.x 代码（含 Youtu 的 `modeling_youtu.py`）
    #    在 `rope_scaling` 未声明时会取 `"default"`。此处按 **4.x 语义忠实重建**：
    #    标准 RoPE（无缩放）→ 返回 `(inv_freq, 1.0)`。
    try:
        from transformers.modeling_rope_utils import ROPE_INIT_FUNCTIONS

        if "default" not in ROPE_INIT_FUNCTIONS:

            def _compute_default_rope_parameters(config, device=None, seq_len=None, **kw):
                import torch

                base = float(getattr(config, "rope_theta", 10000.0) or 10000.0)
                dim = int(config.hidden_size // config.num_attention_heads)
                inv = 1.0 / (base ** (torch.arange(0, dim, 2, dtype=torch.float32, device=device) / dim))
                return inv, 1.0

            ROPE_INIT_FUNCTIONS["default"] = _compute_default_rope_parameters  # type: ignore[index]
            injected.append('ROPE_INIT_FUNCTIONS["default"]')
    except Exception:  # noqa: BLE001  无该模块时跳过（非本次加载路径需要）
        pass

    # ④ `PretrainedConfig.pruned_heads`：v5 移除了该属性，而 4.x 代码会读
    #    `self.config.pruned_heads`（Youtu 的 `modeling_youtu.py` 命中）。它语义上是
    #    "被剪枝的注意力头集合"，**只读且常为空** → 以只读 property 返回 `{}`（不共享可变对象）。
    try:
        from transformers.configuration_utils import PretrainedConfig

        if not hasattr(PretrainedConfig, "pruned_heads"):

            def _get_pruned_heads(self):
                return getattr(self, "_pruned_heads_compat", {})

            def _set_pruned_heads(self, value):  # ⚠️ **必须有 setter**：transformers 会**写**它
                # （实测：只读 property 会让 `ElectraConfig`（LTP 用）报
                #  "Can't set pruned_heads with value {}" → 属**垫片自身引入的回归**）。
                self._pruned_heads_compat = value if value is not None else {}

            PretrainedConfig.pruned_heads = property(  # type: ignore[attr-defined]
                _get_pruned_heads, _set_pruned_heads
            )
            injected.append("PretrainedConfig.pruned_heads")
    except Exception:  # noqa: BLE001
        pass

    return injected


def load_segmenter(name: str = "ltp"):
    """加载分句器（本地路径 + 强制离线）。目前支持 `ltp`（`hanlp` 权重未预置）。"""
    path, _p = _resolve(name)
    _force_offline()
    if name == "ltp":
        try:
            from ltp import LTP
        except Exception as e:
            raise ModelUnavailable(name, f"ltp 不可用：{type(e).__name__}: {e}", "pip install ltp") from e
        patched = _patch_transformers_compat()
        try:
            m = LTP(path)
        except Exception as e:
            raise ModelUnavailable(
                name,
                f"LTP({path}) 加载失败：{type(e).__name__}: {e}",
                "核对 LTPbase 目录完整性（config.json/权重/tokenizer）",
            ) from e
        if patched:
            print(f"[semantic_models] 已注入 transformers 兼容垫片：{patched}")
        return _LTPSegmenter(name, m, path)
    raise ModelUnavailable(name, f"分句后端 {name} 未实装加载适配", "目前支持 ltp；hanlp 待权重预置后再接")


# --------------------------------------------------------------------------
# 指纹（provenance 用）
# --------------------------------------------------------------------------
def embedder_fingerprint(name: str) -> dict:
    """→ `{model, dep_version, weights, ready}`（**键序稳定**，可落盘后逐键 diff）。

    三要素缺一不可：模型名回答"是谁"、依赖版本回答"用什么跑的"、权重结构指纹回答"哪份权重"。
    """
    p = _st.probe(name)
    return {
        "model": name,
        "dep_version": str(p.get("version") or ""),
        "weights": str(p.get("local_fingerprint") or ""),
        "ready": bool(p.get("model_path")),
    }
