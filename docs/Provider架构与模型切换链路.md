# JCode Provider 架构与模型切换链路

本文描述 JCode 当前已经实现的多 Provider 架构、模型档案配置、DeepSeek 原生思考接入，以及 Web 会话在 run 之间切换模型的运行链路。

## 1. 设计目标

JCode 将三个概念分开处理：

- **工作项目**：`--cwd` 指向的目录，决定工具工作区、`.jcode/sessions/`、`.jcode/runs/` 和项目规则 `JCODE.md`。
- **模型档案**：JCode 安装目录下的全局 `.jcode.toml` 中的 `[models.<profile_id>]` 表，用于配置 Provider、模型、地址、密钥和推理能力。
- **Provider 客户端**：把统一的 JCode 调用参数适配为特定厂商的 HTTP 请求，并把厂商响应归一为 `ModelResponse`。

因此，用户可以在任意项目目录启动：

```powershell
& 'D:\jt\ANACONDA\envs_dirs\jcode\python.exe' -m src.app.web --cwd .
```

此时 `.` 只表示当前工作项目；模型列表、API Key、推理强度候选项均来自 JCode 安装目录的 `.jcode.toml`。需要临时替换全局模型配置时，使用显式 `--config <path>`。

## 2. 模型档案

`ModelProfile` 位于 `src/providers/profiles.py`，是 Provider 配置进入运行时后的不可变对象。

```text
ModelProfile
├─ id                         档案标识，例如 deepseek-flash
├─ provider                   Provider 标识，例如 deepseek
├─ model                      厂商模型名，例如 deepseek-v4-flash
├─ api_key / base_url         连接配置，不写入 run 快照
├─ reasoning_mode             none / native / optional
├─ thinking_enabled           是否启用原生思考
├─ reasoning_effort           默认推理强度
├─ reasoning_effort_options   可选择的强度集合
└─ extra                      Provider 专有扩展参数
```

示例：

```toml
default_model = "deepseek-flash"

[models.deepseek-flash]
provider = "deepseek"
model = "deepseek-v4-flash"
base_url = "https://api.deepseek.com"
api_key_env = "DEEPSEEK_API_KEY"
reasoning_mode = "native"
thinking_enabled = true
reasoning_effort = "high"
reasoning_effort_options = ["low", "high", "max"]

[models.gpt-coding]
provider = "openai"
model = "gpt-5"
base_url = "https://api.openai.com/v1"
api_key_env = "OPENAI_API_KEY"
reasoning_mode = "none"
```

`reasoning_mode = "none"` 的档案不能声明 `thinking_enabled`、`reasoning_effort` 或 `reasoning_effort_options`。推理强度是否合法由配置加载和 `validate_model_options()` 双重校验。

## 3. Provider 工厂与路由

Provider 的创建使用注册表加工厂函数，而不是在 Agent 主循环中判断厂商名称。

```text
AppConfig.model_profiles
        │
        ▼
ModelRegistry
├─ profiles: profile_id -> ModelProfile
├─ factories: provider -> ClientFactory
└─ clients: profile_id -> 已缓存的 ModelClient
        │
        ▼
ModelRouter.complete(context, profile_id, model_profile)
        │
        ▼
具体 Provider Client
        │
        ▼
ModelResponse(text, reasoning, input_tokens, output_tokens, raw)
```

装配发生在 `src/app/bootstrap.py`：

- `deepseek` 注册为 `DeepSeekClient`。
- `openai` 注册为 `OpenAICompatibleClient`。
- 新增 MiniMax、Claude 或其他厂商时，实现对应 Client，并通过 `ModelRegistry.register()` 注册新的 `provider` 标识即可。

工厂模式体现在 `ModelRegistry.client()`：它先按 profile 找到 provider 工厂，再延迟构造并缓存该 profile 的 Client。同一个 Provider 可以有多个档案，例如不同模型、不同 API Key 或不同默认推理强度，缓存彼此隔离。

## 4. DeepSeek 原生思考链路

`DeepSeekClient` 使用 Chat Completions API。启用思考时，请求包含：

```json
{
  "thinking": {"type": "enabled"},
  "reasoning_effort": "high"
}
```

关闭思考时，请求改为：

```json
{
  "thinking": {"type": "disabled"},
  "temperature": 0.2
}
```

响应会被归一为：

```text
DeepSeek message.content           -> ModelResponse.text
DeepSeek message.reasoning_content -> ModelResponse.reasoning
```

原生思考不参与工具动作解析。动作解析器只读取 `ModelResponse.text`，并只接受一个 `<tool>`、`<tools>` 或 `<final>` 块；`ModelResponse.reasoning` 单独写入 trace、session history 和 Web 步骤展示。

输出协议的唯一来源是 `src/context/prefix.py` 的 `OUTPUT_PROTOCOL`。`ContextManager` 通过 `render_prefix()` 注入该内容，不能在 manager 中另行维护第二份协议文本。历史中的原生思考以元数据形式展示，不再使用旧的 XML reasoning 标签，避免模型把历史示例误认为可输出协议。

## 5. Web 会话模型切换

Web 的 `WebRunManager` 持有启动时加载的全局 `AppConfig.model_profiles`。项目切换不会重新读取项目内 `.jcode.toml`。

```text
Web 选择 profile / effort
        │
        ▼
POST /api/projects/{project_id}/sessions/{session_id}/model
        │
        ▼
WebRunManager.switch_model()
├─ 校验 profile 属于全局配置
├─ 拒绝 active run 期间切换
├─ 校验 reasoning_effort 属于该档案候选项
├─ 写入 session.active_model_profile
├─ 写入 session.model_options[profile_id]
└─ 写入 model_switched session event
```

切换只允许发生在 run 之间。一个 run 启动时，`resolve_model_snapshot()` 合并 profile 默认值和 session 中该 profile 的 effort 选择，并把无密钥快照写入 `TaskState.model_profile`。后续 Provider 请求、trace 和 report 都使用这份 run 快照，不会受下一次模型切换影响。

Web 不提供 `thinking_enabled` 开关；它是 TOML 中的 Provider 能力配置。对于不支持思考的模型，前端应保留 effort 状态信息但显示“该模型不支持思考”，同时请求侧不得发送 reasoning 参数。

## 6. 一次 run 的完整调用链

```text
用户消息
  -> WebRunManager.start_run()
  -> _config_for_session()
     ├─ cwd = 当前项目目录
     └─ model_profiles = 全局 JCode 配置
  -> build_agent()
  -> ContextManager.build()
  -> ModelRouter.complete()
  -> Provider Client.complete()
  -> ModelResponse
  -> JCodeAgent._parse_action()
  -> 工具执行或 FinalGate
  -> trace.jsonl / session history / report
```

`trace.jsonl` 中的 `run_started`、`model_responded`、`model_parsed`、`tool_requested`、`tool_executed`、`final_readiness_decision` 和 `run_finished` 是审计运行过程的主要事件。`model_responded` 同时记录 `response_text` 与 `reasoning_text`，但不记录 API Key。

## 7. 当前边界与已知风险

当前工具调用仍使用 XML 文本协议，而不是 Provider 原生 tool calling。该设计对所有 Provider 统一，但模型可能发生以下问题：

- 在 `<tool>` 后续写历史格式中的 `[ToolResult ...]`，形成混合协议并被解析器拒绝。
- 只返回原生思考而没有 `content`，导致 `response_text` 为空并触发协议错误。
- 大工具结果会写入 `.jcode/runs/<run_id>/artifacts/`；若 artifact 被外部清理或覆盖，模型后续使用 `read_file` 读取该路径会失败。

当前解析器对混合协议采取严格拒绝，不会从不合法输出中截取前半段工具调用。这是为了避免歧义和安全风险。后续演进方向是：为各 Provider 增加原生 tool calling 适配，将其统一映射到 `ModelAction`，同时为 artifact 使用稳定标识和显式读取工具，并记录 Provider 的 `finish_reason` 等响应元数据。

## 8. 新增 Provider 的最小步骤

1. 在全局 `.jcode.toml` 新增 `[models.<profile_id>]`。
2. 实现 Provider Client，将厂商请求和响应适配为 `ModelResponse`。
3. 在 `build_agent()` 中以 provider 标识注册 Client 工厂。
4. 为请求参数、原生思考字段和异常响应补 Provider 单测。
5. 确认无思考模型不发送 reasoning 参数，且 Web effort 校验正确拒绝不支持的选择。

不要在 `JCodeAgent` 主循环中增加 `if provider == ...` 分支，也不要让项目目录内的 `.jcode.toml` 覆盖全局 Provider 配置。

## 9. 现行校正：MiniMax Responses 与推理开关

本文早期的 Chat Completions、XML 工具协议、`OpenAICompatibleClient` 和“Web 不提供 `thinking_enabled` 开关”描述均已过时。本节以现有 Responses 原生工具调用实现和下一步 MiniMax 方案为准。

JCode 的注册表按 `(provider, api_protocol)` 找到客户端。DeepSeek 和 MiniMax 都注册为 `openai_responses`，但保留独立 Adapter：`DeepSeekClient` 与 `MiniMaxClient`。这样 `ContextResult`、`ModelResponse`、原生工具调用、续接和 Agent 循环保持通用，而厂商的请求字段、推理能力和错误格式封闭在 Provider 层。

MiniMax 使用全局固定 Provider 配置，不可与 DeepSeek 在同一 session 切换：

```toml
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

`[providers.<id>]` 可以声明多个连接配置，每个 `[models.<id>]` 通过 `provider = "<id>"` 选择连接。运行时仍由 session 在所有模型档案中选择模型，因此每次 run 固定一个 Provider；不会在单次工具调用链中跨 Provider。模型档案 ID 含 `.` 时必须写为 `[models."id.with.dot"]`。

`MiniMaxClient` 请求 `base_url + "/responses"`。M3 关闭推理时省略 `reasoning` 并发送 `temperature`；开启时发送 `reasoning.effort` 并省略 `temperature`。M2.x 的推理不可关闭，因此模型档案必须显式声明“始终开启”能力，Web 显示开启且禁用的开关。

模型设置接口扩展为：

```json
{
  "model_profile": "minimax-m3",
  "thinking_enabled": true,
  "reasoning_effort": "medium"
}
```

设置只允许在 run 之间保存至 `session.model_options[profile_id]`。启动 run 时，`resolve_model_snapshot()` 将档案默认值与 session 设置合并到 `TaskState.model_profile`；请求、重试、trace 和报告只消费这份快照。这样用户在运行期间的设置请求会被拒绝，也不会影响已开始的请求。

MiniMax 响应解析遵循 Responses 项类型：顶层 `output_text` 或 `message.output_text` 为最终文本，`reasoning` 为推理记录，`function_call` 为 `ModelToolCall`，`function_call_output` 在工具执行后按同一 `call_id` 回传。`status`、`incomplete_details`、`error` 和 `usage` 进入统一 `ModelResponse`，供现有完成判断、错误恢复和审计使用。

新增 Provider 的固定步骤是：声明 Provider/模型能力 -> 实现独立 Adapter -> 注册 `(provider, protocol)` -> 扩展配置和 Web 设置校验 -> 覆盖请求、响应、工具回放、开关和错误测试。不得通过 Agent 主循环的 Provider 条件分支实现差异。
