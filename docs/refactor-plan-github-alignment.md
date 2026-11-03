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

## 1.5 顶栏与左侧边栏（此前缺失的对照）

**为什么单列一节**：前几版方案只比"列表内部"，而顶栏与侧栏是**第一眼界面语言**，
且它们正是"分辨率一变就散架"的地方。实测见 `reports/github-baseline/CHROME.md`。

| 项 | GitHub（1440） | 工作台（1440） | 结论 |
|---|---|---|---|
| 顶栏高 | **100px**（全局条 + 仓库页签带） | **52px**（单层） | 结构不同：GitHub 是**双层** |
| Logo / 品牌 | **32×32** 图形标 | **187×32** 文字标 | 我们是文字标，占位宽 6 倍 |
| 顶栏搜索入口 | **191×32**（1012 以下收成 **32×32** 图标） | **无** | 缺功能 |
| 头像 | **32×32** | **无** | 缺功能 |
| 仓库页签 | **8 个**，高 **32**，宽 73–157 | 无对应（用侧栏承担） | 导航模型不同 |
| 侧栏宽 | **256px** | **260px** | 差 4px，可接受 |
| 侧栏条目 | **223×32** | **239×32** | 条目更宽（因为侧栏宽 4 + 内边距差） |
| 条目内边距 / 字号 | — | `0 8px` / `14px` | 需按 GitHub 侧栏实测校准 |
| 选中态 | 整行浅底 + 字重加粗 | 底 `#f6f8fa` + `600`，**无左边框** | 底色对，左边框待核 |

**要做的三件事**：
1. 顶栏**双层化**（全局条 64 + 页面条 36 = 100），否则高了/矮了都不像；
2. 补**全局搜索入口**（191×32，<1012 收成 32×32 图标）与**头像**；
3. 侧栏保留 260（与 256 差 4px 可接受），但条目内边距与选中态按 GitHub 校准。



---


---

## 2.5 响应式（分辨率差异的影响）

同一控件在不同宽度下的行为**不一样**，方案必须写死断点策略，否则"改完在 1440 好看、1024 就塌"。

### 实测断点行为（GitHub）

| 控件 | 1440 | 1012 | 768 | 544 |
|---|---|---|---|---|
| 顶栏搜索 | 191×32（带文字） | **32×32**（只剩图标） | 32×32 | 32×32 |
| 列表搜索框 | 1047 | 619 | 375 | 407 |
| 密度开关 | 65×32 @ x=1327 | 899 | 655 | **37**（换行到左侧） |
| 筛选按钮 | 89×32 @ x=614 | 583 | 526 | **67** |
| 内容区左边界 | 425 | 293 | 293 | 33 |

### 我们的断点策略（写进实现）

| 断点 | 布局 |
|---|---|
| ≥1200 | 侧栏 260 + 三列图表 + 表宽 ≤1200 |
| 1012–1199 | 侧栏 260 + **两列**图表 |
| 768–1011 | 侧栏收成 **64px 图标条**；顶栏搜索收成图标；卡片单列 |
| <768 | 侧栏**抽屉**（默认收起）；表格降级为「卡片式行」 |

三条硬约束：
1. 任何宽度下**不出现横向滚动**（列宽算法按容器重算）；
2. 顶栏在 768 以下**不隐藏搜索**（收成图标，与 GitHub 一致）；
3. 三列→两列→单列的切换点在 **1200 / 1012 / 768**，与表宽上限 1200 对齐。


---


---

## 2.6 依赖版本矩阵与本次升级

**核对口径**：以 npm registry 的 `latest` 为准（比 GitHub Releases 更贴近实际可安装版本）。

| 依赖 | 升级前 | 最新 | 结论 |
|---|---|---|---|
| `@primer/css` | 旧版（无版本头，缺 ActionList/SegmentedControl/SelectPanel） | **22.3.3** | **已升级**（728KB → 1,036KB） |
| `@primer/primitives` | **10.4.0** | **11.10.0** | **已升级**（30 个 css 合并为单文件） |
| `@primer/octicons` | 329 个图标（旧 data.json 生成） | **19.42.0** | **已升级**（重新生成，408 个图标） |
| `@primer/behaviors` | **未引入** | **1.11.0** | 待 P0 引入（AnchoredPosition/FocusTrap 做 SelectPanel） |
| `echarts` | **5.6.0** | **6.1.0** | **已升级**（1,006KB → 1,095KB） |
| `echarts-gl` | 旧 | **2.1.0** | **已升级**（625KB） |

> 你列出的版本里只有 `@primer/css` 有偏差：你写 22.3.0，npm 最新是 **22.3.3**，按你的要求「以 GitHub 最新版本为准」升级到 22.3.3。

**升级验证（实测）**：

| 检查 | 结果 |
|---|---|
| ECharts 版本 | `echarts.version = **6.1.0**` |
| 图表页渲染 | metrics **6 个实例**（series 2/1/1/1/1/1）、flow 桑基 1、project 树 1 |
| UI 冒烟 | `ui_smoke` **PASS**（密度 65↔38、面板、Tab 序、标题省略均正常） |
| 旧文件 | 备份在 `web/assets/vendor/_backup_20261104/`，可回滚 |

**风险与注意**：
- ECharts 5→6 是**大版本**：our charts 已经过冒烟，但需要补一条"图表实例数 + series 数"的门禁断言（防止将来某个图静默空白）；
- Primer 22.3.3 体量增加 43%，若在意首屏可后续按需裁剪（只保留用到的组件）。


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
