# JCode Web MVP1 设计

MVP1 的目标是把 JCode 从当前 CLI-only 形态扩展为单用户本地 Web 模式。Web 模式不改变 Agent 主链路，不重新实现 runtime，只在 `app` 层新增一个轻量入口，把会话、运行状态、事件流、用户审批和中止控制暴露给浏览器。

## 目标

- 提供单用户本地网页，用于运行 JCode。
- 提供项目列表。一个项目对应一个本地仓库目录，项目被选中后，工作区就是该仓库。
- 提供会话列表，用户在哪个会话发起聊天，就默认恢复哪个会话。
- 会话列表按项目隔离，只展示当前项目仓库 `.jcode/sessions/` 下已经存在的会话。
- 提供聊天输入、历史消息和最终回答展示。
- 将 `.jcode/runs/<run_id>/trace.jsonl` 和 session events 转成前端可消费的实时事件流。
- 把工具执行日志作为 Web 产品的核心视图，而不是隐藏调试信息。
- 当 Agent 需要用户审批或补充信息时，当前对话轮暂停；用户在前端补充后，从当前等待点继续。
- 支持中止运行；默认等待 10 秒，如果未能停止 Agent，则返回前端并要求用户再次点击。

## 非目标

- 不做多用户账号、认证、租户隔离和远程部署安全模型。
- 不做完整 Web IDE。
- 不做复杂前端工程化，MVP1 优先使用静态 HTML、CSS 和 JavaScript。
- 不优先使用 WebSocket；MVP1 的事件推送优先使用 SSE。
- 不重构 `JCodeAgent.ask()` 为异步生成器或流式 runtime。
- 不改变现有 CLI 行为。

## 架构原则

当前 CLI 链路是：

```text
src/app/cli.py
-> src/app/bootstrap.py
-> src/runtime/agent.py
```

Web 模式新增同级 app adapter：

```text
src/app/cli.py
src/app/web.py
src/app/bootstrap.py
src/runtime/agent.py
```

Web 层只负责项目选择、装配和运行管理：

```text
Browser UI
-> FastAPI Web API
-> WebRunManager
-> selected WebProject root
-> build_agent(config)
-> JCodeAgent.ask()
-> .jcode/runs/<run_id>/trace.jsonl
-> SSE event stream
-> Browser UI timeline
```

MVP1 必须保持一个边界：Web 不重新实现 Agent，不绕过 `JCodeAgent` 的工具、策略、记忆、checkpoint 和 evidence 链路。所有模型调用、工具执行和终态收口仍由 runtime 负责。

## 建议模块

```text
src/app/web.py
src/app/web_projects.py
src/app/web_server.py
src/app/web_runs.py
src/app/web_events.py
src/app/web_static/
  index.html
  app.js
  style.css
```

- `web.py`：Web 模式入口，类似 CLI 的 `main()`。
- `web_projects.py`：维护 Web 控制台项目列表；一个项目对应一个本地仓库目录。
- `web_server.py`：FastAPI app、HTTP 路由和静态文件托管。
- `web_runs.py`：管理后台运行线程、运行状态、审批等待和中止请求。
- `web_events.py`：读取 trace 和 session events，并转成 SSE 事件。
- `web_static/`：MVP1 前端资源。

## 依赖选择

当前项目核心依赖很少，MVP1 只增加 Web 服务所需依赖：

```toml
dependencies = [
  "pydantic>=2.0.0",
  "fastapi",
  "uvicorn",
]
```

不引入 React、Vite 或复杂构建链。前端先使用原生浏览器能力，降低本地运行门槛。

## Web 入口

建议新增独立命令，避免破坏现有 CLI 参数：

```toml
[project.scripts]
jcode = "src.app.cli:main"
jcode-web = "src.app.web:main"
```

启动方式：

```powershell
jcode-web --cwd .
```

或使用项目指定解释器：

```powershell
& 'D:\jt\ANACONDA\envs_dirs\jcode\python.exe' -m src.app.web --cwd .
```

默认监听：

```text
127.0.0.1:8765
```

MVP1 默认只绑定 localhost。

## HTTP API

### 项目列表

```http
GET /api/projects
```

返回：

```json
[
  {
    "id": "default",
    "name": "jcode",
    "root": "D:\\1technical_stack_study\\agent_study\\agent_projects\\jcode",
    "has_git": true,
    "session_count": 7,
    "active_runs": []
  }
]
```

### 创建项目

```http
POST /api/projects
```

请求：

```json
{
  "root": "D:\\1technical_stack_study\\agent_study\\agent_projects\\jcode",
  "name": "jcode"
}
```

行为：

- 校验 `root` 是存在的本地目录。
- 将该目录保存为 Web 项目。
- 后续在该项目下创建或恢复会话时，JCode 工作区就是该目录。

### 项目会话列表

```http
GET /api/projects/{project_id}/sessions
```

返回该项目仓库 `.jcode/sessions/` 下的所有已存在会话。

### 会话列表

```http
GET /api/sessions
```

该接口保留为兼容入口，等价于读取 `default` 项目的会话。MVP1 前端应优先使用项目作用域接口。

返回：

```json
[
  {
    "id": "20260827T230000-abc123",
    "created_at": "...",
    "updated_at": "...",
    "workspace_root": "...",
    "runtime_mode": "default",
    "latest_run_id": "..."
  }
]
```

### 会话详情

```http
GET /api/sessions/{session_id}
```

返回内容包括：

- session id。
- created_at / updated_at。
- runtime_mode。
- history。
- todo ledger。
- run_ids。
- latest_run_id。

### 新建会话

```http
POST /api/projects/{project_id}/sessions
```

行为：

- 在指定项目仓库的 `.jcode/sessions/` 下创建一个空 session。
- 返回 session 基本信息。
- 前端自动选中新 session。

### 发送消息

```http
POST /api/projects/{project_id}/sessions/{session_id}/messages
```

请求：

```json
{
  "message": "帮我实现一个功能"
}
```

行为：

- 后端使用该 `session_id` 构建 agent。
- 后端使用项目 root 作为 `cwd`，默认恢复该 session。
- 后台运行 `agent.ask(message)`。
- HTTP 请求立即返回，不等待最终回答。

返回：

```json
{
  "session_id": "...",
  "run_id": "...",
  "status": "running"
}
```

### 运行状态

```http
GET /api/runs/{run_id}
```

返回：

```json
{
  "run_id": "...",
  "session_id": "...",
  "status": "running",
  "final_text": "",
  "pending_question": null
}
```

状态取值：

```text
idle
running
waiting_approval
aborting
aborted
completed
failed
```

### 运行事件流

```http
GET /api/projects/{project_id}/runs/{run_id}/events
```

使用 SSE：

```text
event: tool_executed
data: {"run_id":"...","name":"shell","status":"success","changed_files":[]}
```

事件来源分两层：

- 历史补发：读取 `.jcode/runs/<run_id>/trace.jsonl`。
- 实时推送：读取 `WebRunManager` 的内存事件队列，或轮询 trace 文件新增内容。

### 用户审批

```http
POST /api/runs/{run_id}/approval
```

请求：

```json
{
  "answer": "允许执行"
}
```

行为：

- 当前运行必须处于 `waiting_approval`。
- 后端把 answer 交还给等待中的 `ask_user_callback`。
- Agent 从等待点继续执行。

### 中止运行

```http
POST /api/runs/{run_id}/abort
```

行为：

- 调用运行中的 `agent.abort()`。
- 默认等待 10 秒。
- 如果运行停止，返回 `aborted`。
- 如果仍未停止，返回 `aborting`，前端提示用户可以再次点击。

返回：

```json
{
  "run_id": "...",
  "status": "aborting",
  "message": "Abort requested. The run is still stopping; click stop again if needed."
}
```

## 运行管理

MVP1 使用一个进程内 `WebRunManager` 管理运行：

```text
WebRunManager
  active_runs: dict[str, WebRun]
```

`WebRun` 保存：

- run_id。
- session_id。
- status。
- agent。
- worker thread。
- started_at / finished_at。
- final_text。
- error。
- pending approval question。
- pending approval choices。
- approval synchronization primitive。

发送消息时：

```text
POST /api/sessions/{session_id}/messages
-> build_agent(config with session_id/resume)
-> inject web ask_user_callback
-> start background thread
-> return run handle
```

同一个 session 在同一时间只允许一个 active run。若已有 active run，后端返回冲突状态，前端提示用户等待或中止。

## 审批暂停与继续

`JCodeAgent` 已有：

```python
ask_user_callback: Callable[[str, list[str]], str] | None
```

Web 模式注入阻塞式 callback：

```text
agent.ask()
-> ask_user tool
-> web ask_user_callback(question, choices)
-> WebRun.status = waiting_approval
-> SSE: approval_required
-> callback 阻塞等待用户 answer
-> POST /api/runs/{run_id}/approval
-> callback 返回 answer
-> WebRun.status = running
-> agent.ask() 继续
```

这不是从 checkpoint 重新启动，而是在当前运行线程的等待点继续。MVP1 采用这种方式，因为它最贴近用户体验，也最少改动 runtime。

## 事件模型

前端至少展示以下事件：

```text
run_started
context_built
model_requested
model_parsed
tool_requested
tool_executed
subagent_completed
checkpoint_created
final_readiness_decision
memory_maintained
run_finished
approval_required
approval_answered
run_aborted
run_failed
```

工具事件需要突出展示：

- tool name。
- args。
- status。
- error_type。
- changed_files。
- metadata.policy。
- result 摘要。

工具执行日志是产品核心，因此 UI 不应把这些信息折叠到普通聊天消息里。

## 前端布局

MVP1 推荐三栏布局：

```text
左侧：Project / Session 列表
中间：Chat / Final Answer
右侧：Run Timeline / Tool Logs
```

左侧：

- 创建项目。
- 项目列表。
- 新建会话。
- 会话列表。
- 最近更新时间。
- 当前会话运行状态。

中间：

- 当前会话历史。
- 用户输入框。
- 发送按钮。
- 停止按钮。
- 审批问题与回答入口。
- 最终回答。

右侧：

- 当前 run id。
- step。
- 模型动作。
- 工具调用。
- 工具结果。
- policy decision。
- changed files。
- checkpoint。
- final gate。

## 会话恢复

Web UI 不直接暴露 CLI 的 `--resume latest` 语义给用户。项目是会话恢复的第一层上下文。

规则：

- 用户打开页面时，默认选中最近更新的项目。
- 选中项目后，前端加载该项目仓库下已有 session。
- 用户点击某个 session 后，后续消息都在该项目的该 session 下发送。
- 后端总是以项目 root 作为 `cwd`，并以该 session id 恢复 agent。
- 新建会话后，session 创建在当前项目仓库的 `.jcode/sessions/` 下。

`latest` 只作为页面初始化时的默认项目和默认 session 选择策略。

## 中止语义

`agent.abort()` 当前只能在 runtime loop 检查 `abort_requested` 时生效，无法强制打断正在执行的模型请求或 shell 调用。

MVP1 中止规则：

- 前端点击停止。
- 后端调用 `agent.abort()`。
- 后端最多等待 10 秒。
- 若 run 结束，返回 `aborted` 或实际终态。
- 若 run 仍在执行，返回 `aborting`。
- 前端保持停止按钮可用，并提示用户稍后再次点击。

MVP1 不做强杀线程。

## 关键风险

### 同步 Agent 与 Web 请求生命周期不匹配

`agent.ask()` 是同步阻塞调用，HTTP 请求不能直接等待完整运行结束，否则前端会长时间无响应，也无法稳定处理中止和审批。

MVP1 处理方式：

- 使用后台线程承载每次 run。
- `POST /api/sessions/{session_id}/messages` 只负责创建 run 并立即返回。
- 前端通过 run status 和 SSE 事件流获取进度。

### 中止不是强制取消

`agent.abort()` 当前依赖 runtime loop 在 step 间检查 `abort_requested`。如果 Agent 正在等待模型请求、执行长时间 shell 命令或卡在阻塞工具里，10 秒内可能无法停止。

MVP1 处理方式：

- abort API 调用 `agent.abort()` 后最多等待 10 秒。
- 10 秒内停止则返回最终状态。
- 10 秒内未停止则返回 `aborting`，前端提示用户稍后再次点击。
- MVP1 不强杀线程，不引入不安全的硬取消。

### 审批等待依赖进程内状态

Web 审批通过阻塞式 `ask_user_callback` 实现。等待问题、候选项和同步原语都保存在 `WebRunManager` 内存中。如果 Web 进程退出，等待中的 run 无法从同一个等待点恢复。

MVP1 处理方式：

- 明确 MVP1 是单用户本地控制台，不承诺进程重启后恢复 pending approval。
- 审批事件写入 SSE，用于前端刷新后恢复界面状态。
- 后续版本再考虑把 pending approval 状态持久化，并把 runtime 改造成可恢复的暂停点。

### SSE 历史补发与实时事件可能重复

前端刷新页面后需要补发 `.jcode/runs/<run_id>/trace.jsonl` 中已有事件，同时继续接收实时事件。如果没有事件序号或去重策略，前端 timeline 可能出现重复日志。

MVP1 处理方式：

- SSE 数据包含稳定的 event index 或 trace file offset。
- 前端按 event id 去重。
- 后端先补历史，再继续监听新增事件。

### Run id 获取存在时序问题

`run_id` 当前由 `JCodeAgent._begin_run()` 在 `agent.ask()` 内部创建。Web API 需要在发送消息后尽快返回 run handle，但真实 run id 可能要等后台线程进入 `_begin_run()` 后才知道。

MVP1 处理方式：

- `WebRunManager` 先创建 web_run_id。
- 后台线程启动后，从 session 最新 run 或 trace 创建结果绑定真实 JCode run_id。
- API 返回 web_run_id，事件流在绑定后返回真实 run_id。
- 后续版本可考虑在 runtime 层提供显式 `start_run` hook，减少推断。

### 同一会话并发运行会破坏 session 一致性

同一个 session 如果同时启动多个 run，都会读写同一个 session json、history、working_memory、todo ledger 和 run_ids，容易出现覆盖、乱序和恢复语义错误。

MVP1 处理方式：

- 同一项目的同一 session 同一时间只允许一个 active run。
- 若 session 已有 running、waiting_approval 或 aborting 状态，发送消息返回冲突。
- 前端提示用户等待完成或先中止当前 run。

### 工具日志可能包含敏感信息

工具执行日志是产品核心，但 shell 输出、文件内容、provider 错误和工具 metadata 可能包含密钥、路径或用户私有信息。

MVP1 处理方式：

- 继续依赖现有 `SecretRedactor` 和 evidence 写入链路。
- 前端默认展示摘要，长输出需要用户展开。
- 不在 Web 层绕过 runtime 的 trace/report 产物直接展示原始未脱敏结果。

### 本地单用户设计不能直接扩展为多用户平台

MVP1 的进程内 run manager、文件型 session store、workspace 读写权限和 shell 工具默认都假设本地单用户可信环境。直接部署到远程或多人使用会引入严重隔离和权限风险。

MVP1 处理方式：

- 默认只监听 `127.0.0.1`。
- 文档明确不支持远程多用户部署。
- 多用户平台需要另行设计认证、workspace 隔离、tool sandbox、secret 管理和审计权限。

## 实施顺序

1. 新增 `jcode-web` 入口。
2. 新增 FastAPI server 和静态页面托管。
3. 实现 session list、session detail 和 create session。
4. 实现发送消息和后台运行 `agent.ask()`。
5. 实现 run status。
6. 实现 SSE 事件流。
7. 实现工具日志 timeline。
8. 实现 Web `ask_user_callback`，支持审批暂停和继续。
9. 实现 abort，默认等待 10 秒。
10. 更新 README。
11. 使用项目指定 Python 环境验证。

验证命令：

```powershell
& 'D:\jt\ANACONDA\envs_dirs\jcode\python.exe' -m pip install -e .
& 'D:\jt\ANACONDA\envs_dirs\jcode\python.exe' -m src.app.web --help
```
