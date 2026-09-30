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
def _split_by_token_anchor(text: str, toks: list, ends: str = "。；！？\n") -> list:
    """**以分词为锚**按句末标点聚合切句 → `list[str]`（`ltp` 与 `hanlp` 两后端**共用**）。

    为什么抽出来（N-181）：两后端此前各写一份**逐字相同**的实现 —— 审计以
    「实现重复」命中（`semantic_models.py:split × 2`）。共用一份不是"为复用而复用"，
    而是**同口径的唯一实现**：分句口径一旦两处漂移，切换后端会**静默改变**条文/关系产物
    （本仓对"口径"类逻辑的一贯纪律：受控 SSOT + 单一实现）。

    实现要点：**不丢字符**（标点归属其所在句）；入参异常由调用方处理（本函数不吞错）。

    ⚠️ **同算法 ≠ 逐字相同**（实测 2026-09-30）：本函数只做"按标点聚合"，**空白字符取决于
    分词器** —— LTP 会把空格作为 token 输出（`第一条 为规范…`），HanLP 不输出空格
    （`第一条为规范…`）。故**切换后端会改变输出文本的空白**（属后端固有差异，不是本函数的
    缺陷）。这也是"启用 ML 分句须先在可评样本量 P/R"的另一理由（见 `semantic_enhance`）。
    """
    if not text or not text.strip():
        return []
    if not toks or not toks[0]:
        return [text.strip()]
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
        """切句：以 LTP 分词为锚 → 委托 `_split_by_token_anchor`（与 hanlp 侧**同一实现**）。

        LTP 分词抛错则**原样抛出**，由调用方回退（不静默降级）。
        """
        return _split_by_token_anchor(text, self.cws([text]))


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


class _HanLPSegmenter:
    """HanLP 后端封装（**如实标注能力边界**，与 `_LTPSegmenter` 同接口）。

    ⚠️ 与 LTP 的差别（实测 2026-09-30，hanlp 2.1.5）：
      · HanLP **有**原生分句工具，但那是**规则法**（`hanlp.utils.rules.split_sentence`），不在本
        MTL 组件里；本类的 `split()` 与 LTP 侧**同口径**：以 `tok`（分词）为锚按句末标点聚合，
        并由 `basis_split` **显式标注非原生 ML 分句** —— 两个后端口径一致，切换不引入语义断层。
      · 本组件为 **MTL 多任务**（tok/pos/ner/srl/dep/sdp/con 一次前向），故 `pos()` 与 `cws()`
        共享同一次调用（LTP 侧需显式列 tasks）。
    """

    #: 切句依据（供调用方与审计判断"这是不是原生 ML 分句"）
    basis_split = "hanlp_tok（MTL 分词+标点聚合；原生分句 API 为规则法，不在本组件内）"

    #: 任务键（**实测** 2026-09-30，HanLP 2.1.5 MTL）：MTL 是**多任务**组件，`tok` 有
    #  `tok/coarse`（粗粒度，如「商业银行」为一个词）与 `tok/fine`（细粒度，如「商业/银行」）
    #  两个头；本仓取 **`tok/fine`**（与 LTP 的 `cws` 同粒度，切换后端不改变下游 token 尺度）。
    TOK_KEY = "tok/fine"
    #: 词性键：`pos` 有三个头（863/ctb/pku）；取 **`pos/ctb`**（HanLP 默认简写 `pos` 即它）。
    POS_KEY = "pos/ctb"

    def __init__(self, name: str, model: Any, path: str, alias: str) -> None:
        self.name = name
        self.model = model
        self.path = path
        self.alias = alias

    def _task_rows(self, key: str, texts, n: int) -> list:
        """取某任务头的结果并**归一为按句列表** `list[list]`。

        ⚠️ 实测歧义（HanLP 2.1.5）：`model(str)` 返回**单句扁平值**（`['商业','银行']`），
        而 `model(list[str])` 返回**按句嵌套**（`[['商业',…], ['本','办法',…]]`）——两者形状相似
        （都是 list）。判别规则：**批量时首元素是 `list`**（tok/fine 的句 = list；dep 的句 = list
        of tuple），单句时首元素是 `str`（tok）或 `tuple`（dep）。此规则对 tok/pos/dep 三种头都成立。
        """
        doc = self.model(list(texts))
        val = doc.get(key) if hasattr(doc, "get") else None
        if not val:
            return [[] for _ in range(n)]
        first = val[0]
        batched = isinstance(first, list) and not (n == 1 and isinstance(first, str))
        rows = [list(x) for x in val] if batched else [list(val)]
        if len(rows) < n:  # 防御：形状意外时按句数补齐（**不静默截断**）
            rows += [[] for _ in range(n - len(rows))]
        return rows[:n]

    def cws(self, texts) -> list:
        """分词（**真实 HanLP MTL `tok/fine` 调用**）→ `list[list[str]]`。"""
        if isinstance(texts, str):
            texts = [texts]
        texts = list(texts)
        return self._task_rows(self.TOK_KEY, texts, len(texts))

    def pos(self, texts) -> list:
        """词性（同一次 MTL 前向的 `pos/ctb` 头）→ `list[list[str]]`（与 `cws` 对齐）。"""
        if isinstance(texts, str):
            texts = [texts]
        texts = list(texts)
        return self._task_rows(self.POS_KEY, texts, len(texts))

    def dep(self, texts) -> list:
        """依存（MTL 的 `dep` 头）→ `list[list]`（对齐 `cws`；供结构增强按需使用）。"""
        if isinstance(texts, str):
            texts = [texts]
        texts = list(texts)
        return self._task_rows("dep", texts, len(texts))

    def split(self, text: str) -> list:
        """切句：以 HanLP 分词为锚 → 委托 `_split_by_token_anchor`（与 ltp 侧**同一实现**）。

        抛错则**原样抛出**，由调用方回退（不静默降级）。
        """
        return _split_by_token_anchor(text, self.cws([text]))


def _hanlp_alias() -> str:
    """HanLP 预训练**别名**（唯一事实源 = 清单 `tools.hanlp.weights.alias`）。

    为何不硬编码：别名变更（换模型/换变体）是**配置事实**；写进代码会让"改配置"必须改源码
    （且两处易漂移）。清单缺失即抛 `ModelUnavailable`（**fail-closed**，不猜默认值）。
    """
    spec = (_st.load_manifest().get("tools") or {}).get("hanlp") or {}
    alias = str(((spec.get("weights") or {}).get("alias")) or "").strip()
    if not alias:
        raise ModelUnavailable(
            "hanlp",
            "清单未声明 `weights.alias`（无法确定要加载哪个预训练模型）",
            "在 config/schema/semantic_tools.json 的 tools.hanlp.weights.alias 声明别名",
        )
    return alias


def load_segmenter(name: str = "ltp"):
    """加载分句器（本地路径 + 强制离线）。支持 `ltp` 与 `hanlp`（后者需权重已预置）。"""
    path, _p = _resolve(name)
    _force_offline()
    if name == "hanlp":
        # 权重目录 = `HANLP_HOME`（HanLP 官方唯一开关：`io_util.hanlp_home()` 读它）。
        # ⚠️ **不设**任何"离线开关"——HanLP 2.1.5 **没有**这类环境变量（本仓已更正过
        #   `MTL_HANLP_OFFLINE` 的假绿灯）；"离线"由**权重已预置**保证：`download()` 会先
        #   `os.path.isfile(save_path)` → 命中即"Using local … ignore …"，**根本不发起请求**。
        os.environ["HANLP_HOME"] = path
        alias = _hanlp_alias()
        try:
            import hanlp
            import hanlp.utils.io_util as _iu
        except Exception as e:
            raise ModelUnavailable(name, f"hanlp 不可用：{type(e).__name__}: {e}", "pip install hanlp") from e
        # ⚠️ **导入序守卫**（N-181）：hanlp 把权重根在**导入时**绑为函数默认参数
        # （`download(url, save_dir=hanlp_home(), …)` / `get_resource(path, save_dir=hanlp_home(), …)`）。
        # 若本进程**先** import 了 hanlp（例如先调用了别处的 probe 而未设 env）、**后**才设
        # `HANLP_HOME`，库内默认值仍指向旧目录 → `hanlp.load()` 会**去联网下载**（本环境必失败并
        # 重试数分钟，实测把 CI 的 pytest 顶到 600s 超时）。此处**显式拦下**并给出可执行指引，
        # 把"静默联网重试"变成**快速、可解释的失败**（fail-closed，符合本仓降级可观测纪律）。
        _baked = [
            x for x in (_iu.download.__defaults__ or ()) if isinstance(x, str) and os.path.isabs(x)
        ]
        if _baked and os.path.abspath(_baked[0]) != os.path.abspath(path):
            raise ModelUnavailable(
                name,
                f"hanlp 的权重根**已在导入时绑定**为 {_baked[0]}（期望 {path}）→ 继续加载会去联网下载",
                "确保 `HANLP_HOME` 在**导入 hanlp 之前**生效：先经 `semantic_tools.probe('hanlp')`"
                "（其 `probe.env` 会先设 env）或先设环境变量再 import",
            )
        try:
            m = hanlp.load(alias)
        except Exception as e:
            raise ModelUnavailable(
                name,
                f"hanlp.load({alias}) 失败：{type(e).__name__}: {e}",
                "核对 HANLP_HOME 下权重完整性（含 load_path 所指权重 zip）；"
                "另一常见原因：setuptools>=81 已移除 `pkg_resources`（hanlp 2.1.5 仍用它）"
                "→ `pip install 'setuptools<81'`",
            ) from e
        return _HanLPSegmenter(name, m, path, alias)
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
    # 目前实装：`ltp`（默认，`split_sentences(prefer="ltp")`）与 `hanlp`（N-181 已接：权重已预置）。
    raise ModelUnavailable(
        name,
        f"分句后端 {name} 未实装加载适配",
        "实装的后端：`ltp`（默认）与 `hanlp`；其它后端须先在 config/schema/semantic_tools.json 登记并补适配",
    )


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
