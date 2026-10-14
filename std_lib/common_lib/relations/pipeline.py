# -*- coding: utf-8 -*-
"""std_lib.common_lib.relations.pipeline — 关系管道（W4-c 三拆）。

DocumentMeta + RelationPipeline（编排入口）；_self_check 离线自检。
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass

from config.enums import (
    BASIS_TYPE_SUBSTANTIVE,
    RELATION_DOC_KIND,
    RELATION_KIND,
    REPEAL_SCOPE_PARTIAL,
)

from .extractor import AliasResolver, RelationExtractor
from .patterns import (
    EXTRACTOR_VERSION,
    RelationConfig,
    default_config,
    docno_signature,
)


# ===========================================================================
# 六、管道层
# ===========================================================================
@dataclass

class DocumentMeta:
    """文件元信息（与项目实体键对齐：RFN=监管文件，IPN=内部制度）。"""

    doc_kind: str = "regulatory"
    ref: str = ""  # RFN-xxx / IPN-xxx
    name: str = ""
    doc_number: str = ""
    source: str = ""
    publish_date: str = ""
    effective_date: str = ""

    def __post_init__(self) -> None:
        if self.doc_kind not in RELATION_DOC_KIND:
            raise ValueError(f"doc_kind 须 ∈ {sorted(RELATION_DOC_KIND)}，得到 {self.doc_kind!r}")


class RelationPipeline:
    """抽取管道：原文 → 抽取 → （可选）别名解析 → 统一 dict。"""

    def __init__(
        self, config: RelationConfig | None = None, alias_resolver: AliasResolver | None = None
    ):
        self.cfg = config or default_config()
        self.extractor = RelationExtractor(self.cfg)
        self.alias_resolver = alias_resolver

    def run(self, text: str, meta: DocumentMeta | None = None) -> dict:
        res = self.extractor.extract(text)
        out = res.to_dict()
        if self.alias_resolver:
            for item in out["basis"] + out["repeal"]:
                item["resolved_name"] = self.alias_resolver.resolve(item["target_name"])
        if meta is not None:
            out["document"] = asdict(meta)
        return out


# ===========================================================================
# 七、自检
# ===========================================================================
def _self_check() -> None:
    p = RelationPipeline()
    r = p.run(
        "为规范管理，根据《中华人民共和国保险法》《中华人民共和国银行业监督管理法》"
        "等法律法规，制定本办法。《商业银行市场风险管理指引》（银监发〔2004〕10号）同时废止。"
    )
    names = [b["target_name"] for b in r["basis"]]
    assert "中华人民共和国保险法" in names, names
    assert r["repeal"] and r["repeal"][0]["target_docno"].startswith("银监发"), r["repeal"]
    # 否定语境不得算废止
    r2 = p.run("现拟废止《旧办法》（拟稿），并征求各分公司意见。")
    assert not r2["repeal"], r2["repeal"]
    # 枚举闭包
    assert BASIS_TYPE_SUBSTANTIVE in {"substantive"} and REPEAL_SCOPE_PARTIAL in {"partial"}
    assert RELATION_KIND == {"basis", "repeal"}
    assert docno_signature("银保监发〔2019〕19号") == "201919", docno_signature(
        "银保监发〔2019〕19号"
    )
    print(f"[common_lib.relations] 自检通过（{EXTRACTOR_VERSION}）")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    _self_check()
    print(
        json.dumps(
            RelationPipeline().run(
                "根据《中华人民共和国预算法》，制定本规定。《政府采购信息公告管理办法》"
                "（财库〔2010〕20号）予以废止。"
            ),
            ensure_ascii=False,
            indent=2,
        )
    )
