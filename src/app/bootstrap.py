from __future__ import annotations

from src.app.config import AppConfig
from src.app.config import compile_static_capacity_input
from src.context.budget import TokenizerAdapter, validate_static_model_capacity
from src.context.manager import ContextManager
from src.context.prefix import render_prefix
from src.evidence.store import RunStore
from src.evidence.session_log import SessionEventBus
from src.memory.durable import DurableMemoryStore
from src.memory.working import WorkingMemory
from src.policy.call_guard import CallGuard
from src.policy.final_gate import FinalGate
from src.policy.permissions import PermissionChecker
from src.policy.sandbox import SandboxPolicy
from src.policy.secrets import SecretRedactor
from src.policy.tool_rules import ToolPolicyChecker
from src.policy.tool_profiles import build_tool_profiles
from src.providers.deepseek import DeepSeekClient
from src.providers.minimax import MiniMaxClient
from src.providers.registry import ModelRegistry
from src.providers.router import ModelRouter
from src.runtime.agent import JCodeAgent
from src.state.resume import build_execution_fingerprint, build_resume_context
from src.state.session import SessionStore
from src.state.workspace import Workspace
from src.tools.executor import ToolExecutor
from src.tools.registry import build_default_registry
from src.workers.manager import WorkerManager


def build_agent(config: AppConfig) -> JCodeAgent:
    workspace = Workspace.build(config.cwd)
    state_dir = workspace.root / ".jcode"
    session_store = SessionStore(state_dir / "sessions")
    run_store = RunStore(state_dir / "runs", workspace_root=workspace.root)
    memory_store = DurableMemoryStore(state_dir / "memory")
    session = session_store.load_requested(config.session_id, config.resume, workspace.root)
    working_memory = WorkingMemory.from_dict(session.get("working_memory", {}), workspace.root)
    redactor = SecretRedactor.from_environment(extra_names=("JCODE_API_KEY",))
    registry = build_default_registry()
    tool_profiles = build_tool_profiles(registry)
    permissions = PermissionChecker(config.approval)
    sandbox = SandboxPolicy(config.sandbox)
    call_guard = CallGuard()
    tool_policy = ToolPolicyChecker(workspace)
    executor = ToolExecutor(
        workspace=workspace,
        registry=registry,
        permissions=permissions,
        tool_policy=tool_policy,
        sandbox=sandbox,
        call_guard=call_guard,
        redactor=redactor,
    )
    model_registry = ModelRegistry(config.model_profiles, config.default_model_profile)
    model_registry.register("deepseek", "openai_responses", DeepSeekClient)
    model_registry.register("minimax", "openai_responses", MiniMaxClient)
    router = ModelRouter(model_registry)
    selected_profile_id = config.explicit_model_profile or str(session.get("active_model_profile") or config.default_model_profile)
    selected_profile = model_registry.profile(selected_profile_id)
    runtime_mode = session.get("runtime_mode", {}) if isinstance(session.get("runtime_mode", {}), dict) else {}
    active_tool_profile_name = "plan" if str(runtime_mode.get("mode", "default")) == "plan" else "default"
    execution_fingerprint = build_execution_fingerprint(
        selected_profile.snapshot(),
        [{"name": item.name, "description": item.description, "parameters": item.parameters} for item in registry.definitions(tool_profiles[active_tool_profile_name].allowed_tools)],
    )
    if config.resume:
        working_memory.resume_context = build_resume_context(
            session=session,
            session_store=session_store,
            run_store=run_store,
            workspace=workspace,
            resume_requested=config.resume,
            execution_fingerprint=execution_fingerprint,
        )
    static_tools = [
        {"type": "function", "name": item.name, "description": item.description, "parameters": item.parameters}
        for item in registry.definitions(tool_profiles[active_tool_profile_name].allowed_tools)
    ]
    static_minimum = compile_static_capacity_input(render_prefix(workspace, registry), static_tools, TokenizerAdapter())["total"]
    validate_static_model_capacity(selected_profile, config.max_new_tokens, static_minimum)
    session_events = SessionEventBus(state_dir / "sessions" / f"{session['id']}.events.jsonl")
    if config.resume:
        session_events.emit("session_resumed", **working_memory.resume_context)
        session_events.emit("resume_checkpoint_evaluated", **working_memory.resume_context)
    workers = WorkerManager(workspace, state_dir / "workers", executor, router, config, session_events=session_events)
    manager = ContextManager(
        workspace=workspace,
        durable_memory=memory_store,
        registry=registry,
        model_profile=selected_profile,
        actual_max_new_tokens=config.max_new_tokens,
        summary_router=router,
        summary_config=config,
    )
    agent = JCodeAgent(
        config=config, 
        workspace=workspace,
        session=session,
        session_store=session_store,
        run_store=run_store,
        memory_store=memory_store,
        session_events=session_events,
        working_memory=working_memory,
        context_manager=manager,
        model_router=router,
        tool_executor=executor,
        worker_manager=workers,
        final_gate=FinalGate(),
        redactor=redactor,
        tool_profiles=tool_profiles,
        active_tool_profile_name=active_tool_profile_name,
        write_scope=[],
    )
    if session.get("active_model_profile") != selected_profile_id:
        agent.switch_model_profile(selected_profile_id, source="cli" if config.explicit_model_profile else "bootstrap")
    if config.plan_topic or config.plan_path:
        agent.enter_plan_mode(config.plan_topic or "plan", config.plan_path)
    return agent
