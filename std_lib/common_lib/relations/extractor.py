# -*- coding: utf-8 -*-
"""std_lib.common_lib.relations.extractor — 关系抽取器（W4-c 三拆）。

BasisRelation/RepealRelation/ExtractionResult 三产物 + RelationExtractor +
AliasResolver。模式与文本工具经 `.patterns`（SSOT）。
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from config.enums import (
    BASIS_TYPE_PROCEDURAL,
    BASIS_TYPE_SUBSTANTIVE,
    REPEAL_ACTION_REPEAL,
    REPEAL_SCOPE_PARTIAL,
    REPEAL_SCOPE_WHOLE,
)

from .patterns import (
    _CN_NUM_RE,
    EXTRACTOR_VERSION,
    SCHEMA_VERSION,
    RelationConfig,
    default_config,
    is_generic_target,
    normalize_doc_name,
    preprocess,
)


# ===========================================================================
# 三、数据结构层
# ===========================================================================
@dataclass

class BasisRelation:
    """依据关系：本文以《target_name》为制定/起草依据。"""

    target_name: str
    normalized_name: str = ""
    target_docno: str = ""
    article: str = ""
    basis_type: str = BASIS_TYPE_SUBSTANTIVE
    is_explicit: bool = True
    source_snippet: str = ""
    # N-98（2026-09-28）：**引用跨度**（＝命中的引用文本本身，如 `《X办法》第12条`）。
    # 与 `source_snippet`（±100 字符**窗口**，含源侧自身上下文的条号）互补：
    # 窗口用于**人工审计/上下文**，跨度用于**目标侧条款定位**（`locate_dst_article`）——
    # 用窗口定位会被窗口内"源侧自身条号"污染（误定位或按"唯一命中才填"弃权）。
    source_span: str = ""
    offset: int = -1

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RepealRelation:
    """废止关系：本文废止/宣布失效《target_name》。"""

    target_name: str
    normalized_name: str = ""
    target_docno: str = ""
    action: str = REPEAL_ACTION_REPEAL
    scope: str = REPEAL_SCOPE_WHOLE
    article: str = ""
    reason: str = ""
    source_snippet: str = ""
    # N-98（2026-09-28）：引用跨度（见 `BasisRelation.source_span` 注记）
    source_span: str = ""
    offset: int = -1

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ExtractionResult:
    """单篇文件的抽取结果。"""

    basis: list[BasisRelation] = field(default_factory=list)
    repeal: list[RepealRelation] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # 被过滤的"纯类型泛指词"目标数（`《条例》`/`《办法》`，2026-09-14 净化，见 is_generic_target）
    filtered_generic: int = 0

    def to_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "extractor_version": EXTRACTOR_VERSION,
            "basis": [b.to_dict() for b in self.basis],
            "repeal": [r.to_dict() for r in self.repeal],
            "warnings": list(self.warnings),
            "filtered_generic": self.filtered_generic,
        }


# ===========================================================================
# 四、抽取器层
# ===========================================================================
class RelationExtractor:
    """通用依据/废止关系抽取器（六类主模式 + 三类辅助模式）。"""

    def __init__(self, config: RelationConfig | None = None):
        self.cfg = config or default_config()
        self._filtered_generic = 0
        self._compile()

    def _skip_generic(self, name: str) -> bool:
        """纯类型泛指词目标（`《条例》`/`《办法》`）→ 过滤并计数（2026-09-14 净化）。

        这类书名号在原文中就是**泛指**（如"依据有关条例"），不指向任何具体文件；
        保留会污染"未定位文件"统计（实测 15 条）。
        """
        if is_generic_target(name):
            self._filtered_generic += 1
            return True
        return False

    def _compile(self) -> None:
        cfg = self.cfg
        trg = "|".join(map(re.escape, cfg.basis_triggers))
        act = "|".join(map(re.escape, cfg.basis_actions))
        hier = "|".join(map(re.escape, cfg.hierarchy_words))
        sep = r"[、，,和及与以及]"
        q, qc = cfg.open_quote, cfg.close_quote
        one = rf"{q}[^{qc}]+{qc}"
        # 书名号列表：**分隔符可缺省**（真实公文中 `《A》《B》` 常紧邻，参照实现要求分隔符会漏抽）
        reflist = rf"{one}(?:\s*(?:{sep})?\s*{one})*"
        # 中间连接段：等? + 位阶词（可连写，如"法律法规"）+ 的+规定/要求/精神? + 逗号?
        mid = (
            rf"\s*(?:等)?\s*(?:(?:有关)?(?:{hier})+)?\s*"
            rf"(?:的\s*(?:相关|有关)?(?:规定|要求|精神|条款|内容|通知|办法|意见|细则))?\s*[，,]?\s*"
        )

        # ① 依据主模式：触发词 + 书名号列表 + 中间连接段 + 动作词
        self.basis_pattern = re.compile(
            rf"(?:{trg})\s*(?P<refs>{reflist}){mid}(?P<action>{act})(?:了|本|该|这)?"
        )
        # ② 条款级依据：触发词 + 《名》 + 第X条
        self.basis_article_pattern = re.compile(
            rf"(?:{trg})\s*{cfg.open_quote}(?P<name>[^{cfg.close_quote}]+){cfg.close_quote}\s*"
            rf"第(?P<article>{_CN_NUM_RE}+)条"
        )
        # ③ 程序性依据：经 + **机关名** + 同意/批准。
        # 2026-09-14 收紧（实测反例）：原 `[\u4e00-\u9fa5]{2,20}?` 最短匹配会跨词捕获，
        # 产出 `批准或者未按照`、`依法`、`部门负责人` 等非机关噪声。现要求：
        # 机关名须以**法定机关后缀**结尾，且**不含连接虚词**（或/未/按照/…）。
        ptrg = "|".join(map(re.escape, cfg.procedural_triggers))
        pact = "|".join(map(re.escape, cfg.procedural_actions))
        org_suffix = r"(?:总局|委员会|监管总局|保监会|银保监会|证监会|国资委|人民银行|银行|分行|局|委|部|厅|院|署|政府|机构|公司|中心|办公室)"
        bad = r"或|未|按照|和|与|等|应当|可以|并|及|由|对|在|以|所|的|有批准权|其他"
        self.procedural_pattern = re.compile(
            rf"(?:{ptrg})(?P<authority>(?:(?!{bad})[\u4e00-\u9fa5]){{2,18}}{org_suffix})(?:{pact})"
        )
        # ④ 废止单文件：（《名》）? （文号）? 动作
        ract = "|".join(map(re.escape, [k for k, _v in cfg.repeal_actions]))
        dn = "|".join(f"(?:{p})" for p in cfg.docno_patterns)
        self.repeal_pattern = re.compile(
            rf"{cfg.open_quote}(?P<name>[^{cfg.close_quote}]+){cfg.close_quote}"
            rf"(?:\s*[（(\[](?P<number>[^）)\]]*)[）)\]]\s*[，,]?)?\s*"
            rf"(?P<action>{ract})"
        )
        # ⑤ 废止列表：动作 + 书名号列表
        self.repeal_list_pattern = re.compile(rf"(?P<action>{ract})\s*(?P<refs>{reflist})")
        _ = dn  # （保留占位说明：文号形态见 self.docno_re，本处不内联以降低正则复杂度）
        # ⑥ 部分废止：《名》 第X条 动作
        self.partial_repeal_pattern = re.compile(
            rf"{cfg.open_quote}(?P<name>[^{cfg.close_quote}]+){cfg.close_quote}\s*(?:中|的)?\s*"
            rf"第(?P<article>{_CN_NUM_RE}+)条(?:\s*(?:至|—|-)\s*第{_CN_NUM_RE}+条)?\s*"
            rf"(?P<action>{ract})"
        )
        # 辅助① 专项废止列表头
        self.repeal_list_header = re.compile(
            rf"(?:决定|现决定)?\s*(?:予以)?(?:废止|宣布失效)\s*(?:以下|下列|如下)?\s*"
            rf"(?P<count>{_CN_NUM_RE}+)?\s*(?:部|件|个|份)?\s*(?:规章|规范性文件|文件|规定|办法)\s*[：:]"
        )
        # 辅助② 列表条目：一、《名称》（文号）。
        self.list_item_pattern = re.compile(
            rf"(?P<idx>{_CN_NUM_RE}+)\s*[、，,．.]\s*{cfg.open_quote}(?P<name>[^{cfg.close_quote}]+){cfg.close_quote}"
            rf"(?:\s*[（(\[](?P<number>[^）)\]]*)[）)\]]\s*[。．.]?)?"
        )
        # 辅助③ 附件提示
        self.attachment_hint = re.compile(r"(?:见|详见|参见)\s*(?:附件|附表|附后|附录)")
        # 文号（废止目标的文号回填）
        self.docno_re = re.compile("|".join(f"(?:{p})" for p in cfg.docno_patterns))
        # 废止原因
        self.reason_res = [re.compile(p) for p in cfg.repeal_reason_patterns]
        self._ract_pairs = cfg.repeal_actions

    # ---- 主入口 ----
    def extract(self, text: str) -> ExtractionResult:
        t = preprocess(text)
        self._filtered_generic = 0
        res = ExtractionResult()
        res.basis = self._extract_basis(t)
        res.repeal, warns = self._extract_repeal(t)
        res.warnings.extend(warns)
        res.filtered_generic = self._filtered_generic
        if self.attachment_hint.search(t):
            res.warnings.append('检测到"见附件"提示，废止/依据清单可能位于附件中')
        return res

    # ---- 依据 ----
    def _extract_basis(self, text: str) -> list[BasisRelation]:
        out: list[BasisRelation] = []
        seen: set[tuple[str, str]] = set()

        for m in self.basis_pattern.finditer(text):
            if self._excluded(text, m.start()):
                continue
            for name in self._quotes(m.group("refs")):
                if self._skip_generic(name):
                    continue
                key = (normalize_doc_name(name), "basis")
                if key in seen:
                    continue
                seen.add(key)
                out.append(
                    BasisRelation(
                        target_name=name.strip(),
                        normalized_name=key[0],
                        source_span=self._span(text, m),  # N-98：引用跨度（命中+紧随尾部）
                        source_snippet=self._snippet(text, m.start()),
                        offset=m.start(),
                    )
                )

        for m in self.basis_article_pattern.finditer(text):
            if self._excluded(text, m.start()):
                continue
            name = m.group("name").strip()
            if self._skip_generic(name):
                continue
            art = "第" + m.group("article") + "条"
            key = (normalize_doc_name(name), "basis")
            if key in seen:
                for r in out:
                    if r.normalized_name == key[0] and not r.article:
                        r.article = art
                        break
                continue
            seen.add(key)
            out.append(
                BasisRelation(
                    target_name=name,
                    normalized_name=key[0],
                    article=art,
                    source_span=self._span(text, m),  # N-98：引用跨度（披露字段）
                    source_snippet=self._snippet(text, m.start()),
                    offset=m.start(),
                )
            )

        for m in self.procedural_pattern.finditer(text):
            if self._excluded(text, m.start()):
                continue
            auth = m.group("authority").strip()
            if not auth:
                continue
            key = (normalize_doc_name(auth), "procedural")
            if key in seen:
                continue
            seen.add(key)
            out.append(
                BasisRelation(
                    target_name=auth,
                    normalized_name=key[0],
                    basis_type=BASIS_TYPE_PROCEDURAL,
                    source_span=self._span(text, m),  # N-98：引用跨度（披露字段）
                    source_snippet=self._snippet(text, m.start()),
                    offset=m.start(),
                )
            )
        return out

    # ---- 废止 ----
    def _extract_repeal(self, text: str) -> tuple[list[RepealRelation], list[str]]:
        out: list[RepealRelation] = []
        warns: list[str] = []
        seen: set[tuple[str, str]] = set()
        reason = self._reason(text)

        def _add(
            name: str,
            action_cn: str,
            *,
            number: str = "",
            scope: str = REPEAL_ACTION_REPEAL,
            article: str = "",
            snippet: str,
            offset: int,
            span: str = "",
        ) -> None:
            action = dict(self._ract_pairs).get(action_cn, REPEAL_ACTION_REPEAL)
            if not name.strip() or self._skip_generic(name):
                return
            key = (normalize_doc_name(name), action)
            if key in seen:
                return
            seen.add(key)
            out.append(
                RepealRelation(
                    target_name=name.strip(),
                    normalized_name=key[0],
                    target_docno=(number or "").strip(),
                    action=action,
                    scope=scope,
                    article=article,
                    reason=reason,
                    source_snippet=snippet,
                    source_span=span,
                    offset=offset,
                )
            )

        # ① 单文件 + 动作
        for m in self.repeal_pattern.finditer(text):
            if self._excluded(text, m.start()) or self._negated(text, m.start()):
                continue
            num = m.group("number") or ""
            if not num:  # 《名》后紧跟动作但无括注 → 从邻域回填文号
                num = self._nearby_docno(text, m.start(), m.end())
            _add(
                m.group("name"),
                m.group("action"),
                number=num,
                scope=REPEAL_SCOPE_WHOLE,
                snippet=self._snippet(text, m.start()),
                span=self._span(text, m),  # N-98：引用跨度（命中+紧随尾部）
                offset=m.start(),
            )

        # ② 动作 + 多文件列表
        for m in self.repeal_list_pattern.finditer(text):
            if self._excluded(text, m.start()) or self._negated(text, m.start()):
                continue
            for name in self._quotes(m.group("refs")):
                _add(
                    name,
                    m.group("action"),
                    scope=REPEAL_SCOPE_WHOLE,
                    snippet=self._snippet(text, m.start()),
                span=self._span(text, m),  # N-98：引用跨度（命中+紧随尾部）
                    offset=m.start(),
                )

        # ③ 部分废止
        for m in self.partial_repeal_pattern.finditer(text):
            if self._excluded(text, m.start()) or self._negated(text, m.start()):
                continue
            _add(
                m.group("name"),
                m.group("action"),
                scope=REPEAL_SCOPE_PARTIAL,
                article="第" + m.group("article") + "条",
                snippet=self._snippet(text, m.start()),
                span=self._span(text, m),  # N-98：引用跨度（命中+紧随尾部）
                offset=m.start(),
            )

        # ④ 专项废止列表（表头 + 编号条目）
        if self.repeal_list_header.search(text):
            items = self._parse_repeal_list(text)
            for it in items:
                _add(
                    it["name"],
                    "废止",
                    number=it["number"],
                    scope=REPEAL_SCOPE_WHOLE,
                    snippet=it["source"],
                    # ④ 专项列表：`it["source"]` 本已是**该条目自身文本**（非窗口）→ 直接作跨度
                    span=it["source"],
                    offset=it["offset"],
                )
            if not items:
                warns.append("检测到废止列表头，但未解析出条目，请检查格式")
        return out, warns

    def _parse_repeal_list(self, text: str) -> list[dict]:
        head = self.repeal_list_header.search(text)
        if not head:
            return []
        out = []
        for m in self.list_item_pattern.finditer(text[head.end() :]):
            name = m.group("name").strip()
            if not name:
                continue
            out.append(
                {
                    "name": name,
                    "number": (m.group("number") or "").strip(),
                    "source": m.group(0).strip(),
                    "offset": head.end() + m.start(),
                }
            )
        return out

    # ---- 辅助 ----
    def _reason(self, text: str) -> str:
        for rp in self.reason_res:
            m = rp.search(text)
            if m:
                return m.group(0).strip()
        return ""

    def _nearby_docno(self, text: str, start: int, end: int, *, window: int = 60) -> str:
        seg = text[max(0, start - window) : min(len(text), end + window)]
        m = self.docno_re.search(seg)
        return re.sub(r"\s+", "", m.group(0)) if m else ""

    def _excluded(self, text: str, pos: int) -> bool:
        w = self.cfg.exclude_window
        ctx = text[max(0, pos - w) : pos + w]
        return any(k in ctx for k in self.cfg.exclude_contexts)

    def _negated(self, text: str, pos: int) -> bool:
        """否定/未生效语境（拟废止、征求意见、草案…）→ 不算废止关系。"""
        w = self.cfg.exclude_window
        ctx = text[max(0, pos - w) : pos + w]
        return any(k in ctx for k in self.cfg.negations)

    def _span(self, text: str, m: re.Match) -> str:
        """引用跨度（N-98）：**命中文本 + 紧随向后尾部**（截至句读，上限 `span_tail_window`）。

        ⚠️ **仅披露，不参与定位**（N-98 实测否决，见 `extract_relations` 否决注记）：
        曾拟以跨度替代窗口作 `locate_dst_article` 的输入，实测目标侧覆盖由 5.7% 降至
        2.3%（只取命中文本）/ 2.5%（前向 200 字符）→ 保留窗口定位。
        本字段的价值转为**披露与后续研究**：给出"引用命中的确切文本"，使窗口内条号与
        引用自身条号可被区分统计（零行为影响）。
        """
        seg = text[m.start() : m.end() + self.cfg.span_tail_window]
        rest = seg[len(m.group(0)) :]
        for i, ch in enumerate(rest):
            if ch in "。；！？\n":
                return seg[: len(m.group(0)) + i]
        return seg

    def _snippet(self, text: str, pos: int) -> str:
        h = self.cfg.snippet_window // 2
        return text[max(0, pos - h) : min(len(text), pos + h)].strip()

    def _quotes(self, seg: str) -> list[str]:
        return re.findall(
            rf"{self.cfg.open_quote}([^{self.cfg.close_quote}]+){self.cfg.close_quote}", seg or ""
        )


# ===========================================================================
# 五、别名解析层
# ===========================================================================
class AliasResolver:
    """文件名称别名解析：精确 → 归一 → 包含（长度阈值默认 4，防"办法"类短词误配）。"""

    def __init__(self, alias_map: dict[str, str] | None = None, min_substr_len: int = 4):
        self.alias_map: dict[str, str] = dict(alias_map or {})
        self.min_substr_len = min_substr_len
        self._index: dict[str, str] = {}
        for k, v in self.alias_map.items():
            self._index[normalize_doc_name(k)] = v
            self._index[normalize_doc_name(v)] = v

    def add(self, alias: str, full_name: str) -> None:
        self.alias_map[alias] = full_name
        self._index[normalize_doc_name(alias)] = full_name
        self._index[normalize_doc_name(full_name)] = full_name

    def resolve(self, name: str) -> str | None:
        if not name:
            return None
        n = normalize_doc_name(name)
        if not n:
            return None
        if n in self._index:
            return self._index[n]
        for key, full in self._index.items():
            if len(key) < self.min_substr_len:
                continue
            if key in n or n in key:
                return full
        return None
