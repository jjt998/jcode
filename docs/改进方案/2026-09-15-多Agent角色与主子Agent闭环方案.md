# 多 Agent 角色与主子 Agent 闭环方案

## 1. 目标

在保留“一个主 Agent 统一编排多个子 Agent”结构的前提下，把当前只有 `Explore` 和 `worker` 两个类型的占位式子任务，升级为可执行、可治理、可验收的多角色子 Agent 闭环。

本方案的目标是：

- 扩展固定的子 Agent 身份，明确每个身份的职责和工具面。
- 让子 Agent 使用真实的 Provider、Context 和 ToolExecutor 链路。
- 让主 Agent 统一负责任务拆分、委派、结果验收和最终交付。
- 记录子 Agent 的工具调用、文件变更、shell 副作用和失败证据。
- 保持单层主-子结构，不引入子 Agent 派生子 Agent 或子 Agent 间直连通信。

## 2. 当前问题

当前子 Agent 入口位于 `src/tools/subagents.py`、`src/workers/manager.py`、`src/workers/runtime.py` 和 `src/runtime/agent.py`。

当前链路可以创建 worker、保存消息并执行 `wait_subagent`，但 `WorkerRuntime.run()` 只拼接 prompt 和消息，不调用 `ModelRouter`，也不构建子 Agent 上下文或执行工具。因此当前多 Agent 只是生命周期占位，不是真实的多角色执行闭环。

当前还存在以下边界：

- 角色校验分散在主 Agent 和 WorkerManager 中。
- `worker` profile 不允许 `run_shell`，没有独立的验证角色。
- `write_scope` 尚未成为完整的子 Agent 写入约束。
- 子 Agent 工具绕过了主 Agent 的标准 ToolExecutor 调用入口。
- `WorkerResult` 只有 worker ID、状态和文本，不能表达验证证据和副作用。
- checkpoint 只保存 worker 引用，恢复后不能重新加载可运行的 worker。

完整工具治理背景见 [工具治理完整链路](../工具治理完整链路.md)。

## 3. 范围和边界

### 3.1 本方案包含

- 固定角色注册表。
- 子 Agent 到 Tool Profile 的明确映射。
- 独立的子 Agent 执行循环。
- 主 Agent 到子 Agent 的任务包和消息协议。
- 结构化结果、trace、artifact 和文件变更记录。
- tester 的 `run_shell` 副作用治理。
- 子 Agent 结果回传后的主 Agent 二次验收。
- 运行内任务的 checkpoint 和恢复边界。
- 角色、工具、结果和错误语义的测试要求。

### 3.2 本方案不包含

- 子 Agent 创建子 Agent。
- 子 Agent 之间的直接通信。
- 多 Agent DAG 或自动任务依赖调度。
- 第一版后台并行执行。
- 跨会话自动恢复未完成的子 Agent 模型上下文。
- 动态加载任意角色定义。
- 用模型自由生成新的身份或修改角色权限。

## 4. 角色模型

角色 ID 是受控协议字段，不是普通提示词。角色在任务创建时解析并冻结，运行中不可由模型修改。

第一版固定以下五种角色：

| 角色 | 职责 | 工具面 | 写入能力 | 主要输出 |
|---|---|---|---|---|
| `explorer` | 调查代码、文档、依赖和当前实现 | `readonly` | 无 | 事实、证据路径、未知项 |
| `planner` | 拆分任务、依赖和验收条件 | `readonly` | 无 | 有序计划和验收标准 |
| `worker` | 修改代码或文档 | `worker` | 允许，受 `write_scope` 限制 | 修改摘要、变更文件、未完成项 |
| `tester` | 执行测试、编译、lint、构建和必要的验证命令 | `tester` | 禁止 `write_file`、`apply_patch` | 命令、返回码、验证结论、副作用 |
| `reviewer` | 审查实现质量、协议偏差和遗漏 | `readonly` | 无 | 按严重级别排列的审查发现 |

`planner` 与 `explorer` 使用相同的只读工具面，但输出契约不同。`planner` 不进入 plan mode，也不修改 plan artifact；主 Agent 仍然拥有最终计划控制权。

第一版不增加 `documenter`。文档修改可以由 `worker` 通过显式 `write_scope` 完成，避免产生只有提示词不同、执行模型相同的冗余角色。

## 5. RoleSpec

角色注册表集中定义角色能力，不再在 `JCodeAgent._handle_subagent_tool()` 中散落角色分支。

每个角色至少包含：

```text
role_id
display_name
system_instruction
tool_profile
allow_write
allow_shell
allow_nested_spawn
output_contract
max_steps
```

第一版所有角色的 `max_steps` 都冻结为创建任务时的 `AppConfig.max_steps`，不为不同角色引入额外默认步数。

建议固定映射如下：

```text
explorer:
  tool_profile = readonly
  allow_write = false
  allow_shell = false
  allow_nested_spawn = false

planner:
  tool_profile = readonly
  allow_write = false
  allow_shell = false
  allow_nested_spawn = false

worker:
  tool_profile = worker
  allow_write = true
  allow_shell = false
  allow_nested_spawn = false

tester:
  tool_profile = tester
  allow_write = false
  allow_shell = true
  allow_nested_spawn = false

reviewer:
  tool_profile = readonly
  allow_write = false
  allow_shell = false
  allow_nested_spawn = false
```

角色 ID 建议统一使用小写。协议切换后只接受当前角色集合，不增加旧类型的隐式别名或兼容兜底。

父 Agent 处于 plan mode 时，只允许创建 `explorer`、`planner` 和 `reviewer`。`worker` 有直接写入能力，`tester` 有 shell 能力，两者在 plan mode 下都必须由 Tool Profile 拒绝。只读子 Agent 不进入自己的 plan mode，也不能写 active plan artifact；计划文件仍由主 Agent 负责。

## 6. Tool Profile

### 6.1 `readonly`

只包含当前注册表中 `read_only=True` 的工具：

- `read_file`
- `list_files`
- `search`

不允许写文件、shell、用户询问、计划切换和子 Agent 工具。

### 6.2 `worker`

沿用当前 worker 工具面，允许读取、搜索、写文件和精确 patch，但不允许：

- `run_shell`
- `ask_user`
- `enter_plan_mode`
- `exit_plan_mode`
- 子 Agent 工具

写入必须通过显式 `write_scope` 约束。

### 6.3 `tester`

新增 tester 工具面：

```text
readonly_tools + run_shell
```

明确禁止：

- `write_file`
- `apply_patch`
- `ask_user`
- `enter_plan_mode`
- `exit_plan_mode`
- `spawn_subagent`
- `send_subagent_message`
- `wait_subagent`

tester 不能直接编辑文件，但 `run_shell` 本身可能产生文件副作用。该副作用不通过默认值、自动清理或回滚处理，直接按照现有 risky 工具快照机制记录。

## 7. 主 Agent 与子 Agent 的关系

系统保持单层主-子结构：

```text
JCodeAgent
  -> WorkerManager
      -> WorkerTask
          -> SubagentRunner
              -> Provider
              -> ContextManager
              -> ToolExecutor
```

主 Agent 是唯一编排者，负责：

1. 判断是否需要委派。
2. 选择角色。
3. 提供任务目标、约束和验收条件。
4. 分配不冲突的 `write_scope`。
5. 发送补充消息。
6. 等待并接收结构化结果。
7. 验证子 Agent 声称的文件、测试和产物。
8. 决定继续委派、修正还是最终交付。

子 Agent 不得：

- 创建新的子 Agent。
- 直接向其他子 Agent 发送消息。
- 询问用户。
- 修改自己的角色和工具面。
- 把未验证的文本结论伪装成执行证据。

## 8. 任务创建协议

新协议将 `subagent_type` 替换为语义明确的 `role`。`spawn_subagent` 创建任务时，主 Agent 提供：

```text
prompt
role
acceptance_criteria
write_scope
```

字段约束：

- `prompt`：非空的单一任务目标。
- `role`：RoleRegistry 中的固定角色 ID。
- `acceptance_criteria`：至少一条可检查的完成条件。
- `write_scope`：只有 `worker` 可以非空，其他角色必须为空。

WorkerManager 完成以下校验：

1. 角色必须存在于 RoleRegistry。
2. 只读角色的 `write_scope` 必须为空。
3. `worker` 的写入必须落在显式 `write_scope` 内。
4. 角色能力必须与 Tool Profile 一致。
5. 不允许嵌套子 Agent 能力。
6. 角色配置、模型档案和权限快照写入 worker 任务状态。

创建后的任务状态为 `created`，此时不请求 Provider，不执行工具。

`send_subagent_message` 只允许向 `created` 状态的任务补充消息。任务进入 `running` 或任一终态后发送消息，返回 `subagent_not_messageable`，不修改已冻结输入。

## 9. 子 Agent 任务上下文

子 Agent 不继承主 Agent 的完整 history 和 Working Memory，只接收独立任务包：

```text
parent_session_id
parent_run_id
worker_id
role
task_prompt
constraints
write_scope
acceptance_criteria
role_snapshot
parent_messages
```

其中 `constraints` 由冻结的 RoleSpec、当前项目规则和 `write_scope` 生成，不接受模型额外提交同名自由文本覆盖系统约束。

子 Agent 自己创建独立的：

- ContextManager 输入。
- History。
- TaskState。
- Provider continuation。
- 工具调用 trace。
- 运行 checkpoint。

子 Agent 可以通过 `read_file`、`search` 和 `list_files` 获取当前工作区事实，不通过复制主 Agent 历史来获得隐式上下文。

## 10. 子 Agent 执行闭环

推荐执行链路：

```text
wait_subagent
  -> WorkerTask 状态变为 running
  -> SubagentRunner 构建角色上下文
  -> Provider 返回文本或原生工具调用
  -> ToolExecutor 按角色 profile 执行
  -> 写入子 Agent history、trace 和 checkpoint
  -> 继续模型回合，直到完成或停止
  -> 生成 WorkerResult
  -> WorkerTask 状态变为终态
  -> 返回父 Agent
```

第一版保持同步等待。`wait_subagent` 负责启动并等待单个任务结束，不引入后台线程、队列或并发调度。

状态迁移固定为：

```text
created -> running -> completed | failed | partial_success | interrupted | timeout
```

同一 worker 第一次 `wait_subagent` 触发执行。对终态 worker 再次调用 `wait_subagent` 时，只读取并返回已经持久化的 `result.json`，不得再次请求 Provider 或重复执行工具。

`WorkerRuntime` 应成为任务状态容器，真实执行逻辑由独立的 `SubagentRunner` 负责，避免继续在 `WorkerRuntime.run()` 中伪造结果。

## 11. tester 的 shell 副作用

tester 执行 `run_shell` 时复用现有工具副作用实现：

```text
执行前 workspace.snapshot()
  -> 执行 run_shell
  -> 执行后 workspace.snapshot()
  -> 计算 changed_files
  -> 写入 ToolResult、TaskState、trace 和 WorkerResult
```

状态语义如下：

| 情况 | 状态 |
|---|---|
| 命令成功且无文件变化 | `completed` |
| 命令成功且检测到文件变化 | `completed`，保留 `changed_files` |
| 命令失败且无文件变化 | `failed` |
| 命令失败且检测到文件变化 | `partial_success` |
| 快照异常导致无法确认变化 | 记录 `snapshot_uncertain=true` |
| 用户中止 shell | `interrupted` |

不得因为 tester 是“验证角色”而静默删除、回滚、忽略或压低 shell 副作用。

父 Agent 收到 tester 结果后必须：

1. 检查 `changed_files`。
2. 对受影响文件重新读取或检查 freshness。
3. 判断变化是否符合验证命令预期。
4. 将副作用纳入最终运行的 changed files 和 stale 证据治理。
5. 不能只依据 shell 返回码认定任务完成。

## 12. WorkerResult

子 Agent 结果不能只有一段文本，至少包含：

```text
worker_id
role
status
summary
changed_files
artifacts
verification
tool_failures
stop_reason
steps
model_profile
```

状态固定为：

```text
created
running
completed
failed
partial_success
interrupted
timeout
```

结果语义：

- `summary` 是子 Agent 的解释，不是独立证据。
- `changed_files` 来自工具结果和工作区快照。
- `verification` 来自可识别的测试、编译、lint、类型检查或构建命令。
- `artifacts` 指向完整输出、结果文件或必要的审计文件。
- `tool_failures` 必须保留未解决工具失败。
- `stop_reason` 说明是正常完成、步数耗尽、Provider 错误、用户中止还是工具失败。

## 13. 父 Agent 验收

主 Agent 接收结果后，不得直接把子 Agent 文本当作最终事实。

### 13.1 explorer

主 Agent 可使用其发现作为调查输入，但重要路径、接口和结论必须通过主 Agent 自己的工具调用或后续 worker 任务验证。

### 13.2 planner

主 Agent 判断计划是否覆盖用户目标、依赖关系和验收条件。planner 不能替代主 Agent 的最终计划判断。

### 13.3 worker

主 Agent 重新读取 `changed_files`，必要时调用 tester 验证。worker 声称完成但没有文件证据时，不得视为完成。

### 13.4 tester

主 Agent 检查命令、返回码、验证类别、changed files 和副作用是否一致。测试通过不等于工作区没有变化。

### 13.5 reviewer

主 Agent 处理 reviewer 的发现，按严重级别决定是否重新派发 worker。reviewer 不直接改变任务状态。

## 14. 工具治理接入

所有子 Agent 工具调用必须进入现有 `ToolExecutor`，并携带：

```text
source = subagent
worker_id
role
parent_run_id
```

不能由 WorkerManager 绕过参数校验、Tool Profile、写入范围、重复调用、权限、sandbox、快照和结果脱敏治理。

父 Agent 调用的三个子 Agent 生命周期工具也必须进入 ToolExecutor。`spawn_subagent` 和 `send_subagent_message` 只改变运行状态；`wait_subagent` 可能触发 worker 写文件或执行 shell，因此标记为 risky，并由 ToolExecutor 对整个等待过程执行工作区前后快照。子 Agent 内部每个具体工具调用仍分别执行自身的治理和快照。

子 Agent 工具调用的特别拒绝规则：

- 任何角色调用子 Agent 工具：`nested_subagent_not_allowed`。
- 只读角色提供非空 `write_scope`：`write_scope_not_allowed`。
- tester 调用 `write_file` 或 `apply_patch`：`tool_profile_denied`。
- tester 通过 shell 修改文件：允许执行，但必须记录副作用。
- 子 Agent 调用 `ask_user`：`interactive_input_not_allowed`。

## 15. 持久化结构

每个 worker 目录保存：

```text
.jcode/workers/<worker_id>/
  task_state.json
  trace.jsonl
  result.json
  artifacts/
```

`task_state.json` 至少保存：

```text
worker_id
parent_session_id
parent_run_id
role
role_snapshot
status
prompt
messages
write_scope
model_profile
created_at
updated_at
```

`trace.jsonl` 至少记录：

```text
subagent_created
subagent_started
model_requested
model_responded
tool_requested
tool_executed
checkpoint_created
subagent_completed
subagent_failed
```

父 Agent 的 session history 仍然记录 `spawn_subagent`、`send_subagent_message` 和 `wait_subagent` 的工具调用原子；子 Agent 的详细 history 保存在自己的 worker 目录中。

## 16. Checkpoint 与恢复边界

父 Agent checkpoint 继续记录 worker 引用，但增加角色和终态摘要：

```text
worker_id
role
status
result_ref
updated_at
```

第一版子 Agent 定义为运行内任务：

- 已完成任务可以在恢复后作为审计事实读取。
- 未完成任务恢复后不自动重跑。
- 不隐式恢复未完成的 Provider continuation 和工具调用。
- 如需继续执行，主 Agent 创建一个新的子 Agent 任务。

这样可以避免恢复时重复执行写操作，也不引入隐藏的恢复兜底。

## 17. 错误和失败语义

### 角色无效

创建阶段直接拒绝，返回 `invalid_subagent_role`，不创建 worker 目录，不请求 Provider。

### 子 Agent Provider 失败

子任务进入 `failed`，保留错误类型、请求摘要和 trace；父 Agent 收到失败结果后自行决定是否重新委派，不由 WorkerManager 自动重试。

### 子 Agent 工具失败

按现有 ToolResult 状态写入子 Agent TaskState。未解决失败进入 `tool_failures`，不能在返回结果时静默删除。

### 子 Agent 写入失败

如果工作区已发生变化，按现有 partial success 规则记录 changed files 和副作用；如果没有变化，则返回明确失败。

### 子 Agent 超步数

任务状态进入 `timeout`，`stop_reason` 固定为 `step_limit_reached`，保留已有工具结果和副作用，不将任务标记为完成。

### 父 Agent 中止

尚未执行的子 Agent 工具调用写入 cancelled；已经开始的 shell 或工具按 interrupted/partial_success 规则记录，不能伪装成成功。

## 18. 实施阶段

### 阶段一：角色协议和工具面

- 建立 RoleRegistry 和 RoleSpec。
- 固定五种角色。
- 将 `spawn_subagent.subagent_type` 替换为 `role`，增加必填 `acceptance_criteria`。
- 增加 `tester` Tool Profile。
- 统一角色校验和 nested spawn 拒绝。
- 让 `write_scope` 在角色层生效。

### 阶段二：真实子 Agent Runner

- 将 WorkerRuntime 收敛为状态容器。
- 新增独立 SubagentRunner。
- 构建子 Agent 独立 Context、History 和 TaskState。
- 复用 ModelRouter 和 ToolExecutor。
- 记录完整 worker trace、checkpoint 和 result。

### 阶段三：父 Agent 验收闭环

- 结构化接收 WorkerResult。
- 父 Agent 验证 changed files。
- tester 结果进入父任务 verification。
- reviewer 结果进入最终收口判断。
- 子 Agent 结果按 artifact 和摘要规则回注父 Agent。

### 阶段四：评测和文档同步

- 增加五种角色的 profile 测试。
- 增加 tester shell 副作用测试。
- 增加 nested spawn、write scope 和角色越权测试。
- 增加 Provider 失败、工具失败、partial success 和中止测试。
- 更新工具治理链路和项目完整链路文档。

## 19. 验收标准

以下条件全部满足，才算完成本方案的最小闭环：

1. 五种角色只能通过固定 RoleRegistry 创建。
2. explorer、planner、reviewer 不能写文件或执行 shell；plan mode 只允许创建这三种角色。
3. worker 只能在显式 `write_scope` 内写入。
4. tester 可以执行 `run_shell`，但不能调用 `write_file` 和 `apply_patch`。
5. tester shell 的 changed files、snapshot uncertainty 和 partial success 都可审计。
6. 子 Agent 使用真实 Provider 和 ToolExecutor，不再返回拼接 prompt 的伪结果。
7. 子 Agent 不能创建子 Agent 或直接联系其他子 Agent。
8. 主 Agent 能收到结构化 WorkerResult，并进行二次验收。
9. 子 Agent 的 history、trace、checkpoint 和 result 可以独立读取。
10. Provider、工具、权限、路径、shell 和用户中止失败都保留明确语义。
11. 恢复不会自动重跑未完成子任务或重复执行写操作。
12. 现有主 Agent 工具治理规则继续生效。

## 20. 后续可选扩展

只有本方案完成并稳定后，才考虑：

- 多个子 Agent 的并行等待。
- 主 Agent 对多个结果的显式合并协议。
- 跨运行恢复仍处于 `created/running` 的任务。
- 独立的 `documenter` 角色。
- 受控的任务依赖图。

这些能力不属于当前最小闭环，不能在第一版实现中隐式加入。
