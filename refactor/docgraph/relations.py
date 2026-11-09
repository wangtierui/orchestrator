# -*- coding: utf-8 -*-
"""docgraph 关系抽取器适配层 —— 本项目唯一显式的外部算法依赖点。

与 ``clean`` 的 ``SENT_END_STRICT``（5 字符常量，已收编到 common/）不同，
``RelationExtractor`` 是一整套法规引用/废止关系的抽取算法，不是一个可逐字搬移的
常量。按「refactor 是独立项目」的边界，本模块：

  * **不向 sys.path 注入任何目录**，不再隐式把父工程拉进来；
  * 只在真正执行抽取时做一次显式、可失败的导入，缺失时给出可操作的错误；
  * 允许调用方通过 ``extract(..., extractor_factory=...)`` 注入自己的实现。

接口契约（由 docgraph/extract.py 消费）：
    extractor.extract(text) -> result
    result.basis  -> [item, ...]   item.target_name / basis_type / article
    result.repeal -> [item, ...]   item.target_name / action / article
"""
from __future__ import annotations


class RelationExtractorUnavailable(RuntimeError):
    """关系抽取器不可用（独立项目未内置该算法）。"""


def build_extractor():
    """构造抽取器；缺依赖时抛 :class:`RelationExtractorUnavailable`。"""
    try:
        from std_lib.common_lib.relations import RelationExtractor
    except ImportError as exc:
        raise RelationExtractorUnavailable(
            "docgraph 需要 RelationExtractor，独立项目未内置该算法。"
            "请通过 extract(..., extractor_factory=...) 注入实现，"
            "或安装提供 std_lib.common_lib.relations 的可选包。"
        ) from exc
    return RelationExtractor()
