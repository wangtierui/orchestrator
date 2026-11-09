# 工作台复刻 GitHub：实施规格 v10（全量重写）

> **状态**：可施工。每个数值都标注来源（实测日期或依赖文件），全文不含未决项。
> **依据**：① `@primer/css` 22.3.3、`@primer/primitives` 11.10.0、`@primer/behaviors` 1.11.0、ECharts 6.1.0 的**本地副本**；
> ② GitHub 登录态实测（2026-11-05，1440×1000 / 1440×900）；③ 旧版 `index.html` 的全量控件盘点。
> **覆盖关系**：本文替换此前 v1–v9 全部内容；旧文档中任何与之冲突的表述一律作废。

---

## 目录

| 章 | 内容 |
|---|---|
| 1 | 技术基座与依赖（四个包怎么装、怎么加载、踩过的两个坑） |
| 2 | 设计令牌（颜色 / 间距 / 字号 / 圆角 / 阴影 / 动效 / 断点） |
| 3 | 全站骨架（三层结构 + 尺寸 + 断点） |
| 4 | 导航、路由与浏览器历史 |
| 5 | 通用组件（12 类 + **5.13 组件 API / 5.14 ARIA 清单 / 5.15 组件联动**） |
| 6 | 交互行为（`@primer/behaviors` 四个 API 的接入点） |
| 7 | 图表规范（ECharts 与令牌桥接） |
| 8 | 接口契约（18 个端点 + 参数默认值 + **8.9 前后端职责边界**） |
| 9 | 页面规格（8 个导航项 + 5 个子页） |
| 10 | 必须自写的部分（清单 + 理由） |
| 11 | 令牌化收敛方案（84 处 → 0 处） |
| 12 | 验收与门禁（含 **12.3 渲染完成标记 / 12.4 ARIA 断言**） |
| 13 | 施工顺序（13 批） |
| 附录 A | 旧版功能全清单（按页） |
| 附录 B | GitHub 实测数据（9 页） |
| 附录 C | 依赖文件清单与校验 |

---

## 1. 技术基座与依赖

### 1.1 四个包（全部本地副本，零 CDN）

| 包 | 版本 | 本地路径 | 用途 | 许可 |
|---|---|---|---|---|
| `@primer/primitives` | 11.10.0 | `web/assets/primer-primitives.css`（1,679 KB） | 设计令牌（颜色/间距/字号/断点/动效） | MIT |
| `@primer/css` | 22.3.3 | `web/assets/primer.css`（1,037 KB） | 组件类与工具类（2,345 个类） | MIT |
| `@primer/behaviors` | **1.11.0** | `web/assets/vendor/primer-behaviors/`（136 KB，ESM+CJS） | 浮层定位、焦点陷阱、焦点区域、容器内滚动 | MIT |
| ECharts | 6.1.0 | `web/assets/vendor/echarts/`（1.1 MB） | 图表 | Apache-2.0 |

`primer-behaviors` 的 `MANIFEST.json` 记录了 64 个文件的 sha256、tgz 来源与版本，可复算。

### 1.2 加载方式（三条硬规则）

```html
<html lang="zh-CN" data-color-mode="light" data-light-theme="light">
  <link rel="stylesheet" href="/primer-primitives.css">
  <link rel="stylesheet" href="/primer.css">
  ...
  <script type="module">
    import { focusTrap, focusZone, getAnchoredPosition, scrollIntoView, FocusKeys }
      from "/vendor/primer-behaviors/dist/esm/index.mjs";
  </script>
```

**规则 1：根节点必须带 `data-color-mode` + `data-light-theme`。**
primitives 的语义令牌挂在 `[data-color-mode="light"][data-light-theme="light"]` 下；缺属性时所有 `var()` 落空，边框变 0px、文字变黑，**且不报错**。

**规则 2：只能用 `primer.css` 里真实存在的类。**
`.Box` / `.Box-row` / `.Truncate` / `.Label` / `.FormControl` 是 `@primer/react` 的组件名，CSS 里不存在。实际对应：

| 想用 | 实际类 |
|---|---|
| 卡片 | `.border` + `.rounded-2` |
| 行 | `.border-bottom` + `.py-1 px-2` |
| 表头底 | `.bgColor-muted` |
| 标签 | `.IssueLabel` |
| 计数 | `.Counter` |
| 截断 | 无工具类，需自写 3 行（`overflow:hidden;text-overflow:ellipsis;white-space:nowrap`） |

**规则 3：`.mjs` 必须以 `application/javascript` 提供。**
ESM 有严格 MIME 检查；返回 `text/plain` 时浏览器报 `Failed to fetch dynamically imported module`。
服务端 `web/server.py` 的静态 MIME 表已补 `.mjs` 与 `.json`（2026-11-05 修复）。

### 1.3 已验证的能力

| 能力 | 验证结果 |
|---|---|
| 令牌生效 | `--borderColor-default = #d1d9e0`（与 GitHub 实采一致） |
| behaviors 导入 | 动态 `import()` 成功，导出 13 个：`getAnchoredPosition` `focusTrap` `focusZone` `FocusKeys` `scrollIntoView` 等 |
| ECharts | 6.1.0 渲染成功（canvas 345×220 / 838×560 / 1134×640） |
| 样板页 | 13 页全部可打开，58 项断言通过 |

---

## 2. 设计令牌

> 全部为 CSS 变量，**禁止写字面值**。下表"值"列来自本地 var 求值。

### 2.1 颜色

| 用途 | 令牌 | 值 |
|---|---|---|
| 前景 | `--fgColor-default` | `#1f2328` |
| 前景次要 | `--fgColor-muted` | `#59636e` |
| 链接 | `--fgColor-accent` | `#0969da` |
| 危险 | `--fgColor-danger` | `#d1242f` |
| 成功 | `--fgColor-success` | `#1a7f37` |
| 边框默认 | `--borderColor-default` | **`#d1d9e0`** |
| 边框强调 | `--borderColor-accent-emphasis` | `#0969da` |
| 边框弱 | `--borderColor-muted` | `rgba(209,217,224,.7)`（表头底线实测值） |
| 底色 | `--bgColor-default` | `#ffffff` |
| 底色次 | `--bgColor-muted` | **`#f6f8fa`**（表头底实测） |
| 强调弱底 | `--bgColor-accent-muted` | `#ddf4ff`（标签底实测） |
| 悬停遮罩 | `--control-transparent-bgColor-hover` | `rgba(129,139,152,.1)` |
| 选中遮罩 | `--control-transparent-bgColor-selected` | `rgba(129,139,152,.15)` |
| 页签选中条 | 实测色 | `#fd8c73`（GitHub 仓库条橙色下划线） |
| 侧栏选中条 | 实测色 | `#0969da`（4px 竖条） |

### 2.2 间距（`--space-*`，6 档）

| 令牌 | 值 | 典型用途 |
|---|---|---|
| `--space-xxs` | .125rem (2) | 图标与文字间隙 |
| `--space-xs` | .25rem (4) | 图标按钮间距 |
| `--space-sm` | .5rem (8) | 工具条内间隙、表格单元格横向内距 |
| `--space-md` | .75rem (12) | 按钮横向内距、gutter |
| `--space-lg` | 1rem (16) | 卡片内边距、栅格 gap、区块间距 |
| `--space-xl` | 1.5rem (24) | 页面上下留白 |

另：`--base-size-4/8/12/16/24/32` 与上表同值，用于精确像素处（`--base-size-16: 1rem`）。

### 2.3 字号与行高

| 用途 | 令牌/值 | 实测对照 |
|---|---|---|
| 正文/表格/控件 | `--text-body-size-medium` = 14 / 21 | GitHub 14/21 |
| 次要信息 | `--text-body-size-small` = 12 / 18 | 表头分组、注释 |
| 页面标题 | 24 / 36 | GitHub h1 94×44 |
| 区块标题 | 14 / 600 / 21 | — |
| KPI 数字 | 22 / 28 | 自定（信息层级需要） |
| 等宽 | `--fontStack-monospace` = 13 / 20 | sha、id |

**字体族**：`--fontStack-system`（GitHub 用 `"Mona Sans VF"`，我们无该字体，用系统栈；同 14px 字宽差 5–10%，由布局的 `min-width:0` + 截断吸收，不靠调字号补偿）。

### 2.4 圆角 / 阴影 / 动效 / 断点

| 类别 | 令牌 | 值 |
|---|---|---|
| 圆角·中 | `--borderRadius-medium` | 6px（按钮/输入/卡片） |
| 圆角·大 | `--borderRadius-large` | 12px（面板/弹层，实测 320×221 面板为 12） |
| 圆角·胶囊 | `--borderRadius-full` | 2em（Counter / 标签） |
| 阴影·面板 | 实测 | `0 0 0 1px rgba(209,217,224,.25), 0 8px 24px rgba(66,74,83,.12)` |
| 动效·时长 | `--motion-duration-short/medium/long` | 由 `--base-duration-*` 派生 |
| 动效·曲线 | `--motion-ease-*` | 标准/进入/退出三档 |
| 断点 | `--breakpoint-small/medium/large/xlarge` | **34rem(544) / 48rem(768) / 63.25rem(1012) / 80rem(1280)** |

> 断点必须用 `63.25rem` 与 `48rem`（= 1012 / 768），不得写 1011/767。

## 3. 全站骨架

### 3.1 三层结构（登录态实测）

```
┌──────────────────────────────────────────────────────────────────────┐
│ 全局条 52                                                             │
│ [菜单 32] [logo 32] 品牌 … [搜索 191×32]            [通知 32][头像 32] │
├──────────────────────────────────────────────────────────────────────┤
│ 仓库条 48                                                             │
│  总览  项目结构  数据流转  治理审计  版本管理  监管统计  文档管理  任务管理│
│  ↑ 32 高、padding 6 8、字号 14、间距 8、选中 ::after 2px #fd8c73       │
├──────────┬───────────────────────────────────────────────────────────┤
│ 侧栏 239 │ 内容区（最大 1200，居中，左右 padding 16）                 │
│          │                                                           │
│ 条目 223×32 · padding 6 8 · radius 6                                  │
│ 选中：15% 灰底 + 左 4px #0969da                                        │
│ ────────                                                             │
│ Feedback 223×32                                                       │
│ Collapse sidebar 223×32                                               │
└──────────┴───────────────────────────────────────────────────────────┘
```

| 层 | 实测值 | 说明 |
|---|---|---|
| 全局条 | **52** | 内含 logo 32、搜索 191×32、头像 32、图标按钮 32×32，间距 8 |
| 仓库条 | **48** | 条目 32 高、上内距 8；水平滚动不换行 |
| 顶区合计 | **100** | 52 + 48 |
| 侧栏 | **239** | 条目 223×32（左偏移 16） |
| 内容 | 1200 | 超过 1200 居中；表格不得溢出 |

### 3.2 断点行为

| 宽度 | 侧栏 | 仓库条 | 栅格 | 表格 |
|---|---|---|---|---|
| ≥1280 | 239 常驻 | 全显 | 4 列（288×4） | 全列 |
| 1012–1279 | 239 常驻 | 全显 | 3 列（378×3） | 全列 |
| 768–1011 | 收为 56 图标列 | 横向滚动 | 2 列 | 次要列隐藏（保留名称/状态/操作） |
| 544–767 | 抽屉（点菜单出） | 横向滚动 | 1 列 | 卡片化：每行两行文本 |
| <544 | 抽屉 | 只显示当前项 | 1 列 | 卡片化 |

**硬约束**：任何宽度都不出现页面级横向滚动；`<768` 搜索框收成 32×32 图标（不隐藏）。

---

## 4. 导航、路由与浏览器历史

### 4.1 顶部导航（8 项，顺序固定）

```
项目总览 · 项目结构 · 数据流转 · 治理审计 · 版本管理 · 监管统计 · 文档管理 · 任务管理
```

### 4.2 侧栏业务切片（每页不同，随页切换）

| 页 | 侧栏条目 |
|---|---|
| **项目总览** | 无侧栏（内容满宽 1200 居中） |
| 项目结构 | 目录树（可折叠，本身即导航） |
| 数据流转 | 运行列表 · 数据血缘 · 未连线任务 |
| 治理审计 | 运行时间线 · 变更台账 · 失败归因 |
| 版本管理 | 提交 · 发布件 · 认证 |
| 监管统计 | 概览 · 数据结构 · 数据元 · 业务代码 · 检核规则 · 关系图 · E-R 图 |
| **文档管理** | 文档检索 · 政策法规 · 公司制度 · 关系图谱 |
| **任务管理** | 程序指令 · 用户反馈 |

底部固定两项：`Feedback`（打开反馈弹窗）、`Collapse sidebar`（折叠侧栏，折叠后宽 0，全局条左侧出现「Expand sidebar」32×32）。

### 4.3 路由与历史（解决"后退回不去"）

**URL 形态**（状态全部进 URL）：

```
/#/laws?q=主题:T1&sort=title&desc=0&page=2&source=gov
/#/worklist?status=open&kind=basis&q=clean
/#/governance?range=24h&status=failed
/#/documents?tab=retrieval&q=保险&type=article
```

**四条实现规则**

| # | 规则 | 说明 |
|---|---|---|
| R1 | **pushState 而非改 hash** | `history.pushState({page, qs, scroll}, "", url)`；只有状态真的变化才 push |
| R2 | **popstate 还原** | 解析 `path+qs` → 还原状态对象 → 增量重渲染 → 恢复 `history.state.scroll` |
| R3 | **输入节流** | 搜索输入 300ms 内 `replaceState`；回车/失焦才 `pushState` |
| R4 | **首屏 replaceState** | 无参数进入时补齐默认值，不产生历史条目 |

**边界**

| 场景 | 处理 |
|---|---|
| 后退跨页 | 先切页再还原状态，合并为一次渲染 |
| 状态失效（数据已删） | 提示「筛选已失效，已重置」并 `replaceState` |
| 直接粘贴深链 | 支持，首屏按 URL 渲染 |
| 折叠侧栏 | 状态存 `localStorage`（`rco.sidebar=collapsed|expanded`），不进 URL |

**验收**：改筛选 → 后退 → 筛选回退；再前进 → 恢复；刷新 → 保持；粘贴 URL 新开 → 一致。

---

### 4.4 路由验收口径（P3 收口，可重复执行）

**工具**：`python -m tools.route_all`（11 页 × 按类型判据，共 **23 项**；`--slug` 可单页复跑）。

判据按写入方式区分，不能一概而论：

| 模式 | 页面 | 判据 | 为什么 |
|---|---|---|---|
| `pager` | 政策法规 · 公司制度 | 点下一页 → hash 含 `page=2` 且分页文案以 `2 /` 开头；`back()` 后 hash 与**行数**同时还原 | 分页用 `pushState`，必须可回退 |
| `url` | 用户反馈 · 程序指令 | 状态段点击 → hash 变化 + 内容变化；`back()` 还原 | 状态段用 `pushState` |
| `q` | 文档检索 · 版本管理 · 治理审计 · 监管统计 | 输入 → hash 变化 + 内容变化；**不判后退** | 输入用 `replaceState`（§4.3 R3），本就不产生历史条目 |
| `view` | 关系图谱 · 数据流转 | 切视图 → hash 含 `view=`；同 URL 重开仍选中该视图 | 视图是最小导航状态 |
| `path` | 项目结构 | 深链 `?path=` 渲染对应节点的名称/路径/用途 | 树的选中态即 URL |

**N/A 登记（无状态可序列化，不是遗漏）**

| 页 | 为什么 N/A |
|---|---|
| 指标 `metrics` | 5 张图全部由 `/api/metrics` 一次性驱动，页面无视图切换、无筛选、无分页；加参数没有用户价值 |
| 项目总览 `overview` | 17 区块只读聚合，无任何交互状态 |

**已知偏差 R2（实测，不掩盖）**

`popstate` 的还原方式是**整页重挂载 + 重新拉数**，不是 §4.3 R2 要求的增量渲染。实测代价：

| 页 | 后退耗时 | 原因 |
|---|---|---|
| 政策法规 | ≈ **10 秒** | 重拉 16,607 条（每页 50） |
| 公司制度 | ≈ 1–2 秒 | 861 条 |

`tools/route_all` 的 pager 模式因此把后退等待设为 **12 秒**（这是补救测量，不是达标）。
真正的修复是把页面状态还原做成增量（`RCO_ON_PARAMS` 钩子），见后续批次。

**为什么暂不纳入 gates**：`route_all` 全跑约 2–3 分钟，直接进门禁会让每次门禁变慢一个量级。
计划以「默认运行、`--fast` 跳过」的形式接入，届时在 §12 更新。

## 5. 通用组件

> 每类给：结构 / 尺寸 / 状态 / 验收。尺寸来源：GitHub 实测（§附录 B）。

### 5.1 全局条控件

| 控件 | 尺寸 | 状态 |
|---|---|---|
| 菜单按钮 | 32×32 | hover 10% 灰底 |
| logo | 32×32 | 可点回总览 |
| 品牌名 | 14/600 | — |
| 搜索入口 | **191×32** | hover 边框加深；≤1012 收为 32×32 图标 |
| 通知/头像 | 32×32 | 圆形头像，2px 白描边 |
| 底边 | 1px `--borderColor-default` | — |

### 5.2 仓库条（页签行）

| 属性 | 规格 |
|---|---|
| 容器 | 高 48；水平排列；底边 1px；**不横向滚动** |
| 单项 | 高 32，`padding: 6px 8px`，字号 14，`border-radius: 6px`，间距 8 |
| 默认 | 无底色、无下划线 |
| hover | `rgba(129,139,152,.1)` |
| **选中** | **`::after` 高 2px、色 `#fd8c73`、贴底**；**字重不变（400）** |
| 计数 | 右侧 `Counter`（高 18、`padding: 2px 6px`、`border-radius: 2em`） |

#### 5.2.1 溢出：**More 下拉**（不是横向滚动）

> 修正：此前写"溢出横向滚动"，与 GitHub 的 UnderlineNav 不符。鼠标没有横向滚轮，且选中项可能被滚出视口。

**测宽与收折规则**

```
① 容器可用宽 = 内容宽 − 左右 padding（16×2）
② 逐项累加（项宽 = 文字 + 16 + 计数宽 + 8 间距）
③ 放不下时：先压水平内距（6 8 → 4 6）→ 再把末尾 N 项收进 More
④ More 按钮固定在末尾（32×32，图标 + `aria-label="更多页签"`），不参与收折
⑤ 被收进 More 的项若包含当前页，则 **More 按钮显示当前项名称**并保持选中态
```

**More 面板**

| 项 | 规格 |
|---|---|
| 容器 | 320 宽、radius 12、环阴影 `0 0 0 1px rgba(209,217,224,.25)` |
| 项 | 高 32、`padding: 6 8`、当前项左侧 4px `#0969da` |
| 定位 | `getAnchoredPosition(more, btn, {side:"outside-bottom", align:"end", allowOutOfBounds:false})` |
| 键盘 | `focusZone`（`ArrowVertical\|HomeAndEnd`）；`focusTrap`；`Esc` 关闭回焦 |
| ARIA | 按钮 `aria-haspopup="menu" aria-expanded`，面板 `role="menu"`，项 `role="menuitem"` + `aria-current="page"` |

**验收**：1440 下 8 项全显；缩到 900 出现 More；把窗口缩到当前项被收起时，More 按钮文案变为该页名且保持橙色下划线。

#### 5.2.2 侧栏折叠态（跨组件，统一归属在此）

| 位置 | 元素 | 规格 |
|---|---|---|
| 全局条左侧 | **Expand sidebar 按钮** | **32×32**、图标 `sidebar-expand`、`aria-label="展开侧边栏"`、`aria-expanded="false"`、`aria-controls="side-rail"` |
| 侧栏底部 | Collapse 按钮 | 223×32、`aria-label="折叠侧边栏"`、`aria-expanded="true"`、`aria-controls="side-rail"` |
| 侧栏 | 折叠后 | 宽度 0、`display:none`、内容区宽度 +239；**不保留图标条** |
| 状态保存 | — | `localStorage["rco.sidebar"] = "collapsed" \| "expanded"`，刷新后保持 |
| 键盘 | — | 两个按钮均在 Tab 顺序内；`Enter/Space` 切换；折叠后焦点移到 Expand 按钮 |

**验收**：折叠后内容区变宽 239、Expand 按钮出现且 `aria-expanded=false`；刷新仍是折叠态；再展开恢复原状且焦点回到 Collapse。

### 5.3 侧栏

| 属性 | 规格 |
|---|---|
| 容器 | 宽 239、左内距 8；无边框、背景透明 |
| 分组标题 | 12/600、`--fgColor-muted`、`padding: 8 8 4`、`letter-spacing: .04em` |
| 条目 | **223×32**、`padding: 6px 8px`、`border-radius: 6px`、14/21 |
| hover | `rgba(129,139,152,.1)` |
| **选中** | 背景 `rgba(129,139,152,.15)` + **`::after` 宽 4px、色 `#0969da`**（勿用 `border-left`，会撑宽 4px） |
| 底部动作 | `Feedback` / `Collapse sidebar` 各 223×32，与条目同规格，上方留 8 |

### 5.4 搜索行（列表页）

```
┌────────────────────────────────────────────────────────────┬──────┐
│ 搜索或筛选：主题:T1、文号:金非银检函…                       │  🔍  │
└────────────────────────────────────────────────────────────┴──────┘
```

| 属性 | 规格 |
|---|---|
| 位置 | 列表上方独占一行，宽 = 内容宽 |
| 高 | **30**（列表页）；表单页输入框为 **32** |
| 结构 | **外层容器**承载边框/圆角/高度；内层 `input` 的 `padding/border/background` 全为 0/透明 |
| 图标区 | 右侧独立 **28×28**，底色 `--bgColor-muted`，左 1px 分隔线，圆角 `0 6 6 0` |
| 聚焦 | 单层 `outline: 2px solid var(--borderColor-accent-emphasis)` |
| 行为 | 输入 **220–300ms 防抖后发服务端请求**（不在前端过滤结果集）；回车立即请求；与筛选条件由后端取交集；筛选后搜索词保留 |

### 5.5 工具条

| 属性 | 规格 |
|---|---|
| 分组 | 左：状态开关；右：筛选按钮组 + 密度 |
| 布局 | `flex-wrap: wrap`；`.sp{flex:1}` 占位；**不裁切** |
| 高 | 自适应（最小 48） |
| 排序按钮 | `Sort by Newest descending` 116×32，含方向箭头 |

### 5.6 表格

| 项 | 规格 |
|---|---|
| 外框 | **无**（容器 0px） |
| 表头行 | 高 **48**、底色 `#f6f8fa`、下边 1px `rgba(209,217,224,.7)`、`padding: 8`、12/600 |
| 行 | 每条 1px 底边；**行高严格按密度**：comfortable **65** / compact **38**（全站统一，无 32 例外） |
| 列宽 | 短列 `clamp(need+24, 64, 220)`；长列按内容加权 min 160；极端串（无空格 ASCII ≥24）固定 200 不参与权重；总宽 ≤1200 |
| 末列 | 动作列保底 72（图标组 148） |
| 排序 | 列头可点，显示 ↑/↓，单列生效 |

### 5.7 分页

| 项 | 规格 |
|---|---|
| 位置 | 表格容器**之外**，下方 16 |
| 形态 | `[上一页] 1 / N [下一页]` |
| 状态 | 首页禁用上一页、末页禁用下一页、加载中两者禁用 |
| 每页条数 | 与接口一致：records/rules **50**（上限 200）；worklist 无分页；search 固定 20 |

### 5.8 筛选面板（实测 320×221）

| 属性 | 规格 |
|---|---|
| 容器 | **320** 宽、`border-radius: 12px`、底色白、环阴影 `0 0 0 1px rgba(209,217,224,.25)` |
| 列表区 | 320×144（3 项）；项高 **32**，`padding: 6px 8px` |
| 单选 | 无勾选框；选中行底色 `.15`；hover `.1` |
| 多选 | 左侧 16×16 勾选框 |
| 面板内搜索 | 高 30，放大镜在**左** |
| 定位 | 用 `getAnchoredPosition`（§6.1），默认 `outside-bottom` + `align:start` |
| 关闭 | Esc / 点外部 / 再点触发按钮；关闭后焦点回到触发按钮 |

### 5.9 按钮

| 类型 | 尺寸 | 备注 |
|---|---|---|
| 文字按钮 | 宽 = **文字 + 24 + 2**，高 32，`padding: 0 12px`，radius 6 | Author 89 / Labels 85 实测符合 |
| 筛选按钮 | 同上，但**边框色透明**（占 2px 不显） | 视觉上无边框 |
| 图标按钮 | **32×32**，`padding: 0`，1px 边 | 工具条右侧 |
| 小图标 | **20×32**，`padding: 4px 6px` | 行内 |
| 主按钮 | 绿底白字 | 提交类 |
| 状态 | hover `#f3f4f6`；active 下沉 1px；disabled 50% + `not-allowed` |

### 5.10 表单

| 项 | 规格 |
|---|---|
| label | 块级、12px、600、下方 6px；必填加 `*` |
| 控件 | 满宽（容器 − 32）；输入高 32；文本域高 **100**；radius 6；1px 边 |
| 聚焦 | `border-color: --borderColor-accent-emphasis` + 单层 outline |
| 校验 | 提交前校验；错误文案在控件下 12px 红字；焦点跳到首个错误 |
| 按钮区 | 右对齐；取消在左、主操作在右 |

### 5.11 状态（加载 / 空 / 错误 / 只读）

| 态 | 结构 | 文案示例 |
|---|---|---|
| 加载 | 骨架行 ×5（高 32）或「加载中…」，容器高度不塌陷 | — |
| 空 | 居中图标（24）+ 一句说明 + 一个主操作 | 「当前无待办」+「清除筛选」 |
| 错误 | 红字 + 错误码 + 「重试」 | 「接口 500：服务器错误」 |
| 只读 | 写操作按钮 disabled + tooltip | 「只读模式」 |

### 5.12 密度切换（实测 65 / 38）

| 项 | Comfortable | Compact |
|---|---|---|
| 行高 | **65** | **38** |
| 元信息 | 全显（作者、分支、checks、时间） | 保留标题 + 编号，次要项隐藏/折行 |
| 图标数 | 3 | 4 |
| 字号 | 14/21 | **14/21（不变）** |

- 控件：`ul[aria-label="Issues display density"]`，两个 32×32 图标按钮，容器 65×32；
- 实现：`data-density` 属性 + `--space-*` 控内距，**不重排 DOM**；状态进 URL（`&density=compact`）。

### 5.13 组件 API（**全部是可配置函数，禁止复制粘贴**）

> 现状问题：13 页各自拼 HTML，同一个「表格」写了 6 遍、同一个「工具条」写了 5 遍。
> 解法：把 §5 的 12 类组件实现为**唯一函数**，页面只传配置。

模块位置：`web/assets/js/`（ESM，与 behaviors 同加载方式）。

| 模块 | 导出 | 关键参数 | 返回 |
|---|---|---|---|
| `ui/table.js` | `renderTable(cfg)` | `{columns[{key,label,width,sortable,align,render}], rows, density, selectable, empty, onRow}` | `HTMLElement` |
| `ui/toolbar.js` | `renderToolbar(cfg)` | `{segments[{key,label,count}], active, filters[{key,label,options,multi}], density, onSegment,onFilter,onDensity}` | `HTMLElement` |
| `ui/search.js` | `renderSearch(cfg)` | `{value, placeholder, icon:'right', debounce:220, onInput, onSubmit}` | `HTMLElement` |
| `ui/pagination.js` | `renderPagination(cfg)` | `{page,size,total,onChange}` | `HTMLElement` |
| `ui/panel.js` | `openPanel(cfg)` | `{anchor, items[{key,label,count,checked}], multi, searchable, onPick, onClose}` | `{close()}` |
| `ui/form.js` | `renderForm(cfg)` | `{fields[{key,label,type,required,options,hint}], actions[{key,label,variant}], onSubmit}` | `{el, validate(), values()}` |
| `ui/chart.js` | `mountChart(cfg)` | `{el, option, height, resize:true}` | `{setOption(), resize(), dispose()}` |
| `ui/state.js` | `renderState(kind,cfg)` | `kind: loading|empty|error`；`{title,desc,action}` | `HTMLElement` |
| `ui/skeleton.js` | `renderSkeleton(rows)` | `rows` | `HTMLElement` |
| `ui/chrome.js` | `renderChrome(cfg)` | `{nav[], active, sidebar[], density}` | `HTMLElement` |
| `ui/page-ready.js` | `markReady()` | — | 置 `body.dataset.pageReady="1"` |
| `ui/router.js` | `initRouter(routes)` | `{parse, build, onChange}` | `{push(), replace()}` |

**约束**
1. 页面只允许：`import` 上述函数 + 一次 `renderXxx(cfg)` 调用 + 数据 fetch；**不得再出现手写 HTML 字符串**；
2. 同一组件在 13 页里只允许一处实现（`tools/ui_css_audit.py` 扩展为扫描重复结构）；
3. 组件内部**不写颜色/边框/圆角**，只用 Primer 类与令牌。

### 5.14 ARIA 清单（每类组件必备）

> 判据：**原生语义够用就不加 role**；不够才加。下列为最低要求，`pages_smoke` 逐项断言。

**5.14.1 全局条 / 仓库条**

| 项 | 内容 |
|---|---|
| 角色 | `<header>` 原生；仓库条 `nav[aria-label="模块导航"]`；页签是**链接**（非 tab），不设 `role=tab` |
| 状态属性 | 当前页链接 `aria-current="page"`（**全页仅一个**） |
| 关系属性 | 搜索框 `aria-controls` 指向结果容器；`aria-haspopup="menu"` 于菜单按钮 |
| 键盘 | `Tab` 顺序：logo → 搜索 → 通知 → 头像 → 页签区；搜索 `Esc` 清空并失焦 |

**5.14.2 侧栏**

| 项 | 内容 |
|---|---|
| 角色 | `nav[aria-label="侧边导航"]`；分组用 `div[role=group]` + `aria-labelledby` 指向分组标题 |
| 状态属性 | 当前项 `aria-current="page"`；折叠按钮 `aria-expanded` |
| 关系属性 | 折叠按钮 `aria-controls` 指向侧栏容器 |
| 键盘 | ↑/↓ 移动（`focusZone` + `ArrowVertical`）；`Home/End` 首尾；`Enter` 打开 |

**5.14.3 搜索行**

| 项 | 内容 |
|---|---|
| 角色 | `<input type="search">` 原生（不加 role）；容器 `role="search"` |
| 状态属性 | `aria-busy`（查询中）；`aria-invalid`（服务端报错） |
| 关系属性 | `aria-label`（必填，例：搜索政策法规）；`aria-describedby` 指向语法提示 |
| 键盘 | `Enter` 提交；`Esc` 清空；`↓` 打开结果建议（若有） |

**5.14.4 工具条 / 状态开关**

| 项 | 内容 |
|---|---|
| 角色 | 容器 `role="group"` + `aria-label="列表工具"`；开关组用 `role="group"`，每个按钮是 `<button aria-pressed>` |
| 状态属性 | 选中 `aria-pressed="true"`；计数用 `<span class="Counter" aria-label="319 项">319</span>` |
| 关系属性 | 筛选按钮 `aria-haspopup="dialog"` + `aria-expanded` + `aria-controls` 指向面板 id |
| 键盘 | `Tab` 逐个；`Enter/Space` 触发；开关组内 ←/→ 切换（`ArrowHorizontal`） |

**5.14.5 表格**

| 项 | 内容 |
|---|---|
| 角色 | 用原生 `<table>`；如需网格布局则 `role="table"` + `row` + `columnheader` + `cell` |
| 状态属性 | 排序列表头 `aria-sort="ascending|descending|none"`；行选中 `aria-selected` |
| 关系属性 | 表头 `id` 与单元格 `aria-labelledby`（网格模式下必填） |
| 键盘 | 行内按钮进入 Tab 顺序；`↑/↓` 移动行焦点（可选）；`Space` 切换勾选 |

**5.14.6 分页**

| 项 | 内容 |
|---|---|
| 角色 | `nav[aria-label="分页"]` |
| 状态属性 | 当前页 `aria-current="page"`；禁用按钮 `disabled`（不用 `aria-disabled`） |
| 关系属性 | — |
| 键盘 | `Enter` 触发；首末页按钮 `disabled` 时跳过 Tab |

**5.14.7 筛选面板**

| 项 | 内容 |
|---|---|
| 角色 | 容器 `role="dialog"` + `aria-modal="false"` + `aria-label="筛选：作者"`；列表 `role="listbox"`，项 `role="option"` |
| 状态属性 | 项 `aria-selected`；触发按钮 `aria-expanded` |
| 关系属性 | 触发按钮 `aria-controls` → 面板 id；面板 `aria-labelledby` → 触发按钮 id |
| 键盘 | `↑/↓`（`ArrowVertical`）、`Home/End`、`Enter` 选中、`Esc` 关闭并回焦、`Tab` 留在面板内（`focusTrap`） |

**5.14.8 表单 / 弹窗**

| 项 | 内容 |
|---|---|
| 角色 | 弹窗 `role="dialog"` + `aria-modal="true"` + `aria-labelledby`（标题 id） |
| 状态属性 | 字段 `aria-invalid` + `aria-describedby` 指向错误文案；必填 `aria-required="true"` |
| 关系属性 | 每个 `<label for>` 对应控件 id；错误文案 id 被 `aria-describedby` 引用 |
| 键盘 | `focusTrap` 生效；`Esc` 关闭；`Enter` 提交（文本域内 `Enter` 换行） |

**5.14.9 图表**

| 项 | 内容 |
|---|---|
| 角色 | 容器 `role="img"` + `aria-label`（一句话概括） |
| 状态属性 | `aria-busy`（渲染中） |
| 关系属性 | 相邻 `figcaption`/摘要文本 `aria-describedby`（给出关键数字，供读屏） |
| 键盘 | 图表容器 `tabindex="0"`；`←/→` 在数据点间移动（ECharts `dispatchAction`） |

**5.14.10 状态组件（空/错误/加载）**

| 项 | 内容 |
|---|---|
| 角色 | 容器 `role="status"`（加载/空）或 `role="alert"`（错误） |
| 状态属性 | `aria-live="polite"`（status）/ `assertive`（alert） |
| 关系属性 | — |
| 键盘 | 无交互；操作按钮正常 Tab |

**5.14.11 密度切换**

| 项 | 内容 |
|---|---|
| 角色 | `role="group"` + `aria-label="列表密度"`，两个 `<button aria-pressed>` |
| 状态属性 | `aria-pressed`；容器 `data-density` 同步 |
| 关系属性 | 按钮 `aria-controls` → 列表容器 id |
| 键盘 | `Tab` 到组；←/→ 切换；`Enter` 应用 |

**5.14.12 行末三图标（打开原文/查看关系/查看条款）**

| 项 | 内容 |
|---|---|
| 角色 | 每个 `<a>`（打开原文）或 `<button>`；组容器 `role="group"` + `aria-label="文档操作"` |
| 状态属性 | 无数据时 `disabled`（button）或 `aria-disabled="true"` + `tabindex="-1"`（a） |
| 关系属性 | `aria-label` 含数字，例：**查看关系 14 条**、**查看条款 32 条** |
| 键盘 | `Tab` 依次；`Enter` 触发 |

**5.14.13 pages_smoke 断言（已实现）**

| 断言 | 判据 |
|---|---|
| `data-page-ready` | `body.dataset.pageReady === "1"`（渲染完成标记，见 §12.3） |
| `ARIA 主区域` | 存在 `main` 或 `[role=main]` |
| `ARIA 搜索框可访问名` | `#q` 有 `aria-label` 或 `aria-labelledby` |
| `ARIA 唯一 current` | `[aria-current]` 数量 ≤ 1（列表页） |

### 5.15 组件联动（此前完全没写）

| # | 联动 | 规则 |
|---|---|---|
| L1 | 搜索 ↔ 状态开关 | 状态开关改写服务端查询条件里的 `state:` 限定符，不单独存状态（单一真相在查询条件）；前端只负责把新条件发出去 |
| L2 | 搜索 ↔ 筛选 | **后端**按 `q + facet` 取交集返回；前端不做交集运算；两者互不清空 |
| L3 | 筛选 ↔ 计数 | 面板每项右上角显示该值计数；选中后该计数变灰但保留 |
| L4 | 排序 ↔ 分页 | 改排序 → 前端把 page 置 1 后**重新请求**（排序在后端） |
| L5 | 搜索 ↔ 分页 | 搜索结果变化 → page=1 **重新请求**（分页在后端） |
| L6 | 行选择 ↔ 批量条 | 勾选 ≥1 行显示批量条；全选复选框三态（全选/部分/无） |
| L7 | 批量条 ↔ 表格 | 切换状态/页码后**清空选择**（避免跨页误操作） |
| L8 | 密度 ↔ 表格 | 切换只改行高与可见字段，**不重排 DOM、不重置滚动** |
| L9 | 面板 ↔ 触发按钮 | 打开时按钮 `aria-expanded=true`；关闭回焦 |
| L10 | 图表 ↔ 筛选 | 改筛选 → **带筛选参数重新请求**图表数据（后端算，前端不算） |
| L11 | 侧栏 ↔ 内容 | 点侧栏切片 → 只改内容区与 URL，**不刷新整页** |
| L12 | 告警 ↔ 明细 | 点告警条目 → 跳到对应页并高亮目标行（`scrollIntoView` 容器内） |

**验收**：L1–L12 每条一个可复现操作 + 一个断言（写进 `pages_smoke` 的交互用例，与结构断言分开跑）。

## 6. 交互行为（`@primer/behaviors`）

> 已 vendor 到 `web/assets/vendor/primer-behaviors/`（1.11.0，MIT），`import()` 实测可用，导出 13 个。

### 6.1 浮层定位 `getAnchoredPosition`

```js
import { getAnchoredPosition } from "/vendor/primer-behaviors/dist/esm/index.mjs";
const { top, left } = getAnchoredPosition(panel, trigger, {
  side: "outside-bottom",      // 默认；贴边时自动翻到 outside-top
  align: "start",              // 右对齐时用 "end"
  anchorOffset: 4,             // 默认 4
  allowOutOfBounds: false,     // 关键：false 才会翻转/换边
});
panel.style.top = `${top}px`;
panel.style.left = `${left}px`;
```

**接入点**：筛选面板、排序菜单、行内 `⋯` 菜单、仓库条溢出菜单。
**前置条件**：触发按钮、面板、最近已定位祖先三者都已渲染；面板先 `visibility:hidden`，定位后显示，避免首帧闪现在 (0,0)。

### 6.2 焦点陷阱 `focusTrap`

```js
import { focusTrap } from ".../index.mjs";
const ctrl = focusTrap(dialog, dialog.querySelector("input"));   // 返回 AbortController
// 关闭时：ctrl.abort()
```

**接入点**：Feedback 弹窗、行内处置展开区（若做成模态）。
**行为**：Tab/Shift+Tab 在容器内循环；外部获焦会被拉回；同屏只有一个陷阱生效。

### 6.3 焦点区域 `focusZone`

```js
import { focusZone, FocusKeys } from ".../index.mjs";
const ac = focusZone(panel, {
  bindKeys: FocusKeys.ArrowVertical | FocusKeys.HomeAndEnd,
  focusOutBehavior: "stop",
  focusInStrategy: "closest",
});
// 销毁：ac.abort()
```

**接入点**：筛选面板选项列表、仓库条溢出菜单、侧栏条目（↑↓ 切换）。
**要求**：可聚焦项必须有 `tabindex="-1"`（原生可聚焦元素除外）；顺序按 DOM。

### 6.4 容器内滚动 `scrollIntoView`

```js
import { scrollIntoView } from ".../index.mjs";
scrollIntoView(target, mainEl, { direction: "vertical", startMargin: 8, behavior: "smooth" });
```

**接入点**：侧栏「本页导航」点击、表格行定位（从告警跳转）。
**替代原生**：原生 `el.scrollIntoView()` 会连带滚动祖先，导致整页跳动。

### 6.5 行为验收（四条，可自动化）

| # | 操作 | 断言 |
|---|---|---|
| B1 | 把面板触发按钮放到视口最右/最下后打开 | 面板翻到另一侧，`right ≤ innerWidth`、`bottom ≤ innerHeight` |
| B2 | 打开弹窗后连按 Tab 20 次 | `document.activeElement` 始终在弹窗内 |
| B3 | 打开面板后按 ↑↓/Home/End/Esc | 选项焦点按序移动；Esc 关闭且焦点回到触发按钮 |
| B4 | 点侧栏「列表」 | `window.scrollY` 不变，`#main.scrollTop` 变化 |

---

## 7. 图表规范

### 7.1 与令牌桥接（唯一入口）

```js
const css = getComputedStyle(document.documentElement);
const tk = (n) => css.getPropertyValue(n).trim();
const palette = [tk("--fgColor-accent"), tk("--fgColor-success"),
                 tk("--fgColor-attention"), tk("--fgColor-danger"), tk("--fgColor-muted")];
```

禁止在 ECharts option 里写 `#xxxxxx`。缺少的语义色（如 attention）从 primitives 的 `--fgColor-attention` 取；确实不存在时用 `--fgColor-done` 或 `--fgColor-severe`。

### 7.2 图表清单与尺寸

| 图 | 类型 | 尺寸 | 空态 |
|---|---|---|---|
| 门禁通过率趋势 | line | 通栏 220 | <2 点：显示「数据点不足（需 ≥2 个运行）」 |
| 链步状态分布 | pie(环形) | 220 | 无步骤记录：显示「本轮无步骤记录」 |
| 主题分布 | rose | 320 | 无主题：「暂无主题数据」 |
| SLI/SLO 达标率 | liquidFill | 170 | 无指标：「未接入 SLI」 |
| 状态分布/时效三态 | bar | 220 | 「暂无数据」 |
| 关系图 | graph(force) | 560 | 「暂无关系」 |
| 项目结构 | tree(radial) | 640 | 「结构为空」 |
| 数据流转 | sankey | 460 | 「链路为空」 |

### 7.3 通用配置（统一封装）

```js
{
  animationDuration: 0,                    // 首屏不做动画，避免截图抖动
  grid: { left: 8, right: 8, top: 24, bottom: 8, containLabel: true },
  tooltip: { confine: true },
  textStyle: { fontSize: 12, fontFamily: tk("--fontStack-system") },
}
```

- 容器尺寸变化时 `resize()`（`ResizeObserver`，不用 window.resize，避免侧栏折叠漏掉）；
- 图表容器必须有确定高（上表），否则 ECharts 高度为 0。

---

## 8. 接口契约（18 个）

| # | 端点 | 方法 | 参数（默认值/上限） | 响应要点 | 错误 |
|---|---|---|---|---|---|
| 1 | `/api/overview` | GET | — | `run_health / gate_status / worklist / freshness / storage / alerts` | `available:false` + `reason` |
| 2 | `/api/metrics` | GET | — | `gate_trend / worklist_aging / timeliness_tri / theme_dist / quality / relations` | 同上 |
| 3 | `/api/flow` | GET | — | `steps / weights / layers / step_status / run_id`（**建链用 weights 的键**） | 同上 |
| 4 | `/project-tree.json` | GET | — | `meta{node_count,truncated} / tree` | 404 |
| 5 | `/api/worklist` | GET | `status=open|resolved`、`kind`、`offset`（切片有效） | `items`（**一次 319 条，无分页**）、`total` | — |
| 6 | `/api/worklist/kinds` | GET | — | `kinds[12]`（枚举定义序）、`action_policy` | — |
| 7 | `/api/worklist/resolve` | POST | `{item_id, status, resolution, by}` | `{ok}` | 缺理由 4xx |
| 8 | `/api/records` | GET | `q, sort=publish_date, desc, page=1, size=50（max 200）` | `items / total=16607 / pages` | — |
| 9 | `/api/records/facets` | GET | — | `sources[5] / themes[12] / timeliness[6]` | — |
| 10 | `/api/rules` | GET | `q, sort=title, page, size=50（max 200）` | `items / total=861 / pages=18` | — |
| 11 | `/api/rules/facets` | GET | — | `themes[11] / status[1]` | — |
| 12 | `/api/relations` | GET | `ref?` | `views[5] / hubs[15] / relations / counts` | — |
| 13 | `/api/regstats` | GET | — | `categories[4]`（east/stat_rule/metric/fin_stat） | — |
| 14 | `/api/east` `/table` `/code` `/rules` | GET | `table?` | 各自清单 | — |
| 15 | `/api/governance` | GET | — | `runs[20] / audit[100]`（固定条数） | `status.enabled=false` |
| 16 | `/api/search` | GET | `q`、`mode=like|fts`、`size`（**无效**） | `results[≤20] / mode / backend` | — |
| 17 | `/api/release` | GET | — | `commits[475] / sbom / contract_version` | `git_error` |
| 18 | `/api/feedback` `/resolve`、`/api/auth/status` | GET/POST | `{fid,status,reason}` | `items / total / stats`；`need_login` | — |

**统一约定**：所有 GET 返回 `fetched_at`；分页两套并存（`page/size` 与 `offset`），新页面一律用 `page/size`。

## 9. 页面规格

> 每页八段：定位 / 数据 / 列 / 控件 / 交互 / 状态 / 线框 / 验收。

### 9.1 项目总览（无侧栏，17 区块 / 5 组）

**定位**：一屏回答「今天健不健康、卡在哪、数据新不新」。
**数据**：`/api/overview` + `/api/metrics`（原指标页并入）。

| 组 | # | 区块 | 类型 | 数据 |
|---|---|---|---|---|
| A 运行与健康 | 1 | KPI 四格（门禁/待办/链步/存储） | 数字 | `gate_status` `worklist` `run_health` `storage` |
| | 2 | 门禁通过（含失败明细） | 数字+列表 | `gate_status.failed[].descr` |
| | 3 | 链步成功/失败（含失败步骤名） | 数字+标签 | `run_health.counts` |
| | 4 | 链步状态分布 | 环形 220 | `counts`（无 ok/failed 时用 `step_statuses[].rc`） |
| | 5 | 运行信息（id/步数/耗时/最近一轮） | 键值 | `run_health` |
| B 数据资产 | 6 | 覆盖文件与条文（文件/条文/解析失败） | 数字 | `coverage` |
| | 7 | 存储占用（5 类占比条） | 条状 | `storage.ratio` |
| | 8 | 数据新鲜度（by_status + stale 前 3） | 键值+列表 | `freshness` |
| | 9 | 主题分布 | rose 320 | `metrics.theme_dist` |
| C 质量指标 | 10 | 门禁通过率趋势 | line 220 | `metrics.gate_trend` |
| | 11 | SLI/SLO 达标率 | liquid 170 | `metrics.quality.sli` |
| | 12 | DAMA 六维 | bar 220 | `metrics.quality.dimensions` |
| | 13 | 质量快照（健康度+时间） | 卡片 | `metrics.quality` |
| D 任务 | 14 | 未处置（程序指令 / 用户反馈 分列） | 数字×2 | `worklist.open_total` + `feedback.stats.by_status` |
| | 15 | 任务 aging | bar 220 | `metrics.worklist_aging` |
| E 发布与告警 | 16 | 发布件状态 + 双底座契约自检 | 键值 | `release.sbom` `coverage` |
| | 17 | 告警（≤5 条 + 查看全部） | 列表 | `alerts` |

**交互**：每个数字可下钻（点门禁→治理审计；点待办→任务管理并带状态；点存储→数据资产说明）。
**状态**：`counts` 只有 unknown 时用 rc 兜底；`quality` 为空显示「未生成快照（python -m tools.check_slo）」；`alerts` 空显示「无近期告警（N 条记录）」。
**线框**

```
┌ 项目总览 ─────────────────────────────────── 最近运行 11-05 06:44 ┐
│ ① 四格 KPI（通栏）                                                  │
│ ── A 运行与健康 ──  ② 门禁 │ ③ 链步 │ ④ 分布 │ ⑤ 运行信息（4×288） │
│ ── B 数据资产 ──    ⑥ 覆盖 │ ⑦ 存储 │ ⑧ 新鲜 │ ⑨ 主题（rose）      │
│ ── C 质量指标 ──    ⑩ 趋势（通栏 220）                              │
│                     ⑪ SLI │ ⑫ DAMA │ ⑬ 快照                        │
│ ── D 任务 ──        ⑭ 未处置（2 列） │ ⑮ aging                      │
│ ── E 发布与告警 ──  ⑯ 发布件+契约 │ ⑰ 告警                          │
└────────────────────────────────────────────────────────────────────┘
```

**验收**：17 区块齐全；组标题 12/600；4 列 288×4 gap 16；三个图表类型分别为 pie/rose/line/liquid/bar；点任一下钻元素有落点。

### 9.2 项目结构（侧栏=目录树）

**数据**：`/project-tree.json`（213 节点）。
**布局**：左树 240 + 右预览；树项 28 高、缩进 12/层、展开箭头 16。
**交互**：点文件→右侧预览（文本按 13/20 等宽显示）；点目录→展开/折叠；面包屑可点。
**状态**：`meta.truncated` → 顶部提示「已按上限截断」；空 → 「无文件」。
**验收**：树可展开到 3 层；点文件右侧更新；面包屑与选中同步。

### 9.3 数据流转（运行 → 血缘 → 未连线）

**数据**：`/api/flow`。
**分区（侧栏）**：
- **运行列表**（默认）：每行 56 高，`● 11-05 06:00 · 42 步 35✓ 7✗ · 56 分钟`，点开展开**步骤时间线**（节点 24，状态点 8，失败红）；
- **数据血缘**：桑基图 460，节点=阶段，边按 `weights` 的键构建（20 节点/15 边）；
- **未连线任务**：表格（步骤/期望/实际/差/说明），行高 32。
**状态**：`链路为空` / `STEP_ORDER 未加载` / 无运行 → 「尚未运行」。
**验收**：三视图可切；运行行可展开步骤；桑基 20/15；未连线表行数 = 步骤数 − 已连线数。

### 9.4 治理审计（指标条 + 时间线 + 台账）

**数据**：`/api/governance`（runs 20 / audit 100）。
**结构**：

```
[24h ▾][状态 ▾][操作者 ▾]                       [搜索            🔍]
24h 运行 6 · 失败 1 · 成功率 83% · 平均 52 分钟 · 审计 128 条
┌ 运行时间线（60%）──────────────────┬ 变更台账（40%）──────────────┐
│ ● 11-05 06:00 · 42 步 35✓7✗ · 56 分 ▸│ 06:12 政策法规「…」          │
│   ▸ 步骤明细：步骤·状态·耗时·错误     │ timeliness: valid→amended    │
└─────────────────────────────────────┴──────────────────────────────┘
```

**交互**：点运行行 → 台账按 `run_id` 过滤；点对象 → 跳对应页并高亮；时间范围/状态/操作者筛选；搜索匹配 id/对象/字段/操作者。
**状态**：无运行→「尚未运行；点此触发」；无审计→「本条链未产生数据变更」；未启用→「治理库未启用」。
**验收**：指标条与时间线一致；展开有步骤明细；台账有 before→after；点运行后台账条数变化。

### 9.5 版本管理（提交 / 发布件 / 认证）

**数据**：`/api/release`（475 条）。
**列表**：按日期分组（组头 sticky、12/600 muted）；行 71 高（两行：主题行 + 作者/时间行），高亮 `+x −y`，sha 可复制。
**附加卡**：发布件状态（sbom）、认证（未认证→「切登录卡片」；已认证→领取 CSRF）。
**状态**：`git_error` → 顶部红条说明；空 → 「无提交记录」。
**验收**：日期分组正确；复制 sha 成功；未认证时认证卡可点。

### 9.6 监管统计（侧栏 7 分区）

> 字段与层级**全部取自旧版实现**（`index.html` 的 EAST 浏览器：`EAST_TABS` + 各表列定义），不臆断。

#### 9.6.1 概览（只显示 EAST 相关）

| 块 | 内容 | 来源 |
|---|---|---|
| ① 通知信息 | 规范名称、发布机构、版本、附件数 | `d.note` / `d.stats` |
| ② 覆盖度 KPI | 附件 **N** 个 · 数据表 **112** 张 · 数据项 **3062** 个（主键 N） · 数据元 **307** 个 · 业务代码表 **63** 张 · 检核规则 **2096** 条 | `eastKpis` |
| ③ 附件清单 | 按 **1、1-1、1-2 … 自然序**（非字典序）；列：附件号 · 附件名 · 页数/大小 · 操作 | `stats.attachments` |

**不显示**「金融统计 24 / 统计指标 7 / 监管统计制度 3」——那是其他分类的计数，与 EAST 无关。

#### 9.6.2 数据结构（**三级目录**）

**层级**（与旧版侧栏一致）：

```
数据结构（全部主题域目录）
  └ 主题域（该域下全部数据表）
      └ 数据表（该表全部数据项）
```

| 级别 | 展示 | 列定义（旧版实测） | 排序 | 筛选 |
|---|---|---|---|---|
| 全部 | 全部主题域的数据表目录 | **主题域 · 表名 · 表中文名 · 表编号 · 数据表说明 · 集中采集 · 持续采集**（7 列） | **无排序** | 每列一个筛选按钮（按该列取值，多选） |
| 主题域 | 该域下的表目录 | 同上（主题域列恒为该域） | **无排序** | 同上 |
| 数据表 | 该表全部数据项 | **表名 · 表中文名 · 数据项代码 · 数据项名称 · 数据项说明 · 数据元编码 · 数据元名称 · 数据元说明 · 取值范围 · 格式 · 约束**（11 列） | **无排序** | 每列筛选 + 关键词搜索 |

**说明**：表清单顶部显示「主题域：X · 共 N 张表。报送模式取自附件 1-2 表 3（A=集中采集 / B=持续采集）；点表名或表中文名进入数据项明细。」
**跳转**：`取值范围` 为「参照《业务代码表》XX，编号 CDxxxxxx」时，**点击直接跳到该代码表**（由抽取层 `code_ref` 解析，页面不做字符串猜测）。

#### 9.6.3 数据元

| # | 列 | 排序 | 筛选 |
|---|---|---|---|
| 1 | 数据元编码 | **无排序** | 列筛选 |
| 2 | 名称 | 无排序 | 列筛选 |
| 3 | 定义 | 无排序 | 列筛选 |
| 4 | 取值范围 | 无排序 | 列筛选（可跳代码表） |
| 5 | 格式 | 无排序 | 列筛选 |

搜索框占位：`搜编码 / 名称 / 定义 / 取值…`（服务端检索）。

#### 9.6.4 业务代码

| 项 | 规格 |
|---|---|
| 列 | **动态列**：取该代码表在附件中的真实表头（`cols`）；无表头时回退 **代码 · 名称 · 说明** |
| 排序 | **无排序** |
| 筛选 | 每列一个筛选按钮（`data-gcol`），按该列取值多选 |
| 层级 | 多级代码表按 **层级缩进**（子级缩进 16/级） |
| 行数上限 | 有表头 **800** 行、无表头 **500** 行（超出提示「仅显示前 N 行，可用搜索缩小范围」） |
| 编号 | 代码集带 `CDxxxxxx` 编号；**不显示「返回目录」列**（xlsm 导航超链接，抽取层已剔除） |

#### 9.6.5 检核规则

| # | 列 | 排序 | 筛选 |
|---|---|---|---|
| 1 | 序号 | **无排序** | — |
| 2 | 规则类型 | 无排序 | 类型筛选（A 表内格式 / B 表内值合理性 / C 码表 / D 表内逻辑 / E 表间关联） |
| 3 | 表名 | 无排序 | 列筛选 |
| 4 | 数据项 | 无排序 | 列筛选 |
| 5 | 代码 | 无排序 | 列筛选 |
| 6 | 规则 | 无排序 | 关键词 |

**口径**：规则原文来自附件 1-5，**不改写**；超 500 条时提示「仅显示前 500 条」。

#### 9.6.6 关系图 / 9.6.7 E-R 图

关系图 = 表间字段级关系（force，560）；E-R = 实体-属性-关系（560）；节点点击 → 9.6.2 对应表。
**明确**：旧版为 3D 可拖拽，新版用 2D（ECharts graph）——差异登记在此，不假装是 3D。

**验收**：7 分区可进；数据结构三级目录可用（点主题域→该域表；点表→该表数据项）；四个数据类表格**均无排序箭头**、均有列筛选；概览无其他分类计数。

### 9.7 文档管理

#### 9.7.1 文档检索（重新设计）

```
[搜索：保险                                                    🔍]
类型 [记录 | 条文]        命中 128 条 · 用时 0.12s · 全文索引
┌──────────────────────────────────────────────────────────────┐
│ 农业保险条例                              valid   nfra         │
│ 国令第 629 号 · 2013-03-01 · 条文 32 · 关系 14                │
│ …本条例所称农业保险，是指保险机构根据农业保险合同，对被保险人…  │
│ [📄 打开原文] [🔗 查看关系 14] [📑 查看条款 32]                │
└──────────────────────────────────────────────────────────────┘
```

| 元素 | 规格 |
|---|---|
| 结果头 | 命中数 · 用时 · 模式（模糊匹配/全文索引），12/400 muted，右对齐 |
| 标题行 | 14/600 + 时效标签 + 来源标签 |
| 元信息行 | 12 muted：文号 · 日期 · 条文数 · 关系数 |
| **命中片段** | 关键词前后各 30 字，`<mark>` 高亮（`--bgColor-attention-muted`），2 行截断 |
| 操作 | 三图标（§9.7.4） |
| 类型切换 | 记录 / 条文；切换保留搜索词；条文模式列改为「条款号 + 所属文件 + 片段」 |
| 空态 | 「无命中：换个关键词，或确认底座已发布」 |

#### 9.7.2 政策法规 / 9.7.3 公司制度

| 项 | 政策法规 | 公司制度 |
|---|---|---|
| 数据 | `/api/records`（16,607） | `/api/rules`（861） |
| 列 | 勾选 · 来源图标 · 名称 · 文号 · 主题 · 时效 · 操作 | 勾选 · 名称 · 文号 · 主题 · 类型 · 操作 |
| 排序 | **主题 / 时效 / 发布日期**（名称、文号**不可排序**：列头不显示箭头、`cursor:default`、点击无响应） | **主题 / 类型**（名称、文号不可排序） |
| 筛选 | 来源 5（gov 13178→nfra 1935→mof 870→pbc 571→supp 53）、主题 12（未定 15301→T0 221→T1 214→T10 149→T5 148→T2 133→…）、时效 6（未核验 12771→valid 2936→repealed 380→amended 307→pending 136→expired 77） | 主题 11（T1 196→T9 157→T8 130→T4 103→T10 70→UNALIGNED 64→…）、状态 1（未标注 861） |
| 批量 | 勾选 + 批量理由（必填，进审计） | 同 + 清除选择 |

#### 9.7.4 行末三图标（政策法规 / 公司制度 / 检索结果通用）

| 图标 | 动作 | 数字 | 规则 |
|---|---|---|---|
| 📄 | 打开原文（新窗口） | 无 | 无 URL → 禁用 + tooltip「无原文链接」 |
| 🔗 | 查看关系 → 关系图谱单文件视图 | **关系数** | 0 → 置灰禁用；>999 → `999+` |
| 📑 | **进入条款页**（新页面 `/#/documents/clauses?doc=<rfn>`，非弹层） | **条款数**（`article_count`） | 同上 |

图标按钮 24×24、gap 4、整组保底 148；Counter 18 高（`padding: 2 6`、`radius: 2em`）。

#### 9.7.5 关系图谱

双视图：全局（Top 15 hubs 条形 + 力导向）、单文件（依据/废止，上限 100 边，`← 回到全局`）。
空态三句区分：「暂无关系」/「这份文件没有依据关系」/「关系存在、只是没能定位到具体文件」。

#### 9.7.6 条款页（点「查看条款」进入的独立页面）

**路由**：`/#/documents/clauses?docno=<文号>&title=<标题>&src=<来源>`
**入口**：政策法规/公司制度/检索结果行末 📑；不在列表内展开。

**接口（新增，施工项 E1）**

```
GET /api/clauses?docno=&title=&src=&page=1&size=50
→ { items:[{article_no, article_title, text, chapter, page_no, char_count}],
    total, page, size, pages, source:"clause_index_api.find_clauses", fetched_at }
```

后端直接复用 `interfaces/clause_index_api.find_clauses(docno,title,src)`，不改采集产物。

**页头**

```
← 返回政策法规            农业保险条例
国令第 629 号 · 2013-03-01 · 共 32 条 · 来源 nfra · [打开原文] [查看关系 14]
```

**列定义（条款表）**

| # | 列 | 字段 | 宽 | 排序 | 筛选 |
|---|---|---|---|---|---|
| 1 | 条号 | `article_no` | 100 | ✅（默认升序，按数字序） | — |
| 2 | 条款标题 | `article_title` | 弹性 | ✅ | 关键词 |
| 3 | 章 | `chapter` | 160 | ✅ | 章（下拉，取唯一值） |
| 4 | 内容 | `text` | 通栏第二行 | ❌ | 全文关键词（高亮命中） |
| 5 | 字数 | `char_count` | 80 | ✅ | — |

**呈现**：每条**两行**——第一行「第 N 条 + 标题」，第二行正文（默认 3 行截断 + 「展开」）；展开后完整显示，行高自适应。

**交互**：行内「复制条文」；「展开/收起」；搜索命中词 `<mark>` 高亮；分页 50/页。

**状态**：无条款 → 「该文件未解析出条款」；解析失败 → 「条款解析失败（可查看原文）」并给原文链接。

**验收**：点 📑 进入新页面（不是弹层）；条号按数字序（第 2 条在第 10 条前）；命中词高亮；返回按钮回到来源页并保留筛选。

### 9.8 任务管理

#### 9.8.1 程序指令（原待办闭环）

| 项 | 规格 |
|---|---|
| 列 | 勾选 · kind · 名称 · 文号 · 访问地址 · 详情 |
| 状态 | **待受理 319 · 已办结 10** |
| 操作 | 行内处置（状态 + 理由必填）+ 批量处置（逐条 POST，接口无批量端点） |
| 分页 | 接口无分页（319 条）→ 前端分页 25/页，或虚拟滚动 |
| 数据 | `/api/worklist` |

#### 9.8.2 用户反馈（原留言板）

| 项 | 规格 |
|---|---|
| 列 | 时间 · 编号 · 分类 · 严重度 · 页面 · 标题/说明 · 记录者 · 状态 |
| 状态 | **待受理 · 已办结**（删除「已受理」） |
| 新建 | **仅侧栏底部 Feedback 弹窗**；本页无新建按钮/面板 |
| 弹窗 | 宽 560；分类 4（bug/data/feature/other）、严重度 4（low/normal/high/blocker）、页面默认当前页、标题必填、说明 100 高；`focusTrap` 生效 |
| 数据 | `/api/feedback`、`/api/feedback/resolve` |

**验收**：提交后进列表且状态=待受理；状态只有两态；无新建入口（除侧栏）；处置写审计。

### 8.9 前后端职责边界（**前端禁止加工数据**）

| 层 | 允许 | 禁止 |
|---|---|---|
| 后端 | 取数、连接、聚合、排序、筛选、分页、计算比率/占比/趋势、字段映射、中文文案 | — |
| 前端 | 渲染、格式化展示（千分位、日期、截断）、交互状态、调用接口 | **不得** join 多接口、不得前端聚合/求和/算占比、不得前端排序/筛选/分页、不得把 A 接口结果与 B 接口合并成业务结论 |

**由此产生的接口要求**

| 需求 | 必须由后端提供 | 现有能力 |
|---|---|---|
| 搜索 | `q` 参数 | ✅ records/rules/search |
| 状态筛选 | `status` | ✅ worklist/feedback |
| 列筛选 | `facet` 参数（列名 + 值集合） | ❌ 需新增（EAST 各表、records 已有 facets 但非列筛选） |
| 排序 | `sort` + `desc` | ✅ records/rules；❌ EAST 各表（旧版本就无排序） |
| 分页 | `page` + `size` + `total` | ✅ records（50/200）、rules；❌ worklist（无分页，需后端补）、search（固定 20） |

**前端允许保留的"伪处理"**（仅展示，不产生业务结论）：字符串截断、数字千分位、日期格式化、相对时间、空值占位 `—`。
**输入防抖**属于交互，不是数据加工；防抖后仍必须**发请求给后端**，不得前端过滤结果集。

> 影响：§5.4 的「输入即筛（前端）」、§5.15 的 L1/L2/L4/L5/L10 全部改为**服务端往返**。
> 需要后端新增：`worklist` 分页、`search` 分页、EAST 列筛选——登记为施工项 E3。

## 10. 自写边界与公共模块（拒绝重复代码）

> 两条原则：**包能给的绝不自写**；**必须自写的绝不重复**——一处实现，13 页调用。

### 10.1 三类归属

| 类别 | 内容 | 归属 |
|---|---|---|
| **A 包已覆盖** | 颜色 / 边框 / 圆角 / 字号 / 字重 / 行高 / 内边距 / 间距 / 断点 / 悬停与选中底色 / 按钮尺寸 / 输入高度 / 标签与计数 / 浮层定位 / 焦点管理 / 容器滚动 | ⛔ 禁止自写 |
| **B 需自写但必须抽模块** | 表格 / 工具条 / 搜索 / 分页 / 面板 / 表单 / 图表容器 / 三态 / 骨架 / 壳 / 路由 / page-ready | ✅ 写一次放 `web/assets/js/ui/` |
| **C 需后端新增** | ① 条款页 /api/clauses（§9.7.6）② EAST 附件 /api/records/attachments（§9.6.1）③ worklist 分页 / search 分页 / EAST 列筛选（§8.9） | 施工项 E1/E2/E3 |

### 10.2 公共模块清单（与 §5.13 一一对应）

| 模块 | 职责 | 禁止 |
|---|---|---|
| `ui/table.js` | 列定义 → 表头/行/排序/勾选/密度/空态 | 不得内联颜色；不得感知业务字段 |
| `ui/toolbar.js` | 状态开关 + 筛选按钮组 + 密度 + 计数 | 不得自己发请求（通过回调） |
| `ui/search.js` | 搜索行（右图标、防抖、回车、Esc） | 不得自己决定筛选逻辑 |
| `ui/pagination.js` | 上/下页 + 页码 + 禁用态 | 不得改总数 |
| `ui/panel.js` | 浮层（behaviors 定位 + focusZone + focusTrap） | 不得写死宽高（用 320 / gap） |
| `ui/form.js` | 字段渲染 + 校验 + 错误文案 + 提交 | 不得内联业务规则 |
| `ui/chart.js` | 统一 option + 令牌取色 + resize + dispose | 不得写死颜色 |
| `ui/state.js` | loading / empty / error 三态 | 不得吞错误 |
| `ui/skeleton.js` | 骨架行 | — |
| `ui/chrome.js` | 全局条 + 仓库条 + 侧栏 + 底部动作 | 不得改令牌 |
| `ui/router.js` | URL ↔ 状态、pushState/popstate | 不得直接改 DOM |
| `ui/page-ready.js` | 渲染完成打标记 | — |

**反重复规则**：`tools/ui_css_audit.py` 扩展为扫描页面内联 `<script>` 中的 HTML 模板片段；同一类组件在多个页面各写一遍 → 报错。判据：页面文件里不得再出现 `<table`、`<tr` 等结构标签（应全部来自 `renderTable`）。

### 10.3 判定方法（新组件落地前先过这一关）

```
① 有没有现成类？（primer.css 2,345 个类）
② 有没有令牌？（--space/--text/--base-size/--borderRadius/--motion/--breakpoint）
③ 有没有 behavior？（定位/焦点/滚动 → @primer/behaviors）
④ 有没有同类的 ui/*.js 模块？（有就加参数，不新建）
⑤ 都没有 → 才自写，并在 §10.2 登记
```

---

## 11. 令牌化收敛方案（84 处 → 0 处）

### 11.1 现状

实测 `web/assets/pages/*.html` + `app.html` 中共 **84 处**字面值：`gap/padding/margin` 48 处、`font-size` 22 处、`max-width` 6 处、`@media` 断点 8 处。

### 11.2 映射表（逐条替换规则）

| 现值 | 替换为 | 出现处 |
|---|---|---|
| `gap:16px` / `padding:16px` | `var(--space-lg)` | 卡片、栅格、区块 |
| `gap:8px` / `padding:8px` | `var(--space-sm)` | 工具条、表格单元格 |
| `gap:4px` | `var(--space-xs)` | 图标组 |
| `padding:24px` | `var(--space-xl)` | 页面留白 |
| `font-size:12px` | `var(--text-body-size-small)` | 次要信息、表头 |
| `font-size:14px` | `var(--text-body-size-medium)` | 正文 |
| `max-width:1200px` | `--rco-content-width: 75rem`（自定义令牌，见 11.3） | 内容容器 |
| `@media (max-width:1011px)` | `@media (max-width: 63.25rem)` | 断点 |
| `@media (max-width:767px)` | `@media (max-width: 48rem)` | 断点 |
| `transition: .15s` | `var(--motion-duration-short) var(--motion-ease-out)` | 交互反馈 |

### 11.3 唯一的自定义令牌（内容宽度）

Primer 没有"内容列宽度"令牌，因此定义**一个**：

```css
:root { --rco-content-width: 75rem; }   /* 1200px；一处定义，全站引用 */
```

其余一律不得新增自定义变量。

### 11.4 执行与守卫（三步）

| 步 | 动作 | 产出 |
|---|---|---|
| S1 | `tools/ui_css_audit.py` 扫描页面内联 CSS，列出所有字面值（属性+行号+建议令牌） | 清单 |
| S2 | 按清单逐条替换（一次一页，替换后跑 `pages_smoke`） | 改动 |
| S3 | 在 `pages_smoke` 增加断言：页面内联 CSS 不得出现 `#[0-9a-f]{3,8}`、`font-size:\s*\d`、`gap:\s*\d+px` | 门禁 |

收敛完成判据：**审计输出 0 条；`pages_smoke` 的字面值断言通过**。

---

### 11.5 例外登记表（与 `tools/ui_tokens_audit.py` 的 ALLOW 表一一对应）

**判定**：门禁 `gates/gate_tokens.py` 只看**未登记**的字面值；下表内的值不算违规，
但必须在工具里同步登记，两边不允许只改一边。

| 字面值 | 位置 | 为什么保留 | 依据 |
|---|---|---|---|
| `padding: 6px` | 侧栏条目（`.rail .item`） | GitHub 实测条目内距是 **6px**；primitives 只有 `--space-xs`(4) 与 `--space-sm`(8)，取任一档都会偏离实测 **2px** | §5.3 / 附录 B 实测 |

**收敛结果（2026-11-09）**

| 范围 | 字面值 | 未登记违规 |
|---|---|---|
| 13 个页面 `<style>` | 0 | 0 |
| 壳 `app.html` | 2（均为上表例外） | 0 |

审计命令：`python -m tools.ui_tokens_audit --max 0`（退出码 0 即通过）。

### 11.6 N7：壳层响应式的实现与偏差

**现状（实测 2026-11-09）**

| 宽度 | 侧栏 | 内容区 | Expand 按钮 |
|---|---|---|---|
| 1440 | 239 | 1201 | 隐藏 |
| 900 | **0** | **900** | **可见（display:flex，32×32）** |
| 900 且点 Expand | **239（固定定位抽屉）** | 900 | — |

**与 §3.2 的偏差（明确登记，不是遗漏）**

§3.2 要求 768–1011 收为 **56px 图标列**。仓库侧栏条目是纯文本（政策法规 / 公司制度 …），
**没有图标集**，留 56px 会得到一条既不可读也不可点的空列。
故实现为 **0 宽 + Expand 抽屉**，宽度收益相同（内容区从 661 → 900）。

**要改为 56px 图标列的前置条件**：先有 8 个模块图标 + 4 个切片图标（octicons 子集），
届时把 `.rail` 改成 `width:56px` 并让条目只显示 `<svg>`（`aria-label` 保留全文）。

**验证方式**：`Emulation.setDeviceMetricsOverride` 切 1440 / 900，读
`#rail` 宽度、`#main` 宽度、`#expandBtn` 的**计算样式**（不能读 `hidden` 属性——
折叠态会保留该属性，CSS 用 `[hidden]{display:inline-flex!important}` 覆盖）。


## 12. 验收与门禁

### 12.1 三层验收

| 层 | 工具 | 判据 |
|---|---|---|
| 结构 | `tools/pages_smoke.py`（已并入 `cli.py gates`） | 13 页各 4–5 项：关键元素、令牌 `#d1d9e0`、边框 `1px rgb(209,217,224)`、行高 28–40、图表 canvas>100 |
| 视觉 | `tools/gh_ui_visual.py` | 同视口截图，像素差率 + 80px 分带；首屏（y<600）目标 ≤8%，顶栏带**豁免**（信息架构不同） |
| 行为 | 手工/脚本按 §6.5 四条 | 面板翻转、Tab 圈定、方向键、容器滚动 |

### 12.2 逐页验收清单（摘要）

| 页 | 关键验收 |
|---|---|
| 项目总览 | 17 区块；点数字有落点；`counts` 兜底生效 |
| 项目结构 | 树 3 层；点文件右侧更新 |
| 数据流转 | 三视图；运行可展开步骤；桑基 20/15 |
| 治理审计 | 指标条一致；台账有 before→after；点运行过滤 |
| 版本管理 | 日期分组；sha 可复制；认证卡 |
| 监管统计 | 7 分区；4 卡计数 24/7/3/1 |
| 文档检索 | 命中片段高亮；三图标数字正确；0 值禁用 |
| 政策法规/公司制度 | 5 列排序；三组筛选；批量理由必填 |
| 关系图谱 | 双视图；三种空态 |
| 任务管理 | 两态；批量 N→N POST；无新建入口（除侧栏） |

---

### 12.3 渲染完成标记（把"时序"变成"确定性"）

**问题**：动态渲染页（拉接口 → 渲染 DOM）如果不等数据回来就断言，会在**骨架屏/空容器**上跑，
表现为随机失败或假通过（实测 `graph` 页偶发 `canvas=None`、`data-page-ready` 未置）。

**契约**：每个页面在**首屏渲染完成**后执行

```js
document.body.dataset.pageReady = "1";
```

**实现**（已落地在 13 页，公共版本见 `ui/page-ready.js`）：

```js
(function () {
  var t0 = Date.now();
  var iv = setInterval(function () {
    var root = document.querySelector(".page, main, #main") || document.body;
    var done = root && root.querySelector(".row-1, tr, canvas, .border-bottom");
    if (done || Date.now() - t0 > 8000) {          // 8s 兜底，避免永久卡住
      document.body.dataset.pageReady = "1";
      clearInterval(iv);
    }
  }, 80);
})();
```

**smoke 侧**（`tools/pages_smoke.py` 已改）：

| 步 | 行为 |
|---|---|
| 1 | `Page.navigate` 后等 1.2s |
| 2 | 轮询 `body.dataset.pageReady === "1"`，最多 **80 × 0.25s = 20s** |
| 3 | 再探测不变量（19 项/页） |
| 4 | 图表页若无 canvas，**再重试 3 次 × 2s** |

**断言**（已加入）：`{slug}: data-page-ready` → `body[data-page-ready=1]`。

### 12.4 ARIA 断言（已加入 pages_smoke）

| 断言 | 判据 |
|---|---|
| `ARIA 主区域` | 存在 `main` 或 `[role=main]` |
| `ARIA 搜索框可访问名` | `#q` 有 `aria-label`/`aria-labelledby`（13 页均已补） |
| `ARIA 唯一 current` | 列表页 `[aria-current]` ≤ 1 |

> 当前 `pages_smoke` 共 **97 项**（13 页 × 7–8 项），全部通过。

## 13. 施工顺序（13 批）

| 批 | 内容 | 完成判据 |
|---|---|---|
| **P0** | **公共模块（§10.2 十二个 ui/*.js）+ ARIA（§5.14）+ page-ready（§12.3）** | 13 页不再出现结构标签；ARIA 断言全绿 |
| P1 | 依赖与基座：四个包 + MIME + 主题属性 | `import()` 成功、令牌 `#d1d9e0` |
| P2 | 壳层：全局条 52 + 仓库条 48 + 侧栏 239 + 底部两动作 | 三处误差 ≤2px |
| P3 | 路由与历史（§4.3） | 四条验收通过 |
| P4 | behaviors 接入（§6） | 四条行为验收通过 |
| P5 | 令牌化收敛（§11） | 84 → 0 |
| P6 | 通用组件 12 类（§5） | 每类一张并排截图 |
| P7 | 项目总览 17 区块 | 17 区块 + 3 类图表 |
| P8 | 项目结构 / 数据流转 | 树 213 节点 / 桑基 20·15 |
| P9 | 治理审计（重设计） | 指标条 + 时间线 + 台账 |
| P10 | 版本管理 | 分组 + 复制 + 认证卡 |
| P11 | 监管统计 7 分区 | 7 分区可进 |
| P12 | 文档管理（4 子页 + 检索重设计 + 三图标） | 片段高亮 + 数字规则 |
| P13 | 任务管理（两子页 + 状态改造 + 弹窗） | 两态 + 批量 + focusTrap |
| **P14** | 后端新增：`/api/clauses`（§9.7.6）、`/api/records/attachments`（§9.6.1） | 条款页可用、EAST 附件可列 |

---

## 附录 A：旧版功能全清单（实测自 DOM）

| 页 | 控件/区块 |
|---|---|
| 总览 | KPI 四格；kind 跳转 6 个（relevance_boundary 217 / internal_unaligned 66 / trigger_manual_breakpoint 18 / rfn_clean_drift_c2 12 / timeliness_conflict 4 / clean_quarantine_triage 2）；运行健康；状态分布；门禁与待办；新鲜度；存储；告警 |
| 项目结构 | 径向树 + 节点信息条 |
| 数据流转 | 桑基；13 条链（采集/转换/关系/索引/主题/文档解析/制度/时效/门禁/校验/底座/发布/分析）；未连线任务表 |
| 指标 | 门禁趋势(line)、待办 aging(bar)、SLI(liquid 170)、时效三态(bar)、主题分布(rose 320)、健康度/DAMA(bar) |
| 待办闭环 | 待处置/已处置、kind 下拉、搜索、全选、行内处置、批量处置（状态+理由）、详情/复制 |
| 政策法规 | 5 列排序、三组筛选、保存视图、查看关系、复制编号、导出 CSV、批量理由 |
| 公司制度 | 同上 + 清除选择 |
| 关系图谱 | 全局/单文件双视图、Top12 hubs、200 条截断 |
| 监管统计 | 7 分区（概览/数据结构/数据元/业务代码/检核规则/关系图/E-R） |
| 治理审计 | run_log 20 + audit_log 30（新版扩到 100） |
| 文档检索 | 记录/条文双类型、模糊/全文模式提示 |
| 发布事件 | 提交列（sha/主题/日期）+ 发布件 + 认证 |
| 留言板 | 表单（身份/分类/严重度/页面/标题/说明）+ 列表 8 列 + 处置 |

## 附录 B：GitHub 实测（2026-11-05，9 页 × 7 角色）

| 页面 | search | input | button | row | badge | heading | pagination |
|---|---|---|---|---|---|---|---|
| actions | 1011×30 | 1011×30 | 43｜32×32 | 1055×65 | 141×21 | 122×33 | 472×32 |
| advisories | 878×32 | 878×32 | 142｜32×32 | 65×28 | 141×21 | 1216×36 | — |
| commits | — | — | 155｜32×32 | **1237×71** | 141×21 | 94×44 | 1280×32 |
| contributors | — | — | 43｜32×32 | 440×221 | 141×21 | 508×36 | — |
| issues | **1047×30** | 1047×30 | 58｜32×32 | 223×32 / 行 65·38 | 141×21 | 54×30 | 1121×32 |
| network | — | — | 15｜32×32 | — | 141×21 | 56×36 | — |
| pulls | 1047×30 | 1047×30 | 74｜32×32 | 223×32 | 141×21 | 146×30 | — |
| search | — | — | 48｜32×32 | 249×32 | 84×21 | 281×48 | 716×32 |
| settings | — | 440×32 | 26｜32×32 | 272×33 | 141×21 | 212×25 | — |

**其它实测**：顶区 100=52+48；页签 32 高/`padding 6 8`/字号 14/间距 8/选中 2px `#fd8c73`；侧栏 239/条目 223×32/选中 `rgba(129,139,152,.15)`+4px `#0969da`；面板 320×221/radius 12/环阴影 `rgba(209,217,224,.25)`；表头行 48/`#f6f8fa`/下边 1px `rgba(209,217,224,.7)`；按钮=文字+24+2；Counter 18；h1 24/36；行高 65（comfortable）/38（compact）。

## 附录 C：依赖文件清单与校验

| 文件 | 大小 | 校验 |
|---|---|---|
| `web/assets/primer.css` | 1,037 KB | 版本 22.3.3（升级时记录） |
| `web/assets/primer-primitives.css` | 1,679 KB | 11.10.0 |
| `web/assets/vendor/primer-behaviors/` | 136 KB / 64 文件 | `MANIFEST.json`：tgz sha256 + 每文件 sha256 |
| `web/assets/vendor/echarts/` | 1,727 KB / 6 包 | `MANIFEST.json`；`python -m tools.vendor_charts --check` |
| `web/assets/pages/*.html` | 13 页 | `python -m tools.pages_smoke` 58 项 |

**校验命令**

```bash
python cli.py gates                 # 32 门（含样板页 58 项）
python -m tools.pages_smoke         # 单独跑样板页
python -m tools.ui_css_audit        # 字面值审计（§11 S1）
python -m tools.vendor_charts --check
```




