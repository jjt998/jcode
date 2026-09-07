# 多 Provider + Responses API 适配改进计划书

## 0. 最终收敛决策

本节覆盖本文早期的 `Instruction`、`ContextEvent`、`ProviderContinuation` 设计，代码实现以本节为准。

- JCode 实例固定 `deepseek + openai_responses`；不支持跨 Provider 或跨协议切换。
- Provider 内仅能在 run 间切换模型和 reasoning effort。
- `ContextResult` 保留原五段业务语义：`prefix`、`skill`、`history`、`working_memory`、`current_request`，另含 `tools` 与审计 `ctx_info`。
- `prefix` 进入 Responses 顶层 `instructions`，作为稳定缓存前缀。
- `skill` 是 history 前的内部 user message；`working_memory` 是 history 后的内部 user message；`current_request` 始终是末尾真实 user message。
- `working_memory` 直接使用现有 `WorkingMemory` 的构建时快照，不引入 `WorkingMemoryContext`。
- `history` 使用 `HistoryEvent`，其中 `tool_call` 与 `tool_result` 由 `call_id` 关联；`ToolEvent` 仅是这两种事件的概念统称。
- 不引入 `ProviderContinuation`。DeepSeek 同 run 回放所需的 reasoning/function call 原生 item 存在关联 `HistoryEvent.metadata`。
- 不再对最终 history 文本调用 `tail_clip()`。仍保留四档压力治理、历史窗口外工具结果压缩、artifact 路径加摘要，以及压力等级 4 的 `compact_history()` / `compact_summary`。

```text
instructions: prefix
input:
  user: [JCode Skill Context] + skill
  native history events
  user: [JCode Working Memory] + working_memory
  user: current_request
```

## 1. 背景与问题

当前 JCode 将上下文拼接为单段文本，再要求模型用 XML 文本协议输出 `<tool>`、`<tools>` 或 `<final>`。该方式存在以下结构性问题：

- 模型会把历史中的 `[ToolResult ...]` 续写到自身输出，造成伪造工具结果。
- 模型输出的 XML 块与原生思考内容容易互相干扰。
- DeepSeek 在思考模式工具调用中允许中间轮 `content` 为空；XML 协议会将其误判为解析失败。
- 工具调用 id、工具结果和思考内容没有作为 API 原生结构传递，无法可靠支撑多轮原生工具调用。
- 输出协议、上下文文本和 Provider 传输格式耦合，后续接入 Responses API 或 Anthropic Messages API 时会不断增加兼容分支。

本计划以 DeepSeek 为第一落地 Provider，彻底移除 XML 工具调用和最终答案协议，并建立可扩展的 Provider Adapter 架构。

## 2. 已确认的产品边界

### 2.1 Provider 固定，不支持跨 Provider 切换

本次不支持会话中切换 DeepSeek、OpenAI、Claude、MiniMax 等 Provider。

- 一个 JCode 运行实例由全局 `.jcode.toml` 固定一个 Provider。
- 同一实例固定一种 API 协议：`openai_responses` 或 `anthropic_messages`。
- Provider 与 API 协议不进入 session，不通过 Web 选择，也不允许在 run 之间切换。
- “多 Provider”仅表示架构可通过新增 Adapter 扩展，不表示当前产品提供跨 Provider 切换。

### 2.2 保留 Provider 内模型与 effort 切换

Web 仅保留以下 run 间切换能力：

- 在当前固定 Provider 下选择已配置模型档案。
- 选择当前模型档案允许的 `reasoning_effort`。
- run 运行中禁止切换模型或 effort。
- 不支持思考的模型展示“不支持思考”，不发送推理强度字段。

### 2.3 不保留 XML 或旧 session 兼容层

本次按新协议直接完成实现：

- 删除 XML `<tool>`、`<tools>`、`<final>`、`<reasoning>` 的生产逻辑、提示词、解析器和测试样例。
- 不为旧 XML history 创建转换器。
- 旧 session/run 作为静态审计文件保留，但不能 resume 到新原生工具调用链路。
- 新 session schema 直接升级；旧 schema 被明确标记为不可续接，不自动迁移。

## 3. 目标架构

```text
全局配置
  ├─ 固定 provider = deepseek
  ├─ 固定 api_protocol = openai_responses / anthropic_messages
  └─ 当前 Provider 下多个模型档案
             │
             ▼
Session / Working Memory / ContextManager
             │
             ▼
        ContextResult
             │
             ▼
Provider Adapter + ProviderContinuation
             │
             ├─ DeepSeek Responses Adapter
             ├─ DeepSeek Anthropic Messages Adapter
             ├─ Future OpenAI Responses Adapter
             └─ Future Anthropic Messages Adapter
             │
             ▼
      ModelResponse + 原生 tool calls
             │
             ▼
JCodeAgent 工具执行与 run 生命周期
```

核心原则：

- `ContextResult` 是 Context 层交给 Provider 层的唯一统一产物。
- `ContextResult` 是结构化上下文，不是 Provider 请求体，也不是最终拼接的单段 prompt。
- Provider Adapter 负责将 `ContextResult` 编译为厂商原生请求，并将原生响应归一为 `ModelResponse`。
- 工具调用和工具结果以受信任的结构化事件保存，不再拼进普通文本 transcript。
- `ProviderContinuation` 仅保存同一 run 内、同一协议所需回传的厂商原生状态；不用于跨 Provider 或跨 run 迁移。

## 4. 配置目标

配置拆分为 Provider 级配置与模型档案配置。Provider 级配置固定实例的协议，模型档案只描述可切换模型与推理能力。

```toml
[provider]
name = "deepseek"
api_protocol = "openai_responses"
base_url = "https://api.deepseek.com"
api_key_env = "DEEPSEEK_API_KEY"

default_model = "deepseek-flash"

[models.deepseek-flash]
model = "deepseek-v4-flash"
reasoning_mode = "native"
thinking_enabled = true
reasoning_effort = "high"
reasoning_effort_options = ["low", "high", "max"]

[models.deepseek-pro]
model = "deepseek-v4-pro"
reasoning_mode = "native"
thinking_enabled = true
reasoning_effort = "high"
reasoning_effort_options = ["low", "high", "max"]
```

约束：

- `provider.name` 与 `provider.api_protocol` 必填。
- 当前支持组合为 `deepseek + openai_responses` 与 `deepseek + anthropic_messages`。
- `base_url`、API Key 和 API 协议不能写入模型档案，避免模型切换改变连接协议。
- `reasoning_effort_options` 只能由模型档案声明；Web 只读取和校验该集合。
- `reasoning_mode = "none"` 的模型不得声明 effort。

## 5. ContextResult 数据合同

现有 `ContextBuildResult` 改名并重构为 `ContextResult`。该类型位于 Context 层，不能包含 XML、HTTP 请求字段或厂商专有消息对象。

```text
ContextResult
├─ instructions: list[Instruction]
├─ events: list[ContextEvent]
├─ tools: list[ContextTool]
├─ model_profile: dict
├─ ctx_info: dict
├─ compact_audit: dict | None
└─ current_request_id: str
```

### 5.1 Instruction

```text
Instruction
├─ kind: system_rule / skill / project_rule / safety_rule / memory
└─ text: str
```

稳定指令保持原有来源：系统规则、技能、`JCODE.md`、安全规则和工作记忆。它们不再包含 XML 输出协议或文本工具定义示例。

### 5.2 ContextEvent

```text
ContextEvent
├─ id
├─ turn_id
├─ kind: user_message / assistant_message / assistant_tool_call / tool_result
├─ content
├─ call_id
├─ tool_name
├─ arguments: dict
├─ tool_status
└─ metadata
```

规则：

- `assistant_tool_call.call_id` 使用 Provider 返回的原生调用 id。
- `tool_result.call_id` 必须与对应的 `assistant_tool_call.call_id` 一致。
- `arguments` 以字典保存，不能只保存 JSON 字符串。
- 工具结果保留状态、artifact 引用、策略决策和文件变更，但不伪装成 assistant 文本。
- 原生 reasoning 不放进通用 `content`；它是审计信息和 continuation 信息。

### 5.3 ContextTool

```text
ContextTool
├─ name
├─ description
├─ parameters_json_schema
├─ read_only
└─ risky
```

`ToolRegistry` 在应用当前 `ToolProfile` 后导出 `ContextTool`。Provider 只能看到当前 run 中真正允许调用的工具。

## 6. Provider Adapter 数据合同

### 6.1 工厂键

Provider 工厂键升级为：

```text
ProviderKey = (provider_name, api_protocol)
```

例如：

```text
(deepseek, openai_responses)
(deepseek, anthropic_messages)
(openai, openai_responses)          未来扩展
(anthropic, anthropic_messages)     未来扩展
```

虽然当前实例固定 Provider，但工厂键保留协议维度，防止后续把厂商与传输协议判断写入 `JCodeAgent`。

### 6.2 统一响应

```text
ModelResponse
├─ content: str
├─ reasoning: str
├─ tool_calls: list[ModelToolCall]
├─ assistant_event: ContextEvent
├─ finish_reason: str
├─ input_tokens
├─ output_tokens
├─ response_metadata: dict
└─ raw: dict
```

```text
ModelToolCall
├─ call_id
├─ name
├─ arguments: dict
└─ provider_metadata
```

Provider Adapter 负责解析原生参数 JSON。参数 JSON 无法解析时，Adapter 返回一个带稳定错误信息的原生工具调用错误，而不是进入旧 parser 分支。

### 6.3 ProviderContinuation

```text
ProviderContinuation
├─ run_id
├─ provider_key
├─ native_assistant_items
├─ native_reasoning_items
├─ native_tool_call_items
├─ native_tool_result_items
└─ call_id_mapping
```

它的用途是满足同一 run 的 API 回传要求：

- DeepSeek Responses：需要回传 reasoning、`function_call` 和 `function_call_output` input items。
- DeepSeek Anthropic Messages：需要回传 `thinking`、`tool_use` 和 `tool_result` blocks。

它需写入 checkpoint 以支持同 run 的恢复，但不能写入普通 session history 作为下次 run 的通用上下文。

## 7. DeepSeek Responses API Adapter

第一阶段默认实现 `deepseek + openai_responses`。

官方协议要点：

- endpoint 基址：`https://api.deepseek.com`。
- API 无状态，不支持 `previous_response_id`。
- 支持 `function` 工具、`function_call` 和 `function_call_output` input items。
- 支持 reasoning item；工具调用链中的原生 reasoning 必须在后续请求中完整回传。
- 支持 `reasoning.effort`；DeepSeek 思考模式不应发送 temperature 等无效参数。

### 7.1 请求编译

```text
ContextResult.instructions
  -> 顶层 instructions

ContextResult.events
  -> input message items

ProviderContinuation
  -> input reasoning / function_call / function_call_output items

ContextResult.tools
  -> tools: [{type: function, name, description, parameters}]
```

DeepSeek Responses 对 developer message 的语义与 OpenAI 不完全一致。DeepSeek Adapter 使用顶层 `instructions` 保存稳定指令；未来 OpenAI Responses Adapter 可将同一 `Instruction` 集编译为 developer message。

### 7.2 响应编译

```text
response.output 中存在 function_call
  -> ModelResponse.tool_calls 非空
  -> 保存原生 function_call/reasoning 至 continuation

response.output 中不存在 function_call
  -> ModelResponse.tool_calls 为空
  -> response.output_text / message text 写入 ModelResponse.content
```

工具执行结果转为：

```json
{
  "type": "function_call_output",
  "call_id": "fc_xxx",
  "output": "工具结果文本"
}
```

## 8. DeepSeek Anthropic Messages API Adapter

第二阶段实现 `deepseek + anthropic_messages`。它由全局配置选择，不与 Responses API 在同一实例内切换。

官方协议要点：

- endpoint 基址：`https://api.deepseek.com/anthropic`。
- 稳定指令位于顶层 `system`。
- 工具定义为 `name`、`description`、`input_schema`。
- assistant 使用 `tool_use` block。
- 工具结果由 user message 中的 `tool_result` block 回传。
- 支持 `thinking` block 与 effort 配置。

编译规则：

```text
ContextResult.instructions -> system
ContextResult.events       -> messages
ContextResult.tools        -> tools
assistant tool call        -> assistant.content.tool_use
tool result                -> user.content.tool_result
```

## 9. JCodeAgent 原生工具调用循环

删除 `parse_model_action()` 后，主循环按 `ModelResponse.tool_calls` 推进：

```text
ContextManager.build()
  -> ContextResult
  -> ProviderAdapter.complete(context_result, continuation)
  -> tool_calls 是否为空？

tool_calls 非空
  -> 记录 assistant_message / assistant_tool_call 结构化事件
  -> 按 Provider 返回顺序执行每个工具
  -> 为每个 call_id 记录 tool_result
  -> 更新 continuation
  -> 下一子轮

tool_calls 为空
  -> final_text = ModelResponse.content
  -> 结束 run
```

终态规则：

| Provider 返回 | 运行时行为 |
|---|---|
| 有 tool calls，content 为空 | 正常中间状态，执行工具并继续 |
| 有 tool calls，content 非空 | 保存中间 assistant 文本，执行工具并继续 |
| 无 tool calls，content 非空 | 成功结束，content 为最终回答 |
| 无 tool calls，content 为空 | 结束为 `empty_model_content`，不重试 |

工具名不存在、参数校验失败、权限拒绝、artifact 不可用均转化为对应 call id 的工具错误结果并回传模型。它们不是模型协议错误，不得中断原生会话链。

## 10. Context、Session 与历史改造

### 10.1 Context

- 删除 `OUTPUT_PROTOCOL` 及 XML 输出示例。
- 删除通过普通文本提示模型调用工具的格式说明。
- 保留工具的自然语言说明、安全边界和当前可用工具集合。
- 不把 `[Assistant]`、`[ToolResult]` 等运行时历史标记作为可被模型续写的文本协议注入。

### 10.2 Session schema

session schema 升级为 v4：

- `history` 保存结构化 `ContextEvent`。
- 模型切换仍保存 `active_model_profile` 与 per-profile `reasoning_effort`。
- 不保存可供不同 Provider 直接复用的原生 HTTP assistant message。
- checkpoint 保存当前 run 的 `ProviderContinuation`。
- schema v3 及更早版本不能 resume 到新 run；保留文件，只读展示历史。

### 10.3 模型切换

模型切换只影响下一 run 的 `ModelProfile` 快照：

```text
可变：model、thinking_enabled、reasoning_effort
固定：provider、api_protocol、base_url、认证方式、工具调用协议
```

## 11. Trace 与 Web 改造

### 11.1 Trace

删除：

```text
model_parsed
model_parse_failed
final_readiness_decision
parser ToolResult
```

新增或调整：

```text
model_responded
├─ content
├─ reasoning_text
├─ finish_reason
├─ native_tool_calls: [{call_id, name, arguments}]
├─ response_metadata
└─ model_profile

native_tool_calls_received
tool_requested
tool_executed
model_completed
run_finished
```

`response_metadata` 记录协议、终止原因和响应状态等非敏感字段；trace 不记录 API Key。

### 11.2 Web

Web 时间线改为直接展示：

- Provider 原生 reasoning。
- Provider 原生 tool calls。
- 每一个 call id 对应的工具执行结果。
- Provider 最终 content。

删除“模型解析结果”和 XML 协议错误展示。模型下拉仅列当前固定 Provider 的模型；effort 控件由模型档案驱动。

## 12. 彻底删除清单

生产代码、测试、前端和文档中移除：

```text
parse_model_action
ModelAction
FINAL_RE / TOOL_RE / TOOLS_RE
<tool> / <tools> / <final> / <reasoning>
OUTPUT_PROTOCOL
model_parse_failed
parser ToolResult
XML 工具调用示例
FinalGate 对模型输出格式的重试控制
OpenAI Chat Completions Adapter
```

同时删除仅为旧文本协议存在的中间转发层；不保留双协议开关、fallback 或旧格式分支。

## 13. 实施阶段

### 阶段 1：配置与核心数据模型

1. 改造 `AppConfig`、`ModelProfile`，拆分 Provider 级固定配置和模型级可切换配置。
2. 引入 `ContextResult`、`Instruction`、`ContextEvent`、`ContextTool`、`ModelToolCall`、`ProviderContinuation`。
3. 升级 session schema 到 v4，并拒绝 resume 旧 schema。
4. 将 Registry 工厂键改为 `(provider_name, api_protocol)`。

### 阶段 2：Context 与工具定义

1. 改造 `ContextManager.build()`，输出 `ContextResult`。
2. 改造历史裁剪、Working Memory 和 resume 逻辑，使其操作结构化事件。
3. 在 `ToolRegistry` 增加导出当前 ToolProfile 可见 `ContextTool` 的能力。
4. 删除 prefix 中的 XML 协议和文本工具调用格式。

### 阶段 3：DeepSeek Responses API

1. 删除现有 `DeepSeekClient` 的 Chat Completions 请求实现。
2. 实现 `DeepSeekResponsesAdapter` 的请求编译、响应解析和 continuation 更新。
3. 实现 `function_call`、`function_call_output` 与 call id 的完整链路。
4. 将原生 reasoning 和 finish reason 写入 `ModelResponse`、checkpoint 与 trace。
5. 把 `openai_responses` 设为默认全局协议。

### 阶段 4：Agent 与工具循环

1. 删除 XML `actions.py` 解析链。
2. 将 `JCodeAgent` 改为依据 `ModelResponse.tool_calls` 执行工具。
3. 对每个工具结果生成原生结构化事件和 continuation item。
4. 实现 `empty_model_content` 终态。
5. 将 FinalGate 收敛为审计，不再触发模型重试。

### 阶段 5：DeepSeek Anthropic Messages API

1. 实现 `DeepSeekAnthropicAdapter`。
2. 实现 `system`、`messages`、`tools`、`thinking`、`tool_use`、`tool_result` 编译。
3. 用同一 `ContextResult` 验证两种 DeepSeek API 协议的行为一致性。
4. 通过全局配置选择该协议，不增加 Web 切换入口。

### 阶段 6：Web、证据与清理

1. 改造 Web 时间线、事件流和历史查看。
2. 删除 XML 相关 UI、trace、测试和文档。
3. 更新 `architecture.md`、Provider 架构文档和 README。
4. 执行全仓搜索，确保生产代码中不存在旧协议符号。

## 14. 测试与验收

### 14.1 Provider 测试

- DeepSeek Responses 请求包含正确的 `instructions`、`input`、`tools`、reasoning effort。
- DeepSeek Responses 正确解析单个和多个 `function_call`。
- `function_call_output` 精确使用原 call id。
- 思考模式工具调用时，reasoning item 被完整回传。
- DeepSeek Anthropic 正确编译 `tool_use` / `tool_result` / `thinking`。
- 无 tool calls 且有 content 时正确完成。
- 有 tool calls 且 content 为空时正确继续。
- 无 tool calls 且 content 为空时以 `empty_model_content` 结束，不重试。

### 14.2 Runtime 测试

- 多工具按 Provider 返回顺序执行并回传。
- 未知工具、非法参数、拒绝和执行失败均回传结构化工具错误。
- run 内禁止模型/effort 切换。
- run 间模型切换正确使用新的模型快照和 effort。
- 旧 schema session 被拒绝 resume。
- checkpoint 恢复同 run continuation 时 call id 和 reasoning 不丢失。

### 14.3 Context 与 Web 测试

- ContextResult 的裁剪不破坏 call id 与 tool-result 关联。
- ToolProfile 过滤后的工具集合同时影响 ContextResult 和 Provider 请求。
- Web 不显示 Provider 选择器。
- Web 模型列表只显示固定 Provider 的档案。
- Web 时间线显示 reasoning、原生工具调用、工具结果和最终 content。

### 14.4 退出条件

完成条件为：

1. DeepSeek Responses API 可完成真实多轮原生工具调用。
2. DeepSeek Anthropic Messages API 可通过同一 ContextResult 完成等价调用。
3. run 终止完全由 `tool_calls` 是否为空决定。
4. 最终答案完全来自 Provider 返回 `content`。
5. 生产代码中不存在 XML 工具/最终协议。
6. 当前固定 Provider 下模型切换与 effort 选择在 Web 和 run 中正常工作。
7. 全部相关单元、集成与 Web 测试通过。

## 15. 风险与约束

- DeepSeek Responses API 无状态，因此 ContextResult 裁剪必须保留当前 run 内尚未闭合的工具调用链，不能只按普通文本长度裁剪。
- Artifact 必须在对应 run 生命周期内稳定存在；工具错误应以结构化 tool output 回传，不能让模型依赖路径文本猜测结果。
- 不启用 DeepSeek strict beta 作为本次前置条件。strict 需要 `/beta` 地址和更严格的 JSON Schema 约束，后续单独评估。
- 不使用 JSON Output 代替工具调用。官方文档说明 JSON Output 可能返回空 content，不适合作为原生函数调用协议。
- 新增 Provider 时只新增 Adapter 与配置校验，不在 `JCodeAgent` 中增加 `if provider == ...` 分支。

## 16. MiniMax Provider 与推理开关实施基线

本节是 MiniMax 接入和 Web 推理开关的现行执行方案，优先于本文早期“仅固定 DeepSeek”的示例表述。

### 16.1 产品边界与实例配置

- MiniMax 与 DeepSeek 处于同一 Provider 层，均使用 `openai_responses`。
- 一个 JCode 实例仍只固定一个 Provider；同一 session 不支持在 DeepSeek 与 MiniMax 之间切换。
- 中转地址使用 `https://minnimax.chat/v1`，Adapter 固定拼接 `/responses`，最终请求地址为 `https://minnimax.chat/v1/responses`。
- API Key 来自全局 Provider 配置或 `MINIMAX_API_KEY`；不得进入模型快照、trace、请求预览或 Web 响应。

```toml
default_model = "minimax-m3"

[providers.deepseek]
name = "deepseek"
api_protocol = "openai_responses"
base_url = "https://api.deepseek.com"
api_key_env = "DEEPSEEK_API_KEY"

[providers.minimax]
name = "minimax"
api_protocol = "openai_responses"
base_url = "https://minnimax.chat/v1"
api_key_env = "MINIMAX_API_KEY"

[models.minimax-m3]
provider = "minimax"
model = "MiniMax-M3"
reasoning_mode = "optional"
thinking_enabled = false
reasoning_effort = "medium"
reasoning_effort_options = ["minimal", "low", "medium", "high"]
```

每个模型档案必须声明 `provider`，引用 `[providers.<provider_id>]`。Provider ID 是配置名称，`name` 是客户端类型；同一类型可配置多个不同中转地址或密钥。包含 `.` 的模型档案 ID 必须加引号，例如 `[models."minimax-m2.7"]`。

### 16.2 推理开关合同

推理开关是 Web 的 run 间设置。TOML 的 `thinking_enabled` 仅定义默认值；真正发送的请求由 run 启动时写入 `TaskState.model_profile` 的快照决定。

| 模型状态 | 控件与请求行为 |
| --- | --- |
| M3 关闭 | 开关可操作，隐藏或禁用 effort；不发送 `reasoning`，发送 `temperature`。 |
| M3 开启 | 显示 `minimal/low/medium/high`；发送 `reasoning: {"effort": <value>}`，不发送 `temperature`。 |
| M2.x | 开关显示开启且禁用；MiniMax 文档规定无法关闭推理。 |
| `reasoning_mode=none` | 不显示推理控件，不发送 `reasoning`。 |

模型设置接口保持 `POST /api/projects/{project_id}/sessions/{session_id}/model`，请求体新增 `thinking_enabled: boolean`。后端必须拒绝 active run 内的模型、开关或 effort 变更。关闭后重新开启时恢复该 profile 上次合法 effort。

### 16.3 分层改造

1. `src/app/config.py`：放开硬编码的 DeepSeek 校验，仅允许当前已注册组合 `deepseek/openai_responses`、`minimax/openai_responses`；`AppConfig` 保留 TOML 实际 Provider。effort 按 Provider/模型能力验证，MiniMax M3 允许 `minimal/low/medium/high`。
2. `src/providers/profiles.py`：补充“推理可关闭/始终开启”能力字段，明确表达 M2.x，不能从默认开关值推断能力。
3. `src/providers/minimax.py`：新建与 `DeepSeekClient` 同层的 `MiniMaxClient`，不建立通用兼容大类；请求与响应差异在该文件封装。
4. `src/app/bootstrap.py`：注册 `("minimax", "openai_responses") -> MiniMaxClient`，不在 `JCodeAgent` 主循环增加厂商分支。
5. `src/state/model_selection.py`：`validate_model_options()` 和 `resolve_model_snapshot()` 同时处理开关、effort 与始终开启能力，生成无密钥快照。
6. `src/app/web_server.py`、`src/app/web_runs.py`：请求模型增加 `thinking_enabled`；按 profile 保存到 `session.model_options[profile_id]`，在 `model_switched` 事件写入脱敏快照。
7. `src/app/web_static/index.html`、`app.js`、`style.css`：增加 switch；关闭时不提交 effort 变更；服务端失败时根据返回 session 恢复 UI，避免显示和持久化状态不一致。

### 16.4 MiniMax Responses Adapter

- 请求使用 `model`、`instructions`、结构化 `input`、`tools`、`max_output_tokens`；原生工具仍使用 `function_call` 和 `function_call_output`，以 `call_id` 闭环。
- 推理开启发送 `reasoning`，省略 `temperature`；关闭省略 `reasoning`，发送 `temperature`。发送前校验 MiniMax 文档的温度范围 `(0, 1]`。
- 本次固定非流式请求，不把 SSE 生命周期接入混入首次 Provider 适配。
- `service_tier`、`top_p`、`metadata`、`prompt_cache_key` 仅可通过受测白名单从 `ModelProfile.extra` 透传，不能覆盖核心字段。
- `output_text` 优先映射为 `ModelResponse.text`；`message`、`reasoning`、`function_call` 分别映射文本、推理和 `ModelToolCall`。
- `status`、`incomplete_details`、`error`、`usage` 映射为现有 `ModelResponse` 字段；HTTP 错误和网络错误继续走 `ProviderRequestError` 与通用重试。
- 同 run 回放继续使用原生 continuation，保存 reasoning/function call/function output 原生 item，不把它们拼成普通 transcript。

### 16.5 验收与测试

- 配置：合法 MiniMax、非法 Provider 协议组合、M3 `minimal`、非法 `xhigh/max`、M2.x 始终开启。
- Adapter：中转 URL、认证头、开关互斥请求体、响应 text/reasoning/function call、工具多轮回放、`incomplete/failed`、429/5xx/Retry-After/超时。
- Web：开关持久化与刷新回显、按 profile 隔离、active run 返回 409、M2.x 禁用、无推理模型隐藏、当前 run 快照不受后续修改影响。
- 回归：DeepSeek 全量测试仍通过；请求预览与真实请求一致；session/trace/checkpoint/report 不含 API Key；经中转完成一次无工具、一次工具调用、一次开推理和一次关推理请求。

实施顺序固定为：配置能力模型 -> MiniMax Adapter -> 注册 -> Web API/状态 -> 前端开关 -> 测试 -> 配置示例和文档。出现中转协议故障时回切至 DeepSeek 配置档案，不删除既有 session/run，保留审计。
