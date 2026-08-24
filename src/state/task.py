from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from src.state.workspace import now_iso


@dataclass
class TaskState:
    run_id: str
    task_id: str
    user_request: str
    step_index: int = 0
    attempts: int = 0
    tool_steps: int = 0
    status: str = "running"
    stop_reason: str = ""
    last_action: dict = field(default_factory=dict)
    completed_steps: list[str] = field(default_factory=list)
    pending_next_step: str = ""
    changed_files: list[str] = field(default_factory=list)
    failed_tools: list[dict] = field(default_factory=list)
    final_answer: str = ""
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)

    @classmethod
    def create(cls, user_request: str) -> "TaskState":
        return cls(run_id=f"run-{uuid.uuid4().hex[:10]}", task_id=f"task-{uuid.uuid4().hex[:10]}", user_request=user_request)

    def to_dict(self) -> dict:
        return dict(self.__dict__)

    def record_tool(self, name: str, result) -> None:
        self.tool_steps += 1
        if result.changed_files:
            known = set(self.changed_files)
            for path in result.changed_files:
                if path not in known:
                    self.changed_files.append(path)
                    known.add(path)
        if result.status not in {"success", "ok"}:
            self.failed_tools.append(
                {"name": name, "status": result.status, "error_type": result.error_type}
            )
        self.updated_at = now_iso()

    def finish(self, status: str, stop_reason: str, final_answer: str = "") -> None:
        self.status = status
        self.stop_reason = stop_reason
        self.final_answer = final_answer
        self.updated_at = now_iso()
