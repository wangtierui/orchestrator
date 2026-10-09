# -*- coding: utf-8 -*-
"""联网冒烟：四个源各跑极小流量（3 条、跳过附件/原文），验证端到端可抓取落盘。

标记为 @pytest.mark.data：依赖外网 + 目标站点可达；无网/CI 用
`pytest -m "not data"` 排除。落盘到 /tmp，不影响仓库数据。
"""
import json
import os
import subprocess
import sys

import pytest

# refactor 子项目根（含 collectors/ 包），用于以 `python -m collectors.xxx` 自包含运行
PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# (源, 输出目录, CLI 参数含 out 旗标, 约定产物文件名)
CASES = [
    ("mof", "/tmp/pytest_smoke/mof",
     ["--outdir", "/tmp/pytest_smoke/mof", "--cache-dir", "/tmp/pytest_smoke/mof_cache",
      "--max-items", "3", "--no-attachments"],
     "mof_laws.json"),
    ("pbc", "/tmp/pytest_smoke/pbc",
     ["--out", "/tmp/pytest_smoke/pbc", "--max-items", "3", "--no-attachments"],
     "pbc_laws.json"),
    ("gov", "/tmp/pytest_smoke/gov",
     ["--source", "xzfgk", "--out-dir", "/tmp/pytest_smoke/gov",
      "--max-items", "3", "--no-details"],
     "gov_laws.json"),
    ("nfra", "/tmp/pytest_smoke/nfra",
     ["--out-dir", "/tmp/pytest_smoke/nfra", "--limit", "3", "--no-originals"],
     "nfra_regulations.json"),
]


@pytest.mark.data
@pytest.mark.parametrize("src,out,args,fn", CASES)
def test_smoke_fetch_and_write(src, out, args, fn):
    proc = subprocess.run(
        [sys.executable, "-m", f"collectors.{src}.collector", *args],
        cwd=PROJ_ROOT,
        capture_output=True, text=True, timeout=180,
    )
    assert proc.returncode == 0, f"{src} 冒烟非零退出:\n{proc.stderr[-3000:]}"
    path = os.path.join(out, fn)
    assert os.path.exists(path), f"{src} 未写出约定文件 {path}"
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)  # 必须是合法 JSON
    assert data, f"{src} 产物为空"
