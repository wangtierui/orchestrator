# regulatory_scrapers/retired/ — 退役脚本隔离层

> W4（《项目最优重构方案_20261012.md》§7.2 A 类，2026-10-15）。本目录收容**零引用且无保留价值**
> 的脚本（三台阶退役：移入 → 观察一个月检周期 → 删除；git 历史即恢复路径）。
> 判据：`gate_config_integrity` J9（retired ⟺ 本目录 + `modules/_manifest.json` 登记 +
> 零可执行引用——注释/文档中的历史提及只披露不阻断）。

| 脚本 | 退役日期 | 原因 | 替代物 |
|---|---|---|---|
| `nfra_prefetch.py` | 2026-10-15 | 零调用方；nfra_weekly 子进程编排只覆盖 fetch_lists/fill_details/fetch_attachments，预热逻辑已收敛 `nfra_common._warmup`（原逐字重复的四文件之一） | `nfra_common` + `nfra_weekly` |
| `nfra_seed_cache.py` | 2026-10-15 | 零调用方；种子缓存能力未接线（缓存目录由 collector 自动维护） | `nfra_collector` 缓存参数 |
| `nfra_validate_cache.py` | 2026-10-15 | 零调用方；只读缓存校验 CLI（2026-09-08 冒烟后未再使用） | `cli.py source list` + `gate_sources_config` |
| `pbc_fix_doc.py` | 2026-10-15 | 零调用方；6 条 `.doc` 附件专项修复（任务已完成，成果在 cleaned） | `doc_convert`（内容哈希缓存版） |
| `pbc_merge_scrape.py` | 2026-10-15 | 零调用方；历史合并抓取脚本（pbc_laws 主库已 JSONL 化并由 `pbc_backfill_pdfs` 续跑） | `pbc_backfill_pdfs.py` |
| `pbc_run_batched.py` | 2026-10-15 | 零调用方；分批驱动壳（规避 OOM 的临时手段；`--max-seconds` 软预算 + 流式合并已从机制上解决峰值问题） | `pbc_collector` / `pbc_backfill_pdfs` |
| `pbc_serve.py` | 2026-10-15 | 零调用方；调试预览 HTTP 服务器（raw JSONL 化后预览改由 `cli.py pbc` 侧只读视图承担） | `tools/docreader_extract.py` 分析视图 |
| `supp_downloader.py` | 2026-10-15 | 零调用方；审计已自判死代码（下载能力由各源 fetch + `http.AdaptiveHttpClient` 承担） | `std_lib/scraper_std/http.py` |
| `supp_fix_raw_factual.py` | 2026-10-15 | 零调用方；一次性事实修正（I1/I2/I4 字段订正，任务已完成） | `supp_normalize`（链内一致化） |
| `supp_mapper.py` | 2026-10-15 | 零 import；其声称的注册点 `clean/supp_runtime.py` **不存在**（幽灵注册点，C1）——模块无加载路径即无用途 | `unified_schema.map_supp`（R5 迁移内置） |
| `build_clean_index.py` | 2026-10-15 | 零执行证据：README/脚本数据流声称「被 `__init__.py` 调用」但 grep 无 import/subprocess 实证；实际重建由 `get_clean_index()` 缺失/失陈时**自建**（`_index_is_usable` 归属+存活性校验）完成；GOV_ARTIFACTS 的 `produced_by` 已改指包本身 | `clean_index.get_clean_index()`（自愈重建） |

**观察纪律**：上述脚本在本目录保留一个完整月检周期（`monthly_check` 期间 gates 全绿即证明无隐藏引用），
之后删除（git 历史可恢复）。观察期内**不得**移回生产路径。
