# -*- coding: utf-8 -*-
"""J15. 内联 JS 语法体检 —— 防「前端语法错误导致全站静默瘫痪」。

背景（2026-10-19 真实事故，判据化的理由）
----------------------------------------
在 `web/assets/index.html` 的**模板字符串内部**加了一段 `//` 注释，而注释里
用反引号标注字段名（`` `c.consistent` ``）⇒ **第一个反引号提前闭合了模板字符串**
⇒ 后面的内容被当成代码 ⇒ `Unexpected identifier` ⇒ **整段 `<script>` 不执行**。

症状极具误导性：
  · 页面停在 HTML 初始态：`加载中…` + `SSE 连接中…`；
  · 服务端**一切正常**：HTTP 200、全部 API 200、`/api/events` 有 `hello`/`ping`。
⇒ 用户报"本地工作台一直显示 SSE 连接中、页面显示加载中"，而第一轮排查会
   自然怀疑后端 / SSE / 端口 / 防火墙——**全部指向错误方向**。

为什么放进门禁而不只是 pytest
------------------------------
改前端的人未必跑全量 pytest，但**每次交付都要过门禁**。这条判据的成本极低
（解析一个 650 行的脚本，毫秒级），而漏检的代价是整站不可用。

实现：复用 `tools/js_syntax_lint.lint_file`（**判据不复制逻辑**——工具与门禁
共用同一份解析，避免"门禁通过了但工具报错"这类双口径漂移）。
"""
from __future__ import annotations

import os
import time

#: 受检的前端入口（新增页面时在此登记；判据遍历）
FRONTEND_FILES = (
    os.path.join("web", "assets", "index.html"),
)


def _check_inline_js(root: str) -> tuple[list[str], dict]:
    """J15：前端内联脚本必须通过语法体检。"""
    problems: list[str] = []
    detail: dict = {"files": {}, "backend": ""}
    try:
        from tools import js_syntax_lint as L
    except Exception as e:  # noqa: BLE001  工具不可导入 ⇒ 判据跳过但**可辨**
        detail["skipped"] = f"js_syntax_lint 不可导入：{type(e).__name__}: {e}"
        return problems, detail

    def _lint_stable(lint, path: str) -> dict:
        """读一次不通过时**短暂重试一次**。

        原因：写文件是"截断 + 重写"，不是原子操作。巡检/链跑恰好落在写入窗口时，
        会读到半个文件（实测 2026-10-28 06:10 报 265 条 `括号未闭合`，而同一文件
        前后两次手跑都 OK）。文件正在被写 ≠ 语法错误 —— 重试仍红才算真问题。
        """
        r = lint(path)
        if r.get("ok"):
            return r
        time.sleep(0.4)
        r2 = lint(path)
        if r2.get("ok"):
            r2["transient"] = True
        return r2

    for rel in FRONTEND_FILES:
        path = os.path.join(root, rel)
        if not os.path.exists(path):
            continue
        r = _lint_stable(L.lint_file, path)
        if r.get("transient"):
            detail.setdefault("transient", []).append(
                f"{rel}: 首次读取命中写入中间态，重试后通过（已忽略，不代表语法错误）")
        detail["backend"] = r.get("backend", "")
        detail["files"][rel] = {"scripts": r["scripts"], "lines": r["lines"],
                                "ok": r["ok"], "issues": len(r["issues"])}
        for it in r["issues"]:
            problems.append(
                f"J15: {rel}:{it['line']}:{it['col']} {it['detail']}"
                "（前端语法错误会使整段脚本不执行 ⇒ 页面停在初始态，"
                "而服务端全绿 ⇒ 极易误判为后端故障）")
        # 降级披露：词法兜底不支持嵌套模板字符串，会**误报**（实测曾把 3 个
        # 历史版本全判 FAIL）⇒ 若非真解析器，必须在 detail 里标明，不留错觉。
        if r.get("backend") != "esprima":
            detail.setdefault("warnings", []).append(
                f"{rel}: 使用词法兜底（esprima 不可用）——不支持嵌套模板，可能误报；"
                "建议安装 dev extra（esprima>=4.0）")
    return problems, detail
