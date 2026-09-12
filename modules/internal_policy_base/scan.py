# -*- coding: utf-8 -*-
"""
internal_policy_base.scan — 内部制度源目录扫描与文件名解构（P6 scanner）

输入：制度源目录（默认可经 INTERNAL_POLICY_ROOT 或 --source-dir 指定，不硬编码路径）。
输出：文件级元数据清单（不解读正文）：
      {
        "ipn": "IPN-<16hex>",            # D-03：独立编号，md5(文号|标题)[:16]
        "file_name": "...",               # 原文件名
        "extension": "pdf|doc|docx|xlsx|xls",
        "docno": "阳光人寿办发〔2023〕58号",  # 文件名解构；无 → ""
        "title": "...",                   # 文件名解构（去文号/扩展名/前导_）；无 → 去扩展名
        "source_path": "绝对路径(源目录)",
        "size_bytes": 123,
        "sha256": "hex",                  # 内容指纹（摄取去重）
        "scanned_at": "YYYY-MM-DD HH:MM:SS",
      }

文件名形态（实测 107 文件）：
  1) 有文号：`阳光人寿办发〔2023〕58号_个险客经渠道团队套利处置管理办法.pdf`
  2) 无文号：`_个险中心城市渠道营销员考勤管理办法（2023版）.pdf`（前导 _）
  3) 无文号无下划线：`某某管理办法.pdf`
"""
from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime

# 文号形态：机关代字〔年〕序号 号（含 全半角括号、纯数字年、可无代字）
_DOCNO_RE = re.compile(
    r"(?P<docno>[\u4e00-\u9fa5]{1,20}(?:〔|\()\d{4}(?:〕|\))\s*第?\s*\d{1,4}\s*号)"
)
# 年份括注（如 （2023版）/（2023年））不属文号
_EXT_RE = re.compile(r"\.(pdf|doc|docx|xlsx|xls|xlsm|csv|txt|wps|rtf)$", re.I)
_SUPPORTED = {".pdf", ".doc", ".docx", ".xlsx", ".xls", ".xlsm"}


def parse_filename(name: str) -> dict:
    """文件名 → {docno, title}（解构锚点，非正文权威）。
    有文号：docno=文号, title=下划线后段（去扩展名）；
    无文号：title=去前导'_'/'-' 后整名（去扩展名）。
    """
    base = _EXT_RE.sub("", name.strip())
    m = _DOCNO_RE.search(name)
    docno = m.group("docno").strip() if m else ""
    if docno:
        # 文号后通常是 _ 分隔标题；兼容"标题（文号）"尾括号形态：
        # 文号后无实质内容（仅"）"等）→ 取文号前段（2026-09-12 实证 4 例）
        after = name[m.end():].lstrip("_").strip("）)（( ")
        after = _EXT_RE.sub("", after).strip()
        if len(after) >= 2:
            title = after
        else:
            before = name[:m.start()].strip("_（( ")
            title = _EXT_RE.sub("", before).strip() if before else _EXT_RE.sub("", base)
    else:
        title = _EXT_RE.sub("", base).lstrip("_").lstrip("-").strip()
    return {"docno": docno, "title": title}


def ipn_of(docno: str, title: str, extension: str = "") -> str:
    """内部制度编号派生（D-03，独立 RFN 空间）：md5(文号|标题|介质) 前 16 位。

    键 = 文号 + '|' + 标题 + (可选 '|' + 扩展名)（2026-09-08 修复）：
      - 同一 OA 文号常整批发文（如「阳光人寿发〔2025〕293号」下发 17 份档案表单），
        若只用文号会全部冲突；
      - 同一制度常见双介质（同名 pdf + xlsx 并存），扩展名参与消歧；
    无文号文件回退标题。与 rfn.unique_key 同构但前缀分离（IPN-）。
    """
    docno = (docno or "").strip()
    title = (title or "").strip()
    ext = (extension or "").strip().lower().lstrip(".")
    key = f"{docno}|{title}|{ext}" if docno else f"{title}|{ext}"
    return "IPN-" + hashlib.md5(key.encode("utf-8")).hexdigest()[:16]


# ============ 正文身份识别（内容权威，2026-09-12） ============
# 背景：部门制度文件名词干噪声多（"23."/"编号1."/"…_盖章"/"-清洁版V3"），文号与标题
# 须以**正文**为准；提取失败回退文件名解构（parse_filename，见模块头）。
_CONTENT_DOCNO_RE = re.compile(
    r"([\u4e00-\u9fa5]{2,20}(?:〔|【|（|\()\s*\d{4}\s*(?:〕|】|）|\))\s*第?\s*\d{1,4}\s*号)")
# 说明：「文件编号：XXX」（体系文件编号，如 SLOC-0203-05）**非发文号**，不作 docno 提取
# （2026-09-12 实测：会顶替真实文号；宁缺毋滥——识别不到则回退文件名解构）。
# 文件名遗留噪声（正文权威提取失败时的 fallback 清洗）：
_TITLE_NOISE_RE = re.compile(
    r"[-_—\s]*((清洁版|盖章版|含水印|无水印|扫描件|复印件|定稿版|终版|最终版)\s*V?\d*|盖章|扫描版)\s*$")
_BOOK_TITLE_RE = re.compile(r"《([^》]{4,90})》")
_GW_TITLE_RE = re.compile(r"^(关于.{2,70}?(?:的通知|的函|的决定|的通报|的公告|的批复|的意见))")
_TITLE_HEAD_CUT = re.compile(r"(一、|第一条|第1条|第一章|第[一二三四五六七八九十]+章|目的|总则)")
# 标题须以制度类关键词**结尾**（可带版本括注）；前缀命中为误（如页眉词"标准化管理体系文件"）
_TITLE_KW_END_RE = re.compile(
    r"(办法|规定|通知|细则|指引|规范|制度|方案|预案|手册|规程|准则|标准|流程|说明|通告|公告|条例|政策)"
    r"([（(][^）)]{1,20}[）)]|版)?$")
# 标题候选黑名单（页眉/体系词等）
_TITLE_DENY_RE = re.compile(r"^(标准化管理体系文件|管理文件|红头文件|文件)")


def _trim_self_repeat(s: str) -> str:
    """消解"公司名+标题+公司名 发布/编制…"自重复（如"XX公司内部控制指引XX公司 发布"）：
    找首段（8-30 字）的复现位置，截到复现起点（保留 [公司名+标题]）。"""
    n = len(s)
    for L in range(min(30, n // 2), 7, -1):
        pos = s.find(s[:L], L)
        if pos > 0:
            return s[:pos]
    return s


def _norm_cmp(s: str) -> str:
    """比较用归一：去空白与常见标点/括号（标题重叠校验用）。"""
    return re.sub(r"[\s\-_—、：:（）()〔〕【】《》]", "", (s or ""))


def _overlaps(a: str, b: str, min_lcs: int = 8) -> bool:
    """a/b 归一后互相包含，或最长公共子串 ≥ min_lcs（正文候选与文件名 title 交叉校验，
    防"正文引用他文"被误当标题——如首部引《中华人民共和国保险法》，2026-09-12 实证）。"""
    a2, b2 = _norm_cmp(a), _norm_cmp(b)
    if not a2 or not b2:
        return False
    if a2 in b2 or b2 in a2:
        return True
    best = 0
    for i in range(len(a2)):
        for L in range(best + 1, len(a2) - i + 1):
            if a2[i:i + L] in b2:
                best = L
            else:
                break
    return best >= min_lcs


def parse_content_identity(text: str, *, head_chars: int = 1500,
                           fallback_title: str = "") -> dict:
    """从正文首部识别 (文号, 标题)——内容权威（保守：识别不了返回空串，调用方回退文件名解构）。

    规则：
      ① 文号：[机关代字〔年〕号] 首个命中（4 位年括号统一归一为〔〕、去内部空格）。
      ② 标题：a) 文号后随的公文式标题（"关于…的通知/函/决定…"）优先；
             b) 否则首部标题段（截到"一、/第一条/第N章/目的/总则"前，6-80 字、
                以制度类关键词结尾、过黑名单）；
      校验：候选标题须与 `fallback_title`（文件名 title）有实质重叠（防引用他文误提）。
      （说明：原"首个书名号"规则已删除——正文首部引用《他法》极常见，误提风险高。）
    """
    t = (text or "").strip()
    if not t:
        return {"docno": "", "title": ""}
    head = t[:head_chars]
    docno = ""
    m = _CONTENT_DOCNO_RE.search(head)
    seg = head
    if m:
        docno = re.sub(r"\s+", "", m.group(1))
        docno = re.sub(r"[（(]\s*(\d{4})\s*[)）]", r"〔\1〕", docno)   # （2019）→〔2019〕归一
        seg = head[m.end():]
    title = ""
    # a) 公文式标题（校验重叠）
    gm = _GW_TITLE_RE.match(seg.lstrip(" ：:，,、"))
    if gm:
        t1 = gm.group(1).strip()
        if not fallback_title or _overlaps(t1, fallback_title):
            title = t1
    # b) 首部标题段（清编号前缀 + 重叠校验 + 关键词结尾 + 黑名单）
    if not title:
        cut = _TITLE_HEAD_CUT.search(seg)
        cand = (seg[:cut.start()] if cut else seg[:120])
        cand = re.sub(r"\s+", "", cand).strip(" 　：:—-、")
        cand = re.sub(r"^[0-9]{1,3}[、.．：:]\s*", "", cand)   # 清"4."/"23、"等编号前缀
        cand = re.split(r"(编制|审核|批准|发布|编写|版本号|生效日期|文件编号)", cand)[0].strip(" 　：:—-、")
        cand = _trim_self_repeat(cand)
        if (6 <= len(cand) <= 80 and _TITLE_KW_END_RE.search(cand)
                and not _TITLE_DENY_RE.match(cand)
                and "。" not in cand and cand[:1] not in ("）", ")", "。")   # 句读残留即拒
                and (not fallback_title or _overlaps(cand, fallback_title))):
            title = cand
    return {"docno": docno, "title": title}


def clean_title_noise(title: str) -> str:
    """清洗文件名遗留噪声——正文提取失败时的 fallback 用：
    ① 前缀编号（"23."/"4："/"(1)"）；② 尾部版本/盖章括注（-清洁版V3/_盖章/（含水印））；
    ③ 去外层书名号（《…》→ …）。"""
    s = (title or "").strip()
    s = re.sub(r"^[0-9]{1,3}\s*[、.．：:]\s*", "", s)
    s = re.sub(r"^[（(][0-9]{1,3}[）)]\s*", "", s)
    s = re.sub(r"^[A-Za-z]{1,3}[0-9]{2,4}\s*", "", s)   # 档案编号前缀（如 L042）
    s = _TITLE_NOISE_RE.sub("", s).strip(" -_—")
    return s.strip("《》").strip()


def scan_directory(root: str, *, supported: set[str] | None = None) -> list[dict]:
    """递归扫描目录下受支持文件 → 元数据清单（按文件名排序，确定性）。"""
    supported = supported or _SUPPORTED
    out = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if not os.path.isdir(root):
        raise FileNotFoundError(f"制度源目录不存在: {root}")
    for dirpath, _dirs, files in os.walk(root):
        for fn in sorted(files):
            ext = os.path.splitext(fn)[1].lower()
            if ext not in supported:
                continue
            p = os.path.join(dirpath, fn)
            sha = hashlib.sha256(open(p, "rb").read()).hexdigest()
            parsed = parse_filename(fn)
            out.append({
                "ipn": ipn_of(parsed["docno"], parsed["title"], extension=ext.lstrip(".")),
                "file_name": fn,
                "relative_path": os.path.relpath(p, root),
                "extension": ext.lstrip("."),
                "docno": parsed["docno"],
                "title": parsed["title"],
                "source_path": p,
                "size_bytes": os.path.getsize(p),
                "sha256": sha,
                "scanned_at": now,
            })
    return out


if __name__ == "__main__":  # 自检
    import json
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    samples = [
        "阳光人寿办发〔2023〕58号_个险客经渠道团队套利处置管理办法.pdf",
        "_个险中心城市渠道营销员考勤管理办法（2023版）.pdf",
        "阳光人寿客户敏感信息查询权限授权管理办法.doc",
    ]
    for s in samples:
        print(json.dumps(parse_filename(s), ensure_ascii=False))
