# GitHub UI 基线（本地采集，供工作台复用）

- 采集时间：`2026-11-03T16:19:02`
- 视口：`1440x1000`（桌面）
- 采集器：`python -m tools.gh_ui_harvest`
- **登录态：未登录**（`meta[user-login]` 为空）—— 登录后才有的控件待补采，见文末边界
- 每页产物：`<slug>.json`（交互清单）+ `<slug>.html`（渲染后整页 DOM，已 gitignore）

## 页面清单

| slug | 标题 | 交互元素 | 同仓链接 |
|---|---|---:|---:|
| `repo` | GitHub - notepad-plus-plus/notepad-plus-plus:  | 208 | 49 |
| `issues` | Issues · notepad-plus-plus/notepad-plus-plus · | 265 | 23 |
| `pulls` | Pull requests · notepad-plus-plus/notepad-plus | 289 | 45 |
| `agents` | Page not found · GitHub · GitHub | 132 | 0 |
| `actions` | All workflows · GitHub | 254 | 21 |
| `projects` | Projects · notepad-plus-plus/notepad-plus-plus | 102 | 11 |
| `wiki` | Home · notepad-plus-plus/notepad-plus-plus Wik | 124 | 18 |
| `security` | Overview · notepad-plus-plus/notepad-plus-plus | 123 | 23 |
| `pulse` | Pulse · notepad-plus-plus/notepad-plus-plus ·  | 199 | 44 |
| `graphs-contributors` | Contributors to notepad-plus-plus/notepad-plus | 226 | 21 |
| `graphs-commit-activity` | Commits · notepad-plus-plus/notepad-plus-plus  | 160 | 15 |
| `graphs-code-frequency` | Code frequency · notepad-plus-plus/notepad-plu | 268 | 15 |
| `network-dependencies` | Dependencies · notepad-plus-plus/notepad-plus- | 123 | 20 |
| `network` | Network Graph · notepad-plus-plus/notepad-plus | 104 | 15 |
| `forks` | Forks · notepad-plus-plus/notepad-plus-plus ·  | 266 | 36 |
| `actions-metrics-usage` | Page not found · GitHub · GitHub | 132 | 0 |
| `actions-metrics-performance` | Page not found · GitHub · GitHub | 132 | 0 |

## 关键控件（实测结构与样式）

### 列表密度开关（表头右侧）

- 容器：`ul[aria-label="Issues display density"]`，prc-SegmentedControl-SegmentedControl-lqIXp，位置 `[1327, 405, 65, 32]`（left/top/w/h），**右端对齐**
- 按钮：`octicon-rows`，32×32，aria-pressed=`true`，tooltip=`Comfortable display density`
- 按钮：`octicon-three-bars`，32×32，aria-pressed=`false`，tooltip=`Compact display density`

### author 面板（单选）

- 尺寸：宽 `320`，圆角 `12px`，边框 `0px/rgb(31, 35, 40)`
- 阴影：`rgba(209, 217, 224, 0.25) 0px 0px 0px 1px, rgba(37, 41, 46, 0.04) 0px 6px 12px -3px, rgba(`
- 搜索框：placeholder `Filter authors`，放大镜在**左侧**
- 选项是否带勾选框：**否**（GitHub 规则：单选无勾选框、多选有）
  - `CopilotGitHubAI` · role=option · checkbox=False
  - `chcg` · role=option · checkbox=False
  - `donhoDon HO` · role=option · checkbox=False

### labels 面板（多选）

- 尺寸：宽 `320`，圆角 `12px`，边框 `0px/rgb(31, 35, 40)`
- 阴影：`rgba(209, 217, 224, 0.25) 0px 0px 0px 1px, rgba(37, 41, 46, 0.04) 0px 6px 12px -3px, rgba(`
- 搜索框：placeholder `Filter labels`，放大镜在**左侧**
- 选项是否带勾选框：**是**（GitHub 规则：单选无勾选框、多选有）
  - `duplicatePopular` · role=option · checkbox=True
  - `enhancementProposed enhanc` · role=option · checkbox=True
  - `rejectPopular` · role=option · checkbox=True

### 列表搜索框

- 输入框：left `293`、宽 `1047`、高 `30`；容器 `[280, 1370]`
- 聚焦：`outline: 2px/solid/rgb(9, 105, 218)`（**单层 2px**，无 box-shadow）
- 输入框底色：`rgba(0, 0, 0, 0)`（透明，底色由容器给）

### pulls 页表头筛选（与 issues 的差异）

| 按钮 | 单选/多选 | 面板搜索框 |
|---|---|---|
| Filter by author | 单选 | placeholder `Filter authors` |
| Filter by label | 多选 | placeholder `Filter labels` |
| Filter by reviews | 无搜索框 | 非 option 结构（菜单项，无勾选框） |
| Filter by assignee | 单选 | placeholder `Filter assignees` |

## 边界（没采到什么，为什么）

1. `agents`、`actions/metrics/usage`、`actions/metrics/performance` 三个 URL 在未登录态返回 **Page not found**（可能需登录或该仓库未启用），已如实记录标题。
2. 采集时为**未登录**状态（`meta[user-login]` 为空）：登录后才出现的控件（如 New issue 表单、部分 Actions 设置）不在基线内。
3. **不做递归爬取**：issues 有 2.9k 条、wiki 多页，递归会无限扩张。本基线记录每个页面**全部可见交互元素**与**全部同仓链接**（`same_repo_links`），需要某条链接的页面时再按需抓取即可，不会漏。
4. pulls 页的表头筛选与 issues 同源（Primer `SelectPanel`），故弹出层只在 issues 做结构化采集；pulls 的按钮清单在其 `<slug>.json` 里。
5. 动态图表（graphs/network）的数据点由 JS 绘制，基线里保留 DOM 与容器尺寸，不保留 canvas 像素。
