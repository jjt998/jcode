# JCode Web MVP2 设计

MVP2 的目标是修正 MVP1 的会话浏览体验：事件流不再作为右侧全局时间线存在，而是归属到每一个对话 turn。用户阅读对话时，可以直接在当前 turn 内展开“推理中”过程，查看本轮 context、模型返回、解析结果、工具请求和工具执行结果。

## 背景

MVP1 已经提供本地单用户 Web 控制台，支持项目、会话、恢复对话、SSE 事件流、用户审批和中止运行。当前主要问题是事件流位于页面最右侧，和中间对话内容分离。用户无法直观看出右侧事件属于哪个 turn，也很难把“用户请求 -> 推理过程 -> 最终回答”作为一个整体浏览。

MVP2 选择方案 A：将事件流内嵌到对应 turn 的推理抽屉中，并移除右侧全局事件栏。全局事件栏、评测回放和跨 run 审计浏览留给后续评测系统扩展。

## 目标

- 移除右侧全局事件栏。
- 将每个 run 的事件流挂载到对应对话 turn 内。
- 在用户消息和助手最终回答之间展示一个默认折叠的过程抽屉。
- 过程抽屉标题显示本 turn 状态和摘要，例如 `推理中 · 8 events · 2 tools · 1 file changed`。
- 过程抽屉内的子事件也默认折叠。
- 展开子事件后直接展示完整原文，不使用摘要替代原文。
- 将完整 context、模型原始返回、工具请求和工具执行结果直接写入 `trace.jsonl`。
- SSE 只做事件级流式更新，最终回答仍一次性出现。
- 逐 token 输出明确留到 MVP3。

## 非目标

- 不做 token streaming。
- 不做右侧全局 timeline。
- 不做全屏 trace 浏览器。
- 不做 context 搜索。
- 不做一键复制。
- 不做评测系统或跨会话审计系统。
- 不把完整原文拆到 artifacts；MVP2 按用户选择直接写入 `trace.jsonl`。

## 页面结构

MVP2 将 MVP1 的三栏布局调整为两栏布局：

```text
左侧：Project / Session
右侧：Conversation
```

左侧继续负责：

- 创建项目。
- 选择项目。
- 创建会话。
- 选择已有会话继续对话。

右侧 conversation 按 turn 渲染：

```text
用户消息

[推理中 · 8 events · 2 tools · 1 file changed]  展开/折叠
  [Context 拼凑]        默认折叠
  [模型原始返回]        默认折叠
  [模型解析结果]        默认折叠
  [工具请求: read_file] 默认折叠
  [工具结果: read_file] 默认折叠
  [Checkpoint]          默认折叠
  [Final gate]          默认折叠

助手最终回答
```

运行中和完成后，过程抽屉都默认折叠。SSE 到达时只更新标题状态、计数和子事件列表，不自动展开抽屉。

## Turn 模型

MVP2 引入面向 Web 展示的 turn view model。前端不再直接把 session history 和 trace events 混合硬拼，而是消费后端构造好的 turn 列表。

```json
{
  "session_id": "20260827T230000-abc123",
  "project_id": "default",
  "turns": [
    {
      "run_id": "run-abc123",
      "user_message": "帮我查看项目结构",
      "assistant_message": "已查看...",
      "status": "completed",
      "event_count": 12,
      "tool_count": 4,
      "changed_files": ["src/app/web.py"],
      "events": []
    }
  ]
}
```

Turn 对齐规则：

- session history 中带 `run_id` 的 user message 是 turn 起点。
- 同一 `run_id` 的 assistant message 是 turn 终点。
- 同一 `run_id` 的 tool history 和 trace events 归入该 turn。
- 若历史 session 没有完整 run_id，前端显示普通历史消息，不强行归入推理抽屉。
- 若 run 中断且没有 assistant message，turn 状态显示 `stopped`、`failed` 或 `incomplete`。

## Evidence 增强

MVP1 的 trace 更偏审计摘要。MVP2 需要把可浏览原文写入 trace。

### context_built

当前 `context_built` 只记录 metadata。MVP2 增加完整 context：

```json
{
  "event": "context_built",
  "run_id": "run-...",
  "sections": {},
  "total_chars": 12345,
  "estimated_input_tokens": 3200,
  "context": "[prefix]\n..."
}
```

### model_responded

当前 `model_requested` 记录 token 估算，但没有模型原文。MVP2 新增 `model_responded`：

```json
{
  "event": "model_responded",
  "run_id": "run-...",
  "response_text": "<tool name=\"read_file\">...</tool>",
  "estimated_input_tokens": 3200,
  "estimated_output_tokens": 400
}
```

`model_requested` 可以继续保留为请求统计事件。`model_responded` 用于前端展示“模型原始返回”。

### model_parsed

继续记录解析后的结构化 action：

```json
{
  "event": "model_parsed",
  "run_id": "run-...",
  "action": {
    "kind": "tool",
    "tool_name": "read_file",
    "content": ""
  }
}
```

### tool_requested

继续记录工具名和完整参数：

```json
{
  "event": "tool_requested",
  "run_id": "run-...",
  "name": "read_file",
  "args": {
    "path": "README.md"
  }
}
```

### tool_executed

MVP1 只保留 `result[:1000]`。MVP2 改为写入完整工具结果：

```json
{
  "event": "tool_executed",
  "run_id": "run-...",
  "name": "read_file",
  "status": "success",
  "error_type": null,
  "changed_files": [],
  "metadata": {},
  "result": "完整工具输出..."
}
```

敏感信息仍必须经过现有 redactor 处理后再写入 trace。

## 事件命名

前端子事件标题面向用户，不直接暴露内部事件名：

```text
context_built -> Context 拼凑
model_responded -> 模型原始返回
model_parsed -> 模型解析结果
tool_requested -> 工具请求
tool_executed -> 工具结果
checkpoint_created -> Checkpoint
final_readiness_decision -> Final gate
memory_maintained -> 记忆整理
run_finished -> 运行结束
approval_required -> 等待确认
approval_answered -> 已确认
```

每个子事件默认折叠。展开后：

- text 字段用 monospace 显示完整原文。
- dict/list 字段格式化为 JSON。
- 大文本区域固定高度滚动，不撑爆页面。
- 老 trace 缺少完整原文字段时，显示 `这个历史事件没有保存完整内容`。

## API 设计

保留现有项目、会话、发送消息和 SSE API。

新增 turn API：

```http
GET /api/projects/{project_id}/sessions/{session_id}/turns
```

返回：

```json
{
  "project_id": "default",
  "session_id": "20260827T230000-abc123",
  "turns": [
    {
      "run_id": "run-abc123",
      "user_message": "...",
      "assistant_message": "...",
      "status": "completed",
      "event_count": 12,
      "tool_count": 4,
      "changed_files": [],
      "events": []
    }
  ]
}
```

发送消息仍使用：

```http
POST /api/projects/{project_id}/sessions/{session_id}/messages
```

SSE 仍使用：

```http
GET /api/projects/{project_id}/runs/{run_id}/events
```

区别是前端不再把 SSE 事件渲染到全局栏，而是按 `web_run_id` 或真实 `run_id` 挂载到当前 pending turn。

## 运行中 Turn 流程

发送新消息时：

1. 前端立即创建 pending turn。
2. pending turn 显示用户消息。
3. pending turn 插入默认折叠的 `推理中` 抽屉。
4. 后端返回 `web_run_id`。
5. 前端连接 SSE。
6. `jcode_run_bound` 到达后，pending turn 绑定真实 `jcode_run_id`。
7. 后续事件持续追加到该 turn 的 reasoning drawer。
8. `web_run_completed` 或 `run_finished` 到达后，插入助手最终回答。
9. turn 状态更新为 `已完成`、`已停止` 或 `失败`。

历史会话加载时：

1. 前端调用 turns API。
2. 后端从 session history、run_ids 和 trace files 构造 turn 列表。
3. 前端一次性渲染所有 turn。
4. 所有 reasoning drawer 和子事件默认折叠。

## 审批体验

如果某个 turn 触发 `approval_required`：

- 该 turn 的抽屉标题变为 `等待确认`。
- 审批输入显示在该 turn 下方。
- 用户提交后，`approval_answered` 事件进入同一个 turn。
- 后续 SSE 事件继续追加到同一 turn。

审批仍使用 MVP1 的阻塞式 `ask_user_callback`，MVP2 不改变暂停/继续机制。

## 状态与摘要

Reasoning drawer 标题根据 turn 状态显示：

```text
推理中 · 8 events · 2 tools · 1 file changed
等待确认 · 9 events · 2 tools
已完成 · 12 events · 4 tools · 3 files changed
已停止 · 5 events
失败 · 6 events
```

摘要字段从 events 计算：

- `event_count`：turn 内事件总数。
- `tool_count`：`tool_requested` 或 `tool_executed` 数量，MVP2 以 `tool_executed` 为准。
- `changed_files`：聚合所有工具事件的 changed files。
- `status`：优先使用 active run 状态；历史 run 使用 `run_finished`、`run_failed` 或 task_state 推断。

## 前端组件建议

MVP2 可以在原生 HTML/CSS/JS 下拆出逻辑组件函数，不引入前端框架。

建议组件：

- `ProjectPanel`：项目与会话选择。
- `TurnList`：渲染会话 turns。
- `TurnItem`：用户消息、reasoning drawer、助手回答。
- `ReasoningDrawer`：turn 级过程抽屉。
- `ReasoningEvent`：单个可折叠子事件。
- `ApprovalPanel`：绑定当前 turn 的确认输入。
- `Composer`：发送消息和停止运行。

CSS 上移除 `.timeline` 相关布局，将 `.conversation` 扩展为主内容区域。

## 兼容策略

- 保留旧 `/api/projects/{project_id}/runs/{run_id}/events`，用于 active run 和历史 run 回放。
- 老 trace 没有 `context`、`response_text` 或完整 `result` 时，子事件仍显示 metadata 和可用字段。
- 老 session 若无法可靠按 run_id 对齐 turn，则展示为普通消息序列。
- MVP1 的项目和会话 API 不删除。

## 风险

### trace 文件显著变大

完整 context、模型响应和工具结果都写入 `trace.jsonl`，单个 run 的 trace 可能快速增大。

处理方式：

- MVP2 接受该成本，换取单文件完整回放。
- 前端默认折叠大文本。
- SSE 补历史时仍发送完整事件；如果后续性能不足，再引入 artifacts 或按需加载。

### SSE 首次补发可能变重

历史 run 展开时，SSE 可能一次性补发大量完整原文。

处理方式：

- MVP2 默认只在用户选择会话或当前 run 时加载。
- 子事件默认折叠，DOM 中仍保存内容但不展开展示。
- 后续版本再考虑分页、懒加载和 artifacts。

### 敏感信息暴露风险增加

context 和工具结果可能包含路径、环境信息或敏感输出。

处理方式：

- 写入 trace 前继续使用现有 `SecretRedactor`。
- 不在 Web 层绕过 runtime evidence 链路。
- 文档明确 Web 模式仍是本地单用户控制台。

### Turn 对齐可能不完整

旧历史、异常中断和缺失 run_id 的消息可能无法准确组成 turn。

处理方式：

- 对齐失败时降级为普通消息。
- 对中断 run 显示 `incomplete`。
- 新 run 从 MVP2 开始保证 turn/run 绑定。

### 大文本渲染卡顿

完整 context 和工具结果可能很长，展开后影响页面滚动和性能。

处理方式：

- 子事件默认折叠。
- 展开内容使用固定高度滚动区域。
- 不自动展开运行中的事件。

## 实施顺序

1. 增强 runtime trace：写入完整 context、模型原始返回和完整工具结果。
2. 新增 `model_responded` 事件。
3. 新增 turn 构造逻辑，从 session history、run_ids 和 trace files 生成 turn view model。
4. 新增 `GET /api/projects/{project_id}/sessions/{session_id}/turns`。
5. 前端移除右侧 timeline DOM 和 CSS。
6. 前端改为两栏布局。
7. 前端加载 session 时渲染 turn list，而不是直接渲染 history。
8. 前端发送消息时创建 pending turn，并将 SSE 事件挂载到该 turn。
9. 实现 turn 级 reasoning drawer 和子事件折叠。
10. 将审批 UI 绑定到对应 turn。
11. 验证历史会话、当前运行、审批、中止和老 trace 兼容。

## MVP3 边界

MVP3 再考虑：

- 模型逐 token 输出。
- 最终回答 streaming。
- 大文本按需加载。
- context 内搜索。
- 子事件复制按钮。
- 全屏 trace 浏览。
- 面向评测系统的全局 timeline 和跨 run 对比。
