# JCode Provider 架构与模型切换链路

本文以当前代码为准，说明 Provider 注册、模型档案、Responses API 请求、原生工具调用以及 Web 会话切换模型的完整链路。

## 1. 配置边界

模型配置读取自 JCode 安装目录的全局 `.jcode.toml`。项目 `--cwd` 只决定工作区，不会覆盖 Provider 配置；需要临时使用另一份配置时，通过 `--config` 指定。

```toml
default_model = "minimax-m3"

[providers.deepseek]
name = "deepseek"
api_protocol = "openai_responses"
base_url = "https://api.deepseek.com"
api_key_env = "DEEPSEEK_API_KEY"

[models.minimax-m3]
provider = "minimax"
model = "MiniMax-M3"
reasoning_mode = "optional"
thinking_enabled = false
reasoning_effort = "medium"
reasoning_effort_options = ["minimal", "low", "medium", "high"]
```

`src/app/config.py` 将 TOML 校验并转换为 `AppConfig`、`ModelProfile`。模型档案必须声明 `provider`、`model`、`api_protocol`、上下文窗口和输出上限；reasoning 选项必须与档案能力一致。

## 2. 注册表与路由

```text
AppConfig.model_profiles
  -> ModelRegistry(profiles, default_profile_id)
  -> register(provider, api_protocol, factory)
  -> client(profile_id)
  -> ModelRouter.complete(...)
  -> DeepSeekClient 或 MiniMaxClient
```

`src/providers/registry.py` 使用 `(provider, api_protocol)` 作为工厂键，并按 profile 缓存客户端。`src/providers/router.py` 只负责选择 profile、取得客户端并转发请求，不包含厂商分支。当前已注册 `deepseek/openai_responses` 和 `minimax/openai_responses` 两个独立 Adapter。

统一接口位于 `src/providers/base.py`：`ModelClient.complete()` 返回 `ModelResponse`；后者包含 `text`、`reasoning`、`tool_calls`、`finish_reason`、token 用量、incomplete 信息和 Provider 错误摘要。`ModelToolCall` 保存 `call_id`、工具名、已解析参数和原生调用 metadata；`ProviderRequestError` 携带状态码、传输错误和重试等待信息。

## 3. Responses API 请求

`src/providers/request.py` 编译统一的 `ProviderInputSnapshot`：

```text
ContextResult.prefix                 -> instructions
skill + HistoryEvent + WorkingMemory -> input
ToolDefinition                       -> tools
```

历史中的工具调用编译成 Responses 原生 `function_call`，工具结果编译成同一 `call_id` 的 `function_call_output`。reasoning continuation 也按 Provider 原生 item 回放；不再使用 XML `<tool>`、`<tools>`、`<final>` 或 Chat Completions 文本协议。

Provider、preview 和审计共同消费同一份 snapshot，避免请求字段漂移。API Key 只在客户端内存中使用，不写入 run 快照、trace 或 report。

## 4. DeepSeek 与 MiniMax Adapter

`src/providers/deepseek.py` 与 `src/providers/minimax.py` 都实现 `ModelClient`，但厂商字段和错误解析封装在各自 Adapter 内。两者都使用 `base_url + "/responses"`，并将 `output_text`、`reasoning`、`function_call`、`status`、`error` 和 `usage` 归一化为 `ModelResponse`。MiniMax 可选推理开启时发送 `reasoning.effort`，关闭时省略 reasoning 并按模型能力发送 temperature。

没有 API Key 时，客户端仍可完成本地上下文编译和 token 统计，但不会发送真实请求。

## 5. 会话级模型切换

Web 接口由 `src/app/web_runs.py:WebRunManager.switch_model()` 处理：

```text
选择 profile / thinking_enabled / reasoning_effort
  -> 校验 profile 与 effort
  -> 拒绝 active run 期间切换
  -> 写入 session.active_model_profile
  -> 写入 session.model_options[profile_id]
  -> 记录 model_switched session event
```

一次 run 启动时，`src/state/model_selection.py:resolve_model_snapshot()` 将档案默认值和 session 选项合并为 `TaskState.model_profile`。本次 run 的请求、重试、trace、report 和 checkpoint 只使用该快照；下一次切换不会改变已经开始的请求。

单个工具调用链不会跨 Provider。切换发生在 run 之间，session 可以选择不同 Provider 下的模型档案，但每个 run 始终固定一个 Provider。

## 6. 一次 run 的调用链

```text
CLI/Web 请求 -> build_agent() -> ContextManager.build()
  -> ModelRouter.complete() -> Provider Client.complete()
  -> ModelResponse -> JCodeAgent 处理原生 tool_calls
  -> ToolExecutor -> function_call_output -> 下一子轮
  -> 无 tool_calls 时写入最终答案
```

运行证据写入 `.jcode/runs/<run_id>/`。`trace.jsonl` 记录 `model_responded`、`native_tool_calls_received`、`tool_executed` 和 `run_finished`；`report.json` 记录响应、工具和记忆维护摘要，但不记录密钥。

## 7. 新增 Provider

1. 在全局配置声明 `[providers.<id>]` 和 `[models.<profile>]`。
2. 在独立模块实现 `ModelClient`，完成请求、响应、原生工具调用、reasoning 和错误映射。
3. 在 `src/app/bootstrap.py` 注册 `(provider, api_protocol)` 工厂。
4. 增加请求快照、工具回放、推理开关、错误恢复和模型切换测试。
5. 不在 `JCodeAgent` 主循环中增加 `if provider == ...` 分支。
