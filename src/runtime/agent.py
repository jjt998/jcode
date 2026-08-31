from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from src.evidence.summaries import build_report
from src.evidence.tool_artifacts import prepare_tool_result_observation
from src.evidence.session_log import SessionEventBus
from src.memory.consolidation import maintain_after_turn
from src.policy.decisions import PolicyDecision
from src.runtime.plan import PlanModeController, runtime_mode_name, runtime_mode_plan_path
from src.runtime.actions import parse_model_action
from src.runtime.transitions import ABORTED, MODEL_ERROR, STEP_LIMIT_REACHED, VALID_FINAL
from src.state.checkpoint import CheckpointManager
from src.state.history import append_history
from src.state.resume import build_resume_context
from src.state.task import TaskState
from src.state.todo import TodoLedger
from src.tools.base import ToolResult

if TYPE_CHECKING:
    from src.app.config import AppConfig
    from src.context.manager import ContextManager
    from src.evidence.store import RunStore
    from src.memory.durable import DurableMemoryStore
    from src.memory.working import WorkingMemory
    from src.policy.final_gate import FinalGate
    from src.policy.secrets import SecretRedactor
    from src.policy.tool_profiles import ToolSetProfile
    from src.providers.router import ModelRouter
    from src.state.session import SessionStore
    from src.state.workspace import Workspace
    from src.tools.executor import ToolExecutor
    from src.workers.manager import WorkerManager


class JCodeAgent:
    config: AppConfig
    workspace: Workspace
    session: dict
    session_path: Path | None
    session_store: SessionStore
    run_store: RunStore
    memory_store: DurableMemoryStore
    session_events: SessionEventBus
    working_memory: WorkingMemory
    context_manager: ContextManager
    model_router: ModelRouter
    tool_executor: ToolExecutor
    worker_manager: WorkerManager
    final_gate: FinalGate
    redactor: SecretRedactor
    plan_mode: PlanModeController
    tool_profiles: dict[str, ToolSetProfile]
    active_tool_profile_name: str
    write_scope: list[str]
    todo_ledger: TodoLedger
    ask_user_callback: Callable[[str, list[str]], str] | None
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
        context_manager,
        model_router,
        tool_executor,
        worker_manager,
        final_gate,
        redactor,
        tool_profiles,
        active_tool_profile_name: str = "default",
        write_scope: list[str] | None = None,
        ask_user_callback=None,
    ):
        self.config = config
        self.workspace = workspace
        self.session = session
        self.session_store = session_store
        self.session_path = self.session_store.root / f"{self.session.get('id', '')}.json"
        self.run_store = run_store
        self.memory_store = memory_store
        self.session_events = session_events
        self.working_memory = working_memory
        self.context_manager = context_manager
        self.model_router = model_router
        self.tool_executor = tool_executor
        self.worker_manager = worker_manager
        self.final_gate = final_gate
        self.redactor = redactor
        self.plan_mode = PlanModeController(self)
        self.tool_profiles = tool_profiles
        self.active_tool_profile_name = active_tool_profile_name
        self.write_scope = list(write_scope or [])
        self.todo_ledger = TodoLedger.from_dict(self.session.get("todo_ledger", {}))
        self.ask_user_callback = ask_user_callback
        self.abort_requested = False
        self._sync_runtime_mode_from_session()

    @property
    def active_tool_profile(self) -> ToolSetProfile:
        return self.tool_profiles[self.active_tool_profile_name]

    def set_tool_profile(self, name: str) -> None:
        if name not in self.tool_profiles:
            raise ValueError(f"unknown tool profile: {name}")
        self.active_tool_profile_name = name

    def run_dream(self, quiet: bool = False, session_ids: list[str] | None = None) -> str:
        from src.memory.consolidation import run_dream

        return run_dream(self, quiet=quiet, session_ids=session_ids)

    def enter_plan_mode(self, topic: str, path: str | None = None) -> str:
        plan_path = self.plan_mode.enter(topic, path=path)
        return f"mode: plan\nplan path: {plan_path}"

    def exit_plan_mode(self) -> str:
        self.plan_mode.exit()
        return "mode: default"

    def todo_add(self, args: dict) -> str:
        item = self.todo_ledger.add(
            args["content"],
            status=args.get("status", "pending"),
            priority=args.get("priority", "normal"),
            note=args.get("note", ""),
        )
        self.session["todo_ledger"] = self.todo_ledger.to_dict()
        self.session_store.save(self.session)
        self.session_events.emit("todo_added", todo_id=item.todo_id, status=item.status, priority=item.priority)
        return f"added {item.todo_id} [{item.status}] {item.priority} - {item.content}"

    def todo_update(self, args: dict) -> str:
        item = self.todo_ledger.update(
            args["todo_id"],
            status=args.get("status"),
            content=args.get("content"),
            priority=args.get("priority"),
            note=args.get("note"),
        )
        self.session["todo_ledger"] = self.todo_ledger.to_dict()
        self.session_store.save(self.session)
        self.session_events.emit("todo_updated", todo_id=item.todo_id, status=item.status, priority=item.priority)
        return f"updated {item.todo_id} [{item.status}] {item.priority} - {item.content}"

    def todo_list(self, args: dict | None = None) -> str:
        return self.todo_ledger.render_list()

    def ask_user(self, question: str, choices: list[str] | None = None) -> str:
        choices = list(choices or [])
        self.session_events.emit("ask_user_requested", question=str(question)[:500], choices=choices)
        if self.ask_user_callback is None:
            return "error: ask_user requires interactive mode"
        answer = str(self.ask_user_callback(str(question), choices))
        self.session_events.emit("ask_user_answered", question=str(question)[:500], answer=answer[:500])
        return answer

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
            if action.kind == "tools":
                self._handle_tool_sequence_action(action, task_state, run_dir, checkpoint)
                continue
            if action.kind != "tool":
                self._handle_invalid_action(action, task_state)
                continue
            self._handle_tool_action(action, task_state, run_dir, checkpoint)

        return self._finish_run(task_state, run_dir, final_text or "Stopped after reaching max steps.", STEP_LIMIT_REACHED)

    def resume(self, session_id: str) -> None:
        self.session = self.session_store.load_requested(session_id, None, self.workspace.root)
        self.working_memory = type(self.working_memory).from_dict(self.session.get("working_memory", {}), self.workspace.root)
        self.todo_ledger = TodoLedger.from_dict(self.session.get("todo_ledger", {}))
        self.working_memory.resume_context = build_resume_context(
            session=self.session,
            session_store=self.session_store,
            run_store=self.run_store,
            workspace=self.workspace,
            resume_requested=session_id,
        )
        self.session_events = SessionEventBus(self.session_events.path.parent / f"{self.session['id']}.events.jsonl")
        self.worker_manager.session_events = self.session_events
        self.plan_mode = PlanModeController(self)
        self._sync_runtime_mode_from_session()
        self.session_events.emit("session_resumed", **self.working_memory.resume_context)
        self.session_events.emit("resume_checkpoint_evaluated", **self.working_memory.resume_context)

    def abort(self) -> None:
        self.abort_requested = True

    def refresh_prefix(self, force: bool = False) -> None:
        return None

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
        context_result = self.context_manager.build(self.session, self.working_memory, user_message)
        self.session["ctx_info"] = context_result.ctx_info
        self.session_store.save(self.session)
        self.working_memory.set_compact_summary(str(context_result.ctx_info.get("history", {}).get("compact_summary", "")).strip())
        compact_info = dict(context_result.ctx_info.get("compact", {}) or {})
        self._emit_compact_context_events(run_dir, task_state, context_result, compact_info)
        if context_result.compact_audit:
            self._record_trace(
                run_dir,
                "compact_history_audit",
                task_state,
                compact=compact_info,
                summary_mode=context_result.compact_audit.get("mode", ""),
                summary_source=context_result.compact_audit.get("source", ""),
                status=context_result.compact_audit.get("status", ""),
                fallback_reason=context_result.compact_audit.get("fallback_reason", ""),
                summary_prompt=context_result.compact_audit.get("prompt", ""),
                summary_response=context_result.compact_audit.get("response", ""),
                summary_text=context_result.compact_audit.get("summary_text", ""),
            )
        self.session_events.emit("context_built", run_id=task_state.run_id, ctx_info=context_result.ctx_info, context=context_result.context)
        self._record_trace(run_dir, "context_built", task_state, ctx_info=context_result.ctx_info, context=context_result.context)
        return context_result

    def _emit_compact_context_events(self, run_dir, task_state, context_result, compact_info: dict) -> None:
        event_payload = {
            "ctx_info": context_result.ctx_info,
            "pressure_level": context_result.ctx_info.get("pressure", {}).get("level", 0),
            "pressure_range": context_result.ctx_info.get("pressure", {}).get("range", ""),
            "should_compact": bool(compact_info.get("should_compact", False)),
            "compact_trigger": compact_info.get("trigger", ""),
            "compact_status": compact_info.get("status", "idle"),
            "summary_source": compact_info.get("summary_source", ""),
            "fallback_reason": compact_info.get("fallback_reason", ""),
            "retain_turns": compact_info.get("retain_turns", 0),
        }
        self.session_events.emit("compact_evaluated", run_id=task_state.run_id, **event_payload)
        self._record_trace(run_dir, "compact_evaluated", task_state, **event_payload)
        if not event_payload["should_compact"] and compact_info.get("status") not in {"applied"}:
            return

        triggered_payload = {
            **event_payload,
            "compact_trigger": compact_info.get("trigger", "") or "pressure_threshold",
        }
        self.session_events.emit("compact_triggered", run_id=task_state.run_id, **triggered_payload)
        self._record_trace(run_dir, "compact_triggered", task_state, **triggered_payload)

        if context_result.compact_audit:
            summary_payload = {
                **triggered_payload,
                "summary_mode": context_result.compact_audit.get("mode", ""),
                "summary_source": context_result.compact_audit.get("source", ""),
                "status": context_result.compact_audit.get("status", ""),
                "fallback_reason": context_result.compact_audit.get("fallback_reason", ""),
                "summary_text": context_result.compact_audit.get("summary_text", ""),
            }
            event_name = "compact_fallback" if context_result.compact_audit.get("status") == "fallback" else "compact_completed"
            self.session_events.emit(event_name, run_id=task_state.run_id, **summary_payload)
            self._record_trace(run_dir, event_name, task_state, **summary_payload)

    def _call_model(self, context_result, task_state, run_dir):
        response = self.model_router.complete(
            context_result.context,
            max_tokens=self.config.max_new_tokens,
            temperature=self.config.temperature,
        )
        self._record_trace(
            run_dir,
            "model_responded",
            task_state,
            estimated_input_tokens=response.input_tokens,
            estimated_output_tokens=response.output_tokens,
            response_text=self.redactor.redact(response.text),
        )
        return response

    def _parse_action(self, response, task_state, run_dir):
        action = parse_model_action(response.text)
        if action.kind == "tools":
            task_state.last_action = {
                "kind": action.kind,
                "tool_name": "",
                "content": f"{len(action.tool_calls)} tools",
                "tool_names": [call.name for call in action.tool_calls],
                "reasoning": action.reasoning,
            }
        else:
            task_state.last_action = {
                "kind": action.kind,
                "tool_name": action.tool_name,
                "content": action.content[:200],
                "reasoning": action.reasoning,
            }
        assistant_history_extra = {
            "action_kind": action.kind,
            "reasoning": action.reasoning,
            "raw_content": action.raw_content or response.text,
        }
        if action.kind == "tool":
            assistant_history_extra.update(
                {
                    "tool_name": action.tool_name,
                    "tool_args": action.tool_args or {},
                }
            )
        elif action.kind == "tools":
            assistant_history_extra.update(
                {
                    "tool_calls": [{"name": call.name, "args": call.args} for call in action.tool_calls],
                }
            )
        elif action.kind == "final":
            assistant_history_extra.update({"final_text": action.content})
        assistant_content = (action.raw_content or response.text) if action.kind == "invalid" else action.content
        self._append_history("assistant", assistant_content, task_state, **assistant_history_extra)
        self._record_trace(run_dir, "model_parsed", task_state, action=task_state.last_action)
        if action.kind == "invalid":
            # 单独记录解析失败原文，便于排查模型到底输出了什么。
            self._record_trace(
                run_dir,
                "model_parse_failed",
                task_state,
                raw_content=action.raw_content or response.text,
                error=action.content,
                reasoning=action.reasoning,
                response_text=self.redactor.redact(action.raw_content or response.text),
            )
        return action

    def _handle_final_action(self, action, task_state, run_dir, checkpoint) -> tuple[bool, str]:
        decision = self.final_gate.check(action.content, task_state, self.working_memory, session=self.session, workspace=self.workspace)
        self._record_trace(run_dir, "final_readiness_decision", task_state, **decision)
        if decision["allowed"]:
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
        self._execute_tool_call(
            action.tool_name,
            action.tool_args or {},
            task_state,
            run_dir,
            checkpoint,
        )

    def _handle_tool_sequence_action(self, action, task_state, run_dir, checkpoint) -> None:
        sequence_id = f"{task_state.run_id}-seq-{task_state.step_index:03d}-{uuid.uuid4().hex[:6]}"
        calls = list(action.tool_calls or [])
        total_steps = len(calls)
        started_at = time.monotonic()
        results: list[dict] = []
        self._record_trace(
            run_dir,
            "tool_sequence_requested",
            task_state,
            sequence_id=sequence_id,
            step_count=total_steps,
            tool_names=[call.name for call in calls],
        )
        for index, call in enumerate(calls, start=1):
            if self.abort_requested:
                self._record_trace(
                    run_dir,
                    "tool_sequence_aborted",
                    task_state,
                    sequence_id=sequence_id,
                    step_index=index,
                    step_count=total_steps,
                    completed_steps=len(results),
                    results=list(results),
                )
                return
            step_meta = {
                "sequence_id": sequence_id,
                "sequence_index": index,
                "sequence_length": total_steps,
            }
            step_started_at = time.monotonic()
            self._record_trace(
                run_dir,
                "tool_sequence_step_requested",
                task_state,
                name=call.name,
                args=call.args,
                **step_meta,
            )
            result = self._execute_tool_call(
                call.name,
                call.args,
                task_state,
                run_dir,
                checkpoint,
                trace_meta=step_meta,
                history_meta=step_meta,
            )
            results.append(
                {
                    "name": call.name,
                    "status": result.status,
                    "error_type": result.error_type,
                    "changed_files": list(result.changed_files),
                    "artifacts": list(result.artifacts),
                    "duration_ms": int((time.monotonic() - step_started_at) * 1000),
                }
            )
        self._record_trace(
            run_dir,
            "tool_sequence_completed",
            task_state,
            sequence_id=sequence_id,
            step_count=total_steps,
            completed_steps=len(results),
            duration_ms=int((time.monotonic() - started_at) * 1000),
            results=results,
        )

    def _execute_tool_call(
        self,
        tool_name: str,
        tool_args: dict,
        task_state,
        run_dir,
        checkpoint,
        *,
        trace_meta: dict | None = None,
        history_meta: dict | None = None,
    ) -> ToolResult:
        """执行工具结果 + 记录工具执行前后工作区状态 + 记录工具执行结果到历史 + 记录工具执行结果到 trace"""
        trace_meta = dict(trace_meta or {})
        history_meta = dict(history_meta or {})
        self._record_trace(run_dir, "tool_requested", task_state, name=tool_name, args=tool_args, **trace_meta)
        if tool_name in {"spawn_subagent", "send_subagent_message", "wait_subagent"}:
            result = self._handle_subagent_tool(tool_name, tool_args, task_state)
        else:
            result = self.tool_executor.execute(
                tool_name,
                tool_args,
                working_memory=self.working_memory,
                tool_profile=self.active_tool_profile,
                write_scope=self.write_scope,
                runtime_mode=runtime_mode_name(self.session),
                plan_path=runtime_mode_plan_path(self.session),
                run_id=task_state.run_id,
                runtime=self,
            )

        result_text, artifact_metadata, result_artifacts = prepare_tool_result_observation(
            self.run_store,
            run_dir,
            tool_name,
            result.text,
            result.artifacts,
        )
        result.text = result_text
        result.artifacts = result_artifacts
        result.metadata.update(artifact_metadata)

        if result.ok and result.changed_files:
            self._mark_stale_file_evidence(result.changed_files)

        task_state.record_tool(tool_name, result)
        self._append_history(
            "tool",
            result.text,
            task_state,
            name=tool_name,
            tool_status=result.status,
            error_type=result.error_type,
            changed_files=result.changed_files,
            artifacts=result.artifacts,
            metadata=result.metadata,
            **history_meta,
        )
        self.working_memory.observe_tool(f"{tool_name}: {result.status}: {result.text}")
        if tool_name == "wait_subagent" and result.status == "success":
            self.working_memory.subagent_results.append(result.text[:1000])

        event_name = "subagent_completed" if tool_name == "wait_subagent" and result.status == "success" else "tool_executed"
        self._record_trace(
            run_dir,
            event_name,
            task_state,
            name=tool_name,
            status=result.status,
            error_type=result.error_type,
            changed_files=result.changed_files,
            metadata=result.metadata,
            result=self.redactor.redact(result.text),
            **trace_meta,
        )
        self._create_checkpoint(checkpoint, task_state, run_dir, "tool_executed")
        self.run_store.write_task_state(run_dir, task_state)
        return result

    def _record_trace(self, run_dir, event: str, task_state, **payload) -> None:
        self.run_store.append_trace(run_dir, event, task_state.run_id, **payload)

    def _append_history(self, role: str, content: str, task_state, **extra) -> None:
        append_history(self.session, role, content, run_id=task_state.run_id, **extra)

    def _mark_stale_file_evidence(self, changed_files: list[str]) -> None:
        """文件成功变更后，标记历史中依赖旧文件内容的 read_file 结果。"""
        changed = {str(path).replace("\\", "/") for path in changed_files if str(path).strip()}
        if not changed:
            return
        for item in self.session.get("history", []):
            if item.get("role") != "tool" or item.get("name") != "read_file":
                continue
            metadata = item.get("metadata")
            if not isinstance(metadata, dict):
                continue
            source_files = metadata.get("source_files", [])
            if not isinstance(source_files, list):
                continue
            stale_paths = {
                str(source.get("path", "")).replace("\\", "/")
                for source in source_files
                if isinstance(source, dict) and str(source.get("path", "")).strip()
            } & changed
            if stale_paths:
                metadata["stale"] = True
                metadata["stale_reason"] = "source_file_changed"
                metadata["stale_paths"] = sorted(stale_paths)

    def _create_checkpoint(self, checkpoint, task_state, run_dir, trigger: str) -> None:
        checkpoint.create(self.session, task_state, self.working_memory, self.worker_manager.worker_refs())
        self._record_trace(run_dir, "checkpoint_created", task_state, trigger=trigger)

    def _finish_run(self, task_state, run_dir, final_text: str, stop_reason: str = VALID_FINAL) -> str:
        task_state.finish("completed" if stop_reason == VALID_FINAL else "stopped", stop_reason, final_text)
        memory_audit = maintain_after_turn(self.memory_store, self.working_memory, task_state.user_request, final_text, agent=self)
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
                ctx_info=self.session.get("ctx_info", {}),
            ),
        )
        self.session["working_memory"] = self.working_memory.to_dict()
        self.session["todo_ledger"] = self.todo_ledger.to_dict()
        self.session.setdefault("runtime_mode", {"mode": "default"})
        self.session.setdefault("run_ids", []).append(task_state.run_id)
        self.session_store.save(self.session)
        self.run_store.write_task_state(run_dir, task_state)
        return final_text

    def _handle_subagent_tool(self, tool_name: str, args: dict, task_state) -> ToolResult:
        subagent_type = str(args.get("subagent_type", "worker") or "worker").strip() or "worker"
        write_scope = list(args.get("write_scope", []) or [])
        prompt = str(args.get("prompt", "") or "")
        if subagent_type not in {"worker", "Explore"}:
            decision = PolicyDecision.deny(
                "tool_profile_denied",
                f"error: tool {tool_name} requested invalid subagent type {subagent_type}",
                layer="tool_profile",
                metadata={"tool_profile": self.active_tool_profile_name},
            )
            return ToolResult(
                "denied",
                decision.message,
                error_type=decision.reason,
                metadata={"policy": [decision.to_dict()], "decision": decision.layer, "tool_name": tool_name, "source": "model", "run_id": task_state.run_id},
                decision=decision.decision,
            )
        if self.plan_mode.mode == "plan" and subagent_type != "Explore":
            decision = PolicyDecision.deny(
                "tool_profile_denied",
                f"error: plan mode only allows Explore subagents, not {subagent_type}",
                layer="tool_profile",
                metadata={"tool_profile": self.active_tool_profile_name},
            )
            return ToolResult(
                "denied",
                decision.message,
                error_type=decision.reason,
                metadata={"policy": [decision.to_dict()], "decision": decision.layer, "tool_name": tool_name, "source": "model", "run_id": task_state.run_id},
                decision=decision.decision,
            )
        if tool_name == "spawn_subagent":
            return self.worker_manager.spawn(prompt, subagent_type=subagent_type, write_scope=write_scope)
        if tool_name == "send_subagent_message":
            worker_id = str(args.get("worker_id", ""))
            if self.plan_mode.mode == "plan":
                worker = self.worker_manager.workers.get(worker_id)
                if worker is not None and worker.subagent_type != "Explore":
                    decision = PolicyDecision.deny(
                        "tool_profile_denied",
                        f"error: plan mode only allows Explore subagents, not {worker.subagent_type}",
                        layer="tool_profile",
                        metadata={"tool_profile": self.active_tool_profile_name},
                    )
                    return ToolResult(
                        "denied",
                        decision.message,
                        error_type=decision.reason,
                        metadata={"policy": [decision.to_dict()], "decision": decision.layer, "tool_name": tool_name, "source": "model", "run_id": task_state.run_id},
                        decision=decision.decision,
                    )
            return self.worker_manager.send(worker_id, str(args.get("message", "")))
        if tool_name == "wait_subagent":
            worker_id = str(args.get("worker_id", ""))
            if self.plan_mode.mode == "plan":
                worker = self.worker_manager.workers.get(worker_id)
                if worker is not None and worker.subagent_type != "Explore":
                    decision = PolicyDecision.deny(
                        "tool_profile_denied",
                        f"error: plan mode only allows Explore subagents, not {worker.subagent_type}",
                        layer="tool_profile",
                        metadata={"tool_profile": self.active_tool_profile_name},
                    )
                    return ToolResult(
                        "denied",
                        decision.message,
                        error_type=decision.reason,
                        metadata={"policy": [decision.to_dict()], "decision": decision.layer, "tool_name": tool_name, "source": "model", "run_id": task_state.run_id},
                        decision=decision.decision,
                    )
            return self.worker_manager.wait(worker_id)
        return ToolResult("denied", f"unknown subagent tool {tool_name}", error_type="unknown_tool")

    def _sync_runtime_mode_from_session(self) -> None:
        mode = runtime_mode_name(self.session)
        if mode == "plan":
            self.active_tool_profile_name = "plan"
            plan_path = runtime_mode_plan_path(self.session)
            self.write_scope = [plan_path] if plan_path else []
        else:
            self.active_tool_profile_name = "default"
            self.write_scope = []
