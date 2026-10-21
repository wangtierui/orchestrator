# -*- coding: utf-8 -*-
"""tests/test_chain_equivalence.py — 编排切换的**等价性证明**（W1，2026-10-13）。

背景：W1 把 `STEP_ORDER`（平铺字面量）换成 `STEP_LAYERS` + `LAYER_EXEC_PLAN` 派生视图。
「重构不改变行为」必须**可证明**，而不是靠人眼看输出。本测试把切换前的真实输出冻结为
**归一化 golden 夹具**（`tests/golden/`），并对当前实现逐行比对：

  · `chain_list_steps.txt` —— `cli.py run --list-steps`（步骤清单口径）
  · `chain_dry_run.txt`    —— `cli.py run --dry-run`（**argv 序列**：执行计划的真正指纹）

归一化（夹具必须可跨机器复用 ⇒ 不得含绝对路径/时间戳/run_id）：
  仓根 → `<ROOT>`；解释器 → `<PY>`；run_id/时间戳/耗时/汇总文件名 → 占位符。

夹具更新（**需显式说明理由**，不得默认重录）：
    python -m tests.test_chain_equivalence --regen
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

import paths

GOLDEN_DIR = os.path.join(paths.TESTS_DIR, "golden")
LIST_STEPS = os.path.join(GOLDEN_DIR, "chain_list_steps.txt")
DRY_RUN = os.path.join(GOLDEN_DIR, "chain_dry_run.txt")


def _normalize(text: str) -> list[str]:
    """归一化命令行输出（去机器相关信息，保留行为指纹）。"""
    t = text
    # 仓根 → `<ROOT>`（两种斜杠形态：argv repr 会把反斜杠转义成双写）。
    # ⚠️ 大小写不敏感：`paths.ROOT` 的盘符写法继承 cwd（小写盘符进入的工作目录 →
    # 小写盘符的 ROOT），而子进程 argv 的盘符经 Windows API 归一为**大写**盘符——
    # 大小写敏感替换会漏（2026-10-15 实测：regen 出的 golden 含 6 行机器路径，
    # gate_hardcoded_paths 拦下）。
    for pat in (paths.ROOT, paths.ROOT.replace("\\", "\\\\")):
        t = re.sub(re.escape(pat), "<ROOT>", t, flags=re.IGNORECASE)
    # 解释器路径（任意盘符下的 python.exe）
    t = re.sub(r"[A-Za-z]:[\\/][^\s\"']*?python(?:\d+\.\d+)?\.exe", "<PY>", t)
    t = re.sub(r"RUN-\d{8}-\d{6}-\d+", "RUN-<id>", t)
    t = re.sub(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", "<ts>", t)
    t = re.sub(r"生产刷新汇总_\d{8}_\d{6}", "生产刷新汇总_<ts>", t)
    # 时效清单文件名按日期滚动（timeliness 步骤选**最新**清单）——数据依赖噪声，
    # 非行为指纹：每日新清单都会令 dry-run 指纹漂移（2026-10-15 实测：golden 录于
    # 10-14，10-15 链运行产生 _20261015 清单即 FAIL）。归入「汇总文件名 → 占位符」类。
    t = re.sub(r"时效性标注结果清单_全量_\d{8}", "时效性标注结果清单_全量_<date>", t)
    t = re.sub(r"总耗时 [\d.]+s", "总耗时 <x>s", t)
    # **raw 体量的 MB 尾数是纯噪声，不是行为指纹**（2026-10-20 修）：
    # raw 里含 `fetch_time` 等时间戳字段 ⇒ 同一批数据重跑也会让字节数抖 ±0.1MB
    # （实测 mof 63.6→63.5MB 只因重跑采集，内容未变），而 golden 固化了它
    # ⇒ 每次采集都可能让本测试假失败（第一百零二批就因此失败过一次）。
    # **只归一化 MB、保留条数**：条数变化意味着数据状态真的变了（值得 FAIL），
    # MB 尾数变化不携带行为信息（归一化掉）。这是"保留信息量最大维度、消除纯噪声"。
    t = re.sub(r"(\d+)条/[\d.]+MB", r"\1条/<mb>MB", t)
    # 同一原则的**第二处形态**（2026-10-21 第一百零六批补）：`[clean]` 行只印
    # 整数 MB（`[clean] mof: raw 108MB → 超时 1800s`），**不带条数** ⇒ 上一行正则
    # 匹配不到。本轮 mof 采集完成后 raw 从 64MB 涨到 108MB（真实新增 182 条详情），
    # 于是 dry-run 指纹漂移、本测试 FAIL —— 但"体量"本身不携带行为信息
    # （同一份代码换个数据量，argv 序列不会变）。
    # 归一化掉；**条数另有断言**（`…/\d+条/<mb>MB` 保留条数 + 各源 clean 步骤仍在），
    # 故不会把"数据状态真变了"这件事一起吞掉。
    t = re.sub(r"(raw )[\d.]+MB", r"\1<mb>MB", t)
    t = re.sub(r"fitz.*deprecated.*", "", t)
    # 环境相关噪声归一（非行为指纹）：
    #  · doctor 摘要的 warn 计数随环境变（node 缺失/OCR 未配/无 token/残留锁文件）；
    #  · 残留单实例锁提示同理。
    t = re.sub(r"前置自检通过（\d+ 项，warn=\d+）", "前置自检通过（<doctor>）", t)
    t = re.sub(r"\[doctor\][^\n]*", "", t)
    # 单实例锁提示随环境状态变化（残留锁文件 → 「等待 PID 结束」/「已有实例在运行」，
    # 无锁 → 正常启动）：属环境噪声不是行为指纹，整行剔除。
    t = re.sub(r"\[refresh\][^\n]*(单实例|等待 PID)[^\n]*", "", t)
    return [ln for ln in t.splitlines() if ln.strip()]


def _run_cli(*args: str) -> str:
    proc = subprocess.run(
        [sys.executable, os.path.join(paths.ROOT, "cli.py"), *args],
        cwd=paths.ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=900,
    )
    return proc.stdout + proc.stderr


def _read_golden(path: str) -> list[str]:
    with open(path, encoding="utf-8") as fh:
        return _normalize(fh.read())


def test_list_steps_matches_golden():
    """`--list-steps` 与切换前 golden 逐行一致（步骤清单口径未变）。"""
    cur = _normalize(_run_cli("run", "--list-steps"))
    assert cur == _read_golden(LIST_STEPS), (
        "--list-steps 与 golden 不一致——STEP_LAYERS 派生的 STEP_ORDER 漂移"
    )


def test_size_suffix_is_normalized_not_fingerprinted() -> None:
    """**防回退**：raw 体量的 MB 尾数必须被归一化掉（它不是行为指纹）。

    实测背景（2026-10-20）：同一批数据重跑采集，mof raw 63.6→63.5MB（仅
    `fetch_time` 之类时间戳字段变化）⇒ golden 固化的尾数让本测试**假失败**。
    修法是"只归一化 MB、保留条数"——条数变化意味着数据状态真的变了。
    """
    a = _normalize("raw: gov=1条/1.0MB | mof=2条/63.6MB")
    b = _normalize("raw: gov=1条/999.9MB | mof=2条/63.5MB")
    assert a == b, f"MB 尾数未被归一化（会假失败）：{a} vs {b}"
    # 条数必须**保留**（数据状态变化应当被看见）
    c = _normalize("raw: mof=3条/63.5MB")
    assert c != a, "条数也被归一化掉了 ⇒ 数据状态变化将不可见"
    assert any("2条" in ln for ln in a), a


def test_dry_run_argv_sequence_matches_golden():
    """`--dry-run` argv 序列与切换前 golden 逐行一致（执行计划指纹未变）。"""
    cur = _normalize(_run_cli("run", "--dry-run"))
    assert cur == _read_golden(DRY_RUN), (
        "--dry-run argv 序列与 golden 不一致——执行顺序/参数发生漂移"
    )


def test_step_order_derived_from_layers_is_32():
    """派生 STEP_ORDER = 32 项且与 plan 覆盖一致（结构自洽，防 plan 漏段/重段）。

    W4（§6.4）：31 → 32（`semantic:search` 插入 analysis:gen 之后——语义检索视图
    的索引对象含 17 项交付物，须取 6.8 终态再嵌入）。
    """
    # ⚠️ 必须用裸名（conftest 的 bootstrap(include_tools=True) 已把 tools/ 注入
    # sys.path）——`from tools import ...` 会让 mypy 全仓走查把同一文件同时登记为
    # `run_production_refresh` 与 `tools.run_production_refresh` 双模块名冲突
    # （问题项 P-5，直接中止全仓检查）。
    import run_production_refresh as rpr

    order = rpr.STEP_ORDER
    assert len(order) == 32, len(order)
    plan_names = [n for _layer, names in rpr.LAYER_EXEC_PLAN for n in names]
    assert plan_names == list(order), "LAYER_EXEC_PLAN 与派生 STEP_ORDER 不一致"
    # 每个 StepSpec 恰好出现在一个 plan 段
    from collections import Counter

    cnt = Counter(plan_names)
    assert [k for k, v in cnt.items() if v > 1] == [], "plan 段有重复步骤"


if __name__ == "__main__":
    if "--regen" in sys.argv:
        os.makedirs(GOLDEN_DIR, exist_ok=True)
        for args, target in ((("run", "--list-steps"), LIST_STEPS),
                             (("run", "--dry-run"), DRY_RUN)):
            with open(target, "w", encoding="utf-8", newline="\n") as fh:
                fh.write("\n".join(_normalize(_run_cli(*args))) + "\n")
            print(f"[regen] {os.path.basename(target)} 已重录（{args}）")
        raise SystemExit(0)
    raise SystemExit("直接运行 pytest；--regen 需显式指定")
