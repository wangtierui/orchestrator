# -*- coding: utf-8 -*-
"""tests/test_mof_early_stop.py — TD-11（mof 采集软耗时上限 + 主机不可用熔断）守卫。

背景（2026-10-20 全链实跑实测）
------------------------------
`collect:mof` 用尽 4h 硬上限（14419.7s ≈ `COLLECT_TIMEOUT["mof"]=14400`）仍跑不完
⇒ **mof raw 停滞 10 天**。两条真实成因：

  ① **网络异常不熔断**：`download_attachment` 只把 **502/503** 计入连续失败，
     而**超时/连接拒绝**（WinError 10060）不计入 ⇒ 主机不可达时每附件走满
     `max_retries=6` 次退避 ≈ 150s 且**永不终止**（实测日志确认是这个分支）。
  ② **无软耗时上限**：gov 三个采集早就用 `--max-seconds`（1500/5100/21000），
     只有 mof 没有 ⇒ 只能等 4h 硬杀，而硬杀**不落盘**（落盘在 try 末尾）⇒ 全丢。

守卫分三类
----------
**A. 早停不得破坏数据（`removed` 误判）** —— 这是本批**实际踩到的严重 bug**：
   早停跳过第 2 个类别（780 条）后，`prev_by_id` 里那 780 条的 id 不在
   `current_raw_by_id` ⇒ 「历史存在但本次列表已无 ⇒ removed」把 **780 条 active
   置 False**（实测 `移除 780 / 活跃 90`，已从备份恢复）。根因是「本次列表里没有」
   有两种原因（真删了 / 我们没抓），早停场景下无法区分 ⇒ **早停必须跳过 removed 判定**。

**B. 早停必须落盘** —— 原实现把落盘内联在 try 末尾，早停 `break` 就不落盘。

**C. 主机不可用熔断（含网络异常）** —— 行为测试：让 `urlopen` 连续抛 `URLError`，
   确认达阈值即熔断、且任一次成功会清零。
"""
from __future__ import annotations

import os
import urllib.error

import pytest

pytestmark = pytest.mark.contract

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

COLLECTOR = os.path.join(ROOT, "modules", "regulatory_scrapers", "collectors",
                         "mof", "collector.py")
ATTACH = os.path.join(ROOT, "modules", "regulatory_scrapers", "collectors",
                      "mof", "attachments.py")
#: TD-11 逻辑本体（纯逻辑、无 IO）——collector 只做装配。
LIMITS = os.path.join(ROOT, "modules", "regulatory_scrapers", "collectors",
                      "mof", "limits.py")


def _code(path: str) -> str:
    """返回「**非纯注释行**」的代码文本（保留字符串字面量与行尾注释）。

    ⚠️ 这里**不能**用 tokenize 把字符串也剥掉：本文件的多数断言针对的正是
    字符串字面量（`status["early_stop"]` / `keep["active"]` / `rec["active"]`），
    剥掉字符串会让断言永远失败——**这是我自己踩过的坑**（v1 用 tokenize 剥
    COMMENT+STRING，4 个用例假失败）。
    只过滤「整行都是注释」的行，足够排除本文件大段说明性注释的干扰。
    """
    out: list[str] = []
    for ln in open(path, encoding="utf-8").read().splitlines():
        if ln.lstrip().startswith("#"):
            continue
        out.append(ln)
    return "\n".join(out)


class TestEarlyStopDoesNotDestroyData:
    """A. 早停不得把「没抓」当成「已删除」。"""

    def test_removed_detection_guarded_by_early_stop(self) -> None:
        """**核心断言**：`removed` 判定必须在 `if not _early["stopped"]` 之内。

        否则早停跳过的类别里，所有历史条目都会因「本次列表里没有」被标 removed
        ——实测把 780/870 条 active 置 False（数据破坏，须从备份恢复）。
        """
        code = _code(COLLECTOR)
        i = code.find('rec["active"] = False')
        assert i > 0, "未找到 removed 判定"
        # 护栏（`if _guard.stopped:`）必须出现在 removed 赋值**之前**。
        # ⚠️ 不用固定字符窗口：护栏注释较长，窗口小了会假失败（v3 实测 900 不够）。
        i_guard = code.find("if _guard.stopped:")
        assert i_guard > 0, "未见早停护栏（if _guard.stopped:）"
        assert i_guard < i, (
            "早停护栏在 removed 判定之后 ⇒ 早停会把未抓取的条目误判为已移除"
            "（数据破坏，实测 780/870 条 active 被置 False）")

    def test_early_stop_keeps_prev_records(self) -> None:
        """早停分支必须从 `prev` **原样保留**（active=True / change=unchanged）。

        实现已抽到 `limits.EarlyStopGuard.keep_prev`（单一出处）；collector 只调用。
        """
        lim = _code(LIMITS)
        i = lim.find("def keep_prev(")
        assert i > 0, "limits.py 未定义 keep_prev"
        seg = lim[i:i + 500]
        assert 'keep["active"] = True' in seg, "keep_prev 未置 active=True"
        assert 'keep["change"] = "unchanged"' in seg, "keep_prev 未标 unchanged"

    def test_history_restore_is_before_active_count(self) -> None:
        """兜底补全必须在 `active_count` 统计**之前**（否则统计不到补回的条目）。

        逻辑本体在 `limits.py`（`restore_missing`），collector 只做装配 ——
        故断言改为「collector 里 restore 调用点在 active_count 之前」。
        """
        code = _code(COLLECTOR)
        i_restore = code.find("restore_missing(")
        i_active = code.find("active_count = sum(")
        assert i_restore > 0 and i_active > 0
        assert i_restore < i_active, "兜底补全在 active_count 之后 ⇒ 统计失真"


class TestEarlyStopFlushes:
    """B. 早停必须落盘（不得因 break 丢进度）。"""

    def test_flush_store_is_shared_exit(self) -> None:
        """落盘统一走 `_flush_store()`（正常结束与早停同一出口）。"""
        code = _code(COLLECTOR)
        assert "def _flush_store(" in code, "未见 _flush_store 定义"
        assert "_flush_store()" in code, "未见调用点"
        # 原内联落盘若仍在，说明有两条路径（早停那条可能不落盘）
        assert code.count("write_raw_jsonl(") <= 1, (
            "write_raw_jsonl 出现多次 ⇒ 存在多条落盘路径（早停可能漏落盘）")

    def test_early_stop_recorded_in_status(self) -> None:
        """早停必须写入 `status["early_stop"]`（**不静默**：本次不完整是可查事实）。

        字段由 `limits.EarlyStopGuard.summary()` 给出（单一出处，collector 不重复拼），
        故断言 collector 的装配 + `limits` 的字段全集。
        """
        code = _code(COLLECTOR)
        assert 'status["early_stop"] = _guard.summary()' in code, (
            "collector 未把 early_stop 接到 guard.summary()")
        lim = _code(LIMITS)
        for k in ("at_s", "reason", "skipped_in_run", "restored_from_history"):
            assert f'"{k}"' in lim, f"limits.summary() 缺字段 {k}"

    def test_warning_does_not_blame_source(self) -> None:
        """早停时的告警不得说成「数据变更建议复核」（那会把排查引向错误方向）。"""
        src = open(COLLECTOR, encoding="utf-8").read()
        i = src.find("早停：活跃")
        assert i > 0, "未见早停专用告警"
        seg = src[i:i + 400]
        assert "数据变更" not in seg, "早停告警仍在暗示「数据变更」"

    def test_logic_lives_in_limits_module(self) -> None:
        """**单一出处**：早停/熔断/沿用逻辑应在 `limits.py`，collector 不重复实现。

        重构后若有人把逻辑搬回 collector，J11 行预算会再次被顶破（实测 822>800），
        且同一条判据会有两处实现（必分叉）。这条钉住分层。
        """
        assert os.path.exists(LIMITS), "limits.py 缺失（逻辑无处安放）"
        lim = _code(LIMITS)
        for sym in ("class EarlyStopGuard", "def content_should_revert",
                    "def merge_attachments"):
            assert sym in lim, f"limits.py 缺 {sym}"
        col = _code(COLLECTOR)
        assert "keep = dict(prev)" not in col, (
            "collector 又自带 keep_prev 实现 ⇒ 与 limits 双出处")


class TestHostUnavailableCircuit:
    """C. 主机不可用（含网络异常）必须熔断。"""

    @pytest.fixture()
    def att(self):
        from modules.regulatory_scrapers.collectors.mof import attachments as A

        return A

    def test_network_error_counts_and_opens_circuit(self, att, monkeypatch) -> None:
        """**行为断言**：连续 `URLError` 达阈值 ⇒ 熔断置位（这是 4h 耗尽的主因）。"""
        monkeypatch.setattr(att, "_ATT_502_STREAK", 0)
        monkeypatch.setattr(att, "_ATT_CIRCUIT_OPEN", False)
        # 睡眠打桩：真实退避 3~30s × 6 次会让本测试跑几分钟
        monkeypatch.setattr(att.time, "sleep", lambda *_a, **_k: None)

        def _boom(*_a, **_k):
            raise urllib.error.URLError("timed out (WinError 10060)")

        monkeypatch.setattr(att.urllib.request, "urlopen", _boom)
        monkeypatch.setattr(att, "ATTACHMENTS_DIR", "/tmp/_td11_att")

        a = {"file_url": "http://10.1.60.36:8888/x.pdf", "file_name": "x.pdf",
             "extension": "pdf", "file_type": "0"}
        for _ in range(att._MAX_ATT_502_STREAK):
            att.download_attachment(a, 1, "/tmp/_td11_out", None,
                                    timeout=1, max_retries=1)
        assert att._ATT_CIRCUIT_OPEN is True, (
            "网络异常未触发熔断 —— 主机不可达时每附件仍会走满重试 "
            "⇒ 4h 硬上限被耗尽（TD-11 原始故障）")

    def test_circuit_open_short_circuits(self, att, monkeypatch) -> None:
        """熔断后在途调用必须直接返回 None（不再发请求）。"""
        monkeypatch.setattr(att, "_ATT_CIRCUIT_OPEN", True)
        called: list[int] = []

        def _should_not_call(*_a, **_k):
            called.append(1)
            raise AssertionError("熔断后不应再发请求")

        monkeypatch.setattr(att.urllib.request, "urlopen", _should_not_call)
        got = att.download_attachment({"file_url": "http://x/y"}, 2,
                                      "/tmp/_td11_out", None)
        assert got is None and not called

    def test_success_resets_streak(self, att, monkeypatch) -> None:
        """成功必须清零连续失败计数（否则偶发抖动会累积成误熔断）。"""
        monkeypatch.setattr(att, "_ATT_502_STREAK", att._MAX_ATT_502_STREAK - 1)
        monkeypatch.setattr(att, "_ATT_CIRCUIT_OPEN", False)
        monkeypatch.setattr(att, "_MAX_ATT_502_STREAK", 8)

        class _Resp:
            def read(self) -> bytes:
                return b"%PDF-1.4 test"

            def __enter__(self):
                return self

            def __exit__(self, *_a) -> None:
                return None

        monkeypatch.setattr(att.urllib.request, "urlopen",
                            lambda *_a, **_k: _Resp())
        monkeypatch.setattr(att, "ATTACHMENTS_DIR", "/tmp/_td11_att")
        monkeypatch.setattr(att, "_finalize_attachment",
                            lambda *a, **k: {"ok": True})
        a = {"file_url": "http://x/y.pdf", "file_name": "y.pdf",
             "extension": "pdf", "file_type": "0"}
        out = att.download_attachment(a, 3, "/tmp/_td11_out", None,
                                      timeout=1, max_retries=1)
        assert out is not None
        assert att._ATT_502_STREAK == 0, "成功后未清零连续失败计数"


class TestSkipIsNotDiscard:
    """D. 「跳过下载」≠「丢弃历史」——TD-11 治理中实测踩到的两个数据陷阱。

    两者都不是我这轮引入的 bug，而是**既有缺陷被 TD-11 方案放大**：
    若照早期建议直接 `--no-attachments` 补主数据，历史附件/正文会被静默抹掉。
    """

    def test_no_attachments_keeps_history_attachments(self) -> None:
        """`--no-attachments` 时必须**沿用历史 attachments**（不得传 []）。

        实测：`build_entry(..., attachments=[])` 会把 `attachments` /
        `attachment_count` / `table_structured` / `table_raw_text` 四字段
        整体清空 ⇒ 主库 63.6MB→36.4MB（差 ≈29.4MB，正是 attachments 体量）。
        """
        code = _code(COLLECTOR)
        assert "_atts_for(" in code, "未见 _atts_for（附件沿用历史的唯一入口）"
        # 不得再出现 `collect_attachments(rid, rate) if fetch_att else []`
        assert "if fetch_att else []" not in code, (
            "仍在用 `… if fetch_att else []` ⇒ --no-attachments 会传空 attachments")

    def test_atts_for_signature_takes_do_fetch(self) -> None:
        """`_atts_for` 必须把 `fetch_att` 作为**形参**（否则闭包延迟绑定 = ruff B023）。"""
        code = _code(COLLECTOR)
        i = code.find("def _atts_for(")
        assert i > 0, "未找到 _atts_for 定义"
        seg = code[i:i + 200]
        assert "do_fetch" in seg, "do_fetch 未作为形参（B023 风险）"

    def test_truncated_content_reverts_to_history(self) -> None:
        """详情返回**截断**正文时必须沿用历史（空值判据抓不到"短"）。

        实测（直连接口，非缓存）：源站对长文返回截断内容
        （6562 vs 27548 / 14409 vs 50772）⇒ 129 条正文静默缩水 6.0MB。
        判据必须是 `detail is None` **或**「新正文 < 历史 × 0.8」——
        只判"空"会漏掉截断（截断后不为空但已失真）。
        """
        code = _code(COLLECTOR)
        assert "_CONTENT_SHRINK_RATIO" in code, "未见截断比例阈值"
        assert "detail is None" in code, "未见「详情未拿到」判据"
        assert "content_text_reverted" in code, (
            "回退未计数 ⇒ 静默决策（无法事后复核）")

    def test_revert_counter_in_status_schema(self) -> None:
        """status 必须有 `content_text_reverted` 字段（**不静默**）。"""
        code = _code(COLLECTOR)
        i = code.find('"content_text_reverted"')
        assert i > 0, "status 初始化缺 content_text_reverted（字段不稳定 ⇒ 下游要判存在性）"


class TestWiring:
    """调度面：mof 采集必须带 `--max-seconds`（对齐 gov）。"""

    def test_mof_argv_has_max_seconds(self) -> None:
        from tools import run_production_refresh as R

        argv = R.COLLECT_CMD["mof"]
        assert "--max-seconds" in argv, (
            "mof 采集 argv 缺 --max-seconds —— 只能等 4h 硬杀，而硬杀不落盘 ⇒ 进度全丢")
        i = argv.index("--max-seconds")
        val = int(argv[i + 1])
        assert 0 < val < R.COLLECT_TIMEOUT["mof"], (
            f"软上限 {val}s 必须**小于**硬上限 {R.COLLECT_TIMEOUT['mof']}s"
            "（否则等软上限也来不及落盘）")

    def test_gov_sources_precedent(self) -> None:
        """**证据锚**：gov 三个采集本来就用 --max-seconds（本方案是**对齐惯例**）。"""
        from tools import run_production_refresh as R

        for k in ("gov_xzfgk", "gov_zhengceku"):
            assert "--max-seconds" in R.COLLECT_CMD[k], k

    def test_collector_runs_in_script_form(self) -> None:
        """**双形态守卫**：必须能以**脚本形态**直接跑（生产调度就是这种）。

        实测踩到：我给 collector 加 `from . import limits`（相对导入）后，
        生产 argv 一跑即崩（`attempted relative import with no known parent
        package`）——而**包形态**一切正常，单测也全绿 ⇒ 只有真跑才暴露。
        仓内既有惯例见 `attachments` 的 try/except 双导入。
        """
        import subprocess
        import sys

        p = os.path.join(ROOT, "modules", "regulatory_scrapers", "collectors",
                         "mof", "collector.py")
        r = subprocess.run([sys.executable, p, "--help"], capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=180, cwd=ROOT)
        assert r.returncode == 0, (
            "脚本形态（= 生产调度真实形态）跑不通 ⇒ 采集会整体失败：\n"
            f"{(r.stderr or '')[-600:]}")
        assert "--max-seconds" in (r.stdout or ""), "help 未见 --max-seconds"

    def test_relative_import_is_guarded(self) -> None:
        """`limits` 的导入必须**双形态兼容**（相对导入 + 脚本形态兜底）。"""
        code = _code(COLLECTOR)
        assert "from . import limits as _limits" in code, "未见相对导入（包形态）"
        assert "import limits as _limits" in code, (
            "未见脚本形态兜底导入 —— 生产 argv（脚本形态）会崩")
