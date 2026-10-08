# -*- coding: utf-8 -*-
"""tests.test_semantic_assist_baseline — `semantic_assist` 的**基线加载与指纹**（N-184 / N-188）。

背景：
  · N-184：基线原取 `cleaned.theme_name`，而该字段**五源 100% 存在却 100% 为空** ⇒ `agreement`
    恒 `None`（"启用前后差异"在生产中不可测）。改为从分类器产物 `_t{N}_final.json` 取基线。
  · N-188：该依赖**未登记水位边** ⇒ 分类器更新后可能静默沿用旧基线；故产物带**基线指纹**
    （每文件 mtime + 条目数）供自证新鲜度。

本测试用**临时目录**（monkeypatch `paths.MODULES_DIR`）验证映射与指纹，不加载任何模型。
"""
from __future__ import annotations

import json
import os

from std_lib.common_lib.norm import norm_title_strict
from tools import semantic_assist as sa


def _write(path: str, items: list) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(items, fh, ensure_ascii=False)


def test_baseline_maps_and_fingerprint(tmp_path, monkeypatch) -> None:
    """映射正确（含源限定键与 `*` 兜底键）+ 指纹含 mtime 与条目数。"""
    data = tmp_path / "regulatory_classifier" / "data"
    data.mkdir(parents=True)
    _write(
        str(data / "_t1_final.json"),
        [
            {"title": "《关于印发〈某办法〉的通知》", "file_src": "nfra", "doc_no": "x"},
            {"title": "另一份通知", "file_src": "gov"},
            {"title": "", "file_src": "gov"},  # 空标题 → 跳过（不产生键）
        ],
    )
    _write(str(data / "_t3_final.json"), [{"title": "第三主题的件", "file_src": "mof"}])
    monkeypatch.setattr(sa.paths, "MODULES_DIR", str(tmp_path), raising=False)

    base_map, files = sa._load_baseline()
    # 期望值**直接取归一函数的输出**（不在测试里复述归一规则，避免口径双写）
    assert base_map[("nfra", norm_title_strict("《关于印发〈某办法〉的通知》"))] == "T1"
    assert base_map[("gov", norm_title_strict("另一份通知"))] == "T1"
    assert base_map[("mof", norm_title_strict("第三主题的件"))] == "T3"
    assert base_map[("*", norm_title_strict("另一份通知"))] == "T1"  # 跨源兜底键
    assert len([k for k in base_map if k[1] == norm_title_strict("另一份通知")]) == 2  # (gov,·) 与 (*,·)

    by_file = {f["file"]: f for f in files}
    assert set(by_file) == {"_t1_final.json", "_t3_final.json"}
    assert by_file["_t1_final.json"]["entries"] == 2, "空标题不应计入条目数"
    assert by_file["_t3_final.json"]["entries"] == 1
    assert by_file["_t1_final.json"]["mtime"], "指纹必须含 mtime（自证新鲜度）"


def test_baseline_missing_dir_is_empty_not_error(tmp_path, monkeypatch) -> None:
    """分类器目录不存在 → 空映射（**不抛**，一致性如实记 n/a）。"""
    monkeypatch.setattr(sa.paths, "MODULES_DIR", str(tmp_path / "nope"), raising=False)
    base_map, files = sa._load_baseline()
    assert base_map == {} and files == []


def test_baseline_broken_json_is_skipped(tmp_path, monkeypatch) -> None:
    """坏 JSON（单文件）不应影响其它文件（逐文件隔离）。"""
    data = tmp_path / "regulatory_classifier" / "data"
    data.mkdir(parents=True)
    (data / "_t1_final.json").write_text("{不是合法 JSON", encoding="utf-8")
    _write(str(data / "_t2_final.json"), [{"title": "有效件", "file_src": "pbc"}])
    monkeypatch.setattr(sa.paths, "MODULES_DIR", str(tmp_path), raising=False)
    base_map, files = sa._load_baseline()
    assert base_map[("pbc", "有效件")] == "T2"
    assert [f["file"] for f in files] == ["_t2_final.json"], "坏文件应被跳过且不产生指纹"


def test_baseline_entries_match_files(tmp_path, monkeypatch) -> None:
    """`classifier_entries` 与指纹条目数一致（口径不自相矛盾）。"""
    data = tmp_path / "regulatory_classifier" / "data"
    data.mkdir(parents=True)
    _write(str(data / "_t5_final.json"), [{"title": f"件{i}", "file_src": "supp"} for i in range(7)])
    monkeypatch.setattr(sa.paths, "MODULES_DIR", str(tmp_path), raising=False)
    base_map, files = sa._load_baseline()
    # 每个标题产生 2 个键（(src,·) 与 (*,·)），指纹按**记录**数计
    assert len(base_map) == 14
    assert sum(f["entries"] for f in files) == 7
    assert os.path.basename(files[0]["file"]) == "_t5_final.json"
