from __future__ import annotations

import hashlib
import json
import sys
import time
import traceback
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from src.evidence.summaries import build_report
from src.evidence.tool_artifacts import prepare_tool_result_observation
from src.evidence.session_log import SessionEventBus
from src.memory.consolidation import maintain_after_turn
from src.policy.decisions import PolicyDecision
from src.providers.base import ModelResponse, ProviderRequestError
from src.providers.continuation import ProviderContinuation
from src.context.budget import validate_static_model_capacity
from src.runtime.plan import PlanModeController, runtime_mode_name, runtime_mode_plan_path
from src.runtime.transitions import ABORTED, MODEL_ERROR, MODEL_OUTPUT_INCOMPLETE, STEP_LIMIT_REACHED, VALID_FINAL, UNEXPECTED_RUNTIME_ERROR
from src.runtime.errors import JCodeRuntimeStopError
from src.state.checkpoint import CheckpointManager
from src.state.history import append_history
from src.state.resume import build_execution_fingerprint, build_resume_context
from src.state.task import TaskState
from src.state.model_selection import resolve_model_snapshot
from src.state.todo import TodoLedger
from src.tools.base import ToolResult


# 临时服务故障可在不改变上下文的前提下重试；参数和鉴权错误必须直接暴露。
RETRYABLE_HTTP_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})
RETRYABLE_PROVIDER_ERROR_CODES = frozenset({"internal_error", "rate_limit_exceeded", "server_error", "service_unavailable", "temporarily_unavailable", "timeout"})
MAX_TRANSPORT_RETRIES = 3
MAX_OUTPUT_CONTINUATIONS = 2
CONTINUATION_REQUEST = """[Continuation Required]
Your previous response was truncated before completion. Continue directly from the unfinished point.
Do not repeat prior content, do not restate the task, and complete the remaining work."""
CANCELLED_TOOL_TEXT = "[JCode tool execution cancelled]\nstatus: cancelled\nreason: user_abort\nmessage: 该工具调用因用户中止当前运行而未实际执行；不得将其视为已完成。"
INTERRUPTED_TOOL_TEXT = "[JCode tool execution interrupted]\nstatus: interrupted\nreason: user_abort\nmessage: 该工具执行期间被用户终止，可能已经产生文件、命令、进程或其他运行时副作用；必须先检查工作区和运行状态，不得将其视为完成。"

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
        self.working_memory.sync_todos(self.todo_ledger.to_dict())
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

    def switch_model_profile(self, profile_id: str, *, source: str = "runtime") -> None:
        """在 run 之间切换 session 的默认模型档案。"""
        profile = self.model_router.registry.profile(profile_id)
        from src.app.config import compile_static_capacity_input
        from src.context.budget import TokenizerAdapter
        from src.context.prefix import render_prefix
        static_tools = [
            {"type": "function", "name": item.name, "description": item.description, "parameters": item.parameters}
            for item in self.context_manager.registry.definitions(self.active_tool_profile.allowed_tools)
        ]
        static_tokens = compile_static_capacity_input(render_prefix(self.workspace, self.context_manager.registry), static_tools, TokenizerAdapter())["total"]
        validate_static_model_capacity(profile, self.config.max_new_tokens, static_tokens)
        previous = str(self.session.get("active_model_profile") or "")
        self.session["active_model_profile"] = profile.id
        self.session.setdefault("model_switches", []).append({"from": previous, "to": profile.id, "source": source})
        self.session_store.save(self.session)
        self.context_manager.model_profile = profile
        self.session_events.emit("model_switched", previous_model_profile=previous, model_profile=profile.snapshot(), source=source)

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
        self.working_memory.sync_todos(self.session["todo_ledger"])
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
        self.working_memory.sync_todos(self.session["todo_ledger"])
        self.session_store.save(self.session)
        self.session_events.emit("todo_updated", todo_id=item.todo_id, status=item.status, priority=item.priority)
        return f"updated {item.todo_id} [{item.status}] {item.priority} - {item.content}"

    def todo_list(self, args: dict | None = None) -> str:
        return self.todo_ledger.render_list()

    def todo_delete(self, args: dict) -> str:
        item = self.todo_ledger.delete(args["todo_id"])
        self.session["todo_ledger"] = self.todo_ledger.to_dict()
        self.working_memory.sync_todos(self.session["todo_ledger"])
        self.session_store.save(self.session)
        self.session_events.emit("todo_deleted", todo_id=item.todo_id)
        return f"deleted {item.todo_id} - {item.content}"

    def todo_archive(self, args: dict) -> str:
        item = self.todo_ledger.archive(args["todo_id"])
        self.session["todo_ledger"] = self.todo_ledger.to_dict()
        self.working_memory.sync_todos(self.session["todo_ledger"])
        self.session_store.save(self.session)
        self.session_events.emit("todo_archived", todo_id=item.todo_id, status=item.status)
        return f"archived {item.todo_id} [{item.status}] - {item.content}"

    def ask_user(self, question: str, choices: list[str] | None = None) -> str:
        choices = list(choices or [])
        self.session_events.emit("ask_user_requested", question=str(question)[:500], choices=choices)
        if self.ask_user_callback is None:
            return "error: ask_user requires interactive mode"
        answer = str(self.ask_user_callback(str(question), choices))
        self.session_events.emit("ask_user_answered", question=str(question)[:500], answer=answer[:500])
        return answer

    def ask(self, user_message: str) -> str:
        """统一收口整个运行链，确保构建、工具和持久化异常都有终态。"""
        self._active_run_context = None
        try:
            return self._ask_loop(user_message)
        except JCodeRuntimeStopError as exc:
            context = self._active_run_context
            if context is not None:
                try:
                    return self._finish_run(*context, exc.user_message, exc.code)
                except Exception as finish_exc:
                    self._print_runtime_error(finish_exc)
                    return exc.user_message
            return exc.user_message
        except Exception as exc:
            self._print_runtime_error(exc)
            context = self._active_run_context
            if context is not None:
                try:
                    return self._finish_run(*context, f"运行时错误: {exc}", UNEXPECTED_RUNTIME_ERROR)
                except Exception as finish_exc:
                    self._print_runtime_error(finish_exc)
                    pass
            return f"运行时错误: {exc}"

    def _print_runtime_error(self, exc: Exception) -> None:
        """将脱敏后的异常堆栈输出到控制台，便于定位后台运行失败。"""
        error_trace = "".join(traceback.format_exception(exc))
        print(self.redactor.redact(error_trace), file=sys.stderr, flush=True)

    def _ask_loop(self, user_message: str) -> str:
        self.abort_requested = False
        task_state, run_dir, checkpoint = self._begin_run(user_message)
        self._active_run_context = (task_state, run_dir)
        final_text = ""

        step = 0
        while step < self.config.max_steps:
            if self.abort_requested:
                return self._finish_run(task_state, run_dir, "Stopped after abort request.", ABORTED)
            task_state.step_index = step + 1
            task_state.attempts += 1

            try:
                request_text = CONTINUATION_REQUEST if task_state.partial_response_parts else user_message
                context_result = self._build_context(request_text, task_state, run_dir)
                response = self._call_model(context_result, task_state, run_dir)

            except JCodeRuntimeStopError as exc:
                self._record_trace(run_dir, "runtime_stopped", task_state, stop_reason=exc.code, audit=exc.audit)
                return self._finish_run(task_state, run_dir, exc.user_message, exc.code)
            except Exception as exc:
                self._print_runtime_error(exc)
                self._record_trace(
                    run_dir,
                    "run_failed",
                    task_state,
                    error_type=type(exc).__name__,
                    message=str(exc)[:500],
                )
                return self._finish_run(task_state, run_dir, f"运行时错误: {exc}", UNEXPECTED_RUNTIME_ERROR)

            tool_calls = list(response.tool_calls or [])
            self._record_model_history(response, task_state)
            if not tool_calls:
                self._create_checkpoint(checkpoint, task_state, run_dir, "model_completed")
                if not self._is_completed_response(response.finish_reason):
                    if self._should_continue_incomplete(response, task_state):
                        task_state.partial_response_parts.append(response.text)
                        task_state.output_continuation_count += 1
                        self._record_recovery_event(
                            run_dir,
                            task_state,
                            "model_output_continuation_scheduled",
                            reason=response.incomplete_reason or "unknown",
                            continuation_count=task_state.output_continuation_count,
                        )
                        self._create_checkpoint(checkpoint, task_state, run_dir, "model_output_continuation")
                        self.run_store.write_task_state(run_dir, task_state)
                        # 续写不是新的任务决策，不消耗 max_steps。
                        continue
                    partial_text = self._combined_response_text(task_state, response.text)
                    return self._finish_run(task_state, run_dir, partial_text, MODEL_OUTPUT_INCOMPLETE)
                final_text = self._combined_response_text(task_state, response.text)
                gate = self.final_gate.check(final_text, task_state, self.working_memory, session=self.session, workspace=self.workspace, context=self.session.get("ctx_info", {}))
                self._record_trace(run_dir, "final_readiness_evaluated", task_state, action=gate.get("action", "safe_finalize"), reasons=gate.get("reasons", []))
                if gate.get("action") == "rerun_agent":
                    # 仅向 Agent 注入当前可纠正事实，避免后台评分污染推理上下文。
                    self.working_memory.note_safety(gate["message"])
                    self._record_trace(run_dir, "final_gate_rerun_requested", task_state, reason=gate["reason"], correction_packet=gate.get("correction_packet", {}))
                    self._create_checkpoint(checkpoint, task_state, run_dir, "final_gate_rerun_requested")
                    self.run_store.write_task_state(run_dir, task_state)
                    step += 1
                    continue
                return self._finish_run(task_state, run_dir, final_text, VALID_FINAL)
            self._record_trace(
                run_dir,
                "native_tool_calls_received",
                task_state,
                tool_calls=[{"call_id": call.call_id, "name": call.name, "arguments": call.arguments} for call in tool_calls],
            )
            for call in tool_calls:
                if self.abort_requested:
                    self._record_cancelled_tool_call(call, task_state, run_dir, checkpoint)
                    continue
                self._execute_tool_call(call.name, call.arguments, task_state, run_dir, checkpoint, call_id=call.call_id)
            step += 1

        return self._finish_run(task_state, run_dir, final_text or "Stopped after reaching max steps.", STEP_LIMIT_REACHED)

    def resume(self, session_id: str) -> None:
        self.session = self.session_store.load_requested(session_id, None, self.workspace.root)
        self.working_memory = type(self.working_memory).from_dict(self.session.get("working_memory", {}), self.workspace.root)
        self.todo_ledger = TodoLedger.from_dict(self.session.get("todo_ledger", {}))
        self.working_memory.sync_todos(self.todo_ledger.to_dict())
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
        profile_id = str(self.session.get("active_model_profile") or self.config.default_model_profile)
        profile = self.model_router.registry.profile(profile_id)
        execution_fingerprint = build_execution_fingerprint(
            resolve_model_snapshot(self.session, profile),
            [{"name": item.name, "description": item.description, "parameters": item.parameters} for item in self.context_manager.registry.definitions(self.active_tool_profile.allowed_tools)],
        )
        continuation_context = {}
        if self.session.get("run_ids"):
            continuation_context = build_resume_context(
                session=self.session,
                session_store=self.session_store,
                run_store=self.run_store,
                workspace=self.workspace,
                resume_requested=None,
                execution_fingerprint=execution_fingerprint,
            )
            changed_paths = sorted(set(continuation_context.get("changed_paths", [])))
            if changed_paths:
                self._mark_stale_file_evidence(changed_paths)
            self.working_memory.resume_context = continuation_context
        task_state = TaskState.create(user_message, execution_fingerprint)
        run_dir = self.run_store.start_run(task_state)
        checkpoint = CheckpointManager(run_dir, self.workspace)
        self.working_memory.task_goal = str(user_message)
        self._append_history("user", user_message, task_state)
        self.session_events.emit(
            "run_started",
            run_id=task_state.run_id,
            task_id=task_state.task_id,
            user_request=user_message[:500],
            model_profile=task_state.model_profile,
        )
        self._record_trace(run_dir, "run_started", task_state, task_id=task_state.task_id, user_request=user_message[:500], model_profile=task_state.model_profile)
        if continuation_context:
            self._emit_session_continuation_events(run_dir, task_state, continuation_context)
        if self.working_memory.resume_context:
            self._record_trace(run_dir, "resume_evaluated", task_state, **self.working_memory.resume_context)
        return task_state, run_dir, checkpoint

    def _emit_session_continuation_events(self, run_dir, task_state, context: dict) -> None:
        """把统一延续评估写入 session event 与 run trace。"""
        event_names = ["session_continuation_evaluated", "workspace_baseline_evaluated", "execution_fingerprint_evaluated", "continuation_compatibility_evaluated"]
        if context.get("changed_paths"):
            event_names.append("workspace_changed_detected")
        for event_name in event_names:
            self.session_events.emit(event_name, run_id=task_state.run_id, **context)
            self._record_trace(run_dir, event_name, task_state, **context)

    def _build_context(self, user_message: str, task_state, run_dir):
        context_result = self.context_manager.build(
            self.session,
            self.working_memory,
            user_message,
            allowed_tools=self.active_tool_profile.allowed_tools,
            provider_continuation=task_state.provider_continuation,
            run_store=self.run_store,
            run_dir=run_dir,
        )
        if getattr(context_result, "session_commit_required", False):
            self.session_store.save(context_result.session_candidate)
            self.session = context_result.session_candidate
            self.working_memory = context_result.working_memory_candidate
        self.session["ctx_info"] = context_result.ctx_info
        self.session_store.save(self.session)
        compact_info = dict(context_result.ctx_info.get("compact", {}) or {})
        self._emit_compact_context_events(run_dir, task_state, context_result, compact_info)
        if context_result.compact_audit:
            self._record_trace(
                run_dir,
                "compact_history_audit",
                task_state,
                compact_status=compact_info.get("status", "idle"),
                compact_trigger=compact_info.get("trigger", ""),
                summary_mode=context_result.compact_audit.get("mode", ""),
                summary_source=context_result.compact_audit.get("source", ""),
                status=context_result.compact_audit.get("status", ""),
                fallback_reason=context_result.compact_audit.get("fallback_reason", ""),
                summary_prompt=context_result.compact_audit.get("prompt", ""),
                summary_response=context_result.compact_audit.get("response", ""),
                summary_text=context_result.compact_audit.get("summary_text", ""),
            )
        context_snapshot = {
            "prefix": context_result.prefix,
            "skill": context_result.skill,
            "history": [event.to_dict() for event in context_result.history],
            "working_memory": context_result.working_memory.to_dict(),
            "provider_input": {
                "instructions": context_result.provider_input.instructions if context_result.provider_input else "",
                "input": context_result.provider_input.input if context_result.provider_input else [],
                "tools": context_result.provider_input.tools if context_result.provider_input else [],
                "serialized_input_tokens": context_result.provider_input.serialized_input_tokens if context_result.provider_input else 0,
            },
        }
        audit_data = {"context_result": context_snapshot, "ctx_info": context_result.ctx_info}
        # 同一步可因输出截断续写多次，审计文件必须按请求尝试号区分，不能覆盖。
        audit_ref = self.run_store.write_audit(
            run_dir,
            f"context-{task_state.step_index:04d}-{task_state.attempts:04d}.json",
            audit_data,
        )
        audit_sha256 = hashlib.sha256(json.dumps(audit_data, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        event_payload = self._context_event_payload(context_result, audit_ref, audit_sha256)
        event_payload["step_id"] = self._context_step_id(task_state)
        self.session_events.emit("context_built", run_id=task_state.run_id, **event_payload)
        self._record_trace(run_dir, "context_built", task_state, **event_payload)
        return context_result

    @staticmethod
    def _context_event_payload(context_result, audit_ref: str, audit_sha256: str) -> dict:
        """事件流只保留 Context 审计引用和可扫描指标。"""
        info = context_result.ctx_info
        compact = dict(info.get("compact", {}) or {})
        return {
            "context_audit_ref": audit_ref,
            "context_audit_sha256": audit_sha256,
            "history_event_count": len(context_result.history),
            "tool_count": len(context_result.tools),
            "input_tokens": int(info.get("serialized_input_tokens", 0) or 0),
            "pressure_level": int(info.get("pressure", {}).get("level", 0) or 0),
            "compact_status": str(compact.get("status", "idle")),
            "compact_trigger": str(compact.get("trigger", "")),
            "compression_before": {
                "fixed_items": {str(item.get("name")): int(item.get("tokens", 0) or 0) for item in info.get("fixed_occupancies", [])},
                "total_input_tokens": int(info.get("serialized_input_tokens", 0) or 0),
                "output_reserved_tokens": int(info.get("actual_max_new_tokens", 0) or 0),
                "safety_margin_tokens": int(info.get("safety_margin_tokens", 0) or 0),
                "remaining_capacity_tokens": int(info.get("final_capacity_status", {}).get("remaining_tokens", 0) or 0),
                "pressure_ratio": info.get("pressure", {}).get("ratio", 0),
                "pressure_level": int(info.get("pressure", {}).get("level", 0) or 0),
            },
        }

    def _emit_compact_context_events(self, run_dir, task_state, context_result, compact_info: dict) -> None:
        event_payload = {
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
        comparison = context_result.ctx_info.get("compression_comparison")
        if comparison and int(event_payload.get("pressure_level", 0) or 0) >= 1:
            audit = dict(context_result.compact_audit or {})
            comparison_payload = {
                **comparison,
                "step_id": self._context_step_id(task_state),
                "pressure_level": int(event_payload.get("pressure_level", 0) or 0),
                "result": {
                    "status": "fallback" if audit.get("status") == "fallback" else "applied",
                    "label": "降级为规则压缩" if audit.get("status") == "fallback" else "",
                    "trigger": compact_info.get("trigger", "") or "pressure_threshold",
                    "summary_source": audit.get("source", "") or "rule",
                    "artifact_ref": audit.get("artifact_ref", ""),
                    # 第四档直接携带最终摘要，前端无需读取历史审计文件。
                    "summary_text": audit.get("summary_text", ""),
                },
            }
            self.session_events.emit("context_compression_compared", run_id=task_state.run_id, **comparison_payload)
            self._record_trace(run_dir, "context_compression_compared", task_state, **comparison_payload)
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
        """复用同一上下文处理临时 Provider 故障，禁止重复构建审计与历史。"""
        for retry_index in range(MAX_TRANSPORT_RETRIES + 1):
            try:
                response = self.model_router.complete(
                    context_result,
                    max_tokens=self.config.max_new_tokens,
                    temperature=self.config.temperature,
                    profile_id=str(task_state.model_profile.get("id") or ""),
                    model_profile=task_state.model_profile,
                )
            except Exception as exc:
                if not self._is_retryable_provider_error(exc) or retry_index >= MAX_TRANSPORT_RETRIES:
                    if retry_index:
                        self._record_recovery_event(run_dir, task_state, "model_retry_exhausted", retry_count=retry_index, error_type=type(exc).__name__, message=str(exc)[:500])
                    raise
                delay_seconds = self._retry_delay_seconds(exc, retry_index)
                self._record_recovery_event(run_dir, task_state, "model_retry_scheduled", retry_count=retry_index + 1, delay_seconds=delay_seconds, error_type=type(exc).__name__, message=str(exc)[:500])
                time.sleep(delay_seconds)
                self._record_recovery_event(run_dir, task_state, "model_retry_attempted", retry_count=retry_index + 1)
                continue
            if response.finish_reason == "failed":
                error = ProviderRequestError(
                    response.provider_error_message or "deepseek response failed",
                    status_code=self._response_status_code(response),
                    retry_after_seconds=response.retry_after_seconds,
                )
                if self._is_retryable_provider_error(error) or self._is_retryable_failed_response(response):
                    if retry_index >= MAX_TRANSPORT_RETRIES:
                        self._record_recovery_event(run_dir, task_state, "model_retry_exhausted", retry_count=retry_index, provider_error_code=response.provider_error_code, message=response.provider_error_message[:500])
                        raise error
                    delay_seconds = self._retry_delay_seconds(error, retry_index)
                    self._record_recovery_event(run_dir, task_state, "model_retry_scheduled", retry_count=retry_index + 1, delay_seconds=delay_seconds, provider_error_code=response.provider_error_code, message=response.provider_error_message[:500])
                    time.sleep(delay_seconds)
                    self._record_recovery_event(run_dir, task_state, "model_retry_attempted", retry_count=retry_index + 1)
                    continue
                raise error
            break

        self._record_trace(
            run_dir,
            "model_responded",
            task_state,
            step_id=self._context_step_id(task_state),
            estimated_input_tokens=response.input_tokens,
            estimated_output_tokens=response.output_tokens,
            response_text=self.redactor.redact(response.text),
            reasoning_chars=len(response.reasoning),
            reasoning_sha256=hashlib.sha256(response.reasoning.encode("utf-8")).hexdigest() if response.reasoning else "",
            finish_reason=response.finish_reason,
            incomplete_reason=response.incomplete_reason,
            provider_error_code=response.provider_error_code,
            provider_error_message=response.provider_error_message[:500],
            native_tool_calls=[{"call_id": call.call_id, "name": call.name, "arguments": call.arguments} for call in response.tool_calls or []],
            model_profile=task_state.model_profile,
        )
        return response

    @staticmethod
    def _context_step_id(task_state) -> str:
        """用步骤和请求尝试号稳定关联 Context、压缩事件与模型响应。"""
        return f"{task_state.run_id}:{task_state.step_index}:{task_state.attempts}"

    @staticmethod
    def _combined_response_text(task_state, response_text: str) -> str:
        """将被截断的旧片段和最后完成片段按生成顺序交付。"""
        return "\n".join(part for part in [*task_state.partial_response_parts, str(response_text).strip()] if part).strip()

    @staticmethod
    def _response_status_code(response: ModelResponse) -> int:
        """错误响应优先使用数值 code，无法识别时由 Provider error type 决定不重试。"""
        try:
            return int(response.provider_error_code)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _is_retryable_provider_error(exc: Exception) -> bool:
        """只重试官方定义的限流、服务端故障和网络传输失败。"""
        if not isinstance(exc, ProviderRequestError):
            return False
        return exc.transport_error or exc.status_code in RETRYABLE_HTTP_STATUS_CODES

    @staticmethod
    def _is_retryable_failed_response(response: ModelResponse) -> bool:
        """兼容 failed 终态中服务端使用字符串 error code 的情况。"""
        return response.provider_error_code.strip().lower() in RETRYABLE_PROVIDER_ERROR_CODES

    @staticmethod
    def _retry_delay_seconds(exc: Exception, retry_index: int) -> float:
        """遵从有效 Retry-After，否则采用有上限的指数退避。"""
        retry_after = getattr(exc, "retry_after_seconds", None)
        if isinstance(retry_after, (int, float)) and retry_after > 0:
            return min(float(retry_after), 30.0)
        return min(0.5 * (2**retry_index), 4.0)

    @staticmethod
    def _should_continue_incomplete(response: ModelResponse, task_state) -> bool:
        """仅在输出截断且尚有续写预算时继续；其他不完整状态不可盲目恢复。"""
        if response.finish_reason != "incomplete" or not response.text.strip():
            return False
        if response.incomplete_reason == "max_output_tokens":
            return task_state.output_continuation_count < MAX_OUTPUT_CONTINUATIONS
        # 服务端未说明原因时仅试一次，避免因未知状态无限续写。
        return not response.incomplete_reason and task_state.output_continuation_count == 0

    def _record_recovery_event(self, run_dir, task_state, event: str, **payload) -> None:
        """让 session event 与 run trace 同步记录模型恢复行为。"""
        self.session_events.emit(event, run_id=task_state.run_id, **payload)
        self._record_trace(run_dir, event, task_state, **payload)

    def _record_model_history(self, response, task_state) -> None:
        """分别写入通用会话历史与当前 run 的 Provider 原生续接项。"""
        raw = response.raw if isinstance(response.raw, dict) else {}
        output = raw.get("output", []) if isinstance(raw.get("output", []), list) else []
        continuation = ProviderContinuation.from_dict(task_state.provider_continuation, run_id=task_state.run_id)
        continuation.add_response_items(output)
        task_state.provider_continuation = continuation.to_dict()
        if response.text:
            self._append_history(
                "assistant",
                response.text,
                task_state,
                metadata={"model_profile": task_state.model_profile},
            )
        for call in response.tool_calls or []:
            task_state.register_native_tool_call(call.call_id, call.name, call.arguments)
            self._append_history(
                "tool_call",
                "",
                task_state,
                tool_name=call.name,
                call_id=call.call_id,
                arguments=call.arguments,
                metadata={},
            )

    @staticmethod
    def _is_completed_response(finish_reason: str) -> bool:
        """仅接受 Provider 明确完成的无工具调用响应作为最终答案。"""
        return str(finish_reason or "").strip() == "completed"


    def _execute_tool_call(
        self,
        tool_name: str,
        tool_args: dict,
        task_state,
        run_dir,
        checkpoint,
        *,
        call_id: str = "",
        trace_meta: dict | None = None,
        history_meta: dict | None = None,
    ) -> ToolResult:
        """执行工具结果 + 记录工具执行前后工作区状态 + 记录工具执行结果到历史 + 记录工具执行结果到 trace"""
        trace_meta = dict(trace_meta or {})
        history_meta = dict(history_meta or {})
        self._record_trace(run_dir, "tool_requested", task_state, name=tool_name, args=tool_args, call_id=call_id, **trace_meta)
        task_state.update_native_tool_call(call_id, "running")
        subagent_snapshot_before = None
        subagent_snapshot_uncertain = False
        if tool_name in {"spawn_subagent", "send_subagent_message", "wait_subagent"}:
            try:
                subagent_snapshot_before = self.workspace.snapshot()
            except Exception:
                subagent_snapshot_uncertain = True
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
                abort_requested=lambda: self.abort_requested,
            )

        if tool_name in {"spawn_subagent", "send_subagent_message", "wait_subagent"} and result.status not in {"success", "ok"}:
            try:
                subagent_snapshot_after = self.workspace.snapshot()
                subagent_changed = (
                    []
                    if subagent_snapshot_uncertain
                    else sorted(set(subagent_snapshot_after) ^ set(subagent_snapshot_before or {}))
                )
            except Exception:
                subagent_changed = []
                subagent_snapshot_uncertain = True
            if subagent_changed or subagent_snapshot_uncertain:
                result.status = "partial_success"
                result.changed_files = sorted(set(result.changed_files) | set(subagent_changed))
                result.text += self._partial_side_effect_notice(result.changed_files)
                result.metadata.update({"side_effect_possible": True, "side_effect_paths_confirmed": bool(result.changed_files), "snapshot_uncertain": subagent_snapshot_uncertain})

        # 工具返回时若已经收到 abort，结果不能被模型误认为本次工具调用已完整完成。
        if self.abort_requested and result.status not in {"denied", "cancelled"}:
            result.status = "interrupted"
            result.error_type = "user_abort"
            result.text = INTERRUPTED_TOOL_TEXT

        if tool_name == "read_file":
            # artifact 文件同样按 read_file 协议直接分段返回，复用原始来源参与 stale 判断。
            if result.metadata.get("artifact_read"):
                source_files = self._artifact_source_files(str(tool_args.get("path", "")))
                if source_files:
                    result.metadata["source_files"] = source_files
        else:
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

        unresolved_before = {item.get("failure_id") for item in task_state.unresolved_tool_failures}
        task_state.record_tool(tool_name, result, arguments=tool_args, call_id=call_id)
        task_state.update_native_tool_call(call_id, result.status)
        unresolved_after = {item.get("failure_id") for item in task_state.unresolved_tool_failures}
        if result.status not in {"success", "ok"}:
            failure = next((item for item in task_state.unresolved_tool_failures if item.get("failure_id") not in unresolved_before), None)
            if failure:
                self._record_trace(run_dir, "tool_failure_recorded", task_state, **failure)
        for failure in task_state.resolved_tool_failures:
            if failure.get("failure_id") not in unresolved_after and failure.get("resolution_evidence") == f"write_file:{call_id or 'success'}":
                self._record_trace(run_dir, "tool_failure_resolved", task_state, **failure)
        if tool_name == "run_shell" and task_state.verification:
            self._record_trace(run_dir, "verification_recorded", task_state, **task_state.verification)
        self._append_history(
            "tool_result",
            result.text,
            task_state,
            tool_name=tool_name,
            call_id=call_id,
            arguments=tool_args,
            metadata={
                **result.metadata,
                "tool_status": result.status,
                "error_type": result.error_type,
                "changed_files": result.changed_files,
                "artifacts": result.artifacts,
            },
            **history_meta,
        )
        self.working_memory.observe_tool(
            tool_name,
            result.status,
            str(result.metadata.get("observation_summary") or result.text),
            str(result.metadata.get("full_output_artifact") or ""),
        )
        if call_id:
            continuation = ProviderContinuation.from_dict(task_state.provider_continuation, run_id=task_state.run_id)
            continuation.add_tool_output(call_id, result.text)
            task_state.provider_continuation = continuation.to_dict()
        if tool_name == "wait_subagent" and result.status == "success":
            self.working_memory.subagent_results.append(result.text[:1000])

        event_name = "subagent_completed" if tool_name == "wait_subagent" and result.status == "success" else "tool_executed"
        self._record_trace(
            run_dir,
            event_name,
            task_state,
            name=tool_name,
            call_id=call_id,
            status=result.status,
            error_type=result.error_type,
            changed_files=result.changed_files,
            artifact_ref=str(result.metadata.get("full_output_artifact") or ""),
            result_summary=self.redactor.redact(result.text),
            **trace_meta,
        )
        self._create_checkpoint(checkpoint, task_state, run_dir, "tool_executed")
        self.run_store.write_task_state(run_dir, task_state)
        return result


    @staticmethod
    def _partial_side_effect_notice(changed_paths: list[str]) -> str:
        """生成仅回注 LLM 的子 Agent 失败副作用提醒。"""
        if changed_paths:
            return "\n工具执行失败，但可能已经对工作区产生部分修改。\n已检测到变更路径：" + ", ".join(str(path) for path in changed_paths) + "\n建议重新读取受影响文件。"
        return "\n工具执行失败，但可能已经对工作区产生部分修改。"

    def _record_cancelled_tool_call(self, call, task_state, run_dir, checkpoint) -> None:
        """为 abort 后尚未执行的原生调用补写结果，保证 function_call 与 output 成对持久化。"""
        call_id = str(call.call_id or "")
        result = ToolResult("cancelled", CANCELLED_TOOL_TEXT, error_type="user_abort", decision="executed")
        task_state.record_tool(call.name, result, arguments=call.arguments, call_id=call_id)
        task_state.update_native_tool_call(call_id, result.status)
        self._append_history(
            "tool_result",
            result.text,
            task_state,
            tool_name=call.name,
            call_id=call_id,
            arguments=call.arguments,
            metadata={
                "tool_status": result.status,
                "error_type": result.error_type,
                "changed_files": [],
                "artifacts": [],
                "cancelled": True,
            },
        )
        self.working_memory.observe_tool(call.name, result.status, result.text)
        continuation = ProviderContinuation.from_dict(task_state.provider_continuation, run_id=task_state.run_id)
        continuation.add_tool_output(call_id, result.text)
        task_state.provider_continuation = continuation.to_dict()
        self._record_trace(
            run_dir,
            "tool_cancelled",
            task_state,
            name=call.name,
            call_id=call_id,
            status=result.status,
            error_type=result.error_type,
            result_summary=result.text,
        )
        self._create_checkpoint(checkpoint, task_state, run_dir, "tool_cancelled")
        self.run_store.write_task_state(run_dir, task_state)

    def _record_trace(self, run_dir, event: str, task_state, **payload) -> None:
        self.run_store.append_trace(run_dir, event, task_state.run_id, **payload)

    def _append_history(self, kind: str, content: str, task_state, **extra) -> None:
        append_history(self.session, kind, content, run_id=task_state.run_id, **extra)

    def _artifact_source_files(self, artifact_path: str) -> list[dict]:
        """从原始工具结果复用文件来源，保证 artifact 分段读取也能参与 stale 判断。"""
        normalized = str(artifact_path).replace("\\", "/")
        for item in reversed(self.session.get("history", [])):
            metadata = item.get("metadata", {})
            if not isinstance(metadata, dict):
                continue
            if str(metadata.get("full_output_artifact", "")).replace("\\", "/") != normalized:
                continue
            source_files = metadata.get("source_files", [])
            if isinstance(source_files, list):
                return [dict(source) for source in source_files if isinstance(source, dict)]
        return []

    def mark_stale_file_evidence(self, changed_paths: list[str]) -> None:
        """文件成功变更后，标记历史中依赖旧文件内容的 read_file 结果。"""
        changed = {str(path).replace("\\", "/") for path in changed_paths if str(path).strip()}
        if not changed:
            return
        for item in self.session.get("history", []):
            if item.get("kind") != "tool_result" or item.get("tool_name") not in {"read_file", "search"}:
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

    def _mark_stale_file_evidence(self, changed_files: list[str]) -> None:
        """保留内部调用别名，所有标记统一落到公共 changed_paths 入口。"""
        self.mark_stale_file_evidence(changed_files)

    def _create_checkpoint(self, checkpoint, task_state, run_dir, trigger: str) -> None:
        checkpoint.create(self.session, task_state, self.working_memory, self.worker_manager.worker_refs())
        self._record_trace(run_dir, "checkpoint_created", task_state, trigger=trigger)

    def _finish_run(self, task_state, run_dir, final_text: str, stop_reason: str = VALID_FINAL) -> str:
        task_state.finish("completed" if stop_reason == VALID_FINAL else "stopped", stop_reason, final_text)
        memory_audit = maintain_after_turn(self.memory_store, self.working_memory, task_state.user_request, final_text, agent=self)
        self._record_trace(run_dir, "memory_maintained", task_state, **memory_audit)
        self._record_trace(
            run_dir,
            "run_finished",
            task_state,
            status=task_state.status,
            stop_reason=stop_reason,
            final_text=self.redactor.redact(final_text),
        )
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
