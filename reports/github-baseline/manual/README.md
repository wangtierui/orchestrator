# 手动保存页面的落点（登录后才有的界面）

在线采集（`tools/gh_ui_harvest.py`）需要 CDP + 已登录的浏览器实例。当登录态在本机
无法建立时（企业 SSO / 二次验证 / 登录在另一台机器），用这里的手动通道：**不需要**
提供密码或 Cookie，只需在自己已登录的浏览器里保存页面。

## 怎么做

1. 在**你已登录 GitHub 的浏览器**里打开目标页面；
2. `Ctrl + S` → 保存类型选 **「网页，完整」（Webpage, Complete）**；
3. 存到本目录（文件名随意，建议带页面含义，例如 `issues-new.html`、`actions-run.html`）；
4. 如果是**弹层**（New issue 的侧栏、Run workflow 对话框、筛选面板）：
   先把弹层点开、让它出现在页面上，再保存；
5. 保存完告诉我，我执行：

```bash
python -m tools.gh_ui_parse_saved
```

产物：同目录 `<文件名>.json`，字段与在线采集一致
（tag / role / text / aria-* / title / data-testid / href / class / disabled + 标题 + 表格 + 地标）。

## 建议优先保存的页面

| 页面 | URL | 为什么要它 |
|---|---|---|
| New issue 表单 | `.../issues/new` | 标题/正文/侧栏 Labels·Assignees·Projects·Milestone 全字段 |
| PR 列表（含 Reviews 面板展开） | `.../pulls` | 与 issues 的筛选差异 |
| Actions 列表 + 任一运行详情 | `.../actions` → 点进 run | Run workflow / Re-run jobs 菜单 |
| Settings 首页 | `.../settings` | 左侧设置导航全集 |
| Security / Advisories | `.../security` → Advisories | 安全页的表格与操作 |

## 与在线采集的差别（不假装等价）

- **没有计算样式**（颜色、圆角、间距需要渲染引擎，静态 HTML 里没有）；
- 只反映**保存那一刻**的 DOM：没点开的弹层不会在里面；
- `*.html` 已在本目录的上一级 `.gitignore` 里排除，不会进仓库。
