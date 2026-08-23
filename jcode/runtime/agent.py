from __future__ import annotations

from jcode.evidence.session_log import SessionEventBus
from jcode.runtime.actions import parse_model_action
from jcode.runtime.completion import finish_run
from jcode.runtime.transitions import ABORTED, MODEL_ERROR, STEP_LIMIT_REACHED, VALID_FINAL
from jcode.state.checkpoint import CheckpointManager
from jcode.state.history import append_history
from jcode.state.resume import build_resume_context
from jcode.state.task import TaskState


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

    def ask(self, user_message: str) -> str:
        self.abort_requested = False
        task_state, run_dir, checkpoint = self._begin_run(user_message)
        final_text = ""

        for step in range(self.config.max_steps):
            if self.abort_requested:
                return self._finish_run(task_state, run_dir, "Stopped after abort request.", ABORTED)
            task_state.step_index = step + 1
            task_state.attempts += 1

            prompt_result = self._build_context(user_message, task_state, run_dir)
            try:
                response = self._call_model(prompt_result, task_state, run_dir)
            except Exception as exc:
                self.run_store.append_trace(
                    run_dir,
                    "run_failed",
                    task_state.run_id,
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
        self.tool_executor.working_memory = self.working_memory
        self.tool_executor.tool_policy.working_memory = self.working_memory
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
        append_history(self.session, "user", user_message, run_id=task_state.run_id)
        self.session_events.emit(
            "run_started",
            run_id=task_state.run_id,
            task_id=task_state.task_id,
            user_request=user_message[:500],
        )
        self.run_store.append_trace(
            run_dir,
            "run_started",
            task_state.run_id,
            task_id=task_state.task_id,
            user_request=user_message[:500],
        )
        if self.working_memory.resume_context:
            self.run_store.append_trace(run_dir, "resume_evaluated", task_state.run_id, **self.working_memory.resume_context)
        return task_state, run_dir, checkpoint

    def _build_context(self, user_message: str, task_state, run_dir):
        prompt_result = self.prompt_builder.build(self.session, self.working_memory, user_message)
        self.run_store.append_trace(run_dir, "prompt_built", task_state.run_id, **prompt_result.metadata)
        return prompt_result

    def _call_model(self, prompt_result, task_state, run_dir):
        response = self.model_router.complete(
            prompt_result.prompt,
            max_tokens=self.config.max_new_tokens,
            temperature=self.config.temperature,
        )
        self.run_store.append_trace(
            run_dir,
            "model_requested",
            task_state.run_id,
            estimated_input_tokens=response.input_tokens,
            estimated_output_tokens=response.output_tokens,
        )
        return response

    def _parse_action(self, response, task_state, run_dir):
        action = parse_model_action(response.text)
        task_state.last_action = {"kind": action.kind, "tool_name": action.tool_name, "content": action.content[:200]}
        self.run_store.append_trace(run_dir, "model_parsed", task_state.run_id, action=task_state.last_action)
        return action

    def _handle_final_action(self, action, task_state, run_dir, checkpoint) -> tuple[bool, str]:
        decision = self.final_gate.check(action.content, task_state, self.working_memory)
        self.run_store.append_trace(run_dir, "final_readiness_decision", task_state.run_id, **decision)
        if decision["allowed"]:
            append_history(self.session, "assistant", action.content, run_id=task_state.run_id)
            checkpoint.create(self.session, task_state, self.working_memory, self.worker_manager.worker_refs())
            self.run_store.append_trace(run_dir, "checkpoint_created", task_state.run_id, trigger="final")
            return True, action.content
        append_history(
            self.session,
            "tool",
            decision["message"],
            name="final_gate",
            run_id=task_state.run_id,
            tool_status="denied",
        )
        self.working_memory.observe_tool(decision["message"])
        return False, ""

    def _handle_invalid_action(self, action, task_state) -> None:
        append_history(self.session, "tool", action.content, name="parser", run_id=task_state.run_id, tool_status="error")
        self.working_memory.observe_tool(action.content)

    def _handle_tool_action(self, action, task_state, run_dir, checkpoint) -> None:
        self.run_store.append_trace(run_dir, "tool_requested", task_state.run_id, name=action.tool_name, args=action.tool_args or {})
        if action.tool_name in {"spawn_subagent", "send_subagent_message", "wait_subagent"}:
            result = self.worker_manager.run_tool(action.tool_name, action.tool_args or {})
        else:
            result = self.tool_executor.execute(action.tool_name, action.tool_args or {})

        task_state.record_tool(action.tool_name, result)
        append_history(
            self.session,
            "tool",
            result.text,
            name=action.tool_name,
            run_id=task_state.run_id,
            tool_status=result.status,
            error_type=result.error_type,
            changed_files=result.changed_files,
            metadata=result.metadata,
        )
        self.working_memory.observe_tool(f"{action.tool_name}: {result.status}: {result.text[:500]}")
        if action.tool_name == "wait_subagent" and result.status == "success":
            self.working_memory.subagent_results.append(result.text[:1000])

        event_name = "subagent_completed" if action.tool_name == "wait_subagent" and result.status == "success" else "tool_executed"
        self.run_store.append_trace(
            run_dir,
            event_name,
            task_state.run_id,
            name=action.tool_name,
            status=result.status,
            error_type=result.error_type,
            changed_files=result.changed_files,
            metadata=result.metadata,
            result=self.redactor.redact(result.text[:1000]),
        )
        checkpoint.create(self.session, task_state, self.working_memory, self.worker_manager.worker_refs())
        self.run_store.append_trace(run_dir, "checkpoint_created", task_state.run_id, trigger="tool_executed")
        self.run_store.write_task_state(run_dir, task_state)

    def _finish_run(self, task_state, run_dir, final_text: str, stop_reason: str = VALID_FINAL) -> str:
        return finish_run(self, task_state, run_dir, final_text, stop_reason)
