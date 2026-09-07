# FinalGate 从答案阻断转为双质量观察：最终讨论方案

## 冻结结论

Final Gate 是 Harness 的最终观察与有限纠正组件。它不替用户接受或拒绝答案；一旦到达 Final Gate，Harness 都完成本次答案收口，并将 `run_status` 写为 `completed`。没有非空 final 时记录 empty 事实，Harness 不得编造用户可见回答。

Final Gate 固定产出两份评分：

1. `agent_quality`：评价 Agent 的决策、工具请求、交付完成、验证和补救。
2. `harness_quality`：评价 Harness 的执行护栏、证据、状态、恢复、止损和收口质量。

Provider、磁盘、操作系统、网络、权限和 sandbox 只作为事件根因，不是第三个评分主体。评分只评价 Agent 与 Harness 对事件的处置。

```json
{
  "run_status": "completed",
  "final_content": {"status": "present|empty", "sha256": ""},
  "assurance": {"final_text": "verified|unverified", "execution_evidence": "verified|unverified", "runtime_state": "verified|unverified"},
  "agent_quality": {"level": "green|yellow|red", "reasons": []},
  "harness_quality": {"level": "green|yellow|red", "reasons": []},
  "events": [],
  "interventions": [],
  "finalization": {"status": "committed|persistence_unverified"}
}
```

协议不再使用 `answer_accepted`、`system_health`、单一 `quality` 或 `final_gate_blocked`。

## 归因与评分

每个事件保存 `cause` 与 `owner`：

```text
cause = agent | harness | provider | environment | unknown
owner = agent | harness
```

`cause` 解释事实来源；`owner` 表示需要评价谁的处置行为。外部故障本身不自动扣任何一方的分。

例如磁盘空间不足导致 `fsync` 失败：`cause = environment`、`owner = harness`。Harness 若固定重试、完整审计、拒绝伪造 artifact 成功并正确收口，两个评分都不降级；只有漏记、伪造成功、错误归因、破坏一致性或越过治理边界时，才为 Harness red。

## Agent 评分和有限重跑

- green：没有 Agent 软/硬事件，也没有 Gate 纠正。
- yellow：有 soft，或历史 hard 已由 Gate 重跑解决。
- red：存在 `hard_unresolved`，或存在不可逆的 Agent hard 约束违规。

| Agent reason | 等级 | Gate 行为 |
| --- | --- | --- |
| `empty_final` | red | 请求 1 次补出 final；仍为空后收口 |
| `delivery_action_failure`、`failed_verification` | red | 单 reason 最多重跑 1 次 |
| `plan_artifact_not_ready`、关键 Agent `unresolved_tool_failure` | red | 单 reason 最多重跑 1 次 |
| `changed_paths_without_verification`、`unresolved_todo` | yellow | 最多重跑 2 次 |
| Agent 的 inspection、统计、扫描、维护失败 | yellow | 只给建议，不重跑 |
| 收到权限、sandbox 或策略拒绝后重复尝试 | yellow | 只给建议，不重跑 |

每个 run 的 Agent 重跑总预算固定为 2。相同 `reason_code + evidence_fingerprint` 不得再次发起同样重跑。预算耗尽后直接收口，保留评分和建议。

`final_context_exceeds_window` 是 Harness red：不得继续下一次推理，不触发 Agent 重跑，但仍然完成最终收口。

## 交付、工具和状态

`delivery_action_failure` 必须由需求台账和验收谓词判定。只有“用户明确要求 + 台账存在 + 谓词可信为 false + Agent 可归责”同时成立，才是 Agent hard。artifact 无法读回、验签失败或事实无法可信求值时，不处罚 Agent。

工具失败划分如下：

- `delivery_action_failure`：Agent 可归责的用户交付失败，Agent red，可重跑。
- `critical_state_action_failure`：调用配对、continuation、checkpoint、session 或状态一致性异常，由 Harness 处置；正确收口不扣分，伪造或破坏状态才 Harness red。
- `noncritical_tool_failure`：inspection 读取事实、统计计算度量、扫描发现路径/状态。Agent 参数、命令、路径错误为 Agent yellow；外部执行失败只评价 Harness 处置。统计本身是用户交付时回到需求台账。
- `destructive_or_unknown_side_effect`：`partial_success` 或中止后副作用未知，是 Harness 能力缺口。正确隔离、记录并收口为 Harness yellow；假定成功、继续不可信运行或丢失证据为 Harness red。代码必须保留中文注释说明多工具副作用当前不能准确归因。

Agent failure 只允许 `soft_unresolved -> soft_solved` 或 `hard_unresolved -> hard_solved`。最终文本不得改变 failure 状态。

## Assurance、事件和收口

`assurance` 分别记录 final 原文、执行证据、运行状态是否可信。`unverified` 只是事实状态，不自动等于 Harness red；Harness 是否正确面对不确定性才影响评分。

事件生命周期固定为：

```text
observed -> attributed -> handling -> resolved|safely_finalized|unresolved|mishandled
```

干预固定为：`agent_rerun`、`harness_retry`、`harness_recovery`、`warning_only`、`safe_finalize`。评分必须由 events、interventions、需求台账和 assurance 重建，不依赖 Final Gate 临时内存。

收口固定流程：

```text
冻结证据
-> 计算双评分与 assurance
-> 可纠正 Agent 问题且有预算时注入 correction packet
-> 否则形成 finalization candidate
-> 尝试持久化和读回校验
-> safe_finalize
-> run_status = completed
```

候选持久化失败时，`finalization.status = persistence_unverified`；不重跑 Agent、不拒绝 final，改由 assurance 和 Harness 评分完整记录。

## 可见性

用户只看 Agent final 原文。Agent 仅在重跑时收到最小 correction packet，包括当前 reason、可信证据、明确补救动作和剩余预算。双评分、完整 events、trace、Harness 内部错误和敏感信息只能进入后台 report/trace；不得污染下一轮 Agent History。

完整实施阶段、数据契约和验收条件见 [FinalGate 改进方案 9.6 最终实施方案](../最终实施方案.md)。
