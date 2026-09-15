from __future__ import annotations

import json
from pathlib import Path

from src.context.manager import ContextManager
from src.evidence.store import RunStore
from src.evidence.tool_artifacts import prepare_tool_result_observation
from src.memory.durable import DurableMemoryStore
from src.memory.working import WorkingMemory
from src.providers.continuation import ProviderContinuation
from src.state.checkpoint import CheckpointManager
from src.state.history import append_history
from src.state.task import TaskState
from src.tools.base import ToolResult
from src.workers.result import WorkerResult
from src.workers.roles import SubagentRoleSpec


class SubagentRunner:
    """运行一个独立子 Agent，复用主系统的上下文、Provider 和工具治理。"""

    def __init__(self, *, workspace, root: Path, tool_executor, model_router, config, session_events, worker, role_spec: SubagentRoleSpec):
        self.workspace = workspace
        self.root = root
        self.tool_executor = tool_executor
        self.model_router = model_router
        self.config = config
        self.session_events = session_events
        self.worker = worker
        self.role_spec = role_spec
        self.redactor = tool_executor.redactor

    def run(self) -> WorkerResult:
        """执行子 Agent 主循环；未处理异常形成明确失败结果。"""
        worker_dir = self.root / self.worker.worker_id
        worker_dir.mkdir(parents=True, exist_ok=True)
        (worker_dir / "artifacts").mkdir(parents=True, exist_ok=True)
        self.worker.status = "running"
        self._write_worker_state(worker_dir)
        self._emit("subagent_started", worker_id=self.worker.worker_id, role=self.worker.role)
        try:
            result = self._run_loop(worker_dir)
        except Exception as exc:
            result = WorkerResult(
                worker_id=self.worker.worker_id,
                role=self.worker.role,
                status="failed",
                text=f"子 Agent 运行失败: {exc}",
                changed_files=[],
                artifacts=[],
                verification={},
                tool_failures=[{"error_type": type(exc).__name__, "message": str(exc)[:500]}],
                stop_reason="runtime_error",
                steps=0,
                model_profile=dict(self.worker.model_profile),
            )
        self.worker.result = result
        self.worker.status = result.status
        self._write_worker_state(worker_dir)
        (worker_dir / "result.json").write_text(json.dumps(result.__dict__, ensure_ascii=False, indent=2), encoding="utf-8")
        event = "subagent_completed" if result.status == "completed" else "subagent_failed"
        self._emit(event, worker_id=self.worker.worker_id, role=self.worker.role, status=result.status, stop_reason=result.stop_reason)
        return result

    def _run_loop(self, worker_dir: Path) -> WorkerResult:
        profile_id = str(self.worker.model_profile.get("id") or self.config.default_model_profile)
        profile = self.model_router.registry.profile(profile_id)
        max_new_tokens = min(int(self.config.max_new_tokens), int(profile.max_output_tokens))
        registry = self.tool_executor.registry
        from src.policy.tool_profiles import build_tool_profiles

        active_profile = build_tool_profiles(registry)[self.role_spec.tool_profile]
        memory = WorkingMemory(self.workspace.root)
        session = {
            "id": self.worker.parent_session_id or f"worker-session-{self.worker.worker_id}",
            "history": [],
            "event_seq": 0,
            "runtime_mode": {"mode": "default"},
        }
        task_text = self._task_text()
        task_state = TaskState.create(task_text, profile.snapshot())
        run_store = RunStore(self.root, workspace_root=self.workspace.root)
        checkpoint = CheckpointManager(worker_dir, self.workspace)
        context_manager = ContextManager(
            workspace=self.workspace,
            durable_memory=DurableMemoryStore(self.root.parent / "memory"),
            registry=registry,
            model_profile=profile,
            actual_max_new_tokens=max_new_tokens,
            summary_router=self.model_router,
            summary_config=self.config,
        )
        append_history(session, "user", task_text, run_id=task_state.run_id)
        changed_files: set[str] = set()
        artifacts: list[str] = []
        for step in range(1, int(self.config.max_steps) + 1):
            task_state.step_index = step
            task_state.attempts += 1
            outcome = context_manager.build(
                session,
                memory,
                task_text,
                allowed_tools=active_profile.allowed_tools,
                provider_continuation=task_state.provider_continuation,
                run_store=run_store,
                run_dir=worker_dir,
            )
            if outcome.session_commit_required and outcome.session_candidate is not None:
                session = outcome.session_candidate
                memory = outcome.working_memory_candidate
            response = self.model_router.complete(
                outcome.context_result,
                max_tokens=max_new_tokens,
                temperature=self.config.temperature,
                profile_id=profile_id,
                model_profile=task_state.model_profile,
            )
            self._record_model(session, task_state, response)
            run_store.append_trace(
                worker_dir,
                "model_responded",
                task_state.run_id,
                role=self.worker.role,
                finish_reason=response.finish_reason,
                response_text=self.redactor.redact(response.text),
                native_tool_calls=[{"call_id": call.call_id, "name": call.name, "arguments": call.arguments} for call in response.tool_calls or []],
            )
            calls = list(response.tool_calls or [])
            if not calls:
                final_text = str(response.text or "").strip()
                if response.finish_reason == "completed":
                    task_state.finish("completed", "completed", final_text)
                else:
                    task_state.finish("failed", response.finish_reason or "model_output_incomplete", final_text)
                self._persist_state(run_store, checkpoint, worker_dir, task_state, session, memory)
                return self._result(task_state, final_text, changed_files, artifacts)
            for call in calls:
                result = self._execute_tool(call, task_state, session, memory, active_profile, run_store, worker_dir)
                changed_files.update(result.changed_files)
                for artifact in result.artifacts:
                    if artifact not in artifacts:
                        artifacts.append(artifact)
            self._persist_state(run_store, checkpoint, worker_dir, task_state, session, memory)
        task_state.finish("timeout", "step_limit_reached", "子 Agent 达到最大步骤数。")
        self._persist_state(run_store, checkpoint, worker_dir, task_state, session, memory)
        return self._result(task_state, task_state.final_answer, changed_files, artifacts)

    def _execute_tool(self, call, task_state, session, memory, profile, run_store, worker_dir) -> ToolResult:
        result = self.tool_executor.execute(
            call.name,
            call.arguments,
            working_memory=memory,
            tool_profile=profile,
            write_scope=self.worker.write_scope,
            runtime_mode="default",
            run_id=task_state.run_id,
            source="subagent",
            runtime=None,
        )
        result_text, metadata, result_artifacts = prepare_tool_result_observation(
            run_store,
            worker_dir,
            call.name,
            result.text,
            result.artifacts,
        )
        result.text = result_text
        result.artifacts = result_artifacts
        result.metadata.update(metadata)
        task_state.record_tool(call.name, result, arguments=call.arguments, call_id=call.call_id)
        task_state.update_native_tool_call(call.call_id, result.status)
        append_history(
            session,
            "tool_result",
            result.text,
            run_id=task_state.run_id,
            tool_name=call.name,
            call_id=call.call_id,
            arguments=call.arguments,
            metadata={**result.metadata, "tool_status": result.status, "changed_files": result.changed_files, "artifacts": result.artifacts},
        )
        continuation = ProviderContinuation.from_dict(task_state.provider_continuation, run_id=task_state.run_id)
        continuation.add_tool_output(call.call_id, result.text)
        task_state.provider_continuation = continuation.to_dict()
        run_store.append_trace(
            worker_dir,
            "tool_executed",
            task_state.run_id,
            role=self.worker.role,
            name=call.name,
            call_id=call.call_id,
            status=result.status,
            error_type=result.error_type,
            changed_files=result.changed_files,
            result_summary=self.redactor.redact(result.text),
        )
        return result

    def _record_model(self, session, task_state, response) -> None:
        raw = response.raw if isinstance(response.raw, dict) else {}
        continuation = ProviderContinuation.from_dict(task_state.provider_continuation, run_id=task_state.run_id)
        continuation.add_response_items(raw.get("output", []))
        task_state.provider_continuation = continuation.to_dict()
        if response.text:
            append_history(session, "assistant", response.text, run_id=task_state.run_id, metadata={"role": self.worker.role, "model_profile": task_state.model_profile})
        for call in response.tool_calls or []:
            task_state.register_native_tool_call(call.call_id, call.name, call.arguments)
            append_history(session, "tool_call", "", run_id=task_state.run_id, tool_name=call.name, call_id=call.call_id, arguments=call.arguments)

    def _persist_state(self, run_store, checkpoint, worker_dir, task_state, session, memory) -> None:
        checkpoint.create(session, task_state, memory, worker_refs=[self.worker.worker_id])
        (worker_dir / "agent_state.json").write_text(json.dumps(task_state.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        (worker_dir / "history.json").write_text(json.dumps(session.get("history", []), ensure_ascii=False, indent=2), encoding="utf-8")
        run_store.append_trace(worker_dir, "checkpoint_created", task_state.run_id, role=self.worker.role)

    def _result(self, task_state, text, changed_files, artifacts) -> WorkerResult:
        status = task_state.status
        stop_reason = task_state.stop_reason
        if status == "completed" and task_state.unresolved_tool_failures:
            status = "partial_success" if changed_files else "failed"
            stop_reason = "unresolved_tool_failures"
        if self.worker.role == "tester" and task_state.verification.get("state") == "failed":
            status = "partial_success" if changed_files else "failed"
            stop_reason = "verification_failed"
        return WorkerResult(
            worker_id=self.worker.worker_id,
            role=self.worker.role,
            status=status,
            text=str(text or ""),
            changed_files=sorted(changed_files),
            artifacts=list(artifacts),
            verification=dict(task_state.verification),
            tool_failures=list(task_state.unresolved_tool_failures),
            stop_reason=stop_reason,
            steps=task_state.step_index,
            model_profile=dict(task_state.model_profile),
        )

    def _task_text(self) -> str:
        criteria = "\n".join(f"- {item}" for item in self.worker.acceptance_criteria)
        messages = "\n".join(f"- {item}" for item in self.worker.messages)
        return (
            f"[Subagent role: {self.role_spec.role_id}]\n{self.role_spec.system_instruction}\n\n"
            f"Task:\n{self.worker.prompt}\n\nAcceptance criteria:\n{criteria}\n\n"
            f"Parent messages:\n{messages or '- none'}\n\nOutput contract:\n{self.role_spec.output_contract}"
        )

    def _write_worker_state(self, worker_dir: Path) -> None:
        (worker_dir / "task_state.json").write_text(json.dumps(self.worker.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    def _emit(self, event: str, **payload) -> None:
        if self.session_events:
            self.session_events.emit(event, **payload)
