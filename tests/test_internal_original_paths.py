# -*- coding: utf-8 -*-
"""test_internal_original_paths.py —— 内部制度原件路径治理回归（2026-09-13 P0 修复）。

覆盖三项 P0（背景见 `reports/内部制度原件双份存储与索引漂移分析_20260913.md`）：
  A) **ingest 幂等键加 `path_key` 维度**：同内容换路径不再静默 skip；"路径跟随"以
     **移动**而非复制实现 → 原件库不再随源目录重组叠加多代副本；
  B) **`internal reocr` 对不可解析原件计数 + 告警**（原为静默 `continue`，实测覆盖率曾仅 46%）；
  C) **门禁 `gate_original_resolvable`**：制度正文类 100% 可解析；表格类失效台账不超登记基线。

纪律：全程 `tmp_path` + monkeypatch 隔离，不触碰真实 `data/`、索引与原件库。
"""
from __future__ import annotations

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, os.path.join(ROOT, "modules"), os.path.join(ROOT, "std_lib")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _write_index(path: str, records: list) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"schema_version": "1.0", "records": records}, fh, ensure_ascii=False)


def _rec(ipn: str, file_name: str, rel: str) -> dict:
    return {"ipn": ipn, "file_name": file_name, "relative_path": rel}


@pytest.fixture()
def gate_env(tmp_path, monkeypatch):
    """把门禁的索引路径/原件库指向 tmp，避免读真实数据。"""
    from gates import gate_original_resolvable as g

    idx = tmp_path / "internal_policy_index.json"
    orig = tmp_path / "originals"
    orig.mkdir()
    monkeypatch.setattr(g, "_INDEX", str(idx))
    monkeypatch.setattr(g, "_ORIGINALS", str(orig))
    return g, idx, orig


class TestGateOriginalResolvable:
    """门禁 C：制度正文 100% 可解析 + 表格类不超基线。"""

    def test_missing_index_fails_and_does_not_pass_silently(self, gate_env):
        g, _idx, _orig = gate_env
        ok, detail = g.run()
        assert not ok, "输入缺失不得空跑放行（对齐 A-07）"
        assert "不存在" in detail["error"]

    def test_policy_unresolvable_fails(self, gate_env):
        g, idx, _orig = gate_env
        _write_index(str(idx), [_rec("IPN-a", "某办法.pdf", "1.部门/某办法.pdf")])
        ok, detail = g.run()
        assert not ok
        assert detail["unresolvable_policy"] == 1
        assert any("制度正文类" in x for x in detail["problems"])

    def test_resolvable_policy_passes(self, gate_env):
        g, idx, orig = gate_env
        d = orig / "1.部门"
        d.mkdir(parents=True)
        (d / "某办法.pdf").write_bytes(b"%PDF-1.4")
        _write_index(str(idx), [_rec("IPN-a", "某办法.pdf", "1.部门/某办法.pdf")])
        ok, detail = g.run()
        assert ok, detail["problems"]
        assert detail["resolvable"] == 1

    def test_non_policy_sheet_within_explicit_baseline_passes(self, gate_env, monkeypatch):
        """显式登记基线时容忍（只减不增）——保留分档能力的回归。"""
        g, idx, _orig = gate_env
        monkeypatch.setattr(g, "KNOWN_NON_POLICY_BASELINE", 1)
        _write_index(str(idx), [_rec("IPN-s", "制度清单.xls", "1.部门/制度清单.xls")])
        ok, detail = g.run()
        assert ok, detail["problems"]
        assert detail["non_policy_sheets"] == 1

    def test_non_policy_sheet_fails_under_zero_baseline(self, gate_env):
        """2026-09-13 治理后基线收紧至 0：索引内不应再有任何非正文件（台账已隔离到 data/ledgers）。"""
        g, idx, _orig = gate_env
        assert g.KNOWN_NON_POLICY_BASELINE == 0, "治理后基线应为 0"
        _write_index(str(idx), [_rec("IPN-s", "制度清单.xls", "1.部门/制度清单.xls")])
        ok, detail = g.run()
        assert not ok
        assert any(">" in x for x in detail["problems"])


class TestReocrMissingAccounting:
    """修复 B：原件不可解析必须计数 + 告警（原静默 continue）。"""

    def test_dangling_original_counted_and_warned(self, tmp_path, capsys):
        from internal_policy_base.extract import reocr_backfill

        data = tmp_path / "data"
        proc = data / "processed"
        proc.mkdir(parents=True)
        (proc / "IPN-x.json").write_text(json.dumps({
            "ipn": "IPN-x", "file_name": "某办法.pdf", "extension": "pdf",
            "original_path": "originals/已不存在的目录/某办法.pdf", "text_chars": 0,
        }, ensure_ascii=False), encoding="utf-8")

        st = reocr_backfill(data_dir=str(data))
        assert st["missing_original"] == 1
        assert st["total"] == 0, "原件不可解析者不得进入 OCR 目标集"
        assert st["missing_details"][0]["ipn"] == "IPN-x"
        out = capsys.readouterr().out
        assert "警告" in out
        assert "reconcile_original_paths" in out, "告警须给出可执行的处置入口"

    def test_no_warning_when_all_resolvable(self, tmp_path, capsys):
        from internal_policy_base.extract import reocr_backfill

        data = tmp_path / "data"
        proc = data / "processed"
        proc.mkdir(parents=True)
        # text_chars>0 且非 force → 不进入目标集，但原件可解析 → 不应触发缺失告警
        (proc / "IPN-y.json").write_text(json.dumps({
            "ipn": "IPN-y", "file_name": "某办法.pdf", "extension": "pdf",
            "original_path": "originals/部门/某办法.pdf", "text_chars": 500,
        }, ensure_ascii=False), encoding="utf-8")
        d = data / "originals" / "部门"
        d.mkdir(parents=True)
        (d / "某办法.pdf").write_bytes(b"%PDF-1.4")

        st = reocr_backfill(data_dir=str(data))
        assert st["missing_original"] == 0
        assert "警告" not in capsys.readouterr().out


class TestIngestPathKey:
    """修复 A：幂等键含落位路径；路径变化走"移动跟随"而非重复复制。"""

    def test_path_key_normalizes_separators(self):
        from internal_policy_base.indexer import _path_key

        assert _path_key("1.部门\\某办法.pdf") == _path_key("1.部门/某办法.pdf")
        assert _path_key("") == ""

    def test_follow_path_moves_and_updates_processed(self, tmp_path, monkeypatch):
        from internal_policy_base import indexer as ix

        orig = tmp_path / "originals"
        proc = tmp_path / "processed"
        old_dir = orig / "旧布局"
        old_dir.mkdir(parents=True)
        src = old_dir / "某办法.pdf"
        src.write_bytes(b"%PDF-1.4 test")
        proc.mkdir()
        (proc / "IPN-a.json").write_text(json.dumps({
            "ipn": "IPN-a", "file_name": "某办法.pdf",
            "relative_path": "旧布局/某办法.pdf",
            "original_path": "originals/旧布局/某办法.pdf",
        }, ensure_ascii=False), encoding="utf-8")

        monkeypatch.setattr(ix, "_ORIGINALS", str(orig))
        monkeypatch.setattr(ix, "_PROCESSED", str(proc))

        f = {"ipn": "IPN-a", "relative_path": "新布局/某办法.pdf"}
        moved = ix._follow_path(ix._path_key("旧布局/某办法.pdf"), f, "2026-09-13 00:00:00")

        assert moved is True
        assert (orig / "新布局" / "某办法.pdf").exists(), "应移动到新路径"
        assert not src.exists(), "旧路径不得残留（防多代副本叠加）"
        rec = json.loads((proc / "IPN-a.json").read_text(encoding="utf-8"))
        assert rec["relative_path"] == "新布局/某办法.pdf"
        assert rec["original_path"] == "originals/新布局/某办法.pdf"
        assert rec["path_relocated_from"] == "旧布局/某办法.pdf", "须留审计痕迹"

    def test_follow_path_false_when_source_missing(self, tmp_path, monkeypatch):
        from internal_policy_base import indexer as ix

        orig = tmp_path / "originals"
        orig.mkdir()
        monkeypatch.setattr(ix, "_ORIGINALS", str(orig))
        monkeypatch.setattr(ix, "_PROCESSED", str(tmp_path / "processed"))

        assert ix._follow_path(ix._path_key("已不存在/某办法.pdf"),
                               {"ipn": "IPN-z", "relative_path": "新/某办法.pdf"},
                               "2026-09-13 00:00:00") is False, "旧件缺失须回退复制链路"


class TestCopyOriginalHardlinkFirst:
    """P2：`copy_original` 硬链接优先（省冗余），且绝不写穿共享 inode。"""

    def test_same_volume_uses_hardlink(self, tmp_path):
        from internal_policy_base.extract import copy_original

        src = tmp_path / "src" / "办法.pdf"
        src.parent.mkdir(parents=True)
        src.write_bytes(b"%PDF-1.4 hardlink")
        dst_dir = tmp_path / "dst"

        out = copy_original(str(src), str(dst_dir), "部门/办法.pdf")
        assert os.path.exists(out)
        assert os.path.samefile(str(src), out), "同卷应建立硬链接（共享数据、不重复占用）"
        assert os.stat(out).st_nlink == 2

    def test_existing_hardlink_is_unlinked_before_rewrite(self, tmp_path):
        """目标已是硬链接时，重写必须断链——否则会写穿共享 inode（污染 corpus 侧）。"""
        from internal_policy_base.extract import copy_original

        shared = tmp_path / "corpus" / "办法.pdf"
        shared.parent.mkdir(parents=True)
        shared.write_bytes(b"ORIGINAL-CORPUS")
        dst_dir = tmp_path / "originals" / "部门"
        dst_dir.mkdir(parents=True)
        dst = dst_dir / "办法.pdf"
        os.link(str(shared), str(dst))            # 目标先成为共享 inode

        new_src = tmp_path / "new" / "办法.pdf"
        new_src.parent.mkdir(parents=True)
        new_src.write_bytes(b"NEW-CONTENT")
        copy_original(str(new_src), str(dst_dir.parent), "部门/办法.pdf")

        assert dst.read_bytes() == b"NEW-CONTENT"
        assert shared.read_bytes() == b"ORIGINAL-CORPUS", "corpus 侧内容不得被牵连修改"


class TestLedgerFilter:
    """P2：台账/清单类 xls/xlsx 不再纳入制度索引。"""

    def test_ledger_detection(self):
        from internal_policy_base.scan import is_non_policy_ledger

        assert is_non_policy_ledger("附件1：2025年制度检视自查情况表-财务部.xls")
        assert is_non_policy_ledger("风控管理制度清单20220914.xlsx")
        assert not is_non_policy_ledger("某管理办法.pdf")
        assert not is_non_policy_ledger("费用管控规则.xlsx"), "非台账类表格不误伤"

    def test_scan_excludes_ledgers_by_default(self, tmp_path):
        from internal_policy_base.scan import scan_directory

        (tmp_path / "部门").mkdir()
        (tmp_path / "部门" / "某办法.pdf").write_bytes(b"%PDF-1.4")
        (tmp_path / "部门" / "制度清单.xls").write_bytes(b"\xd0\xcf\x11\xe0")
        (tmp_path / "部门" / "费用管控规则.xlsx").write_bytes(b"PK\x03\x04")

        names = [f["file_name"] for f in scan_directory(str(tmp_path))]
        assert "某办法.pdf" in names
        assert "费用管控规则.xlsx" in names
        assert "制度清单.xls" not in names, "台账类应被过滤"

        old = [f["file_name"] for f in scan_directory(str(tmp_path), exclude_ledgers=False)]
        assert "制度清单.xls" in old, "开关可恢复旧行为"


class TestDedupeOriginalStorage:
    """P1/P2 工具：内部去冗余 / 跨层硬链接 / 非正文标记 的分类与执行。"""

    def _env(self, tmp_path, monkeypatch):
        from tools import dedupe_original_storage as dd

        orig = tmp_path / "originals"
        corpus = tmp_path / "corpus"
        (orig / "部门").mkdir(parents=True)
        (corpus / "部门").mkdir(parents=True)
        payload = b"%PDF-1.4 same-content"
        (orig / "部门" / "办法.pdf").write_bytes(payload)
        (orig / "部门" / "办法-副本.pdf").write_bytes(payload)   # 同内容、无索引引用
        (corpus / "部门" / "办法.pdf").write_bytes(payload)
        idx = tmp_path / "index.json"
        idx.write_text(json.dumps({"records": [
            {"ipn": "IPN-a", "file_name": "办法.pdf", "relative_path": "部门/办法.pdf"},
            {"ipn": "IPN-b", "file_name": "制度清单.xls", "relative_path": "部门/制度清单.xls"},
        ]}, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(dd, "INDEX_PATH", str(idx))
        monkeypatch.setattr(dd, "ORIGINALS", str(orig))
        monkeypatch.setattr(dd, "CORPUS", str(corpus))
        return dd, orig, corpus

    def test_plan_classifies_move_link_mark(self, tmp_path, monkeypatch):
        dd, _orig, _corpus = self._env(tmp_path, monkeypatch)
        plan = dd.build_plan()

        assert [os.path.basename(p) for p in plan["move"]] == ["办法-副本.pdf"], \
            "应只移出「同内容且无引用」的副本"
        assert len(plan["links"]) == 1, "被引用的保留件应可跨层硬链接"
        assert [r["ipn"] for r in plan["marks"]] == ["IPN-b"], "失效台账应被标记"

    def test_apply_hardlink_shares_inode(self, tmp_path, monkeypatch):
        dd, orig, corpus = self._env(tmp_path, monkeypatch)
        plan = dd.build_plan()
        res = dd.apply_plan(plan, dedupe=False, hardlink=True, mark=False, backup=False)

        assert res["actions"]["linked"] == 1
        assert os.path.samefile(str(orig / "部门" / "办法.pdf"),
                                str(corpus / "部门" / "办法.pdf"))
        assert (corpus / "部门" / "办法.pdf").read_bytes() == b"%PDF-1.4 same-content"


class TestNamingRules:
    """用户规则（2026-09-13）回归：命名 `文号_名称` / `_名称`；文号"内容优先 → 清单兜底"；
    名称不回退清单。本类把批量改名中修正过的四类解析缺陷固化为不变式（详见报告 §11.2）。"""

    def test_docno_shape_rejects_circulation_and_archive_numbers(self):
        from tools.normalize_internal_naming import is_valid_docno

        assert is_valid_docno("阳光人寿发〔2025〕154号")
        # 2026-09-13 用户规则：内部文号须以"阳光人寿/阳光保险"开头 → 监管机关文号一律判否
        assert not is_valid_docno("金办发〔2024〕25号"), "非公司内部文号（缺阳光前缀）"
        assert not is_valid_docno("银保监发〔2019〕29号"), "监管机关文号不得当内部制度文号"
        assert not is_valid_docno("保监发〔2013〕40号"), "正文引用他文（实测反例）"
        assert not is_valid_docno("NEWYXYWFASQ 202503310001"), "公文流转号不得当文号"
        assert not is_valid_docno("SXLQB201912130013"), "OA 档案编号不得当文号"
        assert not is_valid_docno("")

    def test_title_derivation_strips_prefix_and_tail_noise(self):
        from tools.normalize_internal_naming import derive_title_from_name, tidy_title

        cases = {
            "2-关于下发《X》的通知_盖章.pdf": "关于下发《X》的通知",
            "5-阳光人寿个人寿险营销员基本管理办法2020年版（A类）-含水印.pdf":
                "阳光人寿个人寿险营销员基本管理办法2020年版（A类）",
            "_10：-团险代理出单销售实施细则（2022年版）.pdf":
                "团险代理出单销售实施细则（2022年版）",
            "6-附件一 个人寿险营销员行销基本管理办法（C类）.pdf":
                "个人寿险营销员行销基本管理办法（C类）",
        }
        for src, want in cases.items():
            assert tidy_title(derive_title_from_name(src)) == want, src

    def test_inline_docno_variant_chars_and_spaces_cleaned(self):
        from tools.normalize_internal_naming import tidy_title

        assert tidy_title("阳光人寿发（2022）502号阳光人寿银行保险销售人员管理办法") == \
            "阳光人寿银行保险销售人员管理办法", "名称内嵌文号须剔除（文号只在前缀）"
        assert tidy_title("阳光⼈寿个险营销平台中⼼城市保险营销员管理办法") == \
            "阳光人寿个险营销平台中心城市保险营销员管理办法", "康熙部首异体字须归一"
        assert tidy_title("某细则（2025 版）") == "某细则（2025版）", "数字与「版」间空格须去"

    def test_registry_only_supplies_docno_and_name_never_falls_back(self):
        from tools.normalize_internal_naming import match_registry

        rows = [{"dept": "办公室", "name": "阳光人寿绿色办公实施方案",
                 "docno": "阳光人寿办发〔2024〕55号", "valid": "是"},
                {"dept": "CSP业务部", "name": "银保CSP渠道业务人员考勤管理办法(2021版）",
                 "docno": "阳光人寿发〔2021〕424号", "valid": "是"}]
        docno, dept, score = match_registry("阳光人寿绿色办公实施方案", rows)
        assert docno == "阳光人寿办发〔2024〕55号" and dept == "办公室" and score >= 0.85
        # 通知式标题经"剥包裹"后仍能命中
        docno2, _d2, _s2 = match_registry("关于下发《银保CSP渠道业务人员考勤管理办法(2021版）》的通知", rows)
        assert docno2 == "阳光人寿发〔2021〕424号"
        # 无匹配 → 不回退名称（调用方据 docno 为空产出 `_名称`）
        assert match_registry("完全无关的制度名称", rows)[0] == ""

    def test_safe_filename_replaces_windows_illegal_chars(self):
        from tools.normalize_internal_naming import safe_filename

        out = safe_filename('阳光人寿发〔2025〕1号_关于下发《A/B:C*D?E"F<G>H|I》的通知.pdf')
        assert not any(ch in out for ch in '\\/:*?"<>|')
        assert out.endswith(".pdf")

    def test_nonpolicy_extension_partition(self):
        from internal_policy_base.scan import is_non_policy_ledger

        from tools.split_internal_nonpolicy import DOC_EXTS, LEDGER_EXTS

        assert is_non_policy_ledger("附件1：2025年制度检视自查情况表-财务部.xls")
        assert is_non_policy_ledger("风控管理制度清单20220914.xlsx")
        assert not is_non_policy_ledger("费用管控规则.xlsx"), "非台账表格不误伤"
        assert DOC_EXTS == {".pdf", ".doc", ".docx"}
        assert ".xlsx" in LEDGER_EXTS and ".pdf" not in LEDGER_EXTS


class TestDocnoTitleAreaRules:
    """用户规则（2026-09-13，逐条对应报告 §12 的 6 个核查问题）：

    ① 名称不得并入红头机关名；② 文号**只在文档标题处**出现（正文引用/废止声明不算）；
    ③ 公司内部文号须以 `阳光人寿`/`阳光保险` 开头；④ 名称以**文档内容标题**为准；
    ⑤ 文字层只有页眉（无汉字）者须判为扫描件触发 OCR；⑥ 附件型文档继承正文文号。
    """

    # ① 红头机关名剥离
    def test_redhead_and_notice_prefix_stripped_from_docno(self):
        from internal_policy_base.scan import normalize_docno

        assert normalize_docno("阳光人寿保险股份有限公司文件阳光人寿发（2020）404号") == \
            "阳光人寿发〔2020〕404号", "红头机关名（…公司文件）须剥离"
        assert normalize_docno("特此通知阳光保险发【2022】90号") == "阳光保险发〔2022〕90号"
        assert normalize_docno("阳光人寿发[2018]442号") == "阳光人寿发〔2018〕442号", "括号须归一为〔〕"

    # ③ 前缀白名单
    def test_internal_docno_prefix_whitelist(self):
        from internal_policy_base.scan import is_internal_docno

        assert is_internal_docno("阳光人寿办发〔2021〕64号")
        assert is_internal_docno("阳光保险发〔2022〕90号")
        for bad in ("保监发〔2013〕40号", "银保监发〔2021〕14号", "金办发〔2024〕122号",
                    "中国人民银行令〔2016〕第3号", "法释〔2002〕30号"):
            assert not is_internal_docno(bad), bad

    # ② 文号只在标题区域
    def test_docno_extracted_only_from_title_area(self):
        from internal_policy_base.scan import extract_docno_title_area

        # 正文引用他文（括号包裹）→ 不是本文文号
        t1 = ("阳光人寿融客事业部录音管理办法第一章总则第一条为了规范和加强录音管理，"
              "根据《人身保险电话销售业务管理办法》（保监发〔2013〕40号）规定，特制定本办法。")
        assert extract_docno_title_area(t1)[0] == ""
        # 文末"同步废止…"声明 → 超标题区域，不提取
        t2 = "阳光人寿银保CSP渠道线上培训平台学习管理办法" + "正文内容" * 80 + \
            "本管理办法于2022年5月起正式实施，同步废止阳光人寿发【2021】673号文件。"
        assert extract_docno_title_area(t2)[0] == ""
        # 标题区正常 → 提取并归一
        t3 = "阳光人寿发〔2025〕155号关于下发《X》的通知各分公司，总公司各部门："
        assert extract_docno_title_area(t3)[0] == "阳光人寿发〔2025〕155号"

    # ④ 名称以内容标题为准（含"标题与正文连写"与"主送机关连写"）
    def test_content_title_wins_and_stops_before_body(self):
        from internal_policy_base.scan import parse_content_identity

        assert parse_content_identity(
            "经代渠道销售服务人员执业证管理工作指引各分公司：为强化公司从业人员管理…")["title"] == \
            "经代渠道销售服务人员执业证管理工作指引"
        assert parse_content_identity(
            "阳光人寿银保 CSP 渠道线上培训平台学习管理办法为有效推动银保 CSP 渠道健康、持续发展")["title"] == \
            "阳光人寿银保CSP渠道线上培训平台学习管理办法"
        assert parse_content_identity(
            "员工周转房入住协议我同意阳光人寿保险股份有限公司《周转房管理办法》相关规定")["title"] == \
            "员工周转房入住协议"
        # 标题候选含句读 → 判为正文混入 → 拒（调用方回退文件名解构）
        assert parse_content_identity("公司政策、制度出台依据说明表序号名称说明")["title"] == ""
        # 版本括注须保留
        assert parse_content_identity("三级机构银保分级与前线人员管理办法（2022年修订版）制定本办法")["title"] == \
            "三级机构银保分级与前线人员管理办法（2022年修订版）"

    # ⑥ 附件继承正文文号
    def test_attachment_inherits_docno_by_content_containment(self):
        from internal_policy_base.scan import inherit_docno_by_containment

        attach = ("附件1：共建合作签约申请表拟合作项目名称：合作方名称合作类型证照材料清单"
                  "是否合规备注合作方1、保险代理资格证（含专业、兼业）")
        body = ("阳光人寿发〔2021〕209号_关于下发《融客共建业务签约及红黄蓝评价管理办法》的通知"
                "各分公司：为规范共建业务…" + attach)
        items = [{"text": body, "docno": "阳光人寿发〔2021〕209号"},
                 {"text": attach, "docno": ""}]
        assert inherit_docno_by_containment(items) == {1: "阳光人寿发〔2021〕209号"}
        # 无包含关系 → 不继承（宁缺毋滥）
        assert inherit_docno_by_containment(
            [{"text": "阳光人寿发〔2021〕209号_某办法" * 6, "docno": "阳光人寿发〔2021〕209号"},
             {"text": "完全无关的另一份表格内容" * 4, "docno": ""}]) == {}

    # ⑤ OCR 判据：文本层有效性按**汉字数**，不按总字符数
    def test_text_layer_judge_counts_cjk_not_chars(self):
        from std_lib.scraper_std.crawler_common import _TEXT_LAYER_MIN_CJK, cjk_count

        # 实测反例：某红头文件文字层 461 字全为打印页眉（汉字 0）→ 须判为需 OCR
        header = "2020/11/26 eoa.sinosig.com/sys/attachment/sys_att_main.do?method=view 2/4"
        assert len(header) > 30 and cjk_count(header) == 0
        assert cjk_count(header) < _TEXT_LAYER_MIN_CJK, "页眉噪声不得虚增文本层有效性"
        assert cjk_count("阳光人寿发〔2020〕199号") == 6

    def test_scan_judge_ignores_missing_file(self):
        from internal_policy_base.extract import _is_scan_pdf

        assert _is_scan_pdf("__not_exist__.pdf") is False
