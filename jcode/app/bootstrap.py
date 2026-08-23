from __future__ import annotations

from jcode.app.config import AppConfig
from jcode.context.builder import PromptBuilder
from jcode.evidence.store import RunStore
from jcode.evidence.session_log import SessionEventBus
from jcode.memory.durable import DurableMemoryStore
from jcode.memory.working import WorkingMemory
from jcode.policy.call_guard import CallGuard
from jcode.policy.final_gate import FinalGate
from jcode.policy.permissions import PermissionChecker
from jcode.policy.sandbox import SandboxPolicy
from jcode.policy.secrets import SecretRedactor
from jcode.policy.tool_rules import ToolPolicyChecker
from jcode.providers.openai_compatible import OpenAICompatibleClient
from jcode.providers.router import ModelRouter
from jcode.runtime.agent import JCodeAgent
from jcode.state.resume import build_resume_context
from jcode.state.session import SessionStore
from jcode.state.workspace import Workspace
from jcode.tools.executor import ToolExecutor
from jcode.tools.registry import build_default_registry
from jcode.workers.manager import WorkerManager


def build_agent(config: AppConfig) -> JCodeAgent:
    workspace = Workspace.build(config.cwd)
    state_dir = workspace.root / ".jcode"
    session_store = SessionStore(state_dir / "sessions")
    run_store = RunStore(state_dir / "runs")
    memory_store = DurableMemoryStore(state_dir / "memory")
    session = session_store.load_requested(config.session_id, config.resume, workspace.root)
    working_memory = WorkingMemory.from_dict(session.get("working_memory", {}), workspace.root)
    if config.resume:
        working_memory.resume_context = build_resume_context(
            session=session,
            session_store=session_store,
            run_store=run_store,
            workspace=workspace,
            resume_requested=config.resume,
        )
    redactor = SecretRedactor.from_environment(extra_names=("JCODE_API_KEY",))
    registry = build_default_registry(workspace)
    permissions = PermissionChecker(config.approval)
    sandbox = SandboxPolicy(config.sandbox)
    call_guard = CallGuard()
    tool_policy = ToolPolicyChecker(workspace, working_memory)
    executor = ToolExecutor(
        registry=registry,
        permissions=permissions,
        tool_policy=tool_policy,
        sandbox=sandbox,
        call_guard=call_guard,
        redactor=redactor,
        working_memory=working_memory,
    )
    client = OpenAICompatibleClient(config.api_key, config.base_url, config.model)
    router = ModelRouter(client)
    session_events = SessionEventBus(state_dir / "sessions" / f"{session['id']}.events.jsonl")
    if config.resume:
        session_events.emit("session_resumed", **working_memory.resume_context)
        session_events.emit("resume_checkpoint_evaluated", **working_memory.resume_context)
    workers = WorkerManager(workspace, state_dir / "workers", executor, router, config, session_events=session_events)
    builder = PromptBuilder(workspace=workspace, durable_memory=memory_store)
    return JCodeAgent(
        config=config,
        workspace=workspace,
        session=session,
        session_store=session_store,
        run_store=run_store,
        memory_store=memory_store,
        session_events=session_events,
        working_memory=working_memory,
        prompt_builder=builder,
        model_router=router,
        tool_executor=executor,
        worker_manager=workers,
        final_gate=FinalGate(),
        redactor=redactor,
    )
