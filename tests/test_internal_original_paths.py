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

    def test_non_policy_sheet_within_baseline_passes(self, gate_env):
        g, idx, _orig = gate_env
        _write_index(str(idx), [_rec("IPN-s", "制度清单.xls", "1.部门/制度清单.xls")])
        ok, detail = g.run()
        assert ok, detail["problems"]
        assert detail["non_policy_sheets"] == 1  # 1 <= 登记基线

    def test_non_policy_sheet_over_baseline_fails(self, gate_env, monkeypatch):
        g, idx, _orig = gate_env
        monkeypatch.setattr(g, "KNOWN_NON_POLICY_BASELINE", 0)
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
