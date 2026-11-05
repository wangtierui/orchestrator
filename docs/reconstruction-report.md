# 工作台重构报告（阶段一）

> 基准：`docs/spec-github-replica.md` v10.2 ｜ 本次提交：`f488174`
> 结论先说：**规格已落地为可运行的公共模块与壳层能力，13 页中 1 页完成迁移，其余 12 页仍走旧渲染。**
> 未完成项逐条列在 §3，不含"大致完成"这类表述。

---

## 1. 任务清单与状态

| # | 优先级 | 任务 | 依赖 | 预期交付物 | 状态 |
|---|---|---|---|---|---|
| T1 | P0 | 依赖基座：css / primitives / behaviors / echarts 本地化 + MIME + 主题属性 | — | 四个本地包、`.mjs` 正确 MIME、`import()` 可用 | ✅ 完成（`e2f0fb9`） |
| T2 | P0 | 渲染完成标记 + smoke 时序与 ARIA 断言 | T1 | `data-page-ready`、97 项断言 | ✅ 完成（`04bd3bb`） |
| T3 | P0 | 公共 UI 模块（core/table/controls/chart/router） | T1 | `web/assets/js/ui/*.js` 五个模块 | ✅ 完成（`f488174`） |
| T4 | P1 | 壳层：behaviors 接入 + 折叠态 + 侧栏容器滚动 + More 下拉 | T3 | 可折叠侧栏、More 菜单、容器内滚动 | ✅ 完成（`f488174`） |
| T5 | P1 | 路由：URL 状态 + pushState/popstate + 深链 | T3 | `router.js` 已实现，**未接入壳层** | ⚠️ 模块就绪、未接线 |
| T6 | P2 | 页面迁移到模块（13 页） | T3 | 每页改用 `renderTable/renderSearch/...` | ⚠️ 1/13（laws） |
| T7 | P3 | 令牌化收敛（84 处字面值 → 0） | T6 | 审计 0 条 | ⚠️ 新模块已全令牌；旧页仍有字面值 |
| T8 | P4 | 门禁扩展（模块/ARIA/行高/字面值扫描） | T6 | 新断言全绿 | ⚠️ 行高与 ARIA 已加；字面值扫描未加 |
| T9 | P5 | 本报告 | 全部 | 报告文档 | ✅ 完成 |

---

## 2. 变更摘要

### 2.1 新增公共模块（`web/assets/js/ui/`）

| 文件 | 导出 | 关键约束 |
|---|---|---|
| `core.js` | `esc` `el` `token` `fmtInt` `debounce` `renderState` `renderSkeleton` `markReady` `when` | 三态带 `role=status/alert` + `aria-live` |
| `table.js` | `renderTable(cfg)` | 表头 48 + `--bgColor-muted`；行高严格 **65 / 38**；`aria-sort`/`aria-selected`；三态全选；视觉零自写 |
| `controls.js` | `renderSearch` `renderToolbar` `renderPagination` `renderForm` `openPanel` | `openPanel` 内用 behaviors：锚定定位 + `focusTrap` + `focusZone`，自动维护 `aria-expanded/controls` |
| `chart.js` | `palette` `baseOption` `mountChart` `chartHost` | 颜色从令牌取；`ResizeObserver` 自适应；`role=img` + `aria-label` |
| `router.js` | `parse` `build` `initRouter` | 兼容旧 `#page`；push/replace/popstate 三态 |

### 2.2 壳层（`app.html`）

| 能力 | 实现 | 实测 |
|---|---|---|
| 模块出口 | `<script type="module">` 引 behaviors + 五个模块 → `window.RCO` | `RCO` 键：`core,table,controls,chart,router,behaviors` |
| 侧栏折叠 | Expand 32×32（`aria-expanded`/`aria-controls`）+ Collapse（223×32） | 折叠后 `rail` 宽 **0**、Expand 可见、`localStorage` 记忆 |
| 侧栏滚动 | behaviors `scrollIntoView(target, #main)` | 只滚内容区 |
| 仓库条溢出 | More 下拉（测宽收折 + 当前项显示其名称 + `getAnchoredPosition(align:end)` + `focusZone` + `focusTrap` + Esc） | 1440 全显无 More；**900 宽隐藏 1 项并出现 More** |

**踩坑记录（已修）**：壳里 `renderRail` 直接写 `#rail.innerHTML`，把新增的侧栏底部动作一并清空，导致折叠按钮不存在、模块脚本在绑定处抛错。改为写 `#railItems` 后恢复。

### 2.3 样板页迁移：`pages/laws.html`

- 全部改用 `RCO`：搜索（防抖 220ms → **服务端**请求）、工具条（筛选面板走 behaviors）、表格、分页；
- 排序列只保留 **发布日期**（名称/文号不可排序，列头不渲染按钮）；
- 实测：**50 行、行高 65、density=comfortable、分页存在、`data-page-ready=1`**。

### 2.4 门禁

| 项 | 变化 |
|---|---|
| laws 选择器 | `.border-bottom.py-1` → `.rco-tbl__row` |
| 行选择器 | 兼容 `.rco-tbl__row` + `.rco-tbl__cell` |
| 行高判据 | 已迁移页严格 `38 | 65`；未迁移页暂按 28–70（**待迁移后收紧**） |
| 总状态 | **32/32 PASS**，`pages_smoke` 97 项 |

---

## 3. 未完成项（按依赖排序，可直接接着做）

| # | 事项 | 依赖 | 工作量 | 风险 |
|---|---|---|---|---|
| R1 | `router.js` 接入壳层（页面切换改 `#/page?qs`） | — | 中 | 与现有 `#page` 双轨期需兼容，否则书签失效 |
| R2 | 其余 12 页迁移到模块 | R1 | 大（每页 1 次重写 + 冒烟） | 行高从 32 → 65/38，一屏行数减半，用户观感变化需确认 |
| R3 | 反馈弹窗接 `focusTrap`（侧栏 Feedback 按钮目前无行为） | T4 | 小 | — |
| R4 | 令牌化收敛剩余字面值（旧页 84 处中的大部分） | R2 | 中 | 断点 1011/767 需改 1012/768，1px 会改布局 |
| R5 | 字面值扫描进 `pages_smoke`（禁 `#hex`、`font-size:数字`、`gap:数字px`） | R4 | 小 | 未清理前会让门禁长期红，需分页灰度 |
| R6 | 后端新增接口 E1/E2/E3（条款页、EAST 附件、worklist/search 分页、EAST 列筛选） | — | 中 | **前端无法自行补齐**；`/api/search` 固定 20 条、worklist 319 条无分页 |

---

## 4. 遗留风险

1. **规格与实现仍有 12 页差距**：`pages_smoke` 对未迁移页放宽了行高判据，这是"临时豁免"，不收紧就会长期掩盖差异。
2. **数据加工边界（§8.9）尚未在旧页执行**：worklist 前端分页、search 前端过滤仍存在，属于已知违背，需 E3 落地后统一改服务端。
3. **More 下拉的分支未被门禁覆盖**：目前靠手工实测（900 宽），没有自动化断言；窗口尺寸依赖 CDP 覆盖，容易回归。
4. **`window.RCO` 是过渡方案**：页面脚本以 classic 注入，无法 `import`；最终应改为 ESM 页面或把渲染函数移到模块内部。当前方案让"模块被调用"成立，但**没有强制"不许手写 HTML"**——需要 R5 的静态扫描才能约束。
5. **折叠态与 More 共存未验证**：两者都改变可用宽度，尚未测试"折叠 + 窄屏"组合。
6. **旧 `index.html`（326 KB / 162 函数）仍在**：`/legacy.html` 可访问，20 个守卫仍按它断言；迁移完成后需统一清理，否则门禁会一直锁在旧结构上。

---

## 5. 下一步建议（按投入产出）

```
R1（router 接线） → R2 先迁 3 页（rules/search/worklist，同构度最高）
→ R5（扫描进门槛，先只查新模块） → R4（旧页令牌化分批）
→ R3（弹窗 focusTrap） → R6 与后端并行
```

一句话：**这一阶段把"组件只写一次"的地基铺好并验证通了（1 页跑通、壳层 behaviors 生效、门禁未退化）**，但离"13 页全部迁移 + 边界收紧"还有 12 页的实际工作量。
