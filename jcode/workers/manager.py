from __future__ import annotations

import json
import uuid
from pathlib import Path

from jcode.tools.base import ToolResult
from jcode.tools.schemas import SendSubagentMessageArgs, SpawnSubagentArgs, WaitSubagentArgs
from jcode.workers.runtime import WorkerRuntime


class WorkerManager:
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

    def run_tool(self, name: str, args: dict) -> ToolResult:
        if name == "spawn_subagent":
            parsed = SpawnSubagentArgs.model_validate(args)
            worker_id = "worker-" + uuid.uuid4().hex[:8]
            worker = WorkerRuntime(worker_id, parsed.prompt)
            self.workers[worker_id] = worker
            worker_dir = self.root / worker_id
            worker_dir.mkdir(parents=True, exist_ok=True)
            payload = {"worker_id": worker_id, "status": worker.status, "prompt": parsed.prompt}
            (worker_dir / "task_state.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            if self.session_events:
                self.session_events.emit("subagent_spawned", worker_id=worker_id, prompt=parsed.prompt[:500])
            return ToolResult("success", f"spawned {worker_id}", metadata={"worker_id": worker_id, "worker_status": worker.status})
        if name == "send_subagent_message":
            parsed = SendSubagentMessageArgs.model_validate(args)
            worker = self.workers.get(parsed.worker_id)
            if worker is None:
                return ToolResult("error", f"unknown worker {parsed.worker_id}", error_type="unknown_worker")
            worker.send(parsed.message)
            if self.session_events:
                self.session_events.emit("subagent_message_sent", worker_id=parsed.worker_id, message=parsed.message[:500])
            return ToolResult("success", f"sent message to {parsed.worker_id}", metadata={"worker_id": parsed.worker_id, "worker_status": worker.status})
        if name == "wait_subagent":
            parsed = WaitSubagentArgs.model_validate(args)
            worker = self.workers.get(parsed.worker_id)
            if worker is None:
                return ToolResult("error", f"unknown worker {parsed.worker_id}", error_type="unknown_worker")
            result = worker.run()
            worker_dir = self.root / parsed.worker_id
            worker_dir.mkdir(parents=True, exist_ok=True)
            (worker_dir / "result.json").write_text(json.dumps(result.__dict__, ensure_ascii=False, indent=2), encoding="utf-8")
            (worker_dir / "trace.jsonl").write_text(json.dumps({"event": "subagent_completed", **result.__dict__}, ensure_ascii=False) + "\n", encoding="utf-8")
            if self.session_events:
                self.session_events.emit("subagent_completed", worker_id=parsed.worker_id, status=result.status)
            return ToolResult("success", result.text, artifacts=[str(worker_dir / "result.json")], metadata={"worker_id": parsed.worker_id, "worker_status": result.status})
        return ToolResult("denied", f"unknown subagent tool {name}", error_type="unknown_tool")
