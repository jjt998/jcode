# JCode 内部架构约定

JCode 的核心目标是保持本地 Coding Agent 的主链路清晰、轻量、可恢复、可审计。内部模块按单向依赖组织：入口负责装配，runtime 负责编排，其他子系统通过明确接口提供能力。

## 依赖方向

```text
app
-> runtime
-> context / providers / tools / policy / state / memory / workers / evidence
```

- `app` 只负责 CLI、配置读取和默认对象装配，不承载运行逻辑。
- `runtime` 由 `JCodeAgent` 持有主循环，负责 run 生命周期、动作解析、工具分发、终态收口和恢复后的运行衔接。
- `context` 只负责模型上下文拼装、预算估算和上下文区块渲染。
- `providers` 只负责模型协议适配和模型响应包装.
- `tools` 只负责工具定义、参数校验后的执行和工具结果返回；工具注册表不持有 workspace。
- `policy` 负责权限、工具规则、sandbox、重复调用和 final gate，策略检查不得长期持有会被 resume 替换的 working memory。
- `state` 负责 session、task、history、checkpoint、workspace fingerprint 和 resume context 的持久形状。
- `memory` 负责 working/durable memory、检索、安全过滤和轮次整理。
- `workers` 负责轻量子任务生命周期，父 Agent 只消费 worker 的状态、摘要和 artifact。
- `evidence` 负责 trace、session event、artifact 和 report，作为审计事实来源。

## 禁止事项

- 禁止重新引入独立 `Engine` 或 `completion` 这类只接收整个 `agent` 再反向访问所有依赖的假边界。
- 禁止让 `JCodeAgent` 之外的长期对象持有可被 resume 替换的 `working_memory`；需要时通过方法参数传入当前实例。
- 禁止在 `ToolRegistry` 上动态挂载 `workspace` 或其他运行态对象；registry 只保存工具定义。
- 禁止只在 `__init__` 中动态创建实例属性；类实例属性必须在类体中显式声明类型。
- 禁止把运行证据、session history、working memory、checkpoint 混成同一个事实来源：history 面向上下文，trace 面向审计，working memory 面向当前推理，checkpoint 面向恢复。
- 禁止在 provider/router 的内部运行链路继续使用 `prompt` 命名表示 JCode 拼装出的模型输入；统一使用 `context`。

## 工具与策略合同

- 工具执行入口先构造 `ToolCallRequest`，再形成 `ToolInvocation`，之后按 validation、call guard、tool policy、permission、sandbox、execution 的顺序推进。
- 所有治理层都归一为 `PolicyDecision`：`decision` 表示 allow/warn/deny，`reason` 是稳定原因码，`layer` 标记来源层。
- `ToolResult.metadata["policy"]` 保存本次工具调用经过的策略决策，便于 trace/report 审计。
- `ToolResult.status` 继续保持现有兼容值，`ToolResult.decision` 表示治理决策，`ToolResult.ok` 提供成功判断。
- 工具注册表只保存工具定义；workspace、working memory、run id 等运行态数据只能通过执行入口传递。

## 分层记忆合同

- `WorkingMemory` 使用 `jcode.layered_memory.v1` 持久形状，分为 `task`、`files`、`retrieval`、`tools`、`durable`、`safety` 六层。
- `retrieval` 保存最近一次查询和召回结果；上下文构建只读取 working memory 的渲染结果。
- `DurableMemoryStore` 同时保留旧 `notes.jsonl` 兼容入口，并维护 Pico 风格的 daily log、`MEMORY.md` 索引和 topic 文件。
- Daily log 位于 `.jcode/memory/logs/YYYY/MM/YYYY-MM-DD.md`，每轮结束把高层摘要写入追加日志。
- Topic consolidation 是确定性轻量整理：把 daily log 条目分类写入 `topics/project-conventions.md`、`topics/key-decisions.md`、`topics/dependency-facts.md`、`topics/user-preferences.md`，并更新 `MEMORY.md`。
- 当前 consolidation 不启动后台 agent，不读取大 transcript，不引入 Pico 的 Dream/TUI/media 运行面。

## 当前主链路

```text
cli -> bootstrap -> JCodeAgent.ask()
  -> _begin_run()
  -> _build_context()
  -> _call_model()
  -> _parse_action()
  -> _handle_final_action() / _handle_tool_action()
  -> _finish_run()
```

这条链路应该保持直白。新增能力优先放入对应子系统，通过 `JCodeAgent` 编排，不在主循环里堆积无关细节。
