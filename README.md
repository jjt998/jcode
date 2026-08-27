# JCode

JCode 是一个本地 Coding Agent，保留更小的代码体量和更直接的工程链路。

它覆盖本地代码代理最核心的一条链路：命令行入口、运行时装配、ReAct 循环、上下文构建、模型调用、工具执行、子 Agent、策略治理、工作记忆、运行证据、Checkpoint 和最终回答。

JCode 已包含受限 Dream 子 Agent、会话级 plan mode、Explore 子 Agent 和工具 Profile，但还不包含 TUI、完整评测套件、复杂多 Provider 路由、大规模 benchmark、vision/media 工具等非核心能力。

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

## Web 模式

JCode MVP1 提供单用户本地 Web 控制台，用于查看会话、发送任务和观察工具执行日志。

Web 控制台按项目组织工作。一个项目对应一个本地仓库目录；选择项目后，工作区就是该仓库，左侧会显示这个仓库 `.jcode/sessions/` 下已经存在的会话。可以在当前项目中新建会话，也可以选择已有会话继续对话。

首次使用或依赖变更后，先在项目根目录安装：

```powershell
& 'D:\jt\ANACONDA\envs_dirs\jcode\python.exe' -m pip install -e .
```

安装后可以使用命令入口启动：

```powershell
jcode-web --cwd .
```

也可以直接使用项目指定解释器启动：

```powershell
& 'D:\jt\ANACONDA\envs_dirs\jcode\python.exe' -m src.app.web --cwd .
```

启动后在浏览器打开：

```text
http://127.0.0.1:8765
```

Web 模式默认只监听 `127.0.0.1:8765`，只面向本机单用户使用，不作为远程多用户平台。

## 工具概览

JCode 现在提供这些正式入口：

| 入口 | 作用 |
| --- | --- |
| `todo_add` | 新增会话级 todo。 |
| `todo_update` | 更新已有 todo。 |
| `todo_list` | 查看 todo 列表。 |
| `ask_user` | 向用户发起阻塞式澄清。 |
| `enter_plan_mode` | 进入 plan mode。 |
| `exit_plan_mode` | 退出 plan mode。 |
| `spawn_subagent` | 创建子任务。 |
| `send_subagent_message` | 向子任务补充消息。 |
| `wait_subagent` | 等待子任务完成。 |

`worker` 和 `Explore` 都是子 agent 类型，但语义不同：

| 类型 | 语义 | 工具面 | 写入能力 |
| --- | --- | --- | --- |
| `worker` | 普通子任务 | 可写 profile | 允许在显式 `write_scope` 内写入 |
| `Explore` | 只读探索子任务 | readonly profile | 不允许写入 |
| `plan mode` | 会话级规划模式 | plan profile | 只允许 `Explore`，并限制写入 active plan artifact |

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
    MEMORY.md
    logs/YYYY/MM/YYYY-MM-DD.md
    topics/*.md
    dream_reports/*.json
    notes.jsonl  # legacy compatibility
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
- `context`：模型上下文分段构建、prefix 渲染、动态工具定义注入、项目规则注入、预算估算和技能提示注入。
- `tools`：工具注册、参数校验、工作区读写、shell、patch 和子任务工具。
- `policy`：权限、工具规则、重复调用、sandbox、Final Gate 和敏感信息处理。
- `state`：Session、TaskState、History、Checkpoint 和 Workspace。
- `memory`：Working_Memory、Daily Log、Durable Memory、检索、安全过滤和轮次整理。
- `workers`：子 Agent 的创建、消息、等待、结果和 trace；plan mode 下只允许 Explore 子 Agent。
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

- `prefix` 放稳定系统提示词，包括系统规则、输出协议、动态工具定义、工作区 `JCODE.md` 项目规则和安全规则。
- `skill` 放技能相关提示。
- `working_memory` 放 `Working_Memory` 渲染结果，包括当前任务目标、最近文件、文件 freshness、恢复上下文、检索到的长期记忆、子 Agent 结果和工具观察。
- `history` 放当前 session 的历史对话和工具结果。
- `current_request` 放本轮用户请求。

`prefix` 的工具定义来自运行时 `ToolRegistry`，并动态渲染工具名、说明、读写风险标记和 Pydantic 参数 schema，避免模型猜测不存在的工具名。`prefix` 还会读取当前工作区根目录的 `JCODE.md` 作为项目规则；如果文件不存在，则项目规则层渲染为 `(none)`。

变化较快的事实不会塞进稳定前缀，而是进入 `working_memory` 或 `history`。工具定义和 `JCODE.md` 虽然由运行时渲染，但它们属于本轮稳定规则输入，不属于工作记忆。

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

JCode 对外统一使用三层记忆认知：

- `Working_Memory`：当前回合的短期推理状态，包含 `task`、`files`、`retrieval`、`tools`、`safety`。它服务当前上下文构建和工具策略，不追求长期保存。
- `Daily Log`：每轮追加的过程层日志，位于 `.jcode/memory/logs/YYYY/MM/YYYY-MM-DD.md`，记录当天发生了什么、做了什么和本轮摘要。它是整理输入，不是最终知识库。
- `Durable Memory`：长期稳定结论层，包括 `.jcode/memory/MEMORY.md`、`.jcode/memory/topics/*.md`、未来的结构化记忆文件和可检索沉淀内容。`notes.jsonl` 仅作为旧版本兼容入口保留。

每轮结束时，JCode 会先把摘要写入 Daily Log，再把过程信号整理到 Durable Memory 的 topic 和索引里。包含明显密钥、token、password、secret 等敏感特征的内容不会进入长期结论层。

Dream 子 Agent 可以通过内部入口 `agent.run_dream()` 手动触发。Dream 使用受限工具 Profile，只能在 `.jcode/memory/` 内整理 Daily Log、topic 和 `MEMORY.md`，不会修改普通源码文件。

Checkpoint 保存在每次运行的 `checkpoint.json` 中，记录 session、run、step、last action、changed files、working memory、workspace fingerprint 和 worker refs，用于后续恢复判断。

## 子 Agent

JCode 支持子 Agent 工具：

- `spawn_subagent`：创建子任务，默认是普通 worker；plan mode 只允许 Explore。
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


