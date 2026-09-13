# Changelog

## [Unreleased] 2026-09-13 — 克隆可移植性修复（`reports/克隆可移植性检视报告_20260913.md`）

### 修复（阻断级）
- **clean_index 索引出库 + 加载自愈**：`index.json` 原入库且内嵌本机绝对路径（31 处），
  使克隆副本在同机环境下读到**原仓数据**，把"无数据"伪造成 `timeliness verify` 的 `overall=success`。
  现：加入 `.gitignore` + `_index_is_usable()` 归属/存活性校验，不通过即自动重建（记 warning）。
- **盘符门禁扩面**：`gate_hardcoded_paths` 由"仅 `*.py`"扩至 `*.json/.yaml/.yml/.toml/.cfg/.ini/.md/.txt/.mermaid`，
  并输出 `py_count` / `non_py_count`；派生产物（clean_index 索引、recall output）与构建产物（build/dist/*.egg-info）显式排除。
- **数据清单可移植化**：`data_migration_manifest.json` 由绝对路径（97 处、登记过期 0907 快照）改为
  **相对仓库根路径 + 现行快照 sha256**（schema 1.1，80 条，覆盖 scrapers/classifier/drafter/base）。

### 修复（高）
- **依赖归属**：`pypdf` / `pdfplumber`（PDF **文本层**解析，非 OCR）由 `[ocr]` 上移 base；
  新增 `chardet`；`PyMuPDF` 下界 `>=1.24` → `>=1.24.3`（代码用 `import pymupdf`，该名自 1.24.3 起提供）。
- **删除死声明**：`cryptography`（零引用）、`mcp` extra（零 `import mcp`；真实依赖是 Node 侧 `@pkulaw/mcp-cli`）。
- **打包修复**：`packages` 改 `find`；`config/*.yaml` + `schema/*.json` 补 package-data（原 wheel 缺失，非可编辑安装必 FileNotFoundError）；
  `cli.py` 新增源码树校验——缺 `modules/` 时以 rc=4 显式拒绝并给出运行方式指引。
- **用例分层**：新增 `data` 标记（13 项数据依赖用例），无数据环境可 `pytest tests -m "not data"`（211 项）。

### 修复（中/低）
- README/CHANGELOG/clean_index README 中的本机绝对路径改为占位符；README 新增 §6.0「异机部署先决条件」。
- `.codebuddy/`（含本机路径与过时门禁数量的本地记忆）移出版本控制；`.gitignore` 同步。
- 数字校准：pytest 172 → **224**；README 维护约定 gates 13 → **14**。


本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 结构与语义化版本。
历史以 git log 为底稿归组；重大批次回链 `reports/` 专项报告。

## [Unreleased]

## [2.1.0] - 2026-09-12

### 新增（F-L01 分析交付库）
- 规划 §2.1 五级分析 **15 项交付生成器** `tools/gen_analysis_deliveries.py` + `cli.py analysis gen|status`；交付库 `docs/reports/`（`_manifest.json` sha 登记）。
- **数据重建后自动刷新**：`cli classify` 成功尾部自动 gen（`--no-analysis` 逃生）；编排新增阶段 6.8。

### 新增（其余 F 系列批次，回链报告）
- F-D14 状态文件版本锚点（4 类 `_meta`，副本写入）；F-D07 附件 7 字段契约 / F-D09 双轨权威声明；F-D06 五源字段别名契约化。
- F-O04 nfra 周度增量链入编排；F-O02 `source diff` 变更监听（watch_baseline）；F-O08 match 轻量偏移索引（内存数百 MB→MB 级，T1 回归 193/193 全同）。
- F-L02 条款对照素材 956/957 覆盖；F-L03 明细表 14 列（时效状态/核验来源联入分析层）；F-L04 `external_relations.jsonl`（1508 边）；F-L06 `--order date` 时间线；F-L08 vault 导出截断声明。
- **OCR 通路**：PaddleOCR 3.7.0 + Tesseract 5.4 双引擎、`cli internal reocr` 质量闸门、PDF 零文本清零（702 PDF：654 文本层 + 48 Paddle）。
- **部门制度入 IPB**：摄取 937 文件 → 制度 **107 → 957**（同 IPN 多版本去重透明）；merged 957 / clauses 11626。
- **知识库联动**：Obsidian vault 同步（`<Obsidian vault>`，4985 篇）+ llm_wiki v0.6.11 接入契约 + 主题 MOC。
- **EAST 报送文档排除**（六目录 1677 文件剔除，按指示不纳入）。

### 变更
- README **v2.1.0**（依撰写规范刷新：门禁 13 / 用例 68 / 制度 957 / 交付 15）。
- 门禁 **12 → 14 道**：+`gate_field_aliases`（中文列受控注册）、+`gate_secret_scan`（密钥扫描）。
- `run_production_refresh`：+阶段 6.5 变更监听基线、+阶段 6.8 分析交付库。

### 修复
- F-D14 `_meta` 就地注入污染调用方（indexer 汇总 KeyError 实证）→ 五处状态文件改副本写入。
- 同 IPN 多 sha 三层去重口径统一（index/merged/policies）；发布件 `INSERT OR REPLACE` 防御。
- clause_graph `dst_theme='1'` vs `theme='T1'` 前缀归一（内部边原判=0 误判）。
- `subprocess` 8 处补 `timeout=`（防挂起，审查 P2-5）。

### 工程（审查行动 P1/P2，2026-09-12）
- 依赖声明补全（lxml/numpy/pytesseract/openpyxl/xlrd/python-docx）+ ocr extra 版本区间。
- 单测三组（IPB 纯函数 12 / gen_analysis 10 / cli 门面 9）→ 用例 32 → **68**。
- `tools/ci_check.py`（ruff+pytest+gates 一键本地 CI）。

### 文档
- 新增/更新：`reports/项目全面审查报告_20260912.md`、`F-L01_五级分析交付库专项报告_20260912.md`、`运行手册_编排与定时_20260912.md`、各批次遗留项报告。

## [0.1.0] - 2026-09-08

### 新增
- 四仓合一：本仓为唯一演进点（原四仓只读冻结；P0 骨架 → P1–P7 迁移，来源映射见 README §7）。
- 五源采集清洗（39 列契约）、RFN 分类底座链（base→cluster→match→detail→upper）、时效核验（北大法宝 R13 三态）、内部制度摄取/对齐、起草对照、12 道交付门禁、pytest 20 用例、`data_migration_manifest.json` 数据复制追踪。

[Unreleased]: 见 git log
[2.1.0]: 对比 0.1.0 — 见 reports/ 批次报告（F 系列遗留项全落地 + 工程化 P1/P2）
[0.1.0]: P0 骨架初始（2026-09-08）
