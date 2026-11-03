# 全站重构方案：按 GitHub 对标页面重建（Primer + ECharts）

> 目标：**复刻 GitHub 的界面语言，删掉可被 Primer/ECharts 替代的自研实现**。
> 本文是方案，不含实现；验收一律用已建成的基线工具（见 §8），不靠肉眼。

---

## 0. 已确认的决议（2026-11-04）

| # | 决议 | 说明 |
|---|---|---|
| D1 | **overview 用三列** | GitHub `graphs/contributors` 实测是「1 通栏 + 2 列（440×221）」，我们明确改为**三列**：作为有意的偏离记录在案 |
| D2 | **overview/metrics 的栅格参数对齐 contributors** | gap 横竖 **16px**、卡片 `border 1px #d1d9e0`、`radius 6px`、右上角 **Period 控件 183×32** |
| D3 | **列表列宽按 `docs/table-column-width-spec.md`** | 含三条补充：表宽 ≤1200、短列双闸（≤12 全角当量 且 ≤160px）、极端长串固定 200 不参与权重 |
| D4 | **页面映射**（下方 §1） | feedback/search/rules/laws/worklist/regstats ↔ `pulls`；release ↔ `commits/master`；overview/metrics ↔ `graphs/contributors`；表单作业类 ↔ `security/advisories/new` |

### 页面映射表

| 工作台 | GitHub 对标 | 备注 |
|---|---|---|
| `#feedback` 留言板 | `/pulls` | 列表 + 行内处置表单 |
| `#search` 文档检索 | `/pulls` | 搜索框 + 结果列表 |
| `#rules` 公司制度 | `/pulls` | 与 issues 同源，差异仅入口 |
| `#laws` 政策法规 | `/pulls` | 同上 |
| `#worklist` 待办闭环 | `/pulls` | 含批量处置（GitHub 无对应，见 §5.4） |
| `#regstats` 监管统计（各级菜单） | `/pulls` | 除 EAST-概览 / 关系图 / E-R 图 |
| `#release` 发布物 | `/commits/master` | 提交列表 + 详情 |
| `#overview` 总览 | `/graphs/contributors` | **三列**（D1） |
| `#metrics` 指标 | `/graphs/contributors` | 同上 |
| 表单（留言/处置/新建） | `/security/advisories/new` | 878 宽控件、label 外置 |

---

## 1. 现状量化（2026-11-04 实测）

| 项 | 数值 | 含义 |
|---|---|---|
| `web/assets/index.html` | **6,734 行 / 309 KB** | 单文件承载全部页面 |
| 内联 CSS | **1,341 行 / 710 条规则** | 其中 **336 条 `.lv-*`** 是自建列表系统 |
| 内联 JS | **5,320 行 / 162 个函数** | 渲染、组件、图表、交互全在这 |
| `charts.js` | 685 行 | 自研 ECharts 封装（line/bar/pie/sankey/tree/wordcloud…） |
| Primer CSS | 728 KB + primitives 543 KB | **已引入但用得很少**：`Label` 26 次、`btn` 14 次、`Truncate` 3 次 |
| ECharts | 1,006 KB + 7 个插件 | sankey / tree / wordcloud / graph / liquidfill / ecStat / gl |

**关键结论：技术基座已经就位（Primer + ECharts 都在），问题不是"没引入"，而是"自研重复造了一层"。**

---

## 2. 目标与不做的事

**做**
1. 视觉与交互用语统一到 GitHub（间距、圆角、边框、悬停/聚焦色、按钮内距、面板结构）；
2. 能用 Primer 的组件一律用 Primer，**删掉自研的对应实现**；
3. 图表统一走 `charts.js` → ECharts，保留必要的薄封装（主题、空态、响应式）；
4. 列表列宽改弹性算法（D3），不再手写 `--cols`。

**不做**
- 不引入 React/Vue 等框架（当前是原生渲染 + 模板字符串，改造面太大）；
- 不逐像素复刻 GitHub 的**数据页**（issue 详情、PR diff），只做界面语言；
- 不删除 `charts.js` 的对外 API（大量调用点，改成内部换实现）。

---

## 3. 技术基座缺口（必须先补）

`primer.css` 是**旧版命名**（`Label` / `Box` / `UnderlineNav` / `Timeline` / `Popover`），
**不含 GitHub 现在用的这些组件**（实测）：

| 需要的组件 | 现状 | 影响 |
|---|---|---|
| `ActionList`（列表项） | **缺** | 我们的 `.lv-row` 就是它的替代品（336 条规则） |
| `SegmentedControl`（分段控件） | **缺** | 密度开关、布局切换自己实现 |
| `SelectPanel`（筛选面板） | **缺** | `openOptionList` 自研（320 宽、radius 6 vs GitHub 12） |
| `Button`（`.Button`） | **缺** | 我们用的是 `.btn`（自建） |
| `Pagination` | **缺** | 自己写 |
| `Flash` / `Blankslate` | **缺** | 空态/提示自写 |

**P0 必须先做**：升级/补齐 Primer 到含 `ActionList / SegmentedControl / SelectPanel / Button / Pagination` 的版本（v20+），否则"调用 Primer"只能覆盖一半，剩下仍是自研。

---

## 4. 组件替换矩阵（自研 → 目标）

| 现有自研 | 规模 | 替换为 | 处理 |
|---|---|---|---|
| `.lv-gtable` 列表网格（336 条规则） | 大 | Primer `ActionList` + 列宽算法（D3） | 重写，保留类名做过渡别名 |
| `openOptionList` / `ghSelect` | 中 | Primer `SelectPanel`（单选无勾选框、多选有） | 重写；保留 `ghSelect(id, value, items)` 签名 |
| `[data-lvdensity]` 密度按钮 | 小 | `SegmentedControl` + `octicon-rows`/`octicon-three-bars` | 换图标与 tooltip（Comfortable / Compact） |
| `.btn` 14 处 | 小 | `.Button`（padding 0 12px、h32、radius 6） | 直接换类 |
| 自绘分页 | 小 | Primer `Pagination` | 换 |
| `.Truncate` 3 处 | 小 | 保留（Primer 原生） | — |
| `charts.js`（685 行） | 中 | ECharts 薄封装 | **保留**，内部统一主题/空态 |
| 手写 `--cols` 列宽（9 处） | 中 | 弹性列宽函数（D3） | 删除手写值 |

---

## 5. 分阶段实施

### P0 基座（1 批）
- 升级 Primer（或按需抽取 5 个组件样式）；
- 建立 `ui/` 目录约定：`ui/list.js`、`ui/select.js`、`ui/button.css`；
- 产出：Primer 组件可用 + 旧类名兼容层。
- 验收：`cli.py gates` 全绿；列表页视觉不回退。

### P1 列表（最大收益）
- 按 `/pulls` 重建 `.lv-*`：工具条（左状态开关 / 右筛选）、表头筛选面板（320/radius12/单选无勾选）、行高（宽松 65 / 紧凑 38）、悬停 `rgba(129,139,152,.1)`、聚焦 `outline 2px #0969da`；
- 列宽改弹性算法（D3）；
- 覆盖页：laws / rules / worklist / feedback / search / regstats。
- 验收：`gh_ui_compare --mode full` 每类"一致"；列宽 §5 三条全过。

### P2 表单（作业类）
- 按 `/security/advisories/new`：控件满宽、label 外置、行距统一；
- 覆盖：留言弹窗、行内处置、批量处置、反馈提交。
- 验收：控件 878→容器宽、h32、textarea 100；按钮 88×32 与 GitHub 内距一致（文字 + 24）。

### P3 图表页
- overview / metrics 改三列（D1）+ contributors 栅格参数（D2）；
- 补 Period 控件（183×32）；
- 统一 `charts.js` 主题（网格线 #d1d9e0、字号 12/14）。
- 验收：`gh_ui_matrix --spacing` 横竖 gap 均为 16、border 1px、radius 6。

### P4 清理
- 删除被替代的自研 CSS/JS（预计 `.lv-*` 从 336 条降到 ~120 条，内联 CSS 从 1,341 行降到 ~700 行）；
- 删除手写 `--cols`；
- 更新守卫（`test_console_*`）与文档。

---

## 6. 验收体系（不新增流程，复用已有）

| 工具 | 用途 | 通过标准 |
|---|---|---|
| `cli.py gates` | 31 道门禁 | 全绿 |
| `tools/ui_smoke.py` | 运行时冒烟（密度/面板/键盘序） | PASS |
| `tools/gh_ui_compare.py --mode full` | 全元素分类对比 | 每类判定为"一致"或差异有书面理由 |
| `tools/gh_ui_matrix.py` | 页面映射矩阵 + 栅格间距 | gap/border/radius 与基线一致 |
| `tools/gh_ui_states.py` | 悬停/聚焦/按下 | 色值与 GitHub 相同 |
| `python -m pytest` | 全量 | RC=0 |

---

## 7. 风险与回滚

| 风险 | 影响 | 对策 |
|---|---|---|
| Primer 升级导致既有样式回退 | 全站视觉跳变 | 分两步：先并行引入新类，再逐页切换；每页提交可回滚 |
| 列表重写破坏守卫 | 门禁红 | 先改守卫为新契约（已在历史批次演练过） |
| 列宽算法误判（中文宽度） | 列难看 | 用 canvas 实测宽度，不用字符数估算；加 §5 三条硬指标 |
| 三列导致窄屏溢出 | 内容被压 | <1200 时降为 2 列、<768 降为 1 列（断点表另附） |

---

## 8. 工作量估算（按批次）

| 阶段 | 预计 | 批次数 |
|---|---|---|
| P0 基座 | 1 批 | 1 |
| P1 列表 | 3–4 批（6 页 + 列宽算法） | 4 |
| P2 表单 | 2 批 | 2 |
| P3 图表 | 2 批 | 2 |
| P4 清理 | 1 批 | 1 |
| **合计** | **9–10 批** | — |

> 每批都以"门禁全绿 + 对比器判定一致"收尾，不积压。

---

*相关文件：`docs/ui_github_baseline.md`（GitHub 实测基线）、`docs/table-column-width-spec.md`（列宽规范）、`reports/github-baseline/`（对照数据）。*
