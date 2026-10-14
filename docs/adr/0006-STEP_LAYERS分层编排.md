# ADR-0006 STEP_LAYERS 分层编排

- **状态**：已采纳（W1，2026-10-13）
- **动因**：原 `STEP_ORDER` 是 31 步平铺字符串元组，步骤的用途/超时/触发器/产物/
  失败语义散落三处（rpr 注释、`gen_flow_map.PURPOSE`、`STEP_FAILURE`）→ 三处漂移，
  README §8 渲染靠人工同步（D-6/D-7/D-8/D-12/D-13）。

## 决策

`STEP_LAYERS` 为**语义唯一事实源**（代码内 dataclass，不外置 YAML——超时/argv 是
逻辑不是配置；状态机库过重）：

- `StepSpec`：name/kind（main|side|disclosure|conditional|gate|enhancement）/
  fail_semantics（abort|warn|rc0|skip）/purpose/timeout/trigger_id/depends_on/
  produces/degrade_note，**构造时受控值校验**（不合规即导入失败）；
- `LayerSpec` + `LAYER_EXEC_PLAN`：执行顺序单一声明处（层可拆多段插入，精确复刻
  现行 31 步顺序——方案示例与实证的顺序差异已校正：verify 必须先于 consolidate）；
- `STEP_ORDER` 为**派生兼容视图**（`--from`/`--only`/`--list-steps`/门禁判据 R 语义不变）；
- 失败语义由**执行器强制**（`failure_action` 矩阵 + `_stop_requested` 统一判定），
  旁路 WARN 归一、披露 rc0 强制——机制 > 约定。

## 后果

- README §8 / MCP 编排描述 / 多体系频度调度全部派生自同一事实源；
- `tests/test_chain_equivalence.py` 对 golden 夹具逐行比对 `--list-steps`/`--dry-run`
  （步骤清单口径未变的机器证明）；
- 架构共识图（README §4.1）由 `gen_flow_map` 生成 + `--check` 入 CI（静态图必漂移）。
