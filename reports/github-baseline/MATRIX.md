# 页面对照矩阵（1440px）

映射：feedback/search/rules/laws/worklist/regstats ↔ `/pulls`；release ↔ `/commits/master`；overview/metrics ↔ `/graphs/contributors`

| 工作台页 | GitHub | 元素数(GH/我) | 差异最大的三类 |
|---|---|---:|---|
| `feedback` | `pulls` | 214/48 | 搜索框 偏窄（GH 1047 → 我 193）；按钮 偏窄（GH 95 → 我 76）；图标按钮 偏宽（GH 28 → 我 32） |
| `search` | `pulls` | 235/23 | 搜索框 偏窄（GH 1047 → 我 223）；按钮 偏宽（GH 79 → 我 94）；图标按钮 偏宽（GH 28 → 我 32） |
| `rules` | `pulls` | 235/305 | 链接 偏宽（GH 86 → 我 868）；搜索框 偏窄（GH 1047 → 我 644）；按钮 偏宽（GH 79 → 我 94） |
| `laws` | `pulls` | 214/18 | 搜索框 偏窄（GH 1047 → 我 223）；图标按钮 偏宽（GH 28 → 我 32）；按钮 一致（GH 95 → 我 94） |
| `worklist` | `pulls` | 214/959 | 搜索框 偏窄（GH 1047 → 我 193）；按钮 偏窄（GH 95 → 我 50）；图标按钮 偏宽（GH 28 → 我 32） |
| `regstats` | `pulls` | 214/34 | 搜索框 偏窄（GH 1047 → 我 163）；图标按钮 偏宽（GH 28 → 我 32）；按钮 一致（GH 95 → 我 94） |
| `release` | `master` | 361/168 | 按钮 偏宽（GH 124 → 我 229）；图标按钮 偏宽（GH 28 → 我 32） |
| `overview` | `contributors` | 49/24 | 按钮 偏窄（GH 191 → 我 94）；图标按钮 一致（GH 32 → 我 32） |
| `metrics` | `contributors` | 49/21 | 按钮 偏窄（GH 191 → 我 94）；图标按钮 一致（GH 32 → 我 32） |

## 图表栅格间距

| 页面 | 卡片数 | 垂直间距(中位) | 水平间距(中位) | 边框 | 圆角 | period |
|---|---:|---:|---:|---|---|---|
| gh-contributors | 9 | 16 | 16 | 1px/rgb(209, 217, 224) | 6px | ['Period: Last 3 month', 183, 32] |
| wb-overview | 12 | 93 | 16 | 0px/rgb(31, 35, 40) | 0px | None |
| wb-metrics | 6 | — | 16 | 0px/rgb(31, 35, 40) | 0px | None |
