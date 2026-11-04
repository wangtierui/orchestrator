# 平行重建样板页（政策法规列表）

> 目的：在不动旧工作台的前提下，验证「Primer 单一真源 + 仅布局自写」能否达到 GitHub 的观感，
> 并把这套做法固化成后续页面迁移的模板。

## 规则（两条硬约束）

1. **页面根必须带主题属性**，否则 primitives 的主题令牌全部落空（`var()` 静默失效，边框/颜色变 0px/黑）：

```html
<html lang="zh-CN" data-color-mode="light" data-light-theme="light">
```

2. **只能用 `primer.css` 里真实存在的类名**。`@primer/react` 的组件名（`.Box` / `.Box-row` / `.Truncate` / `.Label` / `.FormControl`）在 CSS 里**不存在**。

| 想用的 | 实际存在 |
|---|---|
| `.Box` | `.border` + `.rounded-2` |
| `.Box-row` | `.border-bottom` + `.p-2` |
| `.Label` | `.IssueLabel` |
| `.Truncate` | 无（需自备 `overflow:hidden;text-overflow:ellipsis;white-space:nowrap`） |
| `.Box-header` | 存在（但 `.Box` 不存在，别混用） |

## 自写 CSS（8 条，全部是布局）

```css
body{background:var(--bgColor-default)}     /* 令牌，非色值 */
.page{max-width:1200px;margin:0 auto;padding:16px}
.qwrap{position:relative}
.qico{position:absolute;right:8px;top:50%;transform:translateY(-50%)}
.qinput{padding-right:30px}
.tbl{display:grid;grid-template-columns:240px 150px 96px 80px 120px minmax(0,1fr)}
.row-1{min-height:32px}
.cell{min-width:0;overflow:hidden}
```

## 实测（1440×900）

| 项 | 实测 | 来源 |
|---|---|---|
| 容器边框 | 1px `rgb(209,217,224)` | `--borderColor-default` = `#d1d9e0`（= GitHub 实采） |
| 圆角 | 6px | `--borderRadius-medium` |
| 表头底色 | `rgb(246,248,250)` | `--bgColor-muted` |
| 标签底色 | `rgb(221,244,255)` | `--bgColor-accent-muted` |
| 数据行高 | **38**（GitHub 32，差 6 = 上下各 8 的内边距） | `.p-2` |
| 列宽 | 240/150/96/80/120/**464** | 布局 |
| 数据 | 25 行 / 共 16,607 条 | `/api/records` |

## 已知偏差

- 行高 38 vs GitHub 32：把 `.p-2` 换成 `py-1 px-2`（若存在）可收敛到 ~30，待验证；
- 表头没有 GitHub 的「列内排序箭头」；
- 标签颜色用的是 accent 令牌（真实 GitHub 是按标签自定义色走 `--label-r/g/b`）。

## 怎么访问

```
http://127.0.0.1:8765/pages/laws.html
```
