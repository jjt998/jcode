# FinalGate 从答案阻断转为运行质量观察

## 发现日期

2026-09-06

## 场景反思

snake_game 会话 `20260906T094836.064512+0000-c046a5` 的运行中，`wc` 在 Windows 环境不可用，随后一条 `node -e` 检查又因引号转义失败。之后已经用 PowerShell 完成统计，并用 `node check.js` 完成游戏结构检查，但旧 Final Gate 仍以 `unresolved_tool_failure` 拒绝最终答案。

这次场景暴露的不是“答案不应被接受”，而是职责边界问题：最终答案是否被用户接受，不应由系统代替用户裁决。系统需要把 Agent 的运行质量与 Harness 的处置质量分开观察，再根据运行评分决定是否进行有限纠正。

## 需要重新界定的边界

- Final Gate 同时产出 Agent 运行评分和 Harness 质量诊断。
- 运行评分可以对明确可纠正的问题触发有限重跑，但不替用户裁决答案价值。
- artifact、Provider、checkpoint/session 等外部或执行故障不直接计入 Agent 运行评分；只评价 Harness 的处置质量。
- 运行评分分为全绿、黄色、红色，分别对应无问题、有 soft、有 hard。
- Web 前端保持统一的正常完成展示，颜色只进入后台 report、trace、checkpoint 和 session。
- Agent 可根据系统警告选择重跑或修正，但系统不通过 Gate 循环替用户做最终取舍。
- 到达 Final Gate 后统一完成答案收口并记录 `run_status = completed`；没有 final 时记录 empty 事实，不伪造回答。

## 观察到的失败状态

工具失败采用 `soft_unresolved`、`soft_solved`、`hard_unresolved`、`hard_solved` 四态。检查、统计、扫描和维护类失败通常是 soft；真正影响交付、安全或状态一致性的关键失败才可成为 hard。`partial_success` 的多工具副作用暂时无法准确归因，固定为 soft，并保留中文代码注释提醒这一限制。

Artifact 完整性错误属于外部事件或 Harness 处置事件：瞬时写入错误固定重试两次，仍失败时停止原因为 `artifact_write_failure`；不可重试的完整性错误立即停止，原因为 `artifact_wholeness_error`。故障本身不计入任何一方评分，只有 Harness 漏记、伪造成功、错误恢复或破坏一致性时才记 Harness red。权限、sandbox 和策略拒绝同样先记录事件；Agent 收到拒绝后仍重复尝试时，才另计入 Agent soft。

当前分类已补齐 Provider/协议、用户中止、调用链、freshness、todo、上下文压力和持久化边界。完整矩阵及状态迁移规则见关联讨论；现有代码仍有旧的 hard/block 行为，实施时需按该矩阵整体调整。

## 关联讨论

[FinalGate 从答案阻断转为运行质量观察解决方案讨论](../BUGS-solve/2026-09-06-FinalGate从答案阻断转为运行质量观察.md)
