# clean_index — 五源 clean 产物结构化索引（单一事实源）

## 一、定位

`regulatory_scrapers` 五个数据源（`gov` / `mof` / `nfra` / `pbc` / `supp`）的 **clean 产物的唯一索引事实源**。所有下游（尤其是 `regulatory_classifier` 的分类 / 引用 / 匹配任务）获取五源 clean 数据路径、时间戳、记录数、元数据，**一律经本模块接口，禁止在下流自行 glob 或硬编码路径**。

设计对标 `regulatory_classifier/rfn/`：`get_index()` 单例 + 持久化索引 + 稳定访问层。

## 二、产物与位置

| 项 | 位置 |
|---|---|
| **持久化索引（可直接被程序读取）** | `regulatory_scrapers/clean_index/index.json`（UTF-8，JSON） |
| **访问层（稳定接口）** | `regulatory_scrapers/clean_index/__init__.py`（`get_clean_index()` 单例） |
| **生成器** | `regulatory_scrapers/clean_index/build_clean_index.py` |

## 三、字段结构（index.json）

```jsonc
{
  "schema_version": "1.0.0",
  "generated_at": "2026-08-29T14:30:00+0800",     // 索引生成时间戳（Asia/Shanghai）
  "generator": "regulatory_scrapers/clean_index/build_clean_index.py",
  "description": "...",
  "scraper_root": "D:/WorkBuddy/regulatory_scrapers",
  "sources": {
    "<source_id>": {                               // source_id ∈ SOURCE_SET
      "source_id": "gov",
      "source_name": "国务院及地方政府规章/法规库（gov.cn 体系）",
      "scraper_subdir": "gov_regulations_scraper",
      "cleaned_dir": "D:/WorkBuddy/regulatory_scrapers/gov_regulations_scraper/data/cleaned",
      "snapshots": {
        "<YYYYMMDD>": {                            // 同源可有多份历史快照（如 pbc/supp）
          "date": "20260820",
          "files": {
            "csv":  { "path": "...", "size_bytes": 62707597, "sha256": "...", "record_count": 1234, "modified_at": "..." },
            "jsonl":{ "path": "...", "size_bytes": 86031511, "sha256": "...", "record_count": 1234, "modified_at": "..." }
          },
          "record_count": 1234,                   // = csv 行数（剔除表头），csv/jsonl 应为同一数据集
          "record_source_values": ["xzfgk", "gov-cn", ...],   // 元数据：intra-record `source` 取值
          "timeliness_status_values": ["valid", "repealed", ...],
          "doc_type_values": [...],
          "category_values": [...],
          "companion_docs": ["gov_数据字典_20260820.md", "gov_合规记录_20260820.md", "README.md", ...]
        }
      },
      "latest_date": "20260820",                  // 取 max(date)
      "latest": { "date": "20260820", "csv": "...", "jsonl": "...", "record_count": 1234 },
      "total_record_count": 1234                  // ∑ snapshots.record_count
    }
  },
  "summary": {
    "source_count": 5, "snapshot_count": 9,
    "csv_count": 9, "jsonl_count": 9,
    "total_record_count": 99999
  }
}
```

**字段命名约定（便于下游直接消费）**：`source_id` 与 `unified_schema.SOURCE_SET` 严格对齐；路径一律绝对路径 + 正斜杠（与项目约定一致）；`record_count` 来自 CSV 精确计数（`csv.reader`，剔除空行），可作为分类任务记录总量基准。

## 四、稳定调用接口（regulatory_classifier 直接加载，无需转换）

```python
import sys, os
sys.path.insert(0, "D:/WorkBuddy/regulatory_scrapers")   # 跨项目导入
from clean_index import get_clean_index

idx = get_clean_index()                  # 单例；优先读 index.json（零扫描），缺失则自动扫描并写入

# 1) 分类任务主入口：每源最新快照 csv 清单
for item in idx.all_active_csv():
    print(item["source_id"], item["date"], item["path"], item["record_count"])

# 2) 精确取某源最新数据路径
csv_path  = idx.latest_csv_path("gov")     # -> 绝对路径 或 None
jsonl_path= idx.latest_jsonl_path("nfra")

# 3) 指定历史快照
snap = idx.snapshot("pbc", "20260829")
csv_pbc_0829 = idx.csv_path("pbc", "20260829")

# 4) 当前口径记录总数
total = idx.total_record_count()

# 5) 完整性校验（下游加载前可选）
v = idx.validate_files()                  # -> {missing:[], hash_mismatch:[], ok:[]}

# 6) 陈旧性检查（磁盘文件增减/体积变化）
if not idx.is_fresh():
    idx = get_clean_index(rebuild=True)   # 或 idx = rebuild_index()
```

### 兼容垫片（替换 classifier 既有逻辑）

`recall_audit/align_artifacts.py` 与 `recall_audit/classify_themes.py` 中均存在重复的 `find_cleaned(source)` glob 逻辑。可直接替换为：

```python
from clean_index import get_clean_index
# 原：find_cleaned(src)  -> 现：
csv_path = get_clean_index().find_cleaned(src)   # 等价于 latest_csv_path，返回绝对路径/None
```

无需任何格式转换，接口语义一致（返回最新快照 csv 绝对路径）。

### scanner 召回归档（2026-08-29 改造）

`regulatory_classifier/recall_audit/scanner.py` 已从「硬编码快照日期字典」改造为经 `clean_index` 动态派生，彻底消除硬编码：

```python
from clean_index import get_clean_index, scan_sources

def _load_fresh_index():
    """方案C（2026-08-29·与 Gate1 语义一致）：索引过期时自动重建，避免静默读旧快照。"""
    idx = get_clean_index()
    live = scan_sources(hash_files=False)  # 实时磁盘扫描；失败则退化为直接用既有索引
    # 比对 idx.latest(src) 与 live 的 date/record_count，任一源不一致 → 自动重建
    ...
    return idx

_idx = _load_fresh_index()
CLEANED = {}
for _src in ("gov", "mof", "nfra", "pbc", "supp"):
    _p = _idx.latest_csv_path(_src)
    if not _p or not os.path.exists(_p):
        raise RuntimeError(f"clean_index 未找到 {_src} 的 latest csv，无法构造检索输入")
    CLEANED[_src] = _p
```

- **禁止硬编码** `*_cleaned_(YYYYMMDD)`：五源最新快照一律经 `latest_csv_path(src)` 派生；索引缺失或文件不存在时脚本主动报错，避免静默读旧数据。
- **联动门禁 + 自动重建**：`regulatory_classifier/recall_audit/run_retrieval_after_checks.py` 的 Gate 1 校验「scanner.py 已依赖 `clean_index` 且无硬编码快照日期」（未达标直接 FAIL，不执行重跑）；**并新增「索引陈旧自动重建」**——Gate 1 检测到 `index.json` 与磁盘实时扫描不一致时，会**自动 `get_clean_index(rebuild=True)` 重建索引并重试**，免去手动 `python build_clean_index.py`，并经报告 `index_rebuilt` 字段 + warning 透明通知已纳入新快照。
- **重建步骤**：scrapers 侧新增/更新 cleaned 快照后，**两条路径均无需手动重建**：①经编排器（Gate 1 自动重建）；②`scanner.py` 被直接绕过编排器调用时，其 `_load_fresh_index()`（2026-08-29·方案C）同样会比对 `index.json` 与 `scan_sources` 实时扫描，不一致即**自动 `get_clean_index(rebuild=True)` 重建**，重建后仍不一致则 `RuntimeError`（疑似重建期间磁盘并发写入）。手动 `python build_clean_index.py`（或下游 `get_clean_index(rebuild=True)`）仍为等价可选手段。**方案C 已闭合「直接调用 scanner 无陈旧校验」的缺口**，两条路径语义完全一致。

## 五、与「单一事实源」纪律的一致性

- 索引只描述 **scraper 侧输出**，不重复 `regulatory_classifier/rfn` 的 RFN 编号体系；二者互补（RFN 管「归属/编号」，clean_index 管「原始 clean 产物位置」）。
- 索引 **单一副本**位于 `regulatory_scrapers/clean_index/`；classifier 侧 **禁止**另建副本或硬编码路径，统一经 `get_clean_index()` 访问（类比 rfn 的硬编码禁令）。

## 六、重建 / 刷新约定

- 抓取交付流水线（拍平 / 清洗后）应定期重跑：`python build_clean_index.py`。
- `--no-hash` 可跳过 sha256（仅 size/modified_at）以加快构建；默认开启哈希以保证 `validate_files()` 可用。
- 下游加载前若怀疑索引与磁盘不一致，调用 `idx.is_fresh()` 判断，必要时 `get_clean_index(rebuild=True)` 或 `rebuild_index()`。
- `schema_version` 随结构演进递增；下游解析应兼容历史版本（当前 `1.0.0`）。
