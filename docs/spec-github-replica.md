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
| 5 | 通用组件（12 类，含状态与验收） |
| 6 | 交互行为（`@primer/behaviors` 四个 API 的接入点） |
| 7 | 图表规范（ECharts 与令牌桥接） |
| 8 | 接口契约（18 个端点 + 参数默认值） |
| 9 | 页面规格（8 个导航项 + 5 个子页） |
| 10 | 必须自写的部分（清单 + 理由） |
| 11 | 令牌化收敛方案（84 处 → 0 处） |
| 12 | 验收与门禁 |
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
| 容器 | 高 48；水平排列；溢出横向滚动（隐藏滚动条）；底边 1px |
| 单项 | 高 32，`padding: 6px 8px`，字号 14，`border-radius: 6px`，间距 8 |
| 默认 | 无底色、无下划线 |
| hover | `rgba(129,139,152,.1)` |
| **选中** | **`::after` 高 2px、色 `#fd8c73`、贴底**；**字重不变（400）** |
| 计数 | 右侧 `Counter`（高 18、`padding: 2px 6px`、`border-radius: 2em`） |

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
| 行为 | 输入即筛（前端 220–300ms 防抖）+ 回车应用（服务端）；两者取**交集**，筛选后搜索词保留 |

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
| 行 | 每条 1px 底边；**行高按密度**：comfortable **65** / compact **38**（GitHub issues 实测）；纯文本列表用 32 |
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

| 分区 | 内容 | 数据 |
|---|---|---|
| 概览 | 4 张分类卡（金融统计 24 / 统计指标 7 / 监管统计制度 3 / EAST 1）+ 分类表 | `/api/regstats` |
| 数据结构 | 表 → 字段清单 | `/api/east/table` |
| 数据元 | 数据元 + 取值范围 | `/api/east` |
| 业务代码 | 代码表 + 编号示例 | `/api/east/code` |
| 检核规则 | 规则清单 | `/api/east/rules` |
| 关系图 | 表间关系（graph 560） | `/api/relations` |
| E-R 图 | 实体关系（graph 560） | `/api/relations` |

**验收**：7 分区均可进；分区切换不整页刷新；概览 4 卡计数与接口一致（24/7/3/1）。

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
| 排序 | 名称/文号/主题/时效/发布日期 | 名称/文号/主题/类型 |
| 筛选 | 来源 5（gov 13178→nfra 1935→mof 870→pbc 571→supp 53）、主题 12（未定 15301→T0 221→T1 214→T10 149→T5 148→T2 133→…）、时效 6（未核验 12771→valid 2936→repealed 380→amended 307→pending 136→expired 77） | 主题 11（T1 196→T9 157→T8 130→T4 103→T10 70→UNALIGNED 64→…）、状态 1（未标注 861） |
| 批量 | 勾选 + 批量理由（必填，进审计） | 同 + 清除选择 |

#### 9.7.4 行末三图标（政策法规 / 公司制度 / 检索结果通用）

| 图标 | 动作 | 数字 | 规则 |
|---|---|---|---|
| 📄 | 打开原文（新窗口） | 无 | 无 URL → 禁用 + tooltip「无原文链接」 |
| 🔗 | 查看关系 → 关系图谱单文件视图 | **关系数** | 0 → 置灰禁用；>999 → `999+` |
| 📑 | 查看条款 | **条款数**（`article_count`） | 同上 |

图标按钮 24×24、gap 4、整组保底 148；Counter 18 高（`padding: 2 6`、`radius: 2em`）。

#### 9.7.5 关系图谱

双视图：全局（Top 15 hubs 条形 + 力导向）、单文件（依据/废止，上限 100 边，`← 回到全局`）。
空态三句区分：「暂无关系」/「这份文件没有依据关系」/「关系存在、只是没能定位到具体文件」。

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

## 10. 必须自写的部分（其余一律取自四个包）

> 判据：**四个包能给的，一律不自写**；只有当"包管不到"或"业务特有"时才写，并在此登记。

### 10.1 必须自写（8 类）

| # | 内容 | 为什么包给不了 | 规模 |
|---|---|---|---|
| W1 | **栅格与列宽** | `primer.css` 只有 1–12 栅格工具类，没有「1200 居中 + 4 列 288」这类版面 | 每页 1–2 条 `grid-template-columns` |
| W2 | **内容宽度与居中** | 无 `max-width:1200` 类 | 全站 1 条 |
| W3 | **图表容器高度** | 图表高由信息密度决定（220/320/170/560/640），非令牌 | 每图 1 条 |
| W4 | **ECharts option** | ECharts 不是 Primer 组件；颜色需从令牌读取后传入 | 每图 30–60 行 |
| W5 | **页面级组合** | 「一页多表」「顶部状态条 + 主从两栏」等业务版式 | 每页 2–4 条 |
| W6 | **空/加载/错误态结构** | Primer 有样式无文案与业务语义 | 每页 3 段模板 |
| W7 | **路由与状态序列化** | `@primer/behaviors` 只管交互原语，不管 URL | 1 个模块（约 80 行） |
| W8 | **图标** | 未引入 octicons.js（408 KB）；当前用内联 SVG | 复用现有内联集 |

**不需要自写**（此前误写的部分）：颜色、边框、圆角、字号、字重、行高、内边距、间距、断点值、悬停/选中底色、按钮尺寸、输入框高度、标签与计数样式、浮层定位、焦点管理、容器内滚动。

### 10.2 判定方法（新组件落地前先过这一关）

```
① 有没有现成类？（查 primer.css 2,345 个类）
② 有没有令牌？（查 primitives：--space/--text/--base-size/--borderRadius/--motion/--breakpoint）
③ 有没有 behavior？（定位/焦点/滚动 → @primer/behaviors）
④ 都没有 → 才算自写，并在 §10.1 登记
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

## 13. 施工顺序（13 批）

| 批 | 内容 | 完成判据 |
|---|---|---|
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




