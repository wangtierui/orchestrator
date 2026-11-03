# 登录前 / 登录后 差集

- 未登录基线：`logged-out/`（2026-11-03 15:xx 采集）
- 登录态基线：`./`（2026-11-03 19:26 采集，user 已登录）

| 页面 | 未登录元素 | 登录后元素 | 新增 | 消失 |
|---|---:|---:|---:|---:|
| `actions-metrics-performance` | 103 | 91 | **+83** | -95 |
| `actions-metrics-usage` | 103 | 94 | **+86** | -95 |
| `actions` | 164 | 128 | **+28** | -64 |
| `agents` | 103 | 31 | **+27** | -99 |
| `forks` | 141 | 92 | **+11** | -60 |
| `graphs-code-frequency` | 250 | 37 | **+6** | -219 |
| `graphs-commit-activity` | 142 | 93 | **+7** | -56 |
| `graphs-contributors` | 162 | 121 | **+15** | -56 |
| `issues` | 204 | 148 | **+23** | -79 |
| `li-actions-run` | 0 | 41 | **+41** | -0 |
| `li-compare` | 0 | 34 | **+34** | -0 |
| `li-issues-new` | 0 | 43 | **+43** | -0 |
| `li-security-advisories` | 0 | 53 | **+53** | -0 |
| `li-settings-profile` | 0 | 22 | **+22** | -0 |
| `li-settings` | 0 | 22 | **+22** | -0 |
| `li-user-settings-nav` | 0 | 87 | **+87** | -0 |
| `li-user-settings` | 0 | 87 | **+87** | -0 |
| `network-dependencies` | 100 | 51 | **+6** | -55 |
| `network` | 87 | 38 | **+6** | -55 |
| `projects` | 87 | 45 | **+13** | -55 |
| `pub-activity` | 90 | 90 | **+0** | -0 |
| `pub-commits` | 227 | 227 | **+0** | -0 |
| `pub-community` | 97 | 97 | **+0** | -0 |
| `pub-issue-detail` | 252 | 252 | **+0** | -0 |
| `pub-labels` | 170 | 170 | **+0** | -0 |
| `pub-milestones` | 98 | 98 | **+0** | -0 |
| `pub-pull-detail` | 252 | 252 | **+0** | -0 |
| `pub-releases` | 215 | 215 | **+0** | -0 |
| `pub-security-policy` | 84 | 84 | **+0** | -0 |
| `pub-stargazers` | 103 | 103 | **+0** | -0 |
| `pub-tags` | 106 | 106 | **+0** | -0 |
| `pub-wiki-page` | 106 | 106 | **+0** | -0 |
| `pulls` | 204 | 137 | **+28** | -95 |
| `pulse` | 167 | 118 | **+12** | -61 |
| `repo` | 170 | 123 | **+16** | -63 |
| `security` | 98 | 47 | **+4** | -55 |
| `wiki` | 106 | 56 | **+4** | -54 |

## 只在登录态出现的控件（按出现页数排序，前 25）

| 元素 | 出现页数 | 语义 |
|---|---:|---|
| `BUTTON · aria=Open Copilot…` | 25 |  |
| `BUTTON · aria=Open quick search dialog, type / to search · Type / to s` | 25 | Type / to search |
| `A` | 24 |  |
| `A · Agents` | 21 | Agents |
| `FOOTER · role=contentinfo · Footer © 2026 GitHub, In` | 9 | Footer © 2026 GitHub, In |
| `A · Issues2.9k (2.9k)` | 9 | Issues2.9k (2.9k) |
| `A · Contact` | 9 | Contact |
| `A · Code` | 9 | Code |
| `UL · role=list · CodeIssues2.9k (2.9k)Pul` | 9 | CodeIssues2.9k (2.9k)Pul |
| `A · Wiki` | 9 | Wiki |
| `A · Status` | 9 | Status |
| `A · Actions usage metrics` | 9 | Actions usage metrics |
| `A · notepad-plus-plus` | 9 | notepad-plus-plus |
| `A · Security` | 9 | Security |
| `A · Actions performance metr` | 9 | Actions performance metr |
| `A · Insights` | 9 | Insights |
| `A · aria=GitHub Homepage` | 9 |  |
| `A · Security and quality40 (` | 9 | Security and quality40 ( |
| `A · Pull requests31 (31)` | 9 | Pull requests31 (31) |
| `A · Community` | 9 | Community |
| `A · Projects` | 8 | Projects |
| `BUTTON` | 8 |  |
| `A · Skip to content` | 8 | Skip to content |
| `A · Terms` | 7 | Terms |
| `A · Docs` | 7 | Docs |
