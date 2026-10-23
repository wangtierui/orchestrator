# -*- coding: utf-8 -*-
"""tests/test_import_hygiene.py — 模块级**未定义名**守卫（TD-28 的结构性根治）。

为什么需要这个守卫
------------------
TD-28 的成因不是"某人手滑漏了个 import"，而是**拆分模块时没有结构性检查**：

  · `nfra/attachments_extract.py` 被拆成 fetch / process / report 三个文件；
  · 函数体（含对 `glob`/`time`/`random`/`sniff_kind`/`sha256_of`/`docs_root` 的调用）
    整段搬到了新文件，但**import 语句留在了原文件**；
  · 与此同时，另一批"去重"改动把 `sha256_of` 从两个模块里删了（理由写着
    "零调用方"——那正是拆分造成的假象）。

结果：`ruff` 能报出来（F821），但 ruff **不是**本仓的必跑项，于是 20 处未定义名
在库里躺了整整一个版本周期，直到用户报"附件处理失败"才被发现。

本守卫用 **stdlib `symtable`** 做同样的事（不引新依赖、不依赖 ruff 是否安装）：
对每个模块的每个作用域，凡是"被当作全局名引用、但模块级从未绑定、也不是内建"的名字
⇒ 判为未定义。这覆盖 `import` 漏写、`def`/赋值漏写、以及"删了定义没删调用"三类。

纪律：**新增采集器/拆分模块后必跑**。它比"跑一遍链路"便宜得多，也比人眼可靠。
"""

from __future__ import annotations

import builtins
import os
import symtable

import pytest

pytestmark = pytest.mark.contract

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 受检目录：采集器与共享工具层（历史上出问题的正是这两处）
SCAN_DIRS = (
    os.path.join(ROOT, "modules", "regulatory_scrapers", "collectors"),
    os.path.join(ROOT, "std_lib", "scraper_std"),
    os.path.join(ROOT, "web"),
    os.path.join(ROOT, "interfaces"),
)

_BUILTINS = set(dir(builtins))

#: 由**导入系统**注入、源码里不会出现的模块级名字（不算未定义）。
#: 少了这一条会把 `os.path.dirname(__file__)` 判成错误 —— 实测误报源。
_MODULE_DUNDERS = {
    "__file__", "__name__", "__doc__", "__package__", "__spec__",
    "__loader__", "__builtins__", "__path__", "__annotations__",
    "__dict__", "__cached__",
}


def _module_level_names(top: symtable.SymbolTable) -> set[str]:
    """模块作用域里**被绑定**的名字（导入/赋值/def/class/参数）。"""
    out: set[str] = set()
    for sym in top.get_symbols():
        if sym.is_imported() or sym.is_assigned() or sym.is_namespace():
            out.add(sym.get_name())
    return out


def _walk(tbl: symtable.SymbolTable):
    yield tbl
    for child in tbl.get_children():
        yield from _walk(child)


def undefined_names(path: str) -> list[tuple[str, int, str]]:
    """返回 `[(作用域名, 行号, 名字)]`（模块级未绑定、被当全局引用、非内建）。"""
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    top = symtable.symtable(src, path, "exec")
    defined = _module_level_names(top) | _BUILTINS | _MODULE_DUNDERS
    bad: list[tuple[str, int, str]] = []
    for tbl in _walk(top):
        if tbl is top:
            continue
        for sym in tbl.get_symbols():
            name = sym.get_name()
            # `is_global()` = 该作用域把它当**全局**用；若模块级也没绑定 ⇒ 未定义
            if sym.is_global() and name not in defined:
                bad.append((tbl.get_name(), 0, name))
    return bad


def _py_files(root: str) -> list[str]:
    out: list[str] = []
    for dirpath, _dirs, files in os.walk(root):
        if "__pycache__" in dirpath:
            continue
        for f in files:
            if f.endswith(".py"):
                out.append(os.path.join(dirpath, f))
    return sorted(out)


class TestNoUndefinedNames:
    """A. 全量扫描：任何模块都不得引用未绑定的名字。"""

    def test_scan_scope_nonempty(self) -> None:
        files = [f for d in SCAN_DIRS for f in _py_files(d)]
        assert len(files) > 30, f"受检文件过少（{len(files)}），守卫形同虚设"

    @pytest.mark.parametrize(
        "path",
        [f for d in SCAN_DIRS for f in _py_files(d)],
        ids=lambda p: os.path.relpath(p, ROOT).replace(os.sep, "/"),
    )
    def test_module_has_no_undefined_names(self, path: str) -> None:
        bad = undefined_names(path)
        assert not bad, (
            f"{os.path.relpath(path, ROOT)} 引用了未定义名："
            + "；".join(sorted({b[2] for b in bad}))
            + "\n  典型成因：拆分模块时 import 留在了原文件（TD-28）。")


class TestNfraAttachmentRuntime:
    """B. 三个拆分模块**导入即可用**（静态检查之外的运行时证据）。"""

    def test_modules_import(self) -> None:
        from modules.regulatory_scrapers.collectors.nfra import (  # noqa: F401
            attachment_fetch,
            attachment_process,
            attachment_report,
        )

    def test_key_symbols_bound(self) -> None:
        """拆分最容易丢的符号必须真的绑定在模块上（不是"别处有"）。"""
        from modules.regulatory_scrapers.collectors.nfra import (
            attachment_fetch as F,
        )
        from modules.regulatory_scrapers.collectors.nfra import (
            attachment_process as P,
        )
        from modules.regulatory_scrapers.collectors.nfra import (
            attachment_report as R,
        )

        expect = {
            P: ("sha256_bytes", "docs_root", "structured_table_fields",
                "rich_object_fields", "extract_any", "quality_flags", "ocr_pdf",
                "build_attachment_url", "http_get_bytes", "ATT_DIR"),
            R: ("glob", "time", "sniff_kind", "extract_pdf_text", "sha256_bytes"),
            F: ("random", "time", "ssl", "urllib", "_SSL_CTX", "DEFAULT_HEADERS"),
        }
        for mod, names in expect.items():
            miss = [n for n in names if not hasattr(mod, n)]
            assert not miss, f"{mod.__name__} 缺符号 {miss}"

    def test_url_builder(self) -> None:
        from modules.regulatory_scrapers.collectors.nfra import attachment_fetch as F

        assert F.build_attachment_url({"urlOtherName": "/a/b.pdf"}).endswith("/a/b.pdf")
        assert F.build_attachment_url(
            {"attachmentUrl": "/att", "attachmentName": "c.pdf"}).endswith("/att/c.pdf")
        assert F.build_attachment_url({}) == ""

    def test_sniff_and_extract_are_callable(self) -> None:
        """`sniff_kind` 必须能真的判类型（原来它在本模块里**未定义**）。"""
        from modules.regulatory_scrapers.collectors.nfra import (
            attachment_report as R,
        )
        from modules.regulatory_scrapers.collectors.nfra import (
            attachments_extract as E,
        )

        assert R.sniff_kind is E.sniff_kind, "report 用的 sniff_kind 不是同一个实现"
        assert R.sniff_kind(b"%PDF-1.4\n", "x.pdf") == "pdf"
        assert callable(R.extract_pdf_text) and callable(E.extract_any)

    def test_quality_flags(self) -> None:
        from modules.regulatory_scrapers.collectors.nfra.attachment_quality import (
            quality_flags,
        )

        flags = quality_flags([120], "正文" * 100, False)
        assert isinstance(flags, list)

    def test_no_duplicate_module_constants(self) -> None:
        """模块常量只能定义一次（拆分残留会"后者覆盖前者"，改一份等于没改）。"""
        import ast

        from modules.regulatory_scrapers.collectors.nfra import attachment_fetch as F

        src = open(F.__file__, encoding="utf-8").read()
        tree = ast.parse(src)
        assigned: dict[str, int] = {}
        for node in tree.body:                      # 只看**模块级**
            targets = []
            if isinstance(node, ast.Assign):
                targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                targets = [node.target.id]
            for name in targets:
                assigned[name] = assigned.get(name, 0) + 1
        dup = {k: v for k, v in assigned.items() if v > 1}
        assert not dup, f"模块级常量重复定义：{dup}（改一份会被后一份覆盖）"


class TestConsoleAuthWiring:
    """C. 认证模式与校验方式解耦（113 批的真实故障，见 web/server.py `_auth_kind`）。"""

    def test_auth_kind_maps_auto_to_token(self) -> None:

        src = open(os.path.join(ROOT, "web", "server.py"), encoding="utf-8").read()
        assert "def _auth_kind(" in src, "缺 _auth_kind（策略与实现未解耦）"
        assert 'return "token" if m == "auto" else m' in src

    def test_health_is_public_and_split(self) -> None:
        api = open(os.path.join(ROOT, "web", "api.py"), encoding="utf-8").read()
        # 未认证分支必须**先返回**公开子集，且不得含内部字段
        idx = api.find("if not authed:")
        assert idx > 0, "health 缺未认证分支"
        end = api.find("return 200, base", idx)
        assert end > idx, "未认证分支未以 `return 200, base` 收尾（可能把内部字段一起带出）"
        seg = api[idx:end]
        assert "local_only_routes" not in seg and "gates_registered" not in seg
        assert "client_ip" not in seg, "公开段不该回客户端 IP"
        assert "need_login" in api

    def test_loopback_auto_identity(self) -> None:
        from web import auth

        d = auth.identity(cookies={}, client_ip="127.0.0.1", headers={}, mode="auto")
        assert d["authenticated"] is True and d["src"] == "local"
        # 伪造代理痕迹 ⇒ **不**自动认证（防同机反向代理白拿身份）
        d2 = auth.identity(cookies={}, client_ip="127.0.0.1",
                           headers={"X-Forwarded-For": "1.2.3.4"}, mode="auto")
        assert d2["authenticated"] is False
        # 严格模式本机也要登录
        d3 = auth.identity(cookies={}, client_ip="127.0.0.1", headers={}, mode="token")
        assert d3["authenticated"] is False
