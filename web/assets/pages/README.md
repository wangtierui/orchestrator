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

---

## 第二个页面：待办闭环（复杂页验证）

pages/worklist.html —— 同样两条硬约束，同样的 8 条布局 CSS 思路。

| 项 | 实测 | 说明 |
|---|---|---|
| 行高 | **34**（GitHub 32，差 2 = 行内按钮 24 + 上下 4） | 行内动作按钮压到 24px |
| 数据 | 319 条 / 7 个 kind | /api/worklist?status=open |
| 交互 | 状态开关（待处置/已处置）、kind 下拉、搜索、分页、密度 | 全部接通 |
| 边框 | 1px 
gb(209,217,224) | 令牌 |
| 自写 CSS | 9 条，全布局 | 颜色/边框/圆角全走类名与令牌 |

**结论**：复杂页（带状态切换 + 下拉筛选 + 行内动作）同样可以用「Primer 类 + 布局 CSS」搭出来，
没有出现需要自写视觉才能实现的场景。这验证了迁移路径可行。

### 行内处置表单（已补）

点行内「处置」展开整行详情：处置状态（select）+ 处置理由（textarea）+「取消 / 提交」。

实测：label 在控件上方（stacked=true）、select **1142×32** 满宽、textarea **1142×102**、
两个按钮各 62 宽、文本框边框取 --borderColor-accent-emphasis（聚焦态）。

> 未提交任何真实数据：只验证了 UI 与 payload 结构，没有点「提交」（会写审计台账）。

---

## 第三页：公司制度（与法规同构）

pages/rules.html —— 从 laws 页替换 5 处（接口 /api/rules、列字段、默认排序）即完成，
证明**同构页迁移是复制+换字段，不是重写**。

| 项 | 实测 |
|---|---|
| 行高 | **32**（同 laws） |
| 数据 | 861 条 |
| 边框 | 1px 
gb(209,217,224)（令牌） |
| 首行样例 | 2021年团险业务佣金管理办法 · 阳光人寿发〔2021〕124号 · T1销售行为与消费者保护 · … · policy |

新增自写 CSS：**0 条**（完全复用 laws 的 8 条）。

---

## 第四页：文档检索

pages/search.html —— 从 laws 复制后再换 6 处（接口 /api/search、结果取 
esults 而非 items、
列改为 来源/发布日期、无 total 时隐藏分页、空关键词提示）。

| 项 | 实测 |
|---|---|
| 行高 | **32** |
| 结果 | 输入「保险」→ 16 条 |
| 首行 | 关于将三大粮食作物制种纳入… · 财金〔2018〕91号 · — · — · gov · 2018 |
| 新增自写 CSS | **0 条** |

**接口差异（需注意）**：/api/search 不返回 	otal/pages，只有 
esults 数组（默认 20 条上限），
因此该页**没有分页**——这是接口能力，不是设计遗漏。要支持翻页需后端补 total/offset。

---

## 第五页：留言板

pages/feedback.html —— 从待办复制，换 12 处（接口 /api/feedback、列改 分类/标题/记录者/严重度/时间/状态）。

| 项 | 实测 |
|---|---|
| 行高 | **32** |
| 数据 | 6 条 |
| 首行 | 缺陷 · CDP 自动化测试：反馈弹窗 · wangtierui-lhl · 一般 · 2026-10-29 · 待处置 |
| 新增自写 CSS | **0 条** |

**简化说明**：本页把「操作」列换成了「状态」列（只读展示）。行内处置表单在待办页已验证，
留言板复用同一套组件即可，未重复实现。

---

## 第六页：监管统计（一页多表）

pages/regstats.html —— 与前面几页结构不同：**一页 4 张表**（分类分组），每类一个独立卡片。

| 项 | 实测 |
|---|---|
| 分类 | EAST(1) / 监管统计制度(3) / 统计指标与报送(7) / 金融统计制度(24)，共 **35** 条 |
| 表盒 | **4** 个（每个分类一个 .border.rounded-2） |
| 行高 | **32** |
| 边框 | 1px 
gb(209,217,224)（令牌） |
| 分类筛选 | 选 in_stat → 24 条 / 1 个表盒 |
| 搜索 | 输入「统计」→ 34 条（跨分类即时过滤） |
| 新增自写 CSS | **3 条**（分类块间距、表头 flex、备注字号） |

**结构要点**：「一页多表」不需要特殊组件——每个分类就是一个「表头 + 行列表」，外面各自套
.border.rounded-2。这与 GitHub 用多个独立列表区块的做法一致，不是把多张表塞进一个大框。
