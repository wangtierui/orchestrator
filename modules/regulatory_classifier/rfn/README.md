# rfn — 监管文件编号（RFN）唯一事实源模块

> **定位**：监管文件编号（RFN）的**唯一事实源**与统一访问层。所有全局任务（分类、引用、匹配、校验）获取监管文件编号一律经本模块接口，**禁止在代码中硬编码 RFN、禁止在模块外另建副本**。
>
> 口径基准：**2026-08-31 重构 + 2026-09-01 清理后**（1073 条 / 十主题 + T0 上位法锚点 / RFN-16hex）。

## 一、唯一事实源

| 项 | 位置 | 结构 |
|---|---|---|
| **权威数据（唯一事实源）** | `data/人身保险公司-文件归属表.csv`（1073 条） | 8 列：监管文件编号/文件名称/发文字号/发布日期/文件来源/时效状态/判定日期/编号备注 |
| 主题权威（主题唯一来源） | `data/人身保险公司-主题归属表.csv` | 3 列：监管文件编号/主题/判定依据 |
| 只读索引（本模块内派生） | `rfn/监管文件编号索引.csv`（1073 条，供人工查阅） | 6 列：监管文件编号/主题/文件名称/发文字号/发布日期/时效状态 |
| 统一访问层 | `rfn/__init__.py`（`get_index()` 单例） | — |
| 文件指纹 | `rfn/文件指纹.csv` | 5 列：监管文件编号/文件名称/发文字号/文件指纹/登记时间 |
| 层级同步状态 | `rfn/sync_status.json` | dict{RFN: {status, layers}} |

> 编号变更（新增文件/修订文号/效力调整）**只允许在归属表操作**，随后重新生成 `rfn/监管文件编号索引.csv`（`rebuild_index()`）或运行 `scripts/check_rfn_sync.py --fix` 自动同步。

**⚠️ 结构纪律**：2026-08-31 重构后归属表**不再含「主题」列**（主题拆分至主题归属表），亦删除恒空「同文件主编号」列（2026-09-01 #4）。
因此**任何直接 `row["主题"]` 的写法都会 KeyError** —— 2026-09-01 06:00 调度即因此在 `build_outputs.py` 崩溃。
下游一律改用 `from rfn import load_attr_rows`（自动按 RFN 合并主题列），**禁止各自重复实现「主题注入」**。

## 二、编号格式

```
RFN-<md5(去重键值) 前 16 位十六进制>
```

去重键值（≡ 监管文件编号，一一对应）：

| 情形 | 去重键值 |
|---|---|
| 命中严格文号正则 | `DOC:` + 归一化文号 + `\|` + md5(归一化标题)[:8]（同文号多子文件用标题指纹区分） |
| 无文号 / 非标文号 | `MD5:` + md5(归一化标题 \| 文件来源 \| 发布日期) |

- 编号与主题**有意解耦**（不含 T{n} 前缀）；`seq` 概念已废弃，主题内连续编号由下游 seq 承载；
- 2026-08-31 前旧编号形如 `RFN-T1-142`（八主题 seq 制），**已全部重编**；历史文档中的旧编号仅作叙述引用。

## 三、统一调用接口（各模块禁止自行定义编号）

```python
from rfn import get_index, load_attr_rows, THEME_MAP, RFN_PAT, theme_key

idx = get_index()                      # 单例，全模块共享同一份权威数据
idx.by_rfn("RFN-344cac5f135b22be")     # 编号精确查询 → dict | None
idx.by_title("T1", "保险销售行为管理办法")  # 主题+标题 → dict | None
idx.by_docno("银保监规[2022]24号")      # 发文字号 → list[dict]
idx.by_theme("T1")                     # 主题全部（接受 'T1' 码或全名）→ list[dict]
idx.is_valid("RFN-...")                # 有效性校验 → bool
idx.all_rfns()                         # 全部编号集合 → set
idx.ranges()                           # 各主题计数 → dict
idx.rows()                             # 全部记录（已合并主题列）

rows = load_attr_rows()                # ⭐ 下游脚本加载归属表的唯一入口（已合并「主题」列）
theme_key("T1销售行为与消费者保护")        # 主题名 → 主题码（'T1'）；裸 [：2] 会把 T10 误并入 T1
```

**主题枚举**（单源 `THEME_MAP`，禁止本地重复定义副本）：`T0上位法锚点` + `T1销售行为与消费者保护` ~ `T10数据治理与信息披露`。

**导入方式**（供 scripts/ 下脚本）：在脚本开头将仓库根加入 `sys.path` 后 `from rfn import ...`。

## 四、硬编码禁令（强制）

1. **禁止**在任何脚本/配置中硬编码具体 RFN（如 `"RFN-T1-081"` 写死进逻辑）；
2. **禁止**复制归属表到其他目录作为独立编号源（唯一副本只能在 `regulatory_classifier/data/` 与 `rfn/` 索引）；
3. **禁止**在模块外定义编号规则、主题名映射或另建编号表（主题名一律 `from rfn import THEME_MAP`）；
4. 合规用法：脚本运行时经 `get_index()` / `load_attr_rows()` 查询获得 RFN，用于输出/引用/校验。

## 四之二、入库接口（registry）— 查询调用规范落地

新增监管文件**禁止手工补录编号**，一律经 `rfn/registry.py` 注册（唯一键防并发 + 指纹防重复 + sync_status 层级同步）：

```python
from rfn.registry import register_doc, unique_key, lookup, sync_status_set, pending_syncs, rebuild_index

res = register_doc(theme="T1", title="...", docno="银保监办发〔2019〕19号",
                   pub_date="2019-02-26", source="nfra", fingerprint="sha256hex")
# res = {"rfn": "RFN-<16hex>", "action": "reused"|"created", "sync": {...}}
```

- **唯一键规则**：见第二节去重键值（无文号文件用「归一化标题+文件来源+发布日期」MD5）。
  ⚠️ `organ`（发布机构）入参已于 2026-08-31 移除——归属表无该列；`unique_key` 第 3 位为 **source**，
  传 organ 会导致去重键/RFN 派生错位（2026-09-01 已修 `query_or_register` 同类缺陷）。
- **来源枚举**：`source ∈ {gov, mof, nfra, pbc, supp}`，非法值抛 `ValueError`；缺省回落 `supp`。
- **查重防并发**：已存在 → `reused`（复用现有编号）；不存在 → 由去重键值派生 RFN，追加归属表 + 主题归属表 + 指纹表，并自动重建索引。
- **文件指纹**：登记 sha256 至 `rfn/文件指纹.csv`，防同一文件重复摄入。
- **sync_status**：`rfn/sync_status.json` 记录层级同步进度；写归属表成功后 `status=partial`（文件归属表=ok，其余 pending），各层级完成后 `sync_status_set(rfn, layer, "ok")`，**全部 SYNC_LAYERS 完成** → `synced`；失败记 `pending` 待重试。
- **层级同步顺序**（规范要求）：数据底座 → 文件归属表 → 横向整合分析报告 → 纵向深化分析报告 → 全景分析报告。
- **防并发**：msvcrt 进程锁（`.registry.lock`）。

**隔离测试**：环境变量 `RFN_REGISTRY_CSV` / `RFN_REGISTRY_THEME` / `RFN_REGISTRY_INDEX` / `RFN_REGISTRY_FP` / `RFN_REGISTRY_SYNC`
**五个变量全部**重定向到临时目录，测试才不污染真实库（⚠️ 缺 `RFN_REGISTRY_THEME` 会让 created 路径写真实主题归属表——2026-09-01 已修）。
单元测试：`tests/test_registry.py`（8 用例）、`tests/test_query_or_register.py`（7 用例），均 stdlib 直跑 / pytest 兼容。

## 四之三、三级查询 + 注册（query_or_register）

```python
from rfn.query_or_register import query, query_or_register, LEVEL_DOCNO, LEVEL_TITLE, LEVEL_CONTAIN

level, hit = query(idx, theme="T1", title="...", docno="...", pub_date="...")
res = query_or_register(theme="T1", title="...", docno="...", source="supp")
```

- **L1 文号归一化精确** → `LEVEL_DOCNO`；**L2 标题归一化精确（需主题）** → `LEVEL_TITLE`；
- **L3 标题双向包含 + 佐证**（双方文号相等，或双方无文号且发布日期相等）→ `LEVEL_CONTAIN`；
- 三级均要求强佐证，纯标题包含不触发复用（防误判）；未命中 → `register_doc` 新建。
- ⚠️ `organ` 参数已于 2026-09-01 从 `query`/`query_or_register`/CLI 移除（归属表无该列，且原实现传入 `register_doc` 会抛 TypeError）。

## 五、目标输出目录约定

- `internal_policy_drafter` 等下游**输出目录仅存放实际处理结果**（制度文档、报告产出），**不存放**编号索引、编号说明文档或本模块副本；
- 编号索引与说明文档统一存放于本 `rfn/` 模块（唯一位置）。

## 六、维护流程

| 操作 | 步骤 |
|---|---|
| 新增监管文件 | 1) `register_doc`（或 `query_or_register`）注册（自动分配 RFN + 指纹 + sync_status，**并自动重建索引**）→ 2) 按层级顺序同步（数据底座→归属表→横向/纵向/全景报告，已据实标记 pending）→ 3) `scripts/check_rfn_sync.py --fix` 校验 |
| 修订文号/效力 | 1) 归属表修改 → 2) `scripts/check_rfn_sync.py --fix` 全量同步 |
| 例行检查 | `python scripts/check_rfn_sync.py`（校验模式）/ `--fix`（修复模式） |
| 索引重建（手动） | `python -m rfn.registry --rebuild-index`（或 `python rfn/registry.py --rebuild-index`） |
| 待同步查询 | `python rfn/registry.py --pending` |
| 编号查询 | `python scripts/rfn_lookup.py --rfn/--title/--docno/--theme/--list-ranges` |
