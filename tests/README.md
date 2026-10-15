# tests/README.md — 测试体系说明与**契约测试矩阵**

> 依据：《项目最优重构方案》§9.3（测试体系）与 §13.8（测试深度扩充，P1）。
> 本文件是「哪类契约由哪些用例守住」的**登记表**——避免"以为有测试，其实没有"。

## 一、运行方式

```bash
python -m pytest -m "not data"      # 代码级回归（无数据环境可跑，CI 口径）
python -m pytest                    # 全量（含 data 标记的本机数据验收）
python -m pytest -m contract        # 只跑契约类（门禁/导入面/产物形态/传输行为）
```

- 两个 pytest 标记（`pyproject.toml` 声明）：
  - `data`：依赖本机现行数据产物的验收用例；无数据环境用 `-m "not data"` 排除。
  - `contract`（W5 §13.8-1 新增）：**契约类**用例（门禁判据正/负例、import 面、产物形态、
    传输行为）——无数据环境可跑，与 `data` 互补。
- 超时护栏：`--timeout=900`（防单测挂死掩盖真实失败）；覆盖率 `--cov`（`fail_under=20`）。

## 二、契约测试矩阵（五类 × 位置）

| # | 契约类 | 守什么 | 用例位置 |
|---|---|---|---|
| C1 | **字段契约** | 产物/表的键集与列头（契约清单三方断言） | `tests/test_contract_manifest.py`、`tests/test_contract_api.py`、`tests/test_table_completeness.py`、`tests/test_table_structured.py`（`gate_contract` 为机器守门人） |
| C2 | **产物形态** | 文件名 + 落盘位置 + 结构（JSONL 首行 `_meta` 信封、字段序） | `tests/test_raw_jsonl.py`、`tests/test_clean_index_portability.py`、`tests/test_base_publish.py`、`tests/test_analysis_deliveries.py` |
| C3 | **import 面** | 包可导入、约定入口存在、公共符号集不变 | `tests/test_import_surface.py`、`tests/test_collectors_dual_form.py`（36 项：包形态符号存在性 + 脚本形态可启动）、`tests/test_j9_modules_manifest.py`（三态归属）、`tests/test_interfaces_protocols.py` |
| C4 | **传输行为** | HTTP 单一栈的等价性与降级路径 | `tests/test_http_unification.py`、`tests/test_ocr_correction_equiv.py`（抽取等价）、`tests/test_crawler_extract.py` |
| C5 | **资源版本** | 资源版本递增规则（改一步 → 资源版本递增） | `tests/test_gate_run_steps.py`（E-4 资源版本断言） |
| C6 | **门禁判据**（新增维度） | 每道判据的**正例 + 负例**（改一处必 FAIL） | `tests/test_j9_modules_manifest.py`（9 项）、`tests/test_dependency_audit_gate.py`（9 项）、`tests/test_gate_run_steps.py` |
| C7 | **配置/调度一致性** | yaml ⇄ 代码 ⇄ 文档三方不漂移 | `tests/test_p2_governance_layer.py`（手册/README 由 `gen_schedule_doc` 反向生成）、`tests/test_gates_matrix.py`、`tests/test_triggers_conditions.py` |
| C8 | **链等价** | dry-run 指纹与实现一致 | `tests/test_chain_equivalence.py` + `tests/golden/`（重录：`python -m tests.test_chain_equivalence --regen`） |

**纪律**：新增「拆分/重组/契约变更」类改动，必须同时在对应行登记用例；无对应行的类别
不得声称"已覆盖"。矩阵与实际用例不符时以**实际**为准并当场修正本表。

## 三、已登记的属性测试不变量（§13.8-1，hypothesis）

| 不变量 | 断言 | 位置 | 状态 |
|---|---|---|---|
| ① 清洗规则 | `normalize_date` 对任意日期串的输出 ∈ 受控格式**或**显式隔离 | `tests/test_ssot_convergence.py`（三类日期归一分层）、`tests/test_scrapers_pure.py` | ✅ |
| ② resume 幂等 | 任意中断点续跑结果 = 全量跑结果 | `tests/test_w4_data_layer.py`（水位/跳过集等价性） | ✅（以台账等价实现，非 hypothesis 生成） |
| ③ 水位判据稳定 | 上游版本不变 ⇒ `stale` 判定稳定 | `tests/test_w4_data_layer.py`、`tests/test_governance_watermark.py` | ✅（同上） |

**未做**：`hypothesis` 库本身未引入（生成式属性测试）。理由：① 当前不变量可用
确定性用例覆盖（同等断言强度、成本低一个量级）；② 引入生成式输入会给"确定性优先"的
清洗链带来不可复现失败（与 §13.12 的 atheris 裁定同族）。复发条件：清洗规则出现
**基于值域**的复杂不变量（如日期格式 × 时区 ×  fiscal 边界组合）时再引入。

## 四、性能基线（§13.3 / §13.8-2）

- 步骤耗时基线落 `data/run_state/step_duration_baseline.json`（由链运行时台账派生，
  确认台 P1 顺带采集——H 轨）；**超时上限**（防挂死）与**基线**（防劣化）分离：
  超时上限见 `schedule.yaml` 的 `max_timeout`，基线劣化阈值 >20% 提示（当前为披露，
  转阻断需经「并行披露 2 周」流程）。
- ⚠️ **禁凭单次耗时判回归**：`⚠️长链与 CI 并发` 会让耗时不可比（项目既有教训）。
  基线比较必须同机器、同并发条件、多次取中位数。

## 五、变异测试与安全测试

- **变异测试**：全仓不做（§13.12 已写死理由）。仅在 `gates/` 核心断言层的**存活率抽查**
  由 `gate_*` 的负例测试间接保证（每条判据都有"改一处必 FAIL"的负例）。
- **安全测试**：`bandit` 入 CI（ADR-0011）+ 负例单测
  （`gate_secret_scan` 的假 token 负例；`gate_dependency_audit` 的影子依赖负例）。
| C9 采集器回放（TD-1） | `tests/test_collector_replay.py` + `tests/cassettes/` | 夹具机制 + 纯解析器 + 端到端回放（urlopen / opener.open）；manifest 由 `python -m tools.record_cassettes --rebuild-manifest` 重建 | J13 |
