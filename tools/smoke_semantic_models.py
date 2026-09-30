# -*- coding: utf-8 -*-
"""tools/smoke_semantic_models — P1 语义模型**冒烟自检**（N-156，2026-09-30）

为什么需要：`probe` 只能证明"依赖装了 + 权重目录在"，**不能证明"能加载、能调用"**
（如权重格式不被加载库接受、缺 tokenizer、自定义建模代码缺依赖）。本工具把
"**真实初始化 + 真实调用**"做成可复跑动作，产出**可核验证据**（维度/样例/耗时/离线开关）。

用法：
    python -m tools.smoke_semantic_models            # 全部登记为 local_model 的嵌入 + 分句
    python -m tools.smoke_semantic_models bge_base_zh ltp
退出码：0 全通过；1 有失败（失败项照样打印原因，不静默）。
"""

from __future__ import annotations

import io
import os
import sys
import time

# 唯一引导点（tools 层不得自写 sys.path.insert —— gate_import_bootstrap）
from bootstrap import bootstrap

bootstrap("all")

from std_lib.common_lib import semantic_models as sm
from std_lib.common_lib import semantic_tools as st

EMBEDDERS = ("bge_base_zh", "text2vec", "youtu_embedding")
SEGMENTERS = ("ltp",)
SAMPLE = ("监管机构应当自受理申请之日起二十个工作日内作出决定。", "本办法自公布之日起施行。")
PARA = "第一条 为了规范监管行为，制定本办法。第二条 违反本办法规定的，责令改正；情节严重的，处以罚款。"


def _run_embedder(name: str) -> tuple:
    t0 = time.time()
    emb = sm.load_embedder(name)
    t_load = time.time() - t0
    t1 = time.time()
    vecs = emb.encode(list(SAMPLE))
    t_enc = time.time() - t1
    ok = len(vecs) == 2 and len(vecs[0]) > 0 and all(len(v) == len(vecs[0]) for v in vecs)
    # 语义合理性快检：同批两句的余弦相似度应为有限值（非 NaN）
    sim = sum(a * b for a, b in zip(vecs[0], vecs[1], strict=False))  # 已 L2 归一 → 点积即余弦
    print(f"  [{'OK ' if ok else 'FAIL'}] {name:18} 加载 {t_load:6.1f}s  编码 {t_enc:5.2f}s  "
          f"维度 {len(vecs[0]) if vecs else 0}  余弦 {sim:+.4f}")
    print(f"         指纹 {sm.embedder_fingerprint(name)}")
    return ok, t_load


def _run_segmenter(name: str) -> tuple:
    t0 = time.time()
    seg = sm.load_segmenter(name)
    t_load = time.time() - t0
    t1 = time.time()
    cws = seg.cws([PARA])
    pos = seg.pos([PARA])
    sents = seg.split(PARA)
    t_seg = time.time() - t1
    ok = (
        len(cws) == 1 and len(cws[0]) >= 5  # 真实分词
        and len(pos) == 1 and len(pos[0]) == len(cws[0])  # 词性与分词对齐
        and len(sents) >= 2 and all(s.strip() for s in sents)  # 切句非空
    )
    print(f"  [{'OK ' if ok else 'FAIL'}] {name:18} 加载 {t_load:6.1f}s  调用 {t_seg:5.2f}s  "
          f"分词 {len(cws[0])} 词  词性 {len(pos[0])} 项  切句 {len(sents)} 句")
    print(f"         分词样例 {'/'.join(cws[0][:10])}")
    print(f"         词性样例 {'/'.join(pos[0][:10])}（依据：{getattr(seg, 'basis_split', '?')}）")
    for s in sents[:2]:
        print(f"         · {s[:56]}")
    return ok, t_load


def main(argv=None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    names = argv or list(EMBEDDERS) + list(SEGMENTERS)
    emb_names = [n for n in names if n in EMBEDDERS]
    seg_names = [n for n in names if n in SEGMENTERS]

    print(f"P1 语义模型冒烟自检（{len(emb_names)} 嵌入 + {len(seg_names)} 分句）")
    print("  离线开关：HF_HUB_OFFLINE / TRANSFORMERS_OFFLINE 由加载层强制置位")
    results: dict = {}
    for n in emb_names:
        try:
            results[n] = _run_embedder(n)[0]
        except Exception as e:  # noqa: BLE001  冒烟须报全量结果，不因单项中断
            results[n] = False
            print(f"  [FAIL] {n:18} {type(e).__name__}: {str(e)[:150]}")
            if isinstance(e, sm.ModelUnavailable) and e.how_to_fix:
                print(f"         处置：{e.how_to_fix[:140]}")
    for n in seg_names:
        try:
            results[n] = _run_segmenter(n)[0]
        except Exception as e:  # noqa: BLE001
            results[n] = False
            print(f"  [FAIL] {n:18} {type(e).__name__}: {str(e)[:150]}")

    print(f"  环境复核：HF_HUB_OFFLINE={os.environ.get('HF_HUB_OFFLINE')} "
          f"TRANSFORMERS_OFFLINE={os.environ.get('TRANSFORMERS_OFFLINE')}")
    bad = [k for k, v in results.items() if not v]
    print(f"结果：{len(results) - len(bad)}/{len(results)} 通过" + (f"；失败 {bad}" if bad else ""))
    _ = st  # 保持导入（探测口径唯一事实源）
    return 1 if bad else 0


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    raise SystemExit(main())
