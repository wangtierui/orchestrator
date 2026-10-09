# `std_lib/scraper_std/retired/` —— 共享库退役隔离层

本目录存放**共享库层**的退役模块：不再被任何代码引用、但保留以备参考或回滚的实现。

> ⚠️ **与 `tools/retired/` 的区别（务必知悉）**：
> `tools/retired/` 受 `gate_config_integrity` 的 **J1~J4** 判据保护（清单双向相等 / `category=retired` ⟺ 路径 /
> **零仓内引用** / README 含退役登记）。本目录**尚无对应的门禁判据**（属"库层退役"缺口），
> 只被 ruff / mypy 的 `retired/` **按名排除**规则覆盖（不检查风格与类型）。
> ⇒ 往本目录放东西时，**必须**在此文件登记，并人工确认零引用（命令见下）。

## 退役登记

| 退役日期 | 模块 | 退役理由 | 零引用核验 |
|---|---|---|---|
| 2026-10-09 | `raw_loader.py` | 共享 raw 读取职责已由 `std_lib/scraper_std/pipeline.load_raw_records`（五源 clean 唯一载入点，已支持 `.jsonl`/`.json` 双格式流式）承接；保留双实现会随 raw 形态演进漂移 | `rg "raw_loader" --glob '!.git'`（排除本目录/历史报告）命中 0 —— 详见批 48 报告 T-D |

## 零引用核验命令（放入本目录前必做）

```powershell
cd d:\WorkBuddy\regulatory_compliance_orchestrator
rg -n "raw_loader" --glob "!std_lib/scraper_std/retired/**" --glob "!reports/**" --glob "!archive/**"
# 期望：无输出（0 命中）
```

## 回滚方式

文件级移动即完成回滚（无需改任何调用方——正因零引用）：把文件移回原路径即可。
原实现同时保留在 git 历史中：`git log --follow <file>`。
