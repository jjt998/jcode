# FinalGate 改进方案 9.6：最终实施方案

## 目标与冻结约束

本方案将 Final Gate 从最终答案阻断器改为 Agent 有限纠正和双质量观察器。到达 Final Gate 后，Harness 必须完成答案收口并将 `run_status` 标记为 `completed`；评分、证据不足、外部故障和持久化异常不得再产生 `final_gate_blocked` 或拒绝非空 final。

实现固定遵守以下约束：

- 仅评分 `agent_quality` 与 `harness_quality`；Provider、环境和权限仅为事件根因。
- 不再使用 `answer_accepted`、`system_health`、单一 `quality` 和 `final_gate_blocked`。
- Harness/外部事件不得触发 Agent 重跑；Harness 只做自身恢复与审计。
- Agent 重跑使用同一 run continuation，单 reason 最多 1 次、每个 run 总共最多 2 次；`changed_paths_without_verification` 与 `unresolved_todo` 最多可获得两次机会但共享总预算。
- `partial_success` 和未知副作用暂为 Harness 能力缺口，新增代码保留简明中文注释，禁止升为 Agent hard。
- 不做旧 session、checkpoint 或评分协议兼容兜底，按新 schema 直接读写。

## 目标数据契约

在 TaskState、checkpoint、session、report 和 trace 使用同一 Final Gate 快照：

```json
{
  "run_status": "completed",
  "final_content": {"status": "present|empty", "sha256": ""},
  "assurance": {"final_text": "verified|unverified", "execution_evidence": "verified|unverified", "runtime_state": "verified|unverified"},
  "agent_quality": {"level": "green|yellow|red", "reasons": [], "interventions": []},
  "harness_quality": {"level": "green|yellow|red", "reasons": [], "recovery_actions": []},
  "events": [],
  "interventions": [],
  "finalization": {"status": "committed|persistence_unverified"}
}
```

每个 event 至少包含 `event_id`、`run_id`、`step_index`、`event_type`、`cause`、`owner`、`status`、`evidence`、时间戳。`cause` 固定为 `agent|harness|provider|environment|unknown`；`owner` 仅为 `agent|harness`。

每个 intervention 至少包含 `intervention_id`、关联 event/reason、`kind`、`actor`、attempt、预算前后值、结果和时间戳。kind 固定为 `agent_rerun`、`harness_retry`、`harness_recovery`、`warning_only`、`safe_finalize`。

## 阶段 0：删除旧 Gate 语义

1. 定位 `src/policy/final_gate.py`、`src/runtime/final_readiness.py`、`src/runtime/agent.py` 中的 `block`、`runtime_notice`、`final_gate_blocked` 和“最终文本必须提及失败”的路径。
2. 删除 Gate 将 Agent nonempty final 变为停止结果的控制流。
3. 保留当前结构化 evidence 采集，但将 Gate 输出改为 `rerun_agent` 或 `safe_finalize`。
4. 增加回归测试：hard、重复 soft、预算耗尽时均不再产生 `final_gate_blocked`，最终 `run_status` 均为 `completed`。

## 阶段 1：事件、Agent failure 与 Harness 事件分离

1. 在 `TaskState` 新增独立的 Agent failure、Harness event、intervention、需求台账和 finalization 快照字段；每个新增 dataclass 成员写右侧中文注释。
2. 保留 Agent 四态：`soft_unresolved`、`soft_solved`、`hard_unresolved`、`hard_solved`。
3. 把工具结果转为 event 后再归因：Agent 参数/路径/协议错误进入 Agent；Provider、环境、权限、sandbox、文件系统和不可信状态进入 Harness 事件。
4. 对 `partial_success` 增加中文注释，明确当前多工具副作用无法精确归因，固定为 Harness 能力缺口。
5. 测试事件状态迁移、failure 不被最终文本关闭、同一 failure 不跨等级迁移。

## 阶段 2：需求台账与验收谓词

1. 新增 `requirement_ledger`，仅抽取 `user_explicit` 的文件产物、文件变更、明确验证命令、语义目标和执行约束。
2. 文件需求基础谓词为存在、可读、非空；JSON 等结构化输出增加可解析性和用户明确的必填字段谓词。
3. 语义目标无用户明确验收谓词时只保留证据状态，不能自动生成 Agent red。
4. 只有“用户要求 + 台账 + 谓词可信为 false + Agent 可归责”才能生成 `delivery_action_failure`。
5. 测试“参考路径”不被抽取为交付物、artifact 不可读为 unverifiable 而非 Agent 失败、撤回要求的台账版本化。

## 阶段 3：分类器与双评分聚合

1. 实现 Agent reason 分类：交付、验证、plan artifact、关键工具失败为 hard；辅助工具失败、重复拒绝、未验证变更、未完成 todo 为 soft。
2. 将 `final_context_exceeds_window` 固定为 Harness red，不允许进入下一次 Agent 推理。
3. 实现 Harness 评分：正确恢复或安全收口外部故障可为 green；可信降级和能力缺口为 yellow；伪造成功、漏记、错误归因、不可信续接、越过容量约束为 red。
4. `assurance` 的 `verified|unverified` 与评分分离；unverified 本身不自动扣 Harness 分。
5. 测试 fsync/磁盘满、Provider 超时、权限拒绝、未知副作用、调用配对断裂和超窗上下文的归因与评分。

## 阶段 4：有限重跑和 Correction Packet

1. 实现稳定 evidence fingerprint，键为 reason、关联需求、关键证据摘要。
2. 仅为允许纠正的 Agent reason 创建 correction packet；packet 只含最小事实、所需动作、禁止动作、剩余预算。
3. 单 reason 预算 1、run 总预算 2；`changed_paths_without_verification` 和 `unresolved_todo` 可以两次尝试但共享总预算。
4. 相同指纹重现时不重复重跑；预算耗尽后记录建议并 `safe_finalize`。
5. 测试同一 run 的 continuation 不丢失 History、台账、失败、干预和预算；测试不会无限循环。

## 阶段 5：Assurance、收口候选与幂等提交

1. 分别计算 `final_text`、`execution_evidence`、`runtime_state` 的 assurance。
2. 冻结 evidence 快照，构造带 `finalization_id` 的 candidate；同一 candidate 重放不得重复消费重跑预算或新增重复 final。
3. 按“candidate artifact 写入并读回 -> 原子提交 session/checkpoint 指针 -> 写入 completed 审计”提交。
4. 失败时写 `persistence_unverified`，不得伪造 committed、不得让 Agent 重跑、不得拒绝 final。
5. 测试 final 为空、final 存在但 artifact 不可信、证据冲突、持久化失败、重复 Final Gate 调用。

## 阶段 6：报告、可见性与恢复

1. report 保存完整 events、interventions、assurance、双评分和原始证据摘要；trace 保存时序事件。
2. session 保存 final 原文、台账、活动 finalization 引用和最小恢复摘要；checkpoint 保存 continuation、工具状态、预算和最小快照。
3. Agent 上下文只接收 correction packet，禁止注入评分标签、完整 trace、Harness 内部错误或旧 run 事件。
4. 用户侧只显示 Agent final；后台读取完整诊断。
5. 测试 resume 从结构化状态重新聚合同一评分；新 run 不继承旧 run 的 failure 和预算。

## 阶段 7：全量回归与删除扫描

1. 使用项目指定解释器先运行 Final Gate、TaskState、artifact、checkpoint、resume、context 和 web 相关定向测试。
2. 运行全量测试：

```powershell
& 'D:\jt\ANACONDA\envs_dirs\jcode\python.exe' -m pytest
```

3. 运行 `git diff --check`。
4. 扫描并删除旧协议路径：`final_gate_blocked`、`answer_accepted`、`system_health`、单一 `quality`、将所有 unresolved 工具失败直接标 hard 的逻辑，以及 final 文本必须提及失败的提示。
5. 验收要求：任何到达 Final Gate 的 run 都以 `completed` 收口；只允许有限 Agent 重跑；Harness/外部事件从不触发 Agent 重跑；后台可从持久化证据重建双评分。

## 完成标准

- Final Gate 不再是答案拒绝器。
- Agent 与 Harness 的评分、事件根因和 assurance 互相独立。
- Agent 质量差只触发有限纠正和后台评分，不能无限阻断 final。
- Harness 正确处理外部故障不扣分；处理失当才产生 Harness yellow/red。
- 用户只看到 Agent final，后台可以完整回放本次运行的质量、证据和干预。
