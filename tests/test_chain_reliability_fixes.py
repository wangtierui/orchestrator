# -*- coding: utf-8 -*-
"""tests/test_chain_reliability_fixes.py — 全链实跑暴露的三处缺陷的守卫（第一百零一批）。

三处都是**实跑才暴露**的（门禁与单测都绿，但整链跑不通）：

  ① **`run_clean_pipeline` 的 argparse help 含字面 `%`** ⇒ `add_argument()` 当场抛
     `ValueError: unsupported format character '?' (0xff09)` ⇒ 脚本**启动即崩**
     ⇒ clean 的 **5 个源全部 rc=1**，且 `stderr` 字节数**完全相同（1785B）**
     ——因为报错与数据无关（参数解析阶段）。**识别特征**：5 源同时失败且 stderr 等长。
  ② **`_c_token_exists` 硬编码仓根 `.pkulaw_token`** ⇒ token 实际在
     `timeliness_review/` ⇒ `timeliness:verify` 永远 SKIP（北大法宝预算再足也跑不到）。
     这是 N-169 同类缺陷的**第 3 处**（前两处在 `doctor` 的 `token`/`token_fresh`）。
  ③ **`_run_conditional` 的占位符缺值分支 return 残缺 rec**（缺 `elapsed_s`）
     且不写台账 ⇒ 汇总打印 `r['elapsed_s']` 抛 `KeyError`
     ⇒ **整链在最后一屏异常终止**（42 步全跑完、`gates` 都 PASS 了）。

守卫原则：每条都断言「**当前代码不会再犯**」，并对②③额外断言「**降级可辨**」
（缺字段要显示 `?` 并告警，不能静默吞掉）。
"""
from __future__ import annotations

import io
import os
import re

import pytest

pytestmark = pytest.mark.contract

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*p: str) -> str:
    return open(os.path.join(ROOT, *p), encoding="utf-8").read()


def _code_only(text: str) -> str:
    """剔除注释与字符串，只看**可执行代码**（注释里引用旧写法属正常解释）。"""
    import tokenize

    try:
        out: list[str] = []
        prev = (1, 0)
        with io.StringIO(text) as fh:
            for tok in tokenize.generate_tokens(fh.readline):
                if tok.type in (tokenize.COMMENT, tokenize.STRING):
                    continue
                sr, sc = tok.start
                if sr > prev[0]:
                    out.append("\n" * (sr - prev[0]))
                if (sr, sc) != prev:
                    out.append(" " * (sc - prev[1]))
                out.append(tok.string)
                prev = tok.end
        return "".join(out)
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return text


class TestCleanArgparsePercent:
    """① clean 的 argparse help 不得有裸 `%`（否则启动即崩、5 源全瘫痪）。"""

    def test_no_bare_percent_in_help(self) -> None:
        """**核心断言**：所有 `help=` 字符串里的 `%` 必须已转义为 `%%`。

        `argparse` 内部会做 `help_string % params`；文本里出现裸 `%`（如 `1.50%`）
        会在 `add_argument()` 时抛 `ValueError: unsupported format character`。
        """
        src = _read("modules", "regulatory_scrapers", "clean", "run_clean_pipeline.py")
        bad: list[str] = []
        for ln_no, ln in enumerate(src.splitlines(), 1):
            if "help=" not in ln:
                continue
            # 找 help= 之后的字符串字面量区（到行尾），检查其中的 `%`
            tail = ln.split("help=", 1)[1]
            for m in re.finditer(r"%(?!%)", tail):
                bad.append(f"L{ln_no}: {tail[:80]}")
                break
        assert not bad, ("help= 里出现**未转义的 `%`**（argparse 会对 help 做 % 格式化，"
                         "启动即 ValueError）:\n" + "\n".join(bad))

    def test_script_starts(self) -> None:
        """**行为断言**：脚本必须能完成参数解析（`--help` 不抛）。"""
        import subprocess
        import sys

        p = os.path.join(ROOT, "modules", "regulatory_scrapers", "clean",
                         "run_clean_pipeline.py")
        r = subprocess.run([sys.executable, p, "--help"], capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=120)
        assert r.returncode == 0, (
            f"--help 失败（rc={r.returncode}）——参数解析阶段崩溃：\n"
            f"{(r.stderr or '')[-900:]}")
        assert "schema-fail-rate-max" in (r.stdout or ""), "帮助文本未输出预期选项"


class TestTokenExistsUsesSingleSource:
    """② token 判定必须用 `load_token_any()`（不得硬编码仓根路径）。"""

    def test_no_hardcoded_root_token_path(self) -> None:
        """**根因守卫**：`_c_token_exists` 的实现里不得再出现硬编码仓根路径。"""
        src = _read("std_lib", "common_lib", "triggers.py")
        code = _code_only(src)
        i = code.find("def _c_token_exists")
        assert i >= 0, "未找到 _c_token_exists"
        j = code.find("\ndef ", i + 5)
        body = code[i:j if j > 0 else len(code)]
        assert "load_token_any" in body, (
            "_c_token_exists 未使用 load_token_any()（N-169 同类缺陷会复发："
            "token 实际在 timeliness_review/，硬编码仓根会让 verify 永远 SKIP）")

    def test_condition_reports_real_source(self) -> None:
        """正例：条件判定的说明里必须给出**命中来源**（可追溯）。"""
        from std_lib.common_lib import triggers as T

        ok, detail = T._c_token_exists(True, {})
        assert isinstance(ok, bool)
        assert "token 存在=" in detail
        assert ".pkulaw_token" in detail, f"未披露来源：{detail}"

    def test_want_false_inverts(self) -> None:
        """正例：`want=False` 时，若 token 实际存在则条件**不满足**（语义对称）。"""
        from std_lib.common_lib import triggers as T

        got_true, _ = T._c_token_exists(True, {})
        got_false, _ = T._c_token_exists(False, {})
        assert got_true != got_false, "want=True/False 判定应互斥"


class TestConditionalRecIsComplete:
    """③ 条件步骤的**每个**返回分支都必须给出完整 rec 并写台账。"""

    def test_placeholder_branch_has_elapsed_and_records(self) -> None:
        """**核心断言**：占位符缺值分支必须补 `elapsed_s` 且调 `_record_step`。

        原实现 `return {"step":…, "rc":0, "skipped":True, "reason":…}` 只有 4 字段
        ⇒ ① 汇总打印 KeyError 让整链在最后一屏崩；② 台账无记录（不可追溯）。
        """
        src = _read("tools", "run_production_refresh.py")
        i = src.find("except trg.UnresolvedPlaceholder as e:")
        assert i >= 0, "未找到占位符缺值分支"
        j = src.find("\n        if res.get(", i)
        seg = src[i:j if j > 0 else i + 2000]
        assert "elapsed_s" in seg, "该分支未补 elapsed_s（汇总打印会 KeyError）"
        assert "_record_step(rec)" in seg, "该分支未写台账（事后不可追溯）"

    def test_summary_uses_get_and_warns(self) -> None:
        """**防御断言**：汇总打印不得用直接下标，且缺失要**显式告警**。"""
        src = _read("tools", "run_production_refresh.py")
        i = src.find("print(f\"{'step':<22}{'rc':>4}{'elapsed_s':>12}\")")
        assert i >= 0, "未找到汇总表头"
        j = src.find("print(", i + 40)
        seg = src[i:i + 2600]
        code = _code_only(seg)
        assert "r['elapsed_s']" not in code and 'r["elapsed_s"]' not in code, (
            "汇总打印仍用直接下标 ⇒ 任何产生方漏字段都会让整链在最后一屏崩")
        assert "_miss_fields" in seg, "缺失字段未收集"
        assert "[WARN]" in seg, "缺失字段未显式告警（不能静默吞）"
