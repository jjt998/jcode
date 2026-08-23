from __future__ import annotations

from jcode.evidence.session_log import SessionEventBus
from jcode.runtime.engine import Engine


class JCodeAgent:
    def __init__(
        self,
        *,
        config,
        workspace,
        session,
        session_store,
        run_store,
        memory_store,
        session_events,
        working_memory,
        prompt_builder,
        model_router,
        tool_executor,
        worker_manager,
        final_gate,
        redactor,
    ):
        self.config = config
        self.workspace = workspace
        self.session = session
        self.session_store = session_store
        self.run_store = run_store
        self.memory_store = memory_store
        self.session_events = session_events
        self.working_memory = working_memory
        self.prompt_builder = prompt_builder
        self.model_router = model_router
        self.tool_executor = tool_executor
        self.worker_manager = worker_manager
        self.final_gate = final_gate
        self.redactor = redactor
        self.abort_requested = False
        self.engine = Engine(self)

    def ask(self, user_message: str) -> str:
        return self.engine.ask(user_message)

    def resume(self, session_id: str) -> None:
        self.session = self.session_store.load_requested(session_id, None, self.workspace.root)
        self.working_memory = type(self.working_memory).from_dict(self.session.get("working_memory", {}), self.workspace.root)
        self.tool_executor.working_memory = self.working_memory
        self.tool_executor.tool_policy.working_memory = self.working_memory
        self.session_events = SessionEventBus(self.session_events.path.parent / f"{self.session['id']}.events.jsonl")

    def abort(self) -> None:
        self.abort_requested = True
