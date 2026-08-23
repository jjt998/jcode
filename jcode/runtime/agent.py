from __future__ import annotations

from typing import TYPE_CHECKING

from jcode.evidence.summaries import build_report
from jcode.evidence.session_log import SessionEventBus
from jcode.memory.consolidation import maintain_after_turn
from jcode.runtime.actions import parse_model_action
from jcode.runtime.transitions import ABORTED, MODEL_ERROR, STEP_LIMIT_REACHED, VALID_FINAL
from jcode.state.checkpoint import CheckpointManager
from jcode.state.history import append_history
from jcode.state.resume import build_resume_context
from jcode.state.task import TaskState

if TYPE_CHECKING:
    from jcode.app.config import AppConfig
    from jcode.context.builder import ContextBuilder
    from jcode.evidence.store import RunStore
    from jcode.memory.durable import DurableMemoryStore
    from jcode.memory.working import WorkingMemory
    from jcode.policy.final_gate import FinalGate
    from jcode.policy.secrets import SecretRedactor
    from jcode.providers.router import ModelRouter
    from jcode.state.session import SessionStore
    from jcode.state.workspace import Workspace
    from jcode.tools.executor import ToolExecutor
    from jcode.workers.manager import WorkerManager


class JCodeAgent:
    config: AppConfig
    workspace: Workspace
    session: dict
    session_store: SessionStore
    run_store: RunStore
    memory_store: DurableMemoryStore
    session_events: SessionEventBus
    working_memory: WorkingMemory
    context_builder: ContextBuilder
    model_router: ModelRouter
    tool_executor: ToolExecutor
    worker_manager: WorkerManager
    final_gate: FinalGate
    redactor: SecretRedactor
    abort_requested: bool

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
        context_builder,
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
        self.context_builder = context_builder
        self.model_router = model_router
        self.tool_executor = tool_executor
        self.worker_manager = worker_manager
        self.final_gate = final_gate
        self.redactor = redactor
        self.abort_requested = False

    def ask(self, user_message: str) -> str:
        self.abort_requested = False
        task_state, run_dir, checkpoint = self._begin_run(user_message)
        final_text = ""

        for step in range(self.config.max_steps):
            if self.abort_requested:
                return self._finish_run(task_state, run_dir, "Stopped after abort request.", ABORTED)
            task_state.step_index = step + 1
            task_state.attempts += 1

            context_result = self._build_context(user_message, task_state, run_dir)
            try:
                response = self._call_model(context_result, task_state, run_dir)
            except Exception as exc:
                self._record_trace(
                    run_dir,
                    "run_failed",
                    task_state,
                    error_type=type(exc).__name__,
                    message=str(exc)[:500],
                )
                return self._finish_run(task_state, run_dir, f"Model error: {exc}", MODEL_ERROR)

            action = self._parse_action(response, task_state, run_dir)
            if action.kind == "final":
                handled, final_text = self._handle_final_action(action, task_state, run_dir, checkpoint)
                if handled:
                    return self._finish_run(task_state, run_dir, final_text, VALID_FINAL)
                continue
            if action.kind != "tool":
                self._handle_invalid_action(action, task_state)
                continue
            self._handle_tool_action(action, task_state, run_dir, checkpoint)

        return self._finish_run(task_state, run_dir, final_text or "Stopped after reaching max steps.", STEP_LIMIT_REACHED)

    def resume(self, session_id: str) -> None:
        self.session = self.session_store.load_requested(session_id, None, self.workspace.root)
        self.working_memory = type(self.working_memory).from_dict(self.session.get("working_memory", {}), self.workspace.root)
        self.working_memory.resume_context = build_resume_context(
            session=self.session,
            session_store=self.session_store,
            run_store=self.run_store,
            workspace=self.workspace,
            resume_requested=session_id,
        )
        self.session_events = SessionEventBus(self.session_events.path.parent / f"{self.session['id']}.events.jsonl")
        self.worker_manager.session_events = self.session_events
        self.session_events.emit("session_resumed", **self.working_memory.resume_context)
        self.session_events.emit("resume_checkpoint_evaluated", **self.working_memory.resume_context)

    def abort(self) -> None:
        self.abort_requested = True

    def _begin_run(self, user_message: str):
        task_state = TaskState.create(user_message)
        run_dir = self.run_store.start_run(task_state)
        checkpoint = CheckpointManager(run_dir, self.workspace)
        self.working_memory.task_goal = user_message
        self._append_history("user", user_message, task_state)
        self.session_events.emit(
            "run_started",
            run_id=task_state.run_id,
            task_id=task_state.task_id,
            user_request=user_message[:500],
        )
        self._record_trace(run_dir, "run_started", task_state, task_id=task_state.task_id, user_request=user_message[:500])
        if self.working_memory.resume_context:
            self._record_trace(run_dir, "resume_evaluated", task_state, **self.working_memory.resume_context)
        return task_state, run_dir, checkpoint

    def _build_context(self, user_message: str, task_state, run_dir):
        context_result = self.context_builder.build(self.session, self.working_memory, user_message)
        self._record_trace(run_dir, "context_built", task_state, **context_result.metadata)
        return context_result

    def _call_model(self, context_result, task_state, run_dir):
        response = self.model_router.complete(
            context_result.context,
            max_tokens=self.config.max_new_tokens,
            temperature=self.config.temperature,
        )
        self._record_trace(
            run_dir,
            "model_requested",
            task_state,
            estimated_input_tokens=response.input_tokens,
            estimated_output_tokens=response.output_tokens,
        )
        return response

    def _parse_action(self, response, task_state, run_dir):
        action = parse_model_action(response.text)
        task_state.last_action = {"kind": action.kind, "tool_name": action.tool_name, "content": action.content[:200]}
        self._record_trace(run_dir, "model_parsed", task_state, action=task_state.last_action)
        return action

    def _handle_final_action(self, action, task_state, run_dir, checkpoint) -> tuple[bool, str]:
        decision = self.final_gate.check(action.content, task_state, self.working_memory)
        self._record_trace(run_dir, "final_readiness_decision", task_state, **decision)
        if decision["allowed"]:
            self._append_history("assistant", action.content, task_state)
            self._create_checkpoint(checkpoint, task_state, run_dir, "final")
            return True, action.content
        self._append_history(
            "tool",
            decision["message"],
            task_state,
            name="final_gate",
            tool_status="denied",
        )
        self.working_memory.observe_tool(decision["message"])
        return False, ""

    def _handle_invalid_action(self, action, task_state) -> None:
        self._append_history("tool", action.content, task_state, name="parser", tool_status="error")
        self.working_memory.observe_tool(action.content)

    def _handle_tool_action(self, action, task_state, run_dir, checkpoint) -> None:
        self._record_trace(run_dir, "tool_requested", task_state, name=action.tool_name, args=action.tool_args or {})
        if action.tool_name in {"spawn_subagent", "send_subagent_message", "wait_subagent"}:
            result = self.worker_manager.run_tool(action.tool_name, action.tool_args or {})
        else:
            result = self.tool_executor.execute(action.tool_name, action.tool_args or {}, working_memory=self.working_memory)

        task_state.record_tool(action.tool_name, result)
        self._append_history(
            "tool",
            result.text,
            task_state,
            name=action.tool_name,
            tool_status=result.status,
            error_type=result.error_type,
            changed_files=result.changed_files,
            metadata=result.metadata,
        )
        self.working_memory.observe_tool(f"{action.tool_name}: {result.status}: {result.text[:500]}")
        if action.tool_name == "wait_subagent" and result.status == "success":
            self.working_memory.subagent_results.append(result.text[:1000])

        event_name = "subagent_completed" if action.tool_name == "wait_subagent" and result.status == "success" else "tool_executed"
        self._record_trace(
            run_dir,
            event_name,
            task_state,
            name=action.tool_name,
            status=result.status,
            error_type=result.error_type,
            changed_files=result.changed_files,
            metadata=result.metadata,
            result=self.redactor.redact(result.text[:1000]),
        )
        self._create_checkpoint(checkpoint, task_state, run_dir, "tool_executed")
        self.run_store.write_task_state(run_dir, task_state)

    def _record_trace(self, run_dir, event: str, task_state, **payload) -> None:
        self.run_store.append_trace(run_dir, event, task_state.run_id, **payload)

    def _append_history(self, role: str, content: str, task_state, **extra) -> None:
        append_history(self.session, role, content, run_id=task_state.run_id, **extra)

    def _create_checkpoint(self, checkpoint, task_state, run_dir, trigger: str) -> None:
        checkpoint.create(self.session, task_state, self.working_memory, self.worker_manager.worker_refs())
        self._record_trace(run_dir, "checkpoint_created", task_state, trigger=trigger)

    def _finish_run(self, task_state, run_dir, final_text: str, stop_reason: str = VALID_FINAL) -> str:
        task_state.finish("completed" if stop_reason == VALID_FINAL else "stopped", stop_reason, final_text)
        memory_audit = maintain_after_turn(self.memory_store, self.working_memory, task_state.user_request, final_text)
        self._record_trace(run_dir, "memory_maintained", task_state, **memory_audit)
        self._record_trace(run_dir, "run_finished", task_state, status=task_state.status, stop_reason=stop_reason)
        self.session_events.emit("turn_finished", run_id=task_state.run_id, status=task_state.status, stop_reason=stop_reason)
        trace = self.run_store.read_trace(run_dir)
        self.run_store.write_report(
            run_dir,
            build_report(
                task_state,
                stop_reason,
                final_text,
                trace=trace,
                session_id=self.session.get("id", ""),
                workers=self.worker_manager.worker_refs(),
                memory=memory_audit,
                resume=self.working_memory.resume_context,
            ),
        )
        self.session["working_memory"] = self.working_memory.to_dict()
        self.session.setdefault("run_ids", []).append(task_state.run_id)
        self.session_store.save(self.session)
        self.run_store.write_task_state(run_dir, task_state)
        return final_text
