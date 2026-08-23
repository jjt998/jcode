from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from jcode.state.workspace import now_iso


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
    created_at: str = field(default_factory=now_iso)

    @classmethod
    def create(cls, user_request: str) -> "TaskState":
        return cls(run_id=f"run-{uuid.uuid4().hex[:10]}", task_id=f"task-{uuid.uuid4().hex[:10]}", user_request=user_request)

    def to_dict(self) -> dict:
        return dict(self.__dict__)
