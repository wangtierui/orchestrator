# 版本管理与兼容性（semver 三层 + 版本矩阵 + 弃用周期）

> 依据：《项目最优重构方案》§13.6（W5，P1）。本文是**版本策略的唯一事实源**；
> 机器可查部分在 `config/schema/contract_manifest.json`（契约层版本）与本文 §四（版本矩阵）。

## 一、三层版本，各管一件事

| 层 | 载体 | 语义 | 变更条件 |
|---|---|---|---|
| **契约版本** | `config/schema/contract_manifest.json` 的 `version`（当前 `1.0.0`） | **字段级**兼容性承诺：消费方（如 `clean_index`、published 双底座）可依赖的键集 | 键集**新增** → minor；键集**删除或语义变更** → major |
| **发布件版本** | 各发布件的 `schema_version`（JSONL 首行 `_meta.schema_version`） | 该产物的**结构版本**（字段/列序/信封结构） | 同上（字段级判据），但按产物独立演进 |
| **资源版本** | `STEP_LAYERS` 资源版本（E-4：改一步 → 资源版本递增） | 链级资源（产物/索引/规则表）的版本 | 任一产出物结构变更即递增；链语义变更走 major |

**为什么分三层**：契约是「消费方承诺」，发布件是「产物现实」，资源版本是「链内部记账」。
混为一层就会出现「产物结构变了但契约没变」（消费方静默炸）或「契约变了但产物没变」
（承诺了不存在的能力）。

## 二、兼容规则（只增不改不删）

- **minor（向后兼容）**：新增可选字段、新增产物、新增枚举值（消费方须容忍未知值）。
- **major（不兼容）**：删除字段、改字段语义、改类型/列序、改枚举含义。
  必须附**等价性证明**（批 47 范式：旧口径 vs 新口径的记录数/哈希/抽样比对记录）。
- **消费方守门**：`gate_contract` 断言的键集就是机器守门人——契约清单里的键集与实际
  产物头/键集必须逐项相符；不符即 FAIL。
- **枚举扩展纪律**：受控域（`config/enums.py`）新增值属 minor，但消费方必须
  `assert_enum_bindings` 通过（禁止「加了枚举值但某处硬编码列表没跟上」）。

## 三、弃用策略（与 §7.2 退役三台阶同节奏）

脚本退役与 API 弃用统一为三台阶，观察期 **2 个 Wave**：

1. **弃用告警**：文档标注 deprecated + 门禁开始**披露**命中点（不阻断）；
2. **强制弃用**：门禁转 **FAIL**（必须迁移或显式豁免登记）；
3. **retired 归档**：移入 `retired/` 或删除，判据 J9（零引用）+ README 备案（原因/日期/替代物）。

豁免登记的唯一入口是配置清单（`tools/retired/` 的 `README.md` 与对应清单），
不接受「先留着再说」的口头豁免。

## 四、版本矩阵（发布件 schema_version × 消费方断言语）

| 消费方 | 依赖的版本面 | 当前兼容区间 | 守门判据 |
|---|---|---|---|
| `clean_index`（五源 clean 索引） | cleaned JSONL 的 `_meta.schema_version` | `1.x` | `gate_clean_schema` + `gate_contract` |
| `published` 双底座（external/internal） | published `_meta.schema_version` | `1.x` | `gate_contract` |
| `analysis` / `relevance` / `recall` 视图 | analysis manifest 版本 | `1.x` | `gate_field_aliases` + `gate_contract` |
| `wiki` 投放 | published 行字段集 | `1.x` | `gate_contract` |
| 关系产物（relations） | relations 27 字段契约 | `1.x` | `gate_relations` + `gate_contract` |
| 治理库（governance.db） | `SCHEMA_VERSION`（治理库自身） | `1.x` | `gate_config_integrity`（结构清单） |
| **契约清单** | `contract_manifest.json.version` | `1.x` | `gate_contract`（三方断言） |

**组合规则**：本仓同一时刻只支持「**一个 major**」——即所有产物的 major 号必须一致；
出现 `2.x` 产物与 `1.x` 消费方并存即视为**不支持组合**，须同步升级或提供适配层
（适配层必须显式登记在本文矩阵里，不允许隐式）。

## 五、版本号变更流程

1. 判定层级与 major/minor（依 §二规则）；
2. 改契约清单或发布件 `schema_version`（**一处**——SSOT）；
3. 跑 `python cli.py gates`（`gate_contract` 会当场抓到不一致）；
4. major 变更须在 CHANGELOG「不兼容变更」段落写等价性结论；
5. 更新本文矩阵的「当前兼容区间」（若区间变了）。

## 六、当前值与遗留

- 契约版本 `1.0.0`（21 个契约符号，`gate_contract` 三方断言：文件/符号/计数）。
- 发布件版本：五源 raw/cleaned JSONL 信封 `schema_version` 字段已在链上落盘；
  治理库 `SCHEMA_VERSION` 由结构清单守护。
- **遗留登记**：§13.9 的 `paths.py` 常量返回形态（`str` → `pathlib.Path`）是**破坏面变更**，
  须走 major + 消费方盘点，**待人工确认后单独排波**（不在 W5 内隐式改动）。
