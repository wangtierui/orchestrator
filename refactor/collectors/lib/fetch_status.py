# -*- coding: utf-8 -*-
"""采集阶段**唯一记录状态字段** ``raw_docs.fetch_status`` 的状态机（单一事实来源）。

设计约定
--------
* ``raw_docs.fetch_status`` 是**唯一**表达「这条记录是否成功抓取完成」的字段。
  各源不得再另立记录级状态字段；附件明细、子表里的状态语义字段一律视为**明细数据**，
  不参与记录状态判定、不得与它双写同值。
* **成功只有一种**：``ok``。其余任何取值都表示「未完成/失败」——``full`` 模式据此重抓。
* 附件**拉取失败**（数据没拿到）必须上卷为记录失败；**没有附件**不影响成功。
  附件文本抽取失败属明细诊断（文件已下载、数据未丢），不上卷。
  例外：站点把附件地址写成内网/不可路由 IP（``unreachable_private_ip``），
  已按「只发一次、短超时」尝试过，属源数据缺陷而非采集失败；明细保留该诊断
  值供排查，但不把主记录降级（有正文仍为 ok，无正文仍为 no_content）。
* 取值集合是 PG CHECK 约束的唯一来源：``init/ddl.py`` 直接 import 本模块，
  ``init/schema.sql`` 由 init 工具从 ddl 生成，杜绝多处定义漂移。
* 状态优先级用于 ``on_conflict=update`` 的「不降级守卫」，仅此一处定义，
  ``storage/raw_store.py`` 与 ``storage/sql_backend.py`` 共用 :func:`rank_case`。
"""
from __future__ import annotations

# 历史取值全部保留：PG CHECK 与存量数据兼容，不得删减。
FETCH_STATUS_VALUES = (
    "pending",            # 已入列表、尚未抓取详情（瞬态，一般不应持久化）
    "ok",                 # 抓取+解析成功，有正文
    "no_content",         # 抓到页面但无正文
    "parse_error",        # 抓到但解析失败
    "fetch_failed",       # 列表/详情 HTTP 失败
    "download_failed",    # 附件下载失败
    "attachment_skipped",  # 仅列链接、未下附件
    "attachment_failed",  # 附件下载失败（统一失败态）
    "attachment_saved",   # 附件已存但正文未解析
    "skipped_existing",   # 兼容存量：增量复用旧记录（新代码不再写入）
    "title_link_only",    # 仅标题链接、无详情
    "offline_missing",    # 离线缓存缺失、仅列表面板
)

_FETCH_STATUS_SET = frozenset(FETCH_STATUS_VALUES)

OK = "ok"
NO_CONTENT = "no_content"
ATTACHMENT_FAILED = "attachment_failed"

# 严格成功判定：但凡不是 ok，都视为「不好的状态」。
def is_ok(status) -> bool:
    """记录是否处于完成态。full 模式的 known 闸门与各处判定统一走这里。"""
    return status == OK


def is_valid(status: str) -> bool:
    """状态是否合法（在统一取值集合内）。"""
    return status in _FETCH_STATUS_SET


def normalize(status) -> str:
    """把任意值规整为合法状态；无法识别时回落到 ``no_content``。"""
    if status in _FETCH_STATUS_SET:
        return status
    return NO_CONTENT


# --------------------------------------------------------------------------- #
# 附件失败上卷：唯一、通用，不依赖任何源
# --------------------------------------------------------------------------- #
# 只认「拉取层面的失败」——文件没拿到、数据缺失。抽取文本失败（unsupported/empty/
# corrupt/needs_ocr/library_missing）是明细诊断，文件本身已落盘，不算记录失败。
ATTACHMENT_FAILURE_MARKERS = frozenset({
    "download_failed", "attachment_failed", "fetch_failed", "download_error",
})

# 明确豁免的附件诊断值：站点返回内网/不可路由的附件地址，已尝试过一次，
# 属源数据缺陷（重抓也拿不到）。明细保留该值，但整条附件不参与失败上卷，
# 否则 full 模式会无限重抓这些永远修不好的记录。
ATTACHMENT_NON_FAILURE_MARKERS = frozenset({"unreachable_private_ip"})

# 附件明细里承载「下载结果」的字段名（各源历史命名不同，统一在此收口）
_ATTACHMENT_RESULT_KEYS = ("extract_status", "download_status", "quality")


def has_attachment_failure(attachments) -> bool:
    """附件列表里是否存在「拉取失败」。空列表 / None → False（没有附件 = 成功）。"""
    if not attachments:
        return False
    for att in attachments:
        if not isinstance(att, dict):
            continue
        # 豁免检查放在最前：即使旧数据里该附件残留 fetch_status / failed 标记，
        # 也不因「内网地址不可达」把主记录判失败。
        if any(isinstance(att.get(k), str)
               and att.get(k) in ATTACHMENT_NON_FAILURE_MARKERS
               for k in _ATTACHMENT_RESULT_KEYS):
            continue
        for key in _ATTACHMENT_RESULT_KEYS:
            val = att.get(key)
            if isinstance(val, str) and val in ATTACHMENT_FAILURE_MARKERS:
                return True
            if isinstance(val, (list, tuple, set)) and any(
                    isinstance(v, str) and v in ATTACHMENT_FAILURE_MARKERS for v in val):
                return True
        # 兼容存量：旧附件记录曾自带 fetch_status
        fs = att.get("fetch_status")
        if isinstance(fs, str) and fs not in ("", OK):
            return True
        if att.get("failed") is True:
            return True
    return False


def derive(content, status=None, attachments=None) -> str:
    """记录状态的**唯一推导入口**（供 canonicalize / raw_store 共用）。

    * 源显式给出状态 → 规整后采用（pbc/nfra 的细粒度失败态得以保留）；
    * 否则任一附件拉取失败 → ``attachment_failed``；
    * 否则有正文 → ``ok``；无正文 → ``no_content``。
    * 没有附件不影响成功。
    """
    if status not in (None, ""):
        return normalize(status)
    if has_attachment_failure(attachments):
        return ATTACHMENT_FAILED
    return OK if content else NO_CONTENT


def derive_from_record(record) -> str:
    """从采集器吐出的规范记录推导状态；**入库与进度日志共用此唯一口径**。

    背景：canonicalize 在存储入库边界才补 fetch_status，而 CollectorPipeline
    打点时拿到的是未规范化的原始记录（mof/gov 不带该字段）。若日志直接读
    ``record["fetch_status"]`` 会把全部成功记录误判为未完成。
    这里与 canonicalize 一致：content 取规范键、空白视作空、显式状态优先、
    附件拉取失败上卷。
    """
    if not isinstance(record, dict):
        return NO_CONTENT
    content = record.get("content")
    if not isinstance(content, str) or not content.strip():
        content = ""
    return derive(content, record.get("fetch_status"), record.get("attachments"))


# --------------------------------------------------------------------------- #
# 不降级守卫：状态优先级唯一定义，两个存储后端共用
# --------------------------------------------------------------------------- #
# 数值越高代表「内容越完整/越终态」。关键：attachment_skipped 必须低于
# attachment_failed，否则「跳过→下载失败」的转移会被拦截、循环不停止。
RANK: dict[str, int] = {
    "ok": 5,
    "attachment_saved": 4,
    "attachment_failed": 3,
    "attachment_skipped": 2,
    "title_link_only": 2,
    "fetch_failed": 1,
    "parse_error": 1,
}


def rank_case(col_expr: str) -> str:
    """生成 SQL ``CASE`` 表达式；``col_expr`` 为完整列表达式。

    raw_store 传 ``EXCLUDED.fetch_status``，sql_backend 传
    ``EXCLUDED.payload->>'fetch_status'``，两边不再各写一份字面量。
    """
    whens = " ".join(f"WHEN '{s}' THEN {r}" for s, r in RANK.items())
    return f"CASE COALESCE({col_expr},'') {whens} ELSE 0 END"
