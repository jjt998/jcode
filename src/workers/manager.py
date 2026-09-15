from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from src.tools.base import ToolResult
from src.tools.schemas import SendSubagentMessageArgs, SpawnSubagentArgs, WaitSubagentArgs
from src.workers.roles import get_role_spec, plan_mode_allowed_roles
from src.workers.runtime import WorkerRuntime
from src.workers.runner import SubagentRunner
from src.workers.result import WorkerResult

if TYPE_CHECKING:
    from src.app.config import AppConfig
    from src.evidence.session_log import SessionEventBus
    from src.providers.router import ModelRouter
    from src.state.workspace import Workspace
    from src.tools.executor import ToolExecutor


class WorkerManager:
    workspace: Workspace
    root: Path
    tool_executor: ToolExecutor
    model_router: ModelRouter
    config: AppConfig
    session_events: SessionEventBus | None
    workers: dict[str, WorkerRuntime]

    def __init__(self, workspace, root: Path, tool_executor, model_router, config, session_events=None):
        self.workspace = workspace
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.tool_executor = tool_executor
        self.model_router = model_router
        self.config = config
        self.session_events = session_events
        self.workers: dict[str, WorkerRuntime] = {}
        self._load_persisted_workers()

    def worker_refs(self) -> list[str]:
        return sorted(self.workers)

    def spawn(
        self,
        prompt: str,
        *,
        role: str,
        acceptance_criteria: list[str],
        write_scope: list[str] | None = None,
        model_profile: dict | None = None,
        parent_session_id: str = "",
        parent_run_id: str = "",
        plan_mode: bool = False,
    ) -> ToolResult:
        parsed = SpawnSubagentArgs.model_validate({"prompt": prompt, "role": role, "acceptance_criteria": acceptance_criteria, "write_scope": list(write_scope or [])})
        try:
            spec = get_role_spec(parsed.role)
        except ValueError as exc:
            return ToolResult("denied", str(exc), error_type="invalid_subagent_role")
        if plan_mode and spec.role_id not in plan_mode_allowed_roles():
            return ToolResult("denied", f"error: plan mode does not allow subagent role {spec.role_id}", error_type="tool_profile_denied")
        if parsed.write_scope and not spec.allow_write:
            return ToolResult("denied", f"error: role {spec.role_id} cannot receive write_scope", error_type="write_scope_not_allowed")
        if spec.allow_write and not parsed.write_scope:
            return ToolResult("denied", f"error: role {spec.role_id} requires explicit write_scope", error_type="write_scope_required")
        worker_id = "worker-" + uuid.uuid4().hex[:8]
        snapshot = dict(model_profile or {})
        worker = WorkerRuntime(worker_id, parsed.prompt, role=spec.role_id, acceptance_criteria=list(parsed.acceptance_criteria), write_scope=list(parsed.write_scope), model_profile=snapshot, parent_session_id=parent_session_id, parent_run_id=parent_run_id)
        self.workers[worker_id] = worker
        self._write_state(worker)
        if self.session_events:
            self.session_events.emit("subagent_spawned", worker_id=worker_id, role=worker.role, prompt=worker.prompt[:500], acceptance_criteria=list(worker.acceptance_criteria), write_scope=list(worker.write_scope))
        return ToolResult("success", f"spawned {worker_id}", metadata={"worker_id": worker_id, "worker_status": worker.status, "role": worker.role, "write_scope": list(worker.write_scope), "acceptance_criteria": list(worker.acceptance_criteria)})

    def send(self, worker_id: str, message: str) -> ToolResult:
        worker = self.workers.get(worker_id)
        if worker is None:
            return ToolResult("error", f"unknown worker {worker_id}", error_type="unknown_worker")
        try:
            worker.send(message)
        except ValueError as exc:
            return ToolResult("denied", str(exc), error_type="subagent_not_messageable")
        self._write_state(worker)
        if self.session_events:
            self.session_events.emit("subagent_message_sent", worker_id=worker_id, role=worker.role, message=message[:500])
        return ToolResult("success", f"sent message to {worker_id}", metadata={"worker_id": worker_id, "worker_status": worker.status, "role": worker.role})

    def wait(self, worker_id: str) -> ToolResult:
        worker = self.workers.get(worker_id)
        if worker is None:
            return ToolResult("error", f"unknown worker {worker_id}", error_type="unknown_worker")
        if worker.result is not None:
            return self._result_tool(worker)
        spec = get_role_spec(worker.role)
        before = self.workspace.snapshot()
        result = SubagentRunner(workspace=self.workspace, root=self.root, tool_executor=self.tool_executor, model_router=self.model_router, config=self.config, session_events=self.session_events, worker=worker, role_spec=spec).run()
        after = self.workspace.snapshot()
        result.changed_files = sorted(set(result.changed_files) | set(self.workspace.changed_paths(before, after)))
        if result.status == "failed" and result.changed_files:
            result.status = "partial_success"
            result.stop_reason = "runtime_failed_with_side_effects"
        worker.result = result
        worker.status = result.status
        self._write_state(worker)
        (self.root / worker.worker_id / "result.json").write_text(json.dumps(result.__dict__, ensure_ascii=False, indent=2), encoding="utf-8")
        return self._result_tool(worker, result)

    def run_tool(self, name: str, args: dict) -> ToolResult:
        if name == "spawn_subagent":
            parsed = SpawnSubagentArgs.model_validate(args)
            return self.spawn(parsed.prompt, role=parsed.role, acceptance_criteria=list(parsed.acceptance_criteria), write_scope=list(parsed.write_scope))
        if name == "send_subagent_message":
            parsed = SendSubagentMessageArgs.model_validate(args)
            return self.send(parsed.worker_id, parsed.message)
        if name == "wait_subagent":
            parsed = WaitSubagentArgs.model_validate(args)
            return self.wait(parsed.worker_id)
        return ToolResult("denied", f"unknown subagent tool {name}", error_type="unknown_tool")

    def _result_tool(self, worker: WorkerRuntime, result=None) -> ToolResult:
        value = result or worker.result
        assert value is not None
        text = value.text
        if value.changed_files:
            text += "\nchanged_files:\n" + "\n".join(value.changed_files)
        if value.verification:
            text += "\nverification:\n" + json.dumps(value.verification, ensure_ascii=False)
        tool_status = "success" if value.status == "completed" else value.status
        return ToolResult(tool_status, text, artifacts=list(value.artifacts), changed_files=list(value.changed_files), error_type=None if value.status == "completed" else value.stop_reason, metadata={"worker_id": value.worker_id, "role": value.role, "worker_status": value.status, "write_scope": list(worker.write_scope), "verification": dict(value.verification), "tool_failures": list(value.tool_failures), "stop_reason": value.stop_reason})

    def _write_state(self, worker: WorkerRuntime) -> None:
        directory = self.root / worker.worker_id
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "task_state.json").write_text(json.dumps(worker.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    def _load_persisted_workers(self) -> None:
        """只恢复已持久化的任务事实；created/running 任务不会自动重跑。"""
        for directory in sorted(self.root.glob("worker-*")):
            state_path = directory / "task_state.json"
            if not state_path.is_file():
                continue
            try:
                payload = json.loads(state_path.read_text(encoding="utf-8"))
                role = str(payload["role"])
                worker = WorkerRuntime(
                    str(payload["worker_id"]),
                    str(payload["prompt"]),
                    role=role,
                    acceptance_criteria=list(payload["acceptance_criteria"]),
                    write_scope=list(payload.get("write_scope", [])),
                    model_profile=dict(payload.get("model_profile", {})),
                    parent_session_id=str(payload.get("parent_session_id", "")),
                    parent_run_id=str(payload.get("parent_run_id", "")),
                )
                worker.messages = list(payload.get("messages", []))
                worker.status = str(payload.get("status", "created"))
                result_payload = payload.get("result")
                if isinstance(result_payload, dict):
                    worker.result = WorkerResult(**result_payload)
                self.workers[worker.worker_id] = worker
            except (OSError, KeyError, TypeError, ValueError):
                # 当前协议不迁移旧 worker 状态；损坏状态只保留为目录审计事实。
                continue
