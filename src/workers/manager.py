from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from src.tools.base import ToolResult
from src.tools.schemas import SendSubagentMessageArgs, SpawnSubagentArgs, WaitSubagentArgs
from src.workers.runtime import WorkerRuntime

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

    def worker_refs(self) -> list[str]:
        return sorted(self.workers)

    def spawn(self, prompt: str, *, subagent_type: str = "worker", write_scope: list[str] | None = None) -> ToolResult:
        subagent_type = _clean_subagent_type(subagent_type)
        parsed = SpawnSubagentArgs.model_validate(
            {"prompt": prompt, "subagent_type": subagent_type, "write_scope": list(write_scope or [])}
        )
        worker_id = "worker-" + uuid.uuid4().hex[:8]
        worker = WorkerRuntime(worker_id, parsed.prompt, subagent_type=parsed.subagent_type, write_scope=list(parsed.write_scope))
        self.workers[worker_id] = worker
        worker_dir = self.root / worker_id
        worker_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "worker_id": worker_id,
            "status": worker.status,
            "prompt": parsed.prompt,
            "subagent_type": worker.subagent_type,
            "write_scope": list(worker.write_scope),
        }
        (worker_dir / "task_state.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        if self.session_events:
            self.session_events.emit(
                "subagent_spawned",
                worker_id=worker_id,
                prompt=parsed.prompt[:500],
                subagent_type=worker.subagent_type,
                write_scope=list(worker.write_scope),
            )
        return ToolResult(
            "success",
            f"spawned {worker_id}",
            metadata={
                "worker_id": worker_id,
                "worker_status": worker.status,
                "subagent_type": worker.subagent_type,
                "write_scope": list(worker.write_scope),
            },
        )

    def send(self, worker_id: str, message: str) -> ToolResult:
        worker = self.workers.get(worker_id)
        if worker is None:
            return ToolResult("error", f"unknown worker {worker_id}", error_type="unknown_worker")
        worker.send(message)
        if self.session_events:
            self.session_events.emit("subagent_message_sent", worker_id=worker_id, message=message[:500])
        return ToolResult("success", f"sent message to {worker_id}", metadata={"worker_id": worker_id, "worker_status": worker.status})

    def wait(self, worker_id: str) -> ToolResult:
        worker = self.workers.get(worker_id)
        if worker is None:
            return ToolResult("error", f"unknown worker {worker_id}", error_type="unknown_worker")
        result = worker.run()
        worker_dir = self.root / worker_id
        worker_dir.mkdir(parents=True, exist_ok=True)
        (worker_dir / "result.json").write_text(json.dumps(result.__dict__, ensure_ascii=False, indent=2), encoding="utf-8")
        (worker_dir / "trace.jsonl").write_text(json.dumps({"event": "subagent_completed", **result.__dict__}, ensure_ascii=False) + "\n", encoding="utf-8")
        if self.session_events:
            self.session_events.emit("subagent_completed", worker_id=worker_id, status=result.status, subagent_type=worker.subagent_type)
        return ToolResult("success", result.text, artifacts=[str(worker_dir / "result.json")], metadata={"worker_id": worker_id, "worker_status": result.status, "subagent_type": worker.subagent_type, "write_scope": list(worker.write_scope)})

    def run_tool(self, name: str, args: dict) -> ToolResult:
        if name == "spawn_subagent":
            parsed = SpawnSubagentArgs.model_validate(args)
            return self.spawn(parsed.prompt, subagent_type=parsed.subagent_type, write_scope=list(parsed.write_scope))
        if name == "send_subagent_message":
            parsed = SendSubagentMessageArgs.model_validate(args)
            return self.send(parsed.worker_id, parsed.message)
        if name == "wait_subagent":
            parsed = WaitSubagentArgs.model_validate(args)
            return self.wait(parsed.worker_id)
        return ToolResult("denied", f"unknown subagent tool {name}", error_type="unknown_tool")


def _clean_subagent_type(value: str) -> str:
    subagent_type = str(value or "worker").strip()
    if subagent_type not in {"worker", "Explore"}:
        raise ValueError("subagent_type must be worker or Explore")
    return subagent_type
