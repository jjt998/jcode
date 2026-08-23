# JCode

JCode 是一个轻量级本地 Coding Agent，设计上借鉴 Pico，但刻意保留更小的代码体量和更直接的工程链路。

它覆盖本地代码代理最核心的一条链路：命令行入口、运行时装配、ReAct 循环、上下文构建、模型调用、工具执行、子 Agent、策略治理、工作记忆、运行证据、Checkpoint 和最终回答。

JCode 不包含 Pico 的 TUI、完整评测套件、Dream 整理、复杂多 Provider 路由、大规模 benchmark、vision/media 工具等非核心能力。

## 安装

在项目根目录执行：

```bash
pip install -e .
```

项目要求 Python 3.10 或更高版本，核心依赖只有 `pydantic`。

## 配置

可以通过 `.jcode.toml` 或环境变量配置模型服务。

Windows PowerShell 示例：

```powershell
$env:JCODE_API_KEY="sk-..."
$env:JCODE_BASE_URL="https://api.openai.com/v1"
$env:JCODE_MODEL="gpt-5"
```

`.jcode.toml` 示例：

```toml
provider = "openai"

[providers.openai]
api_key = "sk-..."
base_url = "https://api.openai.com/v1"
model = "gpt-5"

[security]
approval = "ask"
sandbox = "best_effort"

[runtime]
max_steps = 50
max_new_tokens = 8192
```

如果没有配置 `JCODE_API_KEY`，JCode 仍会构建上下文并写入运行证据，但不会发送真实模型请求。

## 运行

```bash
jcode "hi"
python -m jcode --help
```

常用参数：

```bash
jcode --cwd . "帮我查看项目结构"
jcode --resume latest "继续"
jcode --session-id demo-session "实现一个小功能"
```

每次运行都会写入 `.jcode/` 目录：

```text
.jcode/
  runs/<run_id>/
    trace.jsonl
    task_state.json
    checkpoint.json
    report.json
    artifacts/
  sessions/
    <session_id>.json
    <session_id>.events.jsonl
  memory/
    notes.jsonl
  workers/
```

## 核心架构

JCode 保留一条清晰的主链路：

```text
jcode.app.cli
-> jcode.app.bootstrap
-> jcode.runtime.agent
-> context / tools / policy / state / memory / workers / evidence
```

主要职责分层：

- `app`：命令行参数、配置读取和运行时装配。
- `runtime`：Agent 主循环、模型动作解析、终态收口和异常停止。
- `context`：模型上下文分段构建、预算估算、技能提示注入。
- `tools`：工具注册、参数校验、工作区读写、shell、patch 和子任务工具。
- `policy`：权限、工具规则、重复调用、sandbox、Final Gate 和敏感信息处理。
- `state`：Session、TaskState、History、Checkpoint 和 Workspace。
- `memory`：Working Memory、Durable Memory、检索、安全过滤和轮次整理。
- `workers`：轻量子 Agent 的创建、消息、等待、结果和 trace。
- `evidence`：运行 trace、session event、report、artifact 和审计数据。

## 代码约定

- 类实例属性必须在类体中显式声明类型，避免只在 `__init__` 中动态挂载，保证 IDE 可以建立稳定的声明、定义和跳转链路。

## 上下文结构

JCode 始终使用稳定的上下文结构，即使用户只输入一个很短的问题：

```text
prefix
skill
working_memory
history
current_request
```

其中：

- `prefix` 放稳定身份、输出协议和安全规则。
- `skill` 放技能相关提示。
- `working_memory` 放当前任务目标、最近文件、文件 freshness、恢复上下文、检索到的长期记忆、子 Agent 结果和工具观察。
- `history` 放当前 session 的历史对话和工具结果。
- `current_request` 放本轮用户请求。

变化较快的事实不会塞进稳定前缀，而是进入 `working_memory` 或 `history`。

## 工具安全链

工具执行遵循固定链路：

```text
工具查找
-> 参数校验
-> 重复调用检查
-> 行为策略检查
-> 权限检查
-> sandbox 检查
-> 执行工具
-> 工作区变更检测
-> 结果脱敏
-> 写入历史、trace、checkpoint 和 report
```

当前覆盖的安全场景包括：

- 未知工具和参数错误。
- 路径逃逸。
- 写文件前未读文件。
- 重复相同工具调用。
- shell timeout 和 sandbox 拦截。
- 工具执行失败或部分成功。
- 结果中的敏感信息脱敏。
- Final Gate 对空回答和未交代失败工具的拦截。

## 记忆与恢复

JCode 使用两层记忆：

- Working Memory：当前任务内的目标、约束、最近文件、工具观察、子 Agent 结果和恢复上下文。
- Durable Memory：跨 session 的简短长期记忆，保存在 `.jcode/memory/notes.jsonl`。

每轮结束时，JCode 会尝试把任务结果整理成长期记忆。包含明显密钥、token、password、secret 等敏感特征的内容不会写入长期记忆。

Checkpoint 保存在每次运行的 `checkpoint.json` 中，记录 session、run、step、last action、changed files、working memory、workspace fingerprint 和 worker refs，用于后续恢复判断。

## 子 Agent

JCode 支持轻量子 Agent 工具：

- `spawn_subagent`：创建子任务。
- `send_subagent_message`：向已有子任务补充消息。
- `wait_subagent`：等待子任务完成并收集结果。

子 Agent 继承父任务的工作区、工具策略、权限模型和 sandbox 设置。子 Agent 的结果不会直接变成父 Agent 的最终回答，而是进入父 Agent 的 working memory，由主循环决定如何使用。

子任务状态和结果写入：

```text
.jcode/workers/<worker_id>/
  task_state.json
  trace.jsonl
  result.json
```

## 运行证据

JCode 的每次运行都可以审计：

- `trace.jsonl`：逐事件记录 run started、context built、model parsed、tool executed、checkpoint created、memory maintained、run finished 等事件。
- `task_state.json`：当前任务状态、步数、工具数、失败工具、变更文件和最终答案。
- `checkpoint.json`：恢复所需的运行状态和工作区指纹。
- `report.json`：运行汇总、事件计数、worker refs、memory audit 和最终回答长度。
- `<session_id>.events.jsonl`：跨 run 的 session 事件流。

这些文件用于回答两个问题：这次运行做了什么，以及为什么停在当前状态。
