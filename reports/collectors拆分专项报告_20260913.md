# collectors 大文件拆分专项报告（2026-09-13）

> 背景：`reports/项目全面审查报告_20260912.md` P3-1——5 个文件 >800 行（最大 1063），
> cli 611 行承载全分发。本专项对 collectors/std_lib 大文件做**零逻辑变更**拆分。
> 提交：`011ceb8`｜验证：py_compile 0 / import 冒烟 5 全过 / ruff 0 / pytest 172 / gates 14。

---

## 一、拆分矩阵（5 文件 → 10 文件）

| 原文件 | 原行数 | 主文件（现） | 子模块（新） | 子行数 |
|---|---|---|---|---|
| `nfra_fetch_attachments.py` | 1063 | 682 | `nfra_attachments_extract.py` | 524 |
| `mof_collector.py` | 1013 | 606 | `mof_attachments.py` | 606 |
| `pbc_collector.py` | 846 | 735 | `pbc_parse.py` | 300 |
| `gov_collector.py` | 834 | 606 | `gov_parse.py` | 432 |
| `excel_structure.py`（std_lib） | 982 | 732 | `excel_matrix.py` | 408 |

**最大文件：1063 → 735 行**（全部 <800，主文件均值 ~672）。

**分层逻辑**：
- 抽取/解析链（文档格式路由、文本抽取、HTML 解析工具）→ 子模块；
- 网络/调度/入口（`main`、锁、状态）→ 主文件；
- std_lib 的 `excel_matrix`：矩阵工具 + 表头检测层。

## 二、拆分机制（可复用的工程方法）

1. **AST 行区间定位**：按顶层 def/class/赋值节点切块（装饰器/模块级常量正确归属）。
2. **闭包检查（防循环依赖）**：移出集合 S 中函数若引用「主文件残留函数」即报违规；
   `--auto` 模式自动把被依赖函数并入 S（如 mof 附件区依赖 `_request`/`fetch_list_page` →
   一并移入，主文件经 re-export 恢复）。**入口函数（main/run）作硬边界**——被引用即人工。
3. **显式 re-export**：子模块 `__all__` 全量导出（**含 `_` 私有名**）；主文件
   `from <child> import (a, b, c, ...)` 显式列出——外部 `from X import Y` 调用**零变更**。
4. **包内相对导入**：std_lib 场景 `from .excel_matrix import ...`；collectors 拍平目录（sys.path）用裸名。
5. **零逻辑变更**：函数体一字未动；子模块头部复制主文件完整 import/常量区（自足）。

## 三、三个陷阱与修复（实证）

| # | 陷阱 | 表现 | 修复 |
|---|---|---|---|
| 1 | 子模块误复制 `if __name__ == "__main__"` 块 | F821 `Undefined name 'main'` | 脚本删除子模块 `__main__` 块（4 处） |
| 2 | `import *` 触发 F405（may be undefined from star imports） | ruff 103 处 F405 | 改**显式多行导入**（从 `__all__` 生成） |
| 3 | std_lib 包内用裸名 import 子模块 | `ModuleNotFoundError: excel_matrix` | 改相对导入 `from .excel_matrix import ...` |

（附加：条件 try-import 整块复制产生 F401 ×7 → `ruff --add-noqa` 标注；W292 末行换行 auto-fix。）

## 四、兼容性保障

- **外部引用零改动**：`nfra_collector`/`nfra_weekly`/`pbc_merge_scrape`/`run_production_refresh`/
  `flatten_collectors` 等调用方**无需任何修改**（re-export 透明）；
- **私有名**（`_temp_pdf`/`_request`/`_walk_pieces` 等）经 `__all__` 显式导出，主文件内调用不受影响；
- **入口语义不变**：各 collector `main()` 与 CLI 参数保持原样。

## 五、验证清单

| 项 | 结果 |
|---|---|
| py_compile（10 文件） | rc=0 |
| import 冒烟（5 组 re-export 抽查） | 全过（含 `_temp_pdf`/`_request` 等私有名） |
| ruff 全仓 | All checks passed |
| pytest | **172 用例全过** |
| gates | **14 道全 PASS** |

## 六、遗留

- `pbc_parse.py`（300 行）偏小、`pbc_collector`（735）仍偏大——如需进一步均衡可把
  "列表/详情解析区"再并入（本轮以低风险为先未做）；
- collectors 其余文件（nfra_collector 813 / nfra_fetch_attachments 现已 682）<800 达标。

> 编制：CodeBuddy · 2026-09-13 · 回链：审查报告 P3-1
