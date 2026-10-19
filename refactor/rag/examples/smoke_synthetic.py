# -*- coding: utf-8 -*-
"""smoke_synthetic — 不接任何真实数据，用几条假条款打通整条链路。

验证：LlamaIndex 链路能跑 / 向量库可写可读 / 换 embedder 只改 config。
首次运行会自动下载 bge-m3(~2.3GB)；想轻量测试，把 config.embedder_name
改成 "bge_small_zh" 即可。
"""
from __future__ import annotations

from ..canonical import CanonicalChunk
from ..config import RagConfig
from ..ingest import ingest
from ..query import query
from ..runtime import is_mac


def _fake_clauses():
    return [
        CanonicalChunk(
            text="第五十五条 商业银行应当建立健全风险管理体系，对各类风险进行持续监控。",
            meta={"source": "pbc", "title": "商业银行风险管理指引", "document_number": "2020-1",
                  "publish_date": "2020-01-01", "issuing_authority": "中国人民银行",
                  "chapter": "第五章 风险控制", "article_no": "第五十五条",
                  "source_url": "http://example.gov/2020-1"},
        ),
        CanonicalChunk(
            text="第五十六条 银行业金融机构应当定期开展压力测试，评估极端情景下的损失。",
            meta={"source": "pbc", "title": "商业银行风险管理指引", "document_number": "2020-1",
                  "publish_date": "2020-01-01", "issuing_authority": "中国人民银行",
                  "chapter": "第五章 风险控制", "article_no": "第五十六条",
                  "source_url": "http://example.gov/2020-1"},
        ),
        CanonicalChunk(
            text="第十条 保险机构开展互联网保险业务，应当保护投保人信息安全。",
            meta={"source": "nfra", "title": "互联网保险业务监管办法", "document_number": "2020-2",
                  "publish_date": "2020-06-01", "issuing_authority": "金融监管总局",
                  "chapter": "第二章 经营规则", "article_no": "第十条",
                  "source_url": "http://example.gov/2020-2"},
        ),
    ]


def main():
    import os
    embedder_name = os.environ.get("RAG_EMBEDDER", "bge_m3")
    embed_dim = int(os.environ.get("RAG_EMBED_DIM", "1024"))
    llm_name = os.environ.get("RAG_LLM", "ollama")
    cfg = RagConfig(embedder_name=embedder_name, embed_dim=embed_dim, llm_name=llm_name)

    print(f"[smoke] embedder={cfg.embedder_name} dim={cfg.embed_dim} "
          f"vector_store={cfg.vector_store['name']} llm={cfg.llm_name} "
          f"device={'mps' if is_mac() else 'cpu'}")

    print("[smoke] ingesting 3 synthetic clauses ...")
    ingest(_fake_clauses(), cfg)
    print("[smoke] ingest done.")

    q = "商业银行在风险控制方面有什么要求？"
    print(f"[smoke] query: {q}")
    res = query(q, cfg)
    print("\n=== ANSWER ===\n", res["answer"])
    print("\n=== CITATIONS ===")
    for c in res["citations"]:
        print(f"  {c['article_no']} {c['title']} (score={c['score']})")
    if llm_name == "mock":
        print("\n[smoke] 注：answer 由 MockLLM 生成（占位），非真实语义回答；"
              "接入真实 LLM 请把 RAG_LLM 设为 ollama 或 openai。")


if __name__ == "__main__":
    main()
