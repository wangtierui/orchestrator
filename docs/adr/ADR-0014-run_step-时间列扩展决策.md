# C3 决策建议书：`run_step` 是否扩展 schema

> 决策人：项目负责人（2026-10-16 指示「进一步说明扩展与不扩展的优劣，待确认」）
> 结论提要：**不建议全扩，也不建议全不扩**——按字段分项决策，实测依据如下。
> 纪律：所有论断附代码/数据证据；无法判定处标「待人工确认」。

## 一、要决策的到底是什么

诊断能力（最优方案第 2 波）需要 `run_step` 提供四类信息。原「C3 待确认」把它们捆在一起问，
但四者的**可得性差异极大**：

| 信息 | 现状 | 决策 |
|---|---|---|
| ① 记录数（raw/cleaned 条数） | 无此列 | **不扩** — 可从产物侧实时派生（实测 0.067s） |
| ② 每步 argv（实参） | 无此列 | **不扩** — 可 AST 抽取 + 现产物 |
| ③ 步骤开始/结束时刻 | 只有 `recorded_at`（≈结束时刻） | **建议扩**（唯一真正缺失项） |
| ④ 并发/间隔分析 | 无 | 随 ③ 一并解决 |

## 二、方案 A：全不扩展（只读现状）

**做法**：诊断只用现有 8 列（`run_id, step, rc, exit_code, elapsed_s, skipped, note, recorded_at`）。

**优势**
- 零迁移、零写入改动，观测表结构冻结（`_db.py:351` 已定纪律「不可 DROP 重建，结构演进只能 ALTER」）；
- 完全不碰主链；
- 记录数从 `clean_index_api.record_count()` 取（实测五源 0.067s，`is_fresh()=True`）——
  **产物侧是权威，台账反而是二手**。

**劣势（实测量化）**
- **时间窗误差 = 该步耗时**：最近一次真实运行 `recorded_at` 跨度 2511s，而 `clean:gov`
  单步 `elapsed_s=15526.3`（4.3 小时）⇒ 问「某时刻前后发生了什么」时，误差可达数小时；
- `elapsed_s` 只能答「哪步慢」，**答不了**「与哪一步并发/前后相隔多久」（实测无从推断）；
- 失败归因最需要的正是「失败时刻前后，别的步骤在做什么」⇒ 这一项缺了，根因分析第一
  步（确定时间窗）就缺基础。

**结论**：全不扩展在 ①② 上完全成立，但在 ③④ 上留下**不可弥补的分析缺口**。

## 三、方案 B：全扩展（`+started_at +finished_at +argv +record_count`）

**做法**：四类信息全部落库。

**优势**：一次到位，诊断取数最直接；台账自足，不依赖派生。

**劣势**
- **argv 是"看起来最该扩、实际最不该扩"的字段**：argv 的权威来源是代码调用点
  （`tests/test_gov_incremental.py:95` 的 `test_substep_argv_pins_envelope_and_source`
  即锁定其形态），落库会产生**第二份事实源**——代码改了、库里还是旧 argv，两处必然漂移
  （与 `requirements.lock`/`record_count` 类冗余字段同一类问题）；
- **record_count 会与产物不一致**：产物随时可重跑（cleaned 文件就在那儿），台账里的
  记录数只在链跑的那一瞬间为真 ⇒ 又一个需要同步的副本；
- 迁移面变大：4 列 × 5132 行既有数据的向后兼容（老行为 NULL）需要额外口径声明。

**结论**：①② 落库是**制造冗余事实源**，违反项目「一个事实源 + 派生视图」取向。

## 四、方案 C（推荐）：分项决策——③ 扩，①② 派生

### 4.1 扩两列（`started_at` / `finished_at`）的成本实测：**约 6 行、3 文件，主链控制流零改动**

关键事实：**主链手上已经有这两个值**。

```
tools/run_production_refresh.py:563   t0 = time.time()
tools/run_production_refresh.py:667   rec["elapsed_s"] = round(time.time() - t0, 1)
tools/run_production_refresh.py:670   rec["ended_at"] = round(t0 + rec["elapsed_s"], 3)
tools/run_production_refresh.py:1159  def _record_step(rec: dict) -> None:
                                        """…（旁路设施：登记失败不得中断主链）"""
```

即：`ended_at` **已经在算**（`:670`），只是 `_record_step` 的参数清单（`:1164-1171`）
没有把它传给台账。迁移机制也已存在：

```
std_lib/common_lib/governance_store/_db.py:351  # 语义：观测表承载历史、**不可 DROP 重建**，
                                                #        结构演进只能 `ALTER TABLE ADD COLUMN`
std_lib/common_lib/governance_store/_db.py:374  conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
```

落地清单（估）：
| 文件 | 改动 |
|---|---|
| `_db.py` | DDL 加 2 列 + 迁移清单加 2 行（机制现成） |
| `governance_store/ledger.py` `step_record()` | 加 2 个**可选**参数（默认 `""`，老调用点不受影响） |
| `run_production_refresh.py` `_record_step` | 传 `rec.get("started_at")` / `rec.get("ended_at")`（**rec 里已有**；`started_at` 由 `t0` 赋值 1 行） |

主链的**控制流、判据、跳过逻辑一行不动**，且写入点本身已声明「旁路：登记失败不得中断主链」。

### 4.2 ①② 派生：不扩 schema，且派生源比台账更权威

| 派生项 | 实现 | 实测 |
|---|---|---|
| 记录数 | `interfaces/clean_index_api.record_count(src)` | 五源 0.067s：gov 13178 / mof 870 / nfra 1934 / pbc 571 / supp 58；`is_fresh()=True` |
| argv | `tools/gen_flow_map.py` 已用 **AST 抽 `_run` 调用点**生成 README §8 命令列 | 机制已存在，无需新建 |

**关键性质**：派生口径**永远与产物/代码一致**，不需要同步、不可能漂移。这正是项目
一贯取向（README §8 的全链流程图、确认台 P1 桑基图都是派生产物）。

### 4.3 方案 C 的代价（诚实列出）

| 代价 | 评估 |
|---|---|
| 需要一次 schema 迁移 | 低（机制现成；`ALTER TABLE ADD COLUMN` 对 5132 行是毫秒级） |
| 老数据两列为空 | 需明确口径：**时间窗分析只对迁移之后的运行有效**，历史运行仍用 `recorded_at` 近似。须在工具文档与门禁断言中写明，避免下一个人误用 |
| 迁移纪律 | `_db.py:351` 明令观测表「不可 DROP 重建」⇒ 本扩展必须走 `ADD COLUMN`，不得重写表（这条已在纪律里，方案合规） |
| 主链写入面扩大 | `_record_step` 多传 2 个值；因该函数已是「旁路 + 不抛异常」，**失败模式不变** |

## 五、推荐与待确认项

**推荐：方案 C** —— 只扩 `started_at` / `finished_at` 两列；`record_count` / `argv` 一律派生。

| 决策项 | 建议 | 若否决的代价 |
|---|---|---|
| 扩两列（时间） | ✅ 建议扩 | 根因分析缺时间窗基础（误差可达 4.3 小时），只能退化为"按 run 粒度"分析 |
| 扩 `record_count` | ❌ 建议不扩 | 制造与产物不一致的第二事实源 |
| 扩 `argv` | ❌ 建议不扩 | 制造与代码调用点漂移的第二事实源（且已有 AST 抽取可用） |

**仍需人工确认的一点**：老数据（5132 行既有记录）两列为空后，是否需要用 `recorded_at`
反推填一个近似值 `started_at = recorded_at - elapsed_s`？
- 支持：让全量历史可分析（反推在数学上恰好成立，因为 `elapsed_s` 是实测值）；
- 反对：反推值会被误当作实测值引用（**污染数据语义**）。
- 本文档建议：**不填**（宁可明确"历史无此字段"，也不引入看似精确的伪实测值），
并要求所有时间窗分析显式过滤 `started_at IS NOT NULL`。

---

## 附：证据速查

| 论断 | 证据 |
|---|---|
| 观测表只能 ADD COLUMN | `std_lib/common_lib/governance_store/_db.py:351`、`:374` |
| 主链已有 t0/ended_at | `tools/run_production_refresh.py:563`、`:670` |
| 台账写入是旁路 | `tools/run_production_refresh.py:1159-1160`、`:1172-1173` |
| 记录数可派生 | `clean_index_api.record_count()` 实测 0.067s / 五源有数 |
| argv 已可 AST 抽取 | README §8 受管生成块（命令列 ← AST 抽 `_run` 调用点） |
| `recorded_at` 误差 = 步耗时 | 最近 run 跨度 2511s vs `clean:gov` elapsed 15526.3s |
| 同名步骤无重复记录 | 最近 run 内 `GROUP BY step HAVING COUNT(*)>1` = 0 |