# JCode 项目完整链路

本文以当前代码为准，用 Mermaid 展示 JCode 从 CLI 或 Web 接收请求，到上下文构建、Provider 调用、工具循环、状态持久化、记忆维护和最终展示的完整链路。

## 1. 端到端总图

```mermaid
flowchart TD
    User[用户请求] --> Entry{请求入口}
    Entry -->|CLI| CLI[src.app.cli]
    Entry -->|Web| WebAPI[src.app.web_server]
    CLI --> Config[src.app.config 加载全局配置]
    WebAPI --> WebRun[src.app.web_runs WebRunManager]
    WebRun --> Config
    Config --> Bootstrap[src.app.bootstrap build_agent]

    Bootstrap --> Workspace[state.Workspace]
    Bootstrap --> Session[state.SessionStore]
    Bootstrap --> Runtime[runtime.JCodeAgent]
    Bootstrap --> Context[context.ContextManager]
    Bootstrap --> Models[providers.ModelRegistry 与 ModelRouter]
    Bootstrap --> Tools[tools.ToolRegistry 与 ToolExecutor]
    Bootstrap --> Workers[workers.WorkerManager]
    Bootstrap --> Evidence[evidence.RunStore 与 SessionEventBus]
    Bootstrap --> Memory[memory.WorkingMemory 与 DurableMemoryStore]

    Session --> Resume{是否恢复会话}
    Resume -->|是| ResumeEval[state.resume 校验 Checkpoint]
    ResumeEval --> Memory
    Resume -->|否| Runtime
    Memory --> Runtime

    Runtime --> BeginRun[创建 TaskState 和 run 目录]
    BeginRun --> BuildContext[构建 ContextResult]
    BuildContext --> Snapshot[编译 ProviderInputSnapshot]
    Snapshot --> Provider[DeepSeek 或 MiniMax Responses]
    Provider --> Response[ModelResponse]

    Response --> HasTools{存在原生 tool_calls}
    HasTools -->|是| ToolLoop[按顺序执行 ModelToolCall]
    ToolLoop --> Tools
    Tools --> ToolResult[ToolResult 与 function_call_output]
    ToolResult --> StateWrite[更新 History WorkingMemory TaskState]
    StateWrite --> Checkpoint[写入 checkpoint.json]
    Checkpoint --> BuildContext

    HasTools -->|否| Complete{响应是否完整}
    Complete -->|需要续写| Continue[保存 Provider continuation]
    Continue --> BuildContext
    Complete -->|完整| FinalGate[FinalGate 评估最终就绪]
    FinalGate -->|要求纠正| Correction[注入 correction packet]
    Correction --> BuildContext
    FinalGate -->|允许结束| Finish[finish_run]

    Finish --> MemoryMaintain[维护 Daily Log 与 Durable Memory]
    Finish --> EvidenceWrite[写 trace task_state report session]
    MemoryMaintain --> EvidenceWrite
    EvidenceWrite --> Output{结果出口}
    Output -->|CLI| Terminal[终端最终回答]
    Output -->|Web| SSE[SSE 增量事件与最终状态]
    SSE --> Browser[Web 时间线]
```

主循环由 `src/runtime/agent.py:JCodeAgent._ask_loop()` 驱动。一次模型响应可以包含多个原生工具调用；工具结果写回 Provider continuation 后进入下一子轮。没有工具调用时，运行时根据完成状态、输出续写和 FinalGate 决定继续或结束。

## 2. 工具治理分支

```mermaid
flowchart TD
    Call[ModelToolCall] --> Lookup[ToolRegistry 查找]
    Lookup --> Args[Pydantic 参数校验]
    Args --> Profile[ToolSetProfile 校验]
    Profile --> Scope[write_scope 校验]
    Scope --> Repeat{重复调用检查}
    Repeat -->|read_file| ReadCounter[WorkingMemory 版本与范围计数]
    Repeat -->|其他工具| CallGuard[CallGuard 参数与上下文计数]
    ReadCounter --> Policy
    CallGuard --> Policy{工具类型}

    Policy -->|runtime 工具| RuntimeBranch[Plan Todo AskUser Subagent 分支]
    Policy -->|普通工具| ToolPolicy[ToolPolicyChecker]
    ToolPolicy --> Permission[PermissionChecker]
    Permission --> Sandbox[SandboxPolicy]
    Sandbox --> Execute[执行具体工具]
    RuntimeBranch --> Execute

    Execute --> Risky{是否可能产生副作用}
    Risky -->|是| Diff[比较 Workspace 快照]
    Risky -->|否| Redact[SecretRedactor]
    Diff --> Changed[补充 changed_files]
    Changed --> Redact
    Redact --> Large{结果是否超过外置阈值}
    Large -->|是| Artifact[写 runs 下的 artifacts]
    Large -->|否| Inline[结果直接内联]
    Artifact --> History[写 tool_result HistoryEvent]
    Inline --> History
    History --> Stale{源文件是否被后续修改}
    Stale -->|是| MarkStale[旧读取证据标记 stale]
    Stale -->|否| Next[进入下一轮上下文]
    MarkStale --> Next
```

核心源码：

- `src/tools/executor.py`：统一工具治理入口。
- `src/tools/registry.py`：工具注册和 schema 输出。
- `src/policy/call_guard.py`、`permissions.py`、`sandbox.py`、`tool_rules.py`：调用、权限和副作用策略。
- `src/tools/workspace.py`、`shell.py`：工作区文件与 shell 实现。
- `src/evidence/tool_artifacts.py`：大结果外置和观察摘要。

详见 [工具治理完整链路](工具治理完整链路.md)。

## 3. 上下文治理分支

```mermaid
flowchart TD
    Inputs[Session History 与 WorkingMemory] --> Manager[ContextManager.build]
    Rules[JCODE.md 系统与安全规则] --> Prefix[prefix]
    Skills[选中技能] --> Skill[skill]
    Inputs --> History[结构化 HistoryEvent]
    Inputs --> WM[WorkingMemory 冻结快照]
    Registry[ToolRegistry] --> Definitions[ToolDefinition]

    Prefix --> Candidate[ContextResult candidate]
    Skill --> Candidate
    History --> Candidate
    WM --> Candidate
    Definitions --> Candidate

    Candidate --> Count[TokenizerAdapter 统计序列化 token]
    Count --> Pressure{上下文压力等级}
    Pressure -->|Level 0| Keep[保留完整可用历史]
    Pressure -->|Level 1 至 3| Window[收缩历史窗口和低优先级内容]
    Pressure -->|Level 4| Compact[历史压缩事务]

    Compact --> HistoryArtifact[写 history before compact artifact]
    HistoryArtifact --> Verify[fsync 重新读取并验签]
    Verify --> Summary[摘要模型或确定性 fallback]
    Summary --> Commit[原子提交 compact_summary candidate]

    Keep --> Capacity[最终容量校验]
    Window --> Capacity
    Commit --> Capacity
    Capacity --> Snapshot[compile_provider_input_snapshot]
    Snapshot --> Instructions[instructions 等于 prefix]
    Snapshot --> Input[input 等于 skill history memory continuation]
    Snapshot --> Functions[tools 等于 function schemas]
```

发送前必须满足：

```text
serialized_input_tokens + actual_max_new_tokens + safety_margin_tokens
<= min(375000, profile.context_window_tokens)
```

超限会抛出结构化运行时错误，不会静默截断。详见 [上下文治理完整链路](上下文治理完整链路.md)。

## 4. Provider 分支

```mermaid
flowchart LR
    TOML[全局 .jcode.toml] --> AppConfig[AppConfig]
    AppConfig --> Profiles[ModelProfile 集合]
    Profiles --> Registry[ModelRegistry]
    FactoryA[deepseek + openai_responses] --> Registry
    FactoryB[minimax + openai_responses] --> Registry

    Session[Session 模型选项] --> Resolve[state.model_selection]
    Profiles --> Resolve
    Resolve --> RunSnapshot[TaskState.model_profile]

    Context[ContextResult] --> Compile[ProviderInputSnapshot 编译]
    RunSnapshot --> Router[ModelRouter]
    Compile --> Router
    Router --> Client{按 provider 和 protocol 选客户端}
    Client --> DeepSeek[DeepSeekClient]
    Client --> MiniMax[MiniMaxClient]
    DeepSeek --> API[Responses API]
    MiniMax --> API
    API --> Normalize[归一化响应]
    Normalize --> Response[ModelResponse]
    Response --> Text[text 与 reasoning]
    Response --> Calls[ModelToolCall 列表]
    Response --> Status[finish incomplete error usage]
```

注册表按 `(provider, api_protocol)` 选择工厂，并按 profile 缓存客户端。模型只能在 run 之间切换；run 开始后只使用 `TaskState.model_profile` 快照。详见 [Provider 架构与模型切换链路](Provider架构与模型切换链路.md)。

## 5. 记忆分支

```mermaid
flowchart TD
    Run[当前 run] --> WM[WorkingMemory v2]
    Read[文件读取] --> Freshness[文件 freshness 与读取范围]
    Tool[工具结果] --> Observe[工具观察与 artifact 引用]
    Todo[Todo 和子 Agent] --> Projection[进度与结果投影]
    Freshness --> WM
    Observe --> WM
    Projection --> WM

    Durable[DurableMemoryStore] --> Retrieve[词项检索]
    Query[当前任务查询] --> Retrieve
    Retrieve --> Retrieved[retrieved_memory]
    Retrieved --> WM
    WM --> Context[ContextManager 输入]

    Finish[finish_run] --> Maintain[maintain_after_turn]
    Maintain --> Sensitive{敏感信息检查}
    Sensitive -->|通过| Daily[Daily Log]
    Sensitive -->|拒绝| Skip[不写入长期记忆]
    Daily --> Notes[notes.jsonl]
    Notes --> Consolidate[consolidate_daily_logs]
    Daily --> Consolidate
    Consolidate --> Topics[topics 下的主题文件]
    Consolidate --> Index[MEMORY.md 索引]

    Dream[run_dream] --> DreamProfile[dream Tool Profile]
    DreamProfile --> MemoryScope[仅允许 .jcode memory]
    MemoryScope --> Consolidate
    Consolidate --> DreamReport[dream_reports 审计]
```

WorkingMemory 是当前运行控制面，Daily Log 是过程层，Durable Memory 是稳定结论层。详见 [记忆系统完整链路](记忆系统完整链路.md)。

## 6. 子 Agent 分支

```mermaid
flowchart TD
    Parent[JCodeAgent] --> Tool{子 Agent 工具}
    Tool -->|spawn_subagent| Validate[校验 subagent_type 与 write_scope]
    Validate --> Mode{当前是否 plan mode}
    Mode -->|是| ExploreOnly[只允许 Explore]
    Mode -->|否| WorkerOrExplore[允许 worker 或 Explore]
    ExploreOnly --> Manager[WorkerManager.spawn]
    WorkerOrExplore --> Manager
    Manager --> Runtime[创建 WorkerRuntime]
    Manager --> TaskFile[写 workers ID task_state.json]
    Manager --> SpawnEvent[发送 subagent_spawned 事件]

    Tool -->|send_subagent_message| Mailbox[WorkerRuntime mailbox]
    Mailbox --> MessageEvent[subagent_message_sent]
    Tool -->|wait_subagent| RunWorker[WorkerRuntime.run]
    RunWorker --> Result[WorkerResult]
    Result --> ResultFile[result.json 与 trace.jsonl]
    Result --> CompletedEvent[subagent_completed]
    Result --> ToolResult[返回父 Agent ToolResult]
    ToolResult --> ParentMemory[写入父 WorkingMemory]
    ParentMemory --> Parent
```

当前子任务类型只有 `worker` 和 `Explore`。写能力同时受 Tool Profile 和显式 `write_scope` 约束；plan mode 只允许创建 `Explore`。子 Agent 结果不会直接成为最终回答，而是作为工具结果和 WorkingMemory 信息返回主循环。

## 7. 恢复与 Checkpoint 分支

```mermaid
flowchart TD
    Resume[CLI 或 Web 请求恢复 session] --> Load[SessionStore.load_requested]
    Load --> Session[恢复 history working_memory todo runtime_mode]
    Session --> Latest[定位 latest_run_id]
    Latest --> Checkpoint[读取 checkpoint.json schema v3]
    Checkpoint --> Schema{schema 与 resumable 校验}
    Schema -->|失败| Invalid[schema_mismatch 或 checkpoint_not_resumable]
    Schema -->|通过| Fingerprint{工作区 fingerprint 一致}
    Fingerprint -->|否| Mismatch[workspace_mismatch]
    Fingerprint -->|是| Freshness{hot files freshness 一致}
    Freshness -->|否| Partial[partial_stale 与 stale_paths]
    Freshness -->|是| Valid[full_valid]

    Invalid --> ResumeContext[构建 resume_context]
    Mismatch --> ResumeContext
    Partial --> ResumeContext
    Valid --> ResumeContext
    ResumeContext --> WM[写入 WorkingMemory]
    ResumeContext --> Events[session_resumed 与 resume_checkpoint_evaluated]
    WM --> NextRun[下一次 ContextManager.build]

    ToolDone[工具执行完成] --> Create[CheckpointManager.create]
    ModelDone[模型完成或续写] --> Create
    GateRerun[FinalGate 要求纠正] --> Create
    Create --> Saved[保存 task state memory todo continuation worker refs]
```

Checkpoint 保存恢复所需运行快照，但恢复结果只进入 WorkingMemory，不重写既有 HistoryEvent。`workspace_mismatch` 表示整体工作区指纹变化；`partial_stale` 表示最近读取文件的 freshness 发生变化。

## 8. Web 事件分支

```mermaid
flowchart TD
    Browser[浏览器 app.js] --> API[FastAPI web_server]
    API --> Project[WebProjectStore 选择项目]
    API --> Session[创建或读取 Session]
    API --> Send[POST messages]
    Send --> Manager[WebRunManager.start_run]
    Manager --> Conflict{Session 是否已有 active run}
    Conflict -->|是| Reject[返回 409]
    Conflict -->|否| WebRun[创建 WebRun]
    WebRun --> Bind[绑定 RunStore 事件桥]
    Bind --> Thread[后台线程执行 agent.ask]

    Thread --> Trace[trace.jsonl]
    Thread --> SessionEvents[session events jsonl]
    Trace --> Steps[web_steps 构建步骤]
    SessionEvents --> Turns[web_turns 组装会话轮次]
    Steps --> Patch[生成 step_patch]
    Turns --> Snapshot[active_run snapshot]

    Browser --> Stream[GET runs events with after cursor]
    Stream --> SSE[Server Sent Events]
    WebRun --> SSE
    Patch --> SSE
    SSE --> Dedup[按 event_id 去重]
    Dedup --> Apply[增量 patch 当前 turn]
    Apply --> Timeline[更新状态步骤审批和最终文本]

    Browser --> Abort[POST abort]
    Abort --> Aborting[run_abort_requested]
    Aborting --> Thread
    Thread --> End{完成状态}
    End --> Completed[web_run_completed]
    End --> Aborted[run_aborted]
    End --> Failed[run_failed]
    Completed --> SSE
    Aborted --> SSE
    Failed --> SSE
```

活动 run 从内存事件队列增量推送；历史 run 从 `trace.jsonl` 重放。客户端用 `after` cursor 和 `event_id` 去重，`step_patch` 只更新变化的步骤，避免整页或整段会话反复重绘。

## 9. 持久化布局

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
    notes.jsonl
    logs/YYYY/MM/YYYY-MM-DD.md
    topics/*.md
    dream_reports/*.json
  workers/<worker_id>/
    task_state.json
    trace.jsonl
    result.json
```

## 10. 源码阅读顺序

1. `src/app/cli.py`、`src/app/web.py`：启动入口。
2. `src/app/config.py`、`src/app/bootstrap.py`：配置与依赖装配。
3. `src/runtime/agent.py`：模型、工具、续写、终态主循环。
4. `src/context/manager.py`、`src/providers/request.py`：上下文与请求快照。
5. `src/providers/registry.py`、`router.py`、`deepseek.py`、`minimax.py`：Provider 链路。
6. `src/tools/executor.py`、`src/policy/`：工具与安全治理。
7. `src/state/checkpoint.py`、`resume.py`、`session.py`：持久化与恢复。
8. `src/memory/`、`src/workers/`、`src/evidence/`：记忆、子 Agent 和审计。
9. `src/app/web_runs.py`、`web_events.py`、`web_steps.py`、`web_turns.py`：Web 增量展示。
