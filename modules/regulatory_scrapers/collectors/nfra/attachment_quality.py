# -*- coding: utf-8 -*-
"""nfra.attachment_quality — **纯判定层**（E5 拆分，2026-10-16 / D-遗留第五批）

职责：附件质量标志的**纯计算**——输入逐页长度 / 文本 / OCR 标记，输出质量标志列表。

**本层是零外部依赖的纯函数层**（不触网、不落盘、不读磁盘、不依赖全局缓存），
与 E4 的 nfra/extract.py 同构。因此：
  · 等价性可用「重构前后逐字段比对」锁死，不依赖网络夹具形态假设；
  · 可被纯函数回归直接驱动（本仓已有 tests/test_nfra_extract.py 先例）。

函数体自 E5 起**逐字保留**（含原注释），仅改依赖来源——本层原本就零依赖，
所以搬运后函数体一字未改（这是它能被完整回归锁住的前提）。

依赖方向：attachment_quality ← attachment_pipeline ← fetch_attachments
"""
from __future__ import annotations

# 高频常用中文字白名单：用于「全流扫描回收」的精度闸门，过滤二进制误解码乱码
# （mojibake 多由罕见生僻字构成，几乎不含下列常用字）。覆盖监管/金融/表格常用词。
_COMMON_CN = set(
    "的一是中国有人在来发会以行监管理司公办为在和的是有对这中规办法等表指项据标算资风"
    "险产信度备额期初末位单亿元百千分万第条款号施实将及与或并其该各本外币现金债券投资贷"
    "款机构业务系统重要评估披露模板控制安排证券资产交易转换系数比例限额标准规定通知决"
    "定意见批复函照执照请依按结合属于内容如下附件名称时间年月日经已通过未除部分次上下列"
    "前后内外总合计算采用设置要求条件范围对象方式程序结果情况问题说明表示建议允许禁止"
    "必须可以应当不得超过低于高于等于小于大于之间对于关于依据根据按照适用参照引用摘录"
    "值序号类别型号目录收支结算付款存利率限担保抵押评级股股票融资租赁再审批案报示公"
    "细则引范间参净余负债自有其他相应上述下列各项指标名称填报复核负责备注说明摘要"
    "构会监委作金融银业发令年号版本式样页张份件项类目节点线面块格栏行列组合"
)



def quality_flags(page_lens, text, needs_ocr):
    """基于逐页长度与文本特征生成质量标志。"""
    flags = []
    empty_pages = sum(1 for x in page_lens if x == 0)
    if empty_pages:
        flags.append("empty_pages=%d" % empty_pages)
    if needs_ocr:
        flags.append("possible_scan(needs_ocr)")
    # 乱码检测：替换字符比例过高
    if text:
        repl = text.count("\ufffd")
        if repl > 0 and repl / max(len(text), 1) > 0.01:
            flags.append("garbled(replacement_chars=%d)" % repl)
    return flags
