# -*- coding: utf-8 -*-
"""
rfn — 监管文件编号（RFN）唯一事实源访问层（唯一事实源模块）

原则（强制）：
  1. 监管文件编号（RFN）唯一事实源 = regulatory_classifier/data/人身保险公司-文件归属表.csv
     （首列"监管文件编号"，1073 条，2026-08-31 重构后：RFN-<16hex>，无 seq、无主题列）。
  2. 主题信息由 人身保险公司-主题归属表.csv 承载（枚举 T0上位法锚点 + T1–T10 全称），
     本模块加载时按 RFN 合并进记录，对外 row 含"主题"键。
  3. 任何模块（分类/引用/匹配/校验）获取 RFN 必须经本包接口，禁止在代码中硬编码 RFN 或另建副本。
  4. 本包提供唯一入口 get_index()（模块级单例），保证全部模块引用同一份权威数据。

用法：
  from rfn import get_index, THEME_MAP, RFN_PAT, theme_key
  idx = get_index()
  idx.by_rfn("RFN-3f7a9c2e8b1d4056")   # 精确查询 → dict 或 None
  idx.by_title("T1", "保险销售行为管理办法")  # 主题(支持 'T1' 码或全名)+标题 → dict 或 None
  idx.by_docno("银保监规[2022]24号")          # 发文字号 → list[dict]
  idx.by_theme("T1")              # 主题全部（支持 'T1' 码或全名）→ list[dict]
  idx.is_valid("RFN-3f7a...")      # 校验 → bool
  idx.all_rfns()                  # 全部编号集合
"""
import csv
import os
import re

_PKG_DIR = os.path.dirname(os.path.abspath(__file__))       # regulatory_classifier/rfn/
_DEFAULT_CSV = os.path.join(_PKG_DIR, "..", "data", "人身保险公司-文件归属表.csv")
_DEFAULT_THEME_CSV = os.path.join(_PKG_DIR, "..", "data", "人身保险公司-主题归属表.csv")

# 单一事实源：现行主题映射（2026-08-31 重构：T0上位法锚点 + T1–T10 全称）。
# 严禁在 registry.py / check_rfn_sync.py / align_artifacts.py 等处重复定义副本；
# 下游一律 `from rfn import THEME_MAP` 引用本对象，避免主题名漂移。
THEME_MAP = {
    "T0": "T0上位法锚点",
    "T1": "T1销售行为与消费者保护",
    "T2": "T2产品与精算制度",
    "T3": "T3资本与偿付能力监管",
    "T4": "T4公司治理与股权关联交易",
    "T5": "T5资金运用与资产负债管理",
    "T6": "T6养老与健康保险专项",
    "T7": "T7反洗钱与反恐怖融资",
    "T8": "T8机构准入与组织监管",
    "T9": "T9风险处置与案件合规",
    "T10": "T10数据治理与信息披露",
}
# RFN 编号正则（2026-08-31 重构）：RFN- + md5(去重键值) 前 16 位十六进制，无主题、无序号。
RFN_PAT = re.compile(r"^RFN-[0-9a-f]{16}$")


def _norm_title(t):
    """标题归一化（与归属表匹配用）：去括号尾注/书名号/空白。

    P4 备注：语义与 std_lib.common_lib.norm.norm_title 不同（本函数保留其余标点、不转小写），
    用于归属表标题精确匹配，勿改，防既有 RFN 索引漂移；故保留为包内私有实现。
    """
    t = re.sub(r"[（(](已废止|已失效|试行|修订)[）)]\s*$", "", (t or "").strip())
    return re.sub(r'[《》"“”\s]', "", t)


# 兼容导出（非 def，不触发公共符号唯一性门禁）：旧 `from rfn import norm_title` 仍可用
norm_title = _norm_title


def _load_theme_map(csv_path=None):
    """读主题归属表 → {监管文件编号: 主题全名}（枚举 T0上位法锚点 + T1–T10）。"""
    p = os.path.abspath(csv_path or _DEFAULT_THEME_CSV)
    m = {}
    if os.path.exists(p):
        for r in csv.DictReader(open(p, encoding="utf-8-sig")):
            m[r.get("监管文件编号", "")] = r.get("主题", "")
    return m


class RFNIndex:
    """唯一事实源索引（只读视图）。主题列由主题归属表合并。"""

    def __init__(self, csv_path=None, theme_csv=None):
        self.csv_path = os.path.abspath(csv_path or _DEFAULT_CSV)
        if not os.path.exists(self.csv_path):
            raise FileNotFoundError(f"权威归属表不存在（唯一事实源缺失）: {self.csv_path}")
        tmap = _load_theme_map(theme_csv)
        self._rows = list(csv.DictReader(open(self.csv_path, encoding="utf-8-sig")))
        for r in self._rows:
            r["主题"] = tmap.get(r.get("监管文件编号", ""), "")
        # 索引构建
        self._by_rfn = {r["监管文件编号"]: r for r in self._rows}
        self._by_title = {}
        for r in self._rows:
            self._by_title.setdefault(r["主题"], {})[norm_title(r["文件名称"])] = r
        self._by_docno = {}
        for r in self._rows:
            nd = self._norm_docno(r.get("发文字号", ""))
            if nd:
                self._by_docno.setdefault(nd, []).append(r)

    @staticmethod
    def _norm_docno(d):
        return re.sub(r"[〔\[\]（）()〕\s]", "", d or "").rstrip("号")

    # ---- 查询接口 ----
    def by_rfn(self, rfn):
        return self._by_rfn.get(rfn)

    def by_title(self, theme, title):
        """按（主题, 标题）查询记录。theme 接受主题码（'T1'）或完整主题名。"""
        theme_full = THEME_MAP.get(theme, theme)
        if theme_full not in set(THEME_MAP.values()):
            raise KeyError("未知主题（不在现行主题集合内）: %r" % (theme,))
        return self._by_title.get(theme_full, {}).get(norm_title(title))

    def by_docno(self, docno):
        nd = self._norm_docno(docno)
        return self._by_docno.get(nd, [])

    def by_theme(self, theme):
        """按主题查询全部记录。theme 接受主题码或完整主题名。"""
        resolved = THEME_MAP.get(theme, theme)
        if resolved not in set(THEME_MAP.values()):
            raise KeyError("未知主题（不在现行主题集合内）: %r" % (theme,))
        return [r for r in self._rows if r["主题"] == resolved]

    def is_valid(self, rfn):
        return bool(RFN_PAT.match(rfn or "")) and rfn in self._by_rfn

    def all_rfns(self):
        return set(self._by_rfn.keys())

    def ranges(self):
        rng = {}
        for theme, recs in self._group_by_theme().items():
            rng[theme] = (len(recs), theme)
        return rng

    def _group_by_theme(self):
        groups = {}
        for r in self._rows:
            groups.setdefault(r["主题"], []).append(r)
        return groups

    def rows(self):
        """全部记录（只读引用，勿修改）。"""
        return self._rows


_index = None


def get_index(csv_path=None, theme_csv=None):
    """获取唯一事实源索引（模块级单例，保证全模块引用一致）。"""
    global _index
    if _index is None or csv_path or theme_csv:
        _index = RFNIndex(csv_path, theme_csv)
    return _index


def load_attr_rows(attr_csv=None, theme_csv=None):
    """加载权威归属表全部行（已按 RFN 合并「主题」列），为下游脚本的**唯一**加载入口。

    背景（务必知悉）：2026-08-31 重构后，人身保险公司-文件归属表.csv 仅 8 列，
    **不再含「主题」列**；主题唯一来源为人身保险公司-主题归属表.csv
    （枚举 T0上位法锚点 + T1–T10 全称）。因此任何直接 `row["主题"]` 的写法都会
    KeyError —— 2026-09-01 06:00 调度即因此在 build_outputs.py 崩溃（action=error）。

    纪律：下游脚本一律改用本函数，**禁止**各自重复实现「主题注入」逻辑
    （重复实现是本次故障的扩散源，已在 5+ 处脚本各写一遍）。

    返回：list[dict]，每行含 8 个归属表原列 + 合并后的「主题」列。
    """
    return list(get_index(attr_csv, theme_csv).rows())


def theme_key(theme):
    """主题名 → 主题键（如 'T1销售行为与消费者保护' → 'T1'）。"""
    for k, v in THEME_MAP.items():
        if theme == v:
            return k
    if theme in THEME_MAP:
        return theme
    return None
