# -*- coding: utf-8 -*-
"""gov.zhengceku_attach — **附件层**：附件下载与原文抽取（旧 .doc 走 Word COM、表格层级判定、CJK 占比阈值、提速/减重模式的阈值）。

依赖方向：zhengceku_attach → （无层间依赖）
本文件为**纯搬运**（E3 拆分：函数体逐字保留，含原注释），仅改依赖来源。
"""
import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
for _p in (_HERE, _os.path.dirname(_HERE),
           _os.path.abspath(_os.path.join(_HERE, "..", "..", "..", ".."))):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)
# 保留 `_HERE`（不删）：各层常量（`_SRC_ROOT` / `_REPO_ROOT`）用它计算路径
del _p, _os, _sys

import logging
import os

LOG = logging.getLogger("zhengceku")   # 附件层日志（沿用原 zhengceku logger 名）
# 上面的 logger 名**保持与拆分前一致**（`zhengceku`）——日志 handler 的 level/name 匹配依赖它


# ----------------------------------------------------------------------------------
# 常量
# ----------------------------------------------------------------------------------
_SRC_ROOT = os.path.dirname(_HERE)                                    # .../modules/regulatory_scrapers
_WORD_APP = None          # Word COM 单例（跨条目复用，避免每条目重启 Word 进程）
_WORD_DEAD = False        # 单例失效标记（Word 崩溃/不可用后不再重试）
_ATTACH_FAST = os.environ.get("ZC_ATTACH_FAST", "").strip().lower() in ("1", "true", "yes", "on")
_FAST_MIN_BODY = int(os.environ.get("ZC_FAST_MIN_BODY", "200") or 200)


def _word_app():
    """获取（并缓存）Word.Application 单例；不可用返回 None。

    ⚠️ 性能关键：`Dispatch` + `Quit` 每次约 2–3 秒。17,600 条级别采集若每条目启停
    Word 会额外消耗十余小时，故全流程复用同一实例，仅在进程退出时释放
    （`atexit` 注册）。实例失效则置 `_WORD_DEAD` 短路，回退通用抽取。
    """
    global _WORD_APP, _WORD_DEAD
    # ⚠️ 短路修复（2026-09-16）：原写法 `return _WORD_APP` 在 _WORD_DEAD=True 且
    # _WORD_APP 非 None 时**仍返回坏实例**，致 `_WORD_DEAD` 形同虚设（实测同一轮内
    # Word COM 失败后仍被反复调用）。改为 True 时一律返回 None。
    if _WORD_DEAD:
        return None
    if _WORD_APP is not None:
        return _WORD_APP
    if os.environ.get("ZC_NO_WORD_COM", "").strip().lower() in ("1", "true", "yes", "on"):
        # 全量采集时 Office 文件阻止策略弹窗会让 Documents.Open 永久阻塞（隐藏窗口下无法关闭，
        # 进程表现为「存活但无日志、CPU 极低」的僵死）。全量首轮可按需禁用，回退通用抽取。
        LOG.info("Word COM 已按 ZC_NO_WORD_COM 禁用，.doc 走通用抽取")
        _WORD_DEAD = True
        return None
    try:
        import atexit

        import win32com.client as wc  # type: ignore
        if os.environ.get("RCO_ALLOW_OFFICE_COM", "").strip() not in ("1", "true", "True"):
            # 批 56：**默认禁用 Office/WPS COM**（COM 实例与用户桌面会话交互，实测导致剪贴板/焦点被抢占；隐藏窗口下安全对话框还会永久阻塞）
            # ⇒ 需要时显式置 RCO_ALLOW_OFFICE_COM=1 开启
            return None
        app = wc.Dispatch("Word.Application")
        app.Visible = False
        app.DisplayAlerts = False
        _WORD_APP = app

        def _quit():
            global _WORD_APP
            try:
                if _WORD_APP is not None:
                    _WORD_APP.Quit()
            except Exception:  # noqa: BLE001
                pass
            _WORD_APP = None
        atexit.register(_quit)
    except Exception as e:  # noqa: BLE001
        LOG.warning("Word COM 不可用，回退通用抽取：%s", e)
        _WORD_DEAD = True
    return _WORD_APP


def extract_doc_via_word(path: str, timeout: float = 25.0) -> str:
    """Word COM 抽取 .doc/.docx 正文（复用单例）；不可用/失败/超时返回空串（由调用方回退）。

    ⚠️ **看门狗超时（2026-09-16）**：`Documents.Open` 在 Office 文件阻止策略弹窗时
    会**永久阻塞**（`DisplayAlerts=False` 管不住安全对话框，且隐藏窗口下无人点关闭）。
    实测该情形使采集进程僵死 4 小时 45 分（进程存活、内存 110 MB、日志与 CPU 停滞）。
    故置入守护线程 + join(timeout)：超时即放弃并永久标记 `_WORD_DEAD`，主流程不中断。
    卡住的线程为 daemon，不阻止进程退出；其残留的 Word 实例由系统回收。
    """
    global _WORD_DEAD
    app = _word_app()
    if app is None:
        return ""
    import threading
    box: dict = {}

    def _work() -> None:
        doc = None
        try:
            doc = app.Documents.Open(os.path.abspath(path), ReadOnly=True,
                                     AddToRecentFiles=False, Visible=False)
            box["text"] = (doc.Content.Text or "").replace(
                "\r\n", "\n").replace("\r", "\n").strip()
        except Exception as e:  # noqa: BLE001
            box["err"] = e
        finally:
            try:
                if doc is not None:
                    doc.Close(False)
            except Exception:  # noqa: BLE001
                pass

    t = threading.Thread(target=_work, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        _WORD_DEAD = True
        LOG.warning("Word COM 超时（%.0fs，疑 Office 阻止策略弹窗）→ 标记不可用并回退通用抽取：%s",
                    timeout, path)
        return ""
    if "err" in box:
        LOG.warning("Word COM 抽取失败 %s：%s", path, box["err"])
        _WORD_DEAD = True          # 失败视为实例已坏，后续走通用抽取
        return ""
    return box.get("text", "")


def _table_top_level(recs: list) -> dict:
    """把附件记录中的表格结构化字段聚合到记录顶层（与 XzfgkScraper 同口径）。"""
    ts, raw, meth = [], [], ""
    for r in recs:
        if r.get("table_structured"):
            ts.append(r["table_structured"])
        if r.get("table_raw_text"):
            raw.append(r["table_raw_text"])
        if not meth and r.get("table_recovery_method"):
            meth = r["table_recovery_method"]
    out = {}
    if ts:
        out["table_structured"] = ts[0] if len(ts) == 1 else ts
    if raw:
        out["table_raw_text"] = "\n\n".join(raw)
    if meth:
        out["table_recovery_method"] = meth
    return out


def _cjk_ratio(t: str) -> float:
    if not t:
        return 0.0
    return sum(1 for c in t if "\u4e00" <= c <= "\u9fff") / len(t)


def fetch_attachments(html: str, url: str, entry_id: str) -> tuple[list, str]:
    """下载并抽取详情页附件；返回 (附件记录, 附件原文)。

    对 .doc/.docx 用 Word COM **重抽并择优**：Word COM 交付的是 Word 自身渲染的正文
    （段落完整、CJK 占比 ~0.9），而通用抽取对旧版 .doc 走 olefile，会把 OLE 二进制
    噪声一并带出（实测同一文件：olefile 10895 字但 CJK 占比仅 0.398、全文挤在 1 行、
    首尾均为乱码；Word COM 3099 字、CJK 占比 0.906、35 段落）。
    **故择优规则以「文本洁净度」为主、长度为辅**，不能只比长度——否则会选中有噪声的长文本。
    """
    # W4（L1 重组）：import 双形态（包形态相对导入 / 脚本形态裸名）。
    try:
        from .attachments import fetch_gov_attachments  # type: ignore
    except ImportError:  # pragma: no cover - 脚本形态
        from attachments import fetch_gov_attachments  # type: ignore
    recs, att_text = fetch_gov_attachments(
        html, entry_id=entry_id, entry_title="", out_dir=None, base_url=url)
    improved = 0
    for r in recs:
        lp = r.get("local_path") or ""
        if not lp.lower().endswith((".doc", ".docx")):
            continue
        ap = lp if os.path.isabs(lp) else os.path.join(_SRC_ROOT, lp)
        if not os.path.exists(ap):
            continue
        t2 = extract_doc_via_word(ap)
        cur = r.get("text") or ""
        if not t2:
            continue
        # 择优：Word COM 结果洁净（CJK 占比 ≥0.6）即采用；否则仅在明显更长时采用
        if _cjk_ratio(t2) >= 0.6 or len(t2) > len(cur):
            if t2 != cur:
                r["text"], r["extracted"] = t2, True
                r["extract_status"], r["extract_engine"] = "ok_word_com", "word_com"
                improved += 1
    if improved:
        att_text = "\n\n".join(r["text"] for r in recs if r.get("extracted") and r.get("text"))
    return recs, att_text
