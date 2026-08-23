from __future__ import annotations

import json
from pathlib import Path

from jcode.state.workspace import Workspace, now_iso

SCHEMA_VERSION = 1


class CheckpointManager:
    workspace: Workspace
    path: Path

    def __init__(self, run_dir: Path, workspace: Workspace):
        self.workspace = workspace
        self.path = run_dir / "checkpoint.json"

    def create(self, session: dict, task_state, working_memory, worker_refs=None, resumable=True) -> dict:
        data = {
            "schema_version": SCHEMA_VERSION,
            "session_id": session.get("id", ""),
            "run_id": task_state.run_id,
            "task_id": task_state.task_id,
            "task_goal": working_memory.task_goal,
            "step_index": task_state.step_index,
            "last_action": task_state.last_action,
            "completed_steps": list(task_state.completed_steps),
            "pending_next_step": task_state.pending_next_step,
            "status": task_state.status,
            "stop_reason": task_state.stop_reason,
            "changed_files": list(task_state.changed_files),
            "recent_files": list(working_memory.recent_files),
            "file_freshness": dict(working_memory.file_freshness),
            "working_memory": working_memory.to_dict(),
            "workspace_fingerprint": self.workspace.fingerprint(),
            "worker_refs": list(worker_refs or []),
            "resumable": bool(resumable),
            "created_at": now_iso(),
        }
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return data

    def evaluate(self) -> tuple[str, dict]:
        if not self.path.exists():
            return "no_checkpoint", {}
        data = json.loads(self.path.read_text(encoding="utf-8"))
        return evaluate_checkpoint_data(data, self.workspace)


def evaluate_checkpoint_path(path: Path, workspace: Workspace) -> tuple[str, dict]:
    if not path.exists():
        return "no_checkpoint", {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return evaluate_checkpoint_data(data, workspace)


def evaluate_checkpoint_data(data: dict, workspace: Workspace) -> tuple[str, dict]:
    if data.get("schema_version") != SCHEMA_VERSION:
        return "schema_mismatch", data
    if not data.get("resumable", False):
        return "checkpoint_not_resumable", data
    if data.get("workspace_fingerprint") != workspace.fingerprint():
        return "workspace_mismatch", data
    return "full_valid", data
