# GitHub Issues 基线（实测记录）

> 目的：把"照 GitHub 做"从口号变成**可核对的数值**。任何一次 UI 改动都应先看这张表，
> 再决定改不改（避免"记忆里的 GitHub"和"现在的 GitHub"打架）。

- 抓取对象：`https://github.com/notepad-plus-plus/notepad-plus-plus/issues`
- 抓取时间：2026-10-27（页面 HTML 358,894 B；样式表 53 个，按需下载 60 余份）
- 抓取方式：`Invoke-WebRequest` 存原始 HTML → 从 `<link rel=stylesheet>` 取 CSS →
  在 CSS 里按 HTML 中出现的 hash 类名定位组件规则

## 一、可直接引用的数值

| 项 | 实测值 | 出处（在页面 CSS 里） |
|---|---|---|
| 全局头部高度 | **56px**（`padding-block:12px` × 2 + 32px logo） | `.HeaderMktg{padding-top/bottom:var(--base-size-12)}` + 32px octicon |
| 全局头部底色（light 主题） | **#25292e**（深灰，**不是**浅灰） | `--header-bgColor` |
| 页面底色 / 卡片底色 | `#ffffff` / `--bgColor-muted #f6f8fa` | `--bgColor-default` / `--bgColor-muted` |
| 边框（默认 / 更轻） | `#d1d9e0` / `#d1d9e0b3` | `--borderColor-default` / `--borderColor-muted` |
| 次要文字 | `#59636e` | `--fgColor-muted` |
| 正文字号 | **14px**（`--text-body-size-medium = .875rem`） | `--base-text-size-sm` |
| 元数据字号 | **12px**（`--text-body-size-small = .75rem`） | `--base-text-size-xs` |
| 圆角（小/中/大） | 3px / **6px** / 12px | `--borderRadius-*` |
| 控件高度（small/medium） | 28px / **32px** | `--control-small-size / medium-size` |
| 间距刻度 | 4 / 8 / 12 / 16 px | `--base-size-4/8/12/16` |
| 列表容器 | `border:1px solid var(--borderColor-muted)` + `radius:6px` + `overflow:hidden` | `.listContainer__NnQKCt` |
| 列表行 | `min-height:2rem`(32px)、`font-size:14px`、`gap:4px` | `.listItem__X41oTe` |
| 行内网格 | `selection \| status \| primary \| metadata \| actions` | 同上（`grid-template-areas`） |
| 行内元数据 | `font-size:12px`、muted、`gap:4px`、**与标题同行** | `.metadata__lB1fsH.secondary` |
| 紧凑行 | `min-height:1rem`，单行（metadata 并到同一行） | `.listItem:where(.compact)` |

## 二、结构事实（比数值更容易被忽略）

1. **头部深、内容浅**：GitHub 的 light 主题头部是深灰 `#25292e`，第二层（仓库页签）才是白底。
2. **仓库页签**：`UnderlineNav`，16px 图标 + 14px 文字 + 计数徽章，激活项**橙色下划线** `#fd8c73`，
   容器 `overflow` 由组件自己处理（放不下时折叠成 More，**不出横向滚动条**）。
3. **Issues 列表没有表头**：它是一串"行"，不是表格；行首 = 勾选框 + 状态圆点，
   标题后**同一行**跟 `#编号 · 时间 · 作者 · 标签`，行尾是评论数图标。
4. **列表容器只有一个框**：1px + 6px 圆角；内部不再套卡片。
5. **标签**：12px、18px 高、999 圆角、带 1px 边（不是纯色块）。

## 三、本仓的取舍（为什么没有 1:1 复刻）

| 差异 | 我们的做法 | 原因 |
|---|---|---|
| 头部高度 | **52px**（实测 56px） | 用户在规范里明确"精确锁定 52px"；高度由 `--header-height` 一个变量控制，改一行即 56 |
| 边框/次要文字 | `#d0d7de` / `#656d76`（实测 `#d1d9e0` / `#59636e`） | 用户逐条写死；与实测差 1–2 个色阶，肉眼不可辨 |
| 列表有表头 | 保留**轻量表头**（12px、muted、1px 下边框、无灰底） | 业务需要"字段菜单（隐藏/排序/筛选）+ 拖拽换列"，GitHub 的纯列表没有这一层 |
| 行尾动作 | 显示「关系」入口 | 业务动作（跳到关系图谱）在 GitHub 无对应物 |
| 左侧栏 | 保留（本页第二维 + Saved Views） | 用户明确要求左侧栏 |

## 四、复核方式

```bash
# 1) 重新抓一版基线（网络可用时）
Invoke-WebRequest https://github.com/notepad-plus-plus/notepad-plus-plus/issues -UseBasicParsing

# 2) 本地校验我们的实现是否仍符合本文档
python -m pytest tests/test_console_ui_flat.py -q
```
