# 控件状态差分（默认 / 悬停 / 聚焦 / 按下）

- 采集时间：`2026-11-03T19:42:46`，视口 1440×1000，**已登录**
- 方法：CDP 真实派发 `mouseMoved / mousePressed / mouseReleased`，逐状态读 `getComputedStyle`，
  只记录**与默认态不同的字段**（含元素本身与最近一层「有背景/边框/阴影」的祖先）
- 原始数据：`states.json`

## repo

| 控件 | 默认 | 悬停 | 聚焦 | 按下 |
|---|---|---|---|---|
| top-search | bg=rgba(0, 0, 0, 0) | backgroundColor=rgba(255, 255, 255, 0) | backgroundColor=rgba(255, 255, 255, 0) · outlineColor=rgb(9, 105, 218) · outlineWidth=2px | backgroundColor=rgba(255, 255, 255, 0) · outlineColor=rgb(9, 105, 218) · outlineWidth=2px |
| tab-issues | bg=rgba(0, 0, 0, 0) | backgroundColor=rgba(129, 139, 152, 0.075) | 无变化 | 无变化 |

## issues

| 控件 | 默认 | 悬停 | 聚焦 | 按下 |
|---|---|---|---|---|
| list-search | bg=rgba(0, 0, 0, 0) | 无变化 | 无变化 | 无变化 |
| density-btn | bg=rgba(0, 0, 0, 0) | 无变化 | outlineColor=rgb(9, 105, 218) · outlineWidth=2px | outlineColor=rgb(9, 105, 218) · outlineWidth=2px |
| filter-author | bg=rgba(0, 0, 0, 0) | backgroundColor=rgba(129, 139, 152, 0.1) | backgroundColor=rgba(129, 139, 152, 0.1) · outlineColor=rgb(9, 105, 218) · outlineWidth=2px | backgroundColor=rgba(129, 139, 152, 0.15) · outlineColor=rgb(9, 105, 218) · outlineWidth=2px |
| tab-open | bg=rgba(0, 0, 0, 0) | backgroundColor=rgba(129, 139, 152, 0.1) | backgroundColor=rgba(129, 139, 152, 0.1) | backgroundColor=rgba(129, 139, 152, 0.15) |
| tab-closed | bg=rgba(0, 0, 0, 0) | backgroundColor=rgba(129, 139, 152, 0.1) | backgroundColor=rgba(129, 139, 152, 0.1) | backgroundColor=rgba(129, 139, 152, 0.15) |
| row-title | bg=rgba(0, 0, 0, 0) | backgroundColor=rgba(129, 139, 152, 0.1) · boxShadow=rgba(255, 255, 255, 0) 0px 0px 0px 1px inset · outlineColor=rgba(0, 0, 0, 0) | backgroundColor=rgba(129, 139, 152, 0.1) · boxShadow=rgba(255, 255, 255, 0) 0px 0px 0px 1px inset · outlineColor=rgba(0, 0, 0, 0) | backgroundColor=rgba(129, 139, 152, 0.15) · boxShadow=rgba(255, 255, 255, 0) 0px 0px 0px 1px inset · outlineColor=rgba(0, 0, 0, 0) |
| pagination | bg=rgb(9, 105, 218) | outlineWidth=0px | color=None · backgroundColor=None · boxShadow=None | color=None · backgroundColor=None · boxShadow=None |

## Author 筛选面板（展开前后）

- `aria-expanded`：None → None
- 面板：宽 `None`、圆角 `None`、边框 `None`
- 阴影：`None`
- 选项：`None`

