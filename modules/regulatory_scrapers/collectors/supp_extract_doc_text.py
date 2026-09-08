# -*- coding: utf-8 -*-
"""
supp_extract_doc_text.py —— 从 Word 97-2003 (.doc) OLE 二进制中提取正文文本。

环境约束：onnxruntime 不可用 → PaddleOCR/rapidocr 不可用；WPS/LibreOffice CLI 在
无头环境失败或被安全策略拦截。故采用「UTF-16LE 解码 + 常见字锚点过滤」方案：
  - .doc 正文以 UTF-16LE 存储，随机 OLE 二进制字节被误解码为 CJK 时形成短垃圾串；
  - 真实中文句子几乎必含高频功能字/领域字（的/第/条/规/定/保/险/监/管…），
    垃圾串几乎不含这些锚点 → 据此整段丢弃垃圾，保留真实正文。

用法：
  python scripts/supp_extract_doc_text.py
输出：data/raw/_sources/_docs/<name>_body.txt / _附件1.txt（UTF-8，已清洗）
"""
from __future__ import annotations

# ---- P3b 仓库引导（migrate_collectors_p3b.py 注入）：使 std_lib/config 可导入 ----
import os as _os
import sys as _sys

_GUIDE_ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "..", ".."))
if _GUIDE_ROOT not in _sys.path:
    _sys.path.insert(0, _GUIDE_ROOT)
del _GUIDE_ROOT, _os, _sys
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HERE)
SRC_DIR = os.path.join(PROJECT_ROOT, "data", "raw", "_sources")
DOCS_DIR = os.path.join(SRC_DIR, "_docs")  # 原始 .doc 二进制落盘处
# 提取出的正文/附件文本写入 _sources/ 根（与 enrich_placeholder_records /
# enrich_attachments 读取路径一致），文件名匹配 <key>_body.txt / <key>_附件N.txt

# ---- 高频锚点字（真实中文几乎必含其一；垃圾串几乎不含）----
ANCHOR = set(
    "的一是不了人保险公司国中为对和等第条规定应按将各机构本项行业管理监督通知文件年月日号"
    "及或其与该此行进作与时在向内由使被予可须得应于应当每该当并且若如除另附表中内上述下列"
    "以上以下之前之后之内的了着过起来等著关关于就便把被让使叫成做当为要需须应得能会可"
    "银保监办发便函总局办公厅部门"
)
# ---- 常见字集合（短串 2-3 字时要求全为常见字才保留）----
COMMON = set(
    "的一是不了人保险公司国中为对和等第条规定应按将各机构本项行业管理监督通知文件年月日号"
    "及或其与该此行进作与时在向内由使被予可须得应于应当每该当并且若如除另附表中内上述下列"
    "以上以下之前之后之内的了着过起来等著关关于就便把被让使叫成做当为要需须应得能会可"
    "银保监办发便函总局办公厅部门法施实细则办法意见批复决定命令公告通告报告计划方案措施"
    "内容要求标准条件范围对象程序方式期限责任主体权利义务收益风险资金费用价格成本收益"
    "销售投保承保理赔客户消费者投保人被保险人受益人代理人经纪人渠道网点机构员工人员"
    "产品服务业务活动行为信息数据资料记录档案系统平台网络网站移动互联电子渠道"
    "开展加强规范完善建立健全落实执行检查监督审计评价考核问责处罚整改纠正"
    "公开公平公正诚信合法合规适当合理必要有效及时准确真实完整安全保密"
    "一二三四五六七八九十百千万元角分百分比个点倍增长降低提高减少增加变动"
    "甲乙丙丁子丑寅卯天地人和气水火木金土东南西北中上下左右前后内外"
    "省市区县镇乡村街道路号栋单元室组队社村委镇政区代码邮政"
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    "，。、：；（）()《》〈〉""''·—－~！？．：；，"
)
# ---- 已知格式噪声 ASCII 标记（丢弃整段）----
JUNK_ASCII_TOKENS = {
    "worddocument", "office", "microsoft", "wps", "kso", "kwps", "table", "style",
    "normal", "theme", "font", "xml", "schema", "xsd", "rels", "content", "docprops",
    "heading", "times", "calibri", "arial", "simsun", "宋体", "黑体", "楷体", "仿宋",
    "正文", "标题", "批注", "页眉", "页脚", "目录", "char", "entry", "piece", "list",
    "ole", "storag", "stream", "fib", "clx", "pcd", "prm", "fkp", "plcf", "sttbf",
    "ffn", "chp", "pap", "sep", "sed", "fld", "bkf", "bkd", "fc", "lcb", "fct",
    "default", "template", "encoding", "utf", "unicode", "ascii", "ansi", "gbk",
    "cp", "lcid", "ver", "rev", "build", "app", "doc", "dot", "rtf", "txt", "pdf",
}

CJK_RUN = re.compile(r"[㐀-鿿豈-﫿]+")  # 扩展A+基本汉字+扩展兼容
# 仅保留常见全角标点/数字/货币，丢弃半角片假名(0xFF61-FF9F)与全角字母(Ｘ＀＿等垃圾)
FULLWIDTH_KEEP = set("（）．，：；！？＂＇～“”‘’〈〉《》、％＋－＝＄￥＃＆＊")


def keep_individual(c: str) -> bool:
    """单字符保留判定：ASCII 可打印 / CJK 标点与全角空格 / 常见全角标点 / 全角数字。"""
    return (
        (0x20 <= ord(c) <= 0x7E) or                                  # ASCII 可打印
        (0x3000 <= ord(c) <= 0x303F) or                              # CJK 标点/全角空格
        (c in FULLWIDTH_KEEP) or                                     # 常见全角标点/数字/货币
        (0xFF10 <= ord(c) <= 0xFF19) or                              # 全角数字
        (ord(c) in (0x2013, 0x2014, 0x2018, 0x2019, 0x201C, 0x201D, 0x2026, 0x3001, 0x3002))
    )


KEEP_INDIV = keep_individual  # 兼容别名

def _is_cjk(seg: str) -> bool:
    """判断 out 中的片段是否为被保留的真实中文文本片（CJK 串以汉字起头）。"""
    return bool(seg) and (0x3400 <= ord(seg[0]) <= 0x9FFF or 0x20000 <= ord(seg[0]) <= 0x2A6DF)

def _is_cjk_char(c: str) -> bool:
    return 0x3400 <= ord(c) <= 0x9FFF or 0x20000 <= ord(c) <= 0x2A6DF

def _keep_longest_dense_span(text: str, win: int = 100, thresh: float = 0.20) -> str:
    """（保留备用）返回 text 中 CJK 密度≥阈值的「最长连续区间」。"""
    n = len(text)
    if n == 0:
        return text
    best_len = best_start = best_end = 0
    i = 0
    while i < n:
        seg = text[i:i + win]
        cjk = sum(1 for c in seg if _is_cjk_char(c))
        if (cjk / max(1, len(seg))) >= thresh:
            j = i
            while j < n:
                seg2 = text[j:j + win]
                cjk2 = sum(1 for c in seg2 if _is_cjk_char(c))
                if (cjk2 / max(1, len(seg2))) >= thresh:
                    j += 1
                else:
                    break
            length = j - i
            if length > best_len:
                best_len, best_start, best_end = length, i, j
            i = j
        else:
            i += 1
    if best_len == 0:
        return text
    return text[best_start:best_end]

# Word 内部样式名/结构名标记（绝不出现于真实法规正文），用于界定正文终点
_STYLE_MARKERS = (
    "正文文本缩进", "正文文本", "普通表格", "批注框文本", "批注文字",
    "列出段落", "已访问的超链接", "Char Char1", "HTML ", "批注引用",
)

def _trim_to_content(text: str, win: int = 100, start_thresh: float = 0.15) -> str:
    """截取 [正文起始, 首个样式名标记之前] 的文本。

    处理 .doc UTF-16LE 误抽取的三种噪声：
      ① 文件头二进制（起点前，无中文）→ 起点取首个中文高密度窗口跳过；
      ② 正文之后的「中间二进制块」（无中文、无样式标记）→ 终点取 prefix 内
         最后一个汉字之后的位置截断，整块丢弃；
      ③ 样式名区 / 重复存储副本（普通表格/正文文本/…）→ 首个样式标记截断。
    """
    n = len(text)
    if n == 0:
        return text
    # 起点：首个中文高密度窗口
    start = 0
    for i in range(0, n, 20):
        seg = text[i:i + win]
        cjk = sum(1 for c in seg if _is_cjk_char(c))
        if (cjk / max(1, len(seg))) >= start_thresh:
            start = i
            break
    # 终点①：首个样式名标记（含其后的样式区与重复副本）
    sub = text[start:]
    end1 = n
    for m in _STYLE_MARKERS:
        idx = sub.find(m)
        if idx != -1:
            end1 = start + idx
            break
    # 终点②：prefix 内「最后一个中文高密度区间」的末尾（容忍表格单元格间的小
    # 间隙 < gap，但正文与中间二进制块/样式区之间的大断裂 > gap 会截断），从而
    # 干净丢弃其后无中文的二进制块与样式区，且不误切附表表格。
    prefix = text[start:end1]
    dense_end = _last_dense_end(prefix, win=200, thresh=0.08, gap=120)
    cut = start + dense_end if dense_end > 0 else end1
    return text[start:cut].strip()

def _last_dense_end(s: str, win: int = 200, thresh: float = 0.08, gap: int = 120) -> int:
    """返回 s 中「最后一个中文高密度区间」的末尾下标（容忍 gap 个字符的低密度）。"""
    n = len(s)
    best = -1
    i = 0
    while i < n:
        seg = s[i:i + win]
        cjk = sum(1 for c in seg if _is_cjk_char(c))
        if (cjk / max(1, len(seg))) >= thresh:
            j = i
            low = 0
            while j < n:
                seg2 = s[j:j + win]
                cjk2 = sum(1 for c in seg2 if _is_cjk_char(c))
                if (cjk2 / max(1, len(seg2))) >= thresh:
                    low = 0
                    j += 1
                else:
                    low += 1
                    if low > gap:
                        break
                    j += 1
            if j > best:
                best = j
            i = j
        else:
            i += 1
    return best

def _keep_cjk_run(run: str) -> bool:
    if not run:
        return False
    n = len(run)
    if n <= 3:
        return all(c in COMMON for c in run)
    # 长串：含锚点字 或 全部为常见字 → 保留
    if any(c in ANCHOR for c in run):
        return True
    if all(c in COMMON for c in run):
        return True
    # 否则按常见字占比 >=60% 放行（容忍个别生僻字）
    common_ratio = sum(1 for c in run if c in COMMON) / n
    return common_ratio >= 0.6

def _is_junk_ascii(tok: str) -> bool:
    low = tok.lower()
    if any(j in low for j in JUNK_ASCII_TOKENS):
        return True
    # 无空格且长度>40 的 ASCII 串（疑似 hex/base64 二进制）
    if len(tok) > 40 and " " not in tok and re.fullmatch(r"[A-Za-z0-9+/=]{20,}", tok):
        return True
    return False

def extract(data: bytes) -> str:
    text = data.decode("utf-16le", errors="ignore")
    out = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if 0x3400 <= ord(c) <= 0x9FFF or 0x20000 <= ord(c) <= 0x2A6DF:
            # CJK 串
            m = CJK_RUN.match(text, i)
            run = m.group(0)
            if _keep_cjk_run(run):
                out.append(run)
            else:
                out.append("")  # 丢弃垃圾串（不插入空格，避免粘连）
            i = m.end()
        elif KEEP_INDIV(c):
            out.append(c)
            i += 1
        else:
            # 控制/私有区/其他 → 视为分隔符，输出一个换行占位以保留段落感
            if c in "\r\n\x0b\x0c":
                out.append("\n")
            else:
                out.append(" ")
            i += 1
    raw = "".join(out)

    # ---- 关键修复：截取「正文起始 → 首个样式名标记之前」----
    # .doc 正文是连续文本片；文件头尾的 OLE 二进制被误解码为符号行/孤立常见字，
    # 正文之后还有一段「样式名区」（普通表格/正文文本/正文文本缩进/批注框文本/
    # 列出段落/已访问的超链接/Char Char1/HTML 等，均为 Word 内部样式/结构名，
    # 绝不出现于真实法规正文）。据此：起点取首个中文高密度窗口，终点取首个样式
    # 名标记，干净截取正文并天然去除重复存储副本与头尾二进制噪声。
    raw = _trim_to_content(raw)

    # 清理
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    # 去除分页标记（Word 自动分页产生的 "PAGE N"）
    raw = re.sub(r"^[ \t]*PAGE[ \t]+\d+[ \t]*$", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"\n{3,}", "\n\n", raw)
    # 删除纯 ASCII 噪声行（整行都是 JUNK_ASCII 标记或长 hex）
    lines = []
    for ln in raw.split("\n"):
        ln = ln.strip()
        if not ln:
            lines.append("")
            continue
        # 仅 ASCII 的行
        if all(ord(ch) < 128 for ch in ln):
            if _is_junk_ascii(ln):
                continue
            # 短 ASCII（<=30）可能是真实编号/英文术语，保留
            lines.append(ln)
        else:
            lines.append(ln)
    # 合并多余空行
    cleaned = "\n".join(lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    # 去除行内多余空格
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned.strip()

def main() -> int:
    jobs = [
        ("保监发_2010_107号_附件一.doc", "保监发_2010_107号_body.txt"),
        ("银保监办便函_2021_673号_附件1_续保表述要求.doc", "银保监办便函_2021_673号_附件1.txt"),
    ]
    for src, out in jobs:
        sp = os.path.join(DOCS_DIR, src)
        op = os.path.join(SRC_DIR, out)
        with open(sp, "rb") as f:
            data = f.read()
        txt = extract(data)
        with open(op, "w", encoding="utf-8") as f:
            f.write(txt)
        print(f"[extract] {src} -> {out} ({len(txt)} chars)")
        print("=" * 60)
        print(txt[:1500])
        print("...")
        print(txt[-800:])
        print("=" * 60)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
