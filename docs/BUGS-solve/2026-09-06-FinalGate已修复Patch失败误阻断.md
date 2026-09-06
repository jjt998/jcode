# FinalGate 已修复 Patch 失败误阻断解决方案

## 目标

将“历史上曾失败”与“当前仍未解决”分离，让 Final Gate 只依据运行时结构化证据决定 `allow`、`runtime_notice` 或 `block`。

## 实施内容

### 1. 工具失败状态分层

`TaskState` 保留 `failed_tools` 作为历史审计，并新增：

- `tool_attempts`：本次运行全部工具尝试。
- `unresolved_tool_failures`：当前仍影响最终收口的失败。
- `resolved_tool_failures`：已被后续明确证据关闭的失败。
- `verification`：最近测试、编译、lint、类型检查或构建命令状态。
- `final_readiness_summary`：最近一次 Gate 决策。

`apply_patch` 的 `patch_nonunique` 失败仅在同一规范化路径的 `write_file` 成功、确实产生文件变更时，才能转入 `resolved_tool_failures`。测试、权限、sandbox、artifact 和子任务失败不能被写文件关闭。

### 2. 结构化 Final Readiness

新增 `src/runtime/final_readiness.py`，从 TaskState、session、workspace 与 `ctx_info` 生成：

- unresolved failures
- verification
- changed paths
- 明确声明的必需文件产物
- pending todos
- plan artifact 状态
- Context 压力、压缩状态和最终容量
- 带 severity/evidence 的 reasons

Final 文本中的“error”“失败”等词不参与工具失败状态转移。

### 3. Gate 决策

hard reason 立即 `block`：

- `unresolved_tool_failure`
- `failed_verification`
- `missing_required_artifact`
- `plan_artifact_not_ready`
- `final_context_exceeds_window`

soft reason 第一次返回 `runtime_notice`，相同 reason 未产生新证据时第二次 `block`，避免无限循环：

- `changed_paths_without_verification`
- `unresolved_todo`
- `context_pressure_without_compaction`

### 4. 文件写入与 Patch 诊断

- `write_file` 对已存在文件检查 read freshness；外部变更后返回 `write_conflict`，不覆盖文件。
- Working Memory 记录 `jcode_modified_files`，JCode 自己成功写入后刷新当前 freshness，允许同一 run 连续修改；外部 freshness 变化记录为 `external_stale_paths`。
- 外部冲突返回“该文件被外部势力改动了，请你重读后做下一步打算”提示，并在 policy metadata 中标记 `stale=true`。
- `apply_patch` 保留精确匹配，不做模糊替换。
- patch 匹配失败返回 path、match_count、freshness、newline 和 UTF-8 BOM 元数据，便于模型重新读取并生成正确操作。

### 5. 审计与持久化

新增或扩展 trace：

- `tool_failure_recorded`
- `tool_failure_resolved`
- `verification_recorded`
- `final_readiness_evaluated`
- `final_gate_blocked`

checkpoint 和 report 保存 unresolved/resolved failures、verification 与最终 readiness decision；Web 时间线展示 Gate 评估和阻断事件。

## 验证

```text
D:\jt\ANACONDA\envs_dirs\jcode\python.exe -m pytest
68 passed

D:\jt\ANACONDA\envs_dirs\jcode\python.exe -m compileall -q src test
通过

git diff --check
通过
```

覆盖场景：

- patch 失败后同路径 write_file 成功，不再保留 unresolved patch failure。
- shell 验证失败不能由 write_file 关闭。
- changed files 未验证先 notice、同一状态再次收口时 block。
- 明确要求创建的文件缺失时 block。
- 外部变更后的 write_file 被 freshness 冲突阻断。

## 关联 Bug

[2026-09-06-FinalGate已修复Patch失败误阻断.md](../BUGS-after9.6/2026-09-06-FinalGate已修复Patch失败误阻断.md)
