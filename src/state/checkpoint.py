from __future__ import annotations

import json
from pathlib import Path

from src.state.workspace import Workspace, now_iso
from src.tools.workspace import freshness

SCHEMA_VERSION = 4


class CheckpointManager:
    workspace: Workspace
    path: Path

    def __init__(self, run_dir: Path, workspace: Workspace):
        self.workspace = workspace
        self.path = run_dir / "checkpoint.json"

    def create(self, session: dict, task_state, working_memory, worker_refs=None, resumable=True) -> dict:
        # 基线同时用于恢复比较和指纹计算，只扫描一次工作区。
        workspace_baseline = self.workspace.baseline()
        workspace_fingerprint = self.workspace.fingerprint_from_baseline(workspace_baseline)
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
            "unresolved_tool_failures": list(task_state.unresolved_tool_failures),
            "resolved_tool_failures": list(task_state.resolved_tool_failures),
            "verification": dict(task_state.verification),
            "final_readiness_summary": dict(task_state.final_readiness_summary),
            "agent_events": list(task_state.agent_events),
            "harness_events": list(task_state.harness_events),
            "interventions": list(task_state.interventions),
            "agent_rerun_count": task_state.agent_rerun_count,
            "agent_rerun_budget": task_state.agent_rerun_budget,
            "agent_quality": dict(task_state.agent_quality),
            "harness_quality": dict(task_state.harness_quality),
            "assurance": dict(task_state.assurance),
            "finalization": dict(task_state.finalization),
            "requirement_ledger": list(task_state.requirement_ledger),
            "provider_continuation": dict(task_state.provider_continuation),
            "output_continuation_count": task_state.output_continuation_count,
            "partial_response_parts": list(task_state.partial_response_parts),
            "working_memory": working_memory.to_dict(),
            "todo_ledger": dict(session.get("todo_ledger", {})),
            "workspace_fingerprint": workspace_fingerprint,
            "workspace_baseline": workspace_baseline,
            "execution_fingerprint": dict(task_state.model_profile),
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


def evaluate_checkpoint_path(path: Path, workspace: Workspace, *, workspace_baseline: dict | None = None) -> tuple[str, dict]:
    if not path.exists():
        return "no_checkpoint", {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return evaluate_checkpoint_data(data, workspace, workspace_baseline=workspace_baseline)


def evaluate_checkpoint_data(data: dict, workspace: Workspace, *, workspace_baseline: dict | None = None) -> tuple[str, dict]:
    """检查 checkpoint 格式、可恢复性、工作区指纹和文件 freshness。"""
    if data.get("schema_version") != SCHEMA_VERSION:
        return "schema_mismatch", data
    if not data.get("resumable", False):
        return "checkpoint_not_resumable", data

    current_fingerprint = workspace.fingerprint_from_baseline(workspace_baseline) if workspace_baseline is not None else workspace.fingerprint()
    checkpoint_fingerprint = str(data.get("workspace_fingerprint", "") or "")
    if checkpoint_fingerprint != current_fingerprint:
        return "workspace_mismatch", data

    stale_paths = _stale_paths(data, workspace)
    if stale_paths:
        payload = dict(data)
        payload["stale_paths"] = stale_paths
        return "partial_stale", payload

    return "full_valid", data

def _stale_paths(data: dict, workspace: Workspace) -> list[str]:
    """从checkpoint的最近读过但 freshness 已变化的文件。"""
    stale = []
    memory = dict(data.get("working_memory", {}) or {})
    files = dict(memory.get("files", {}) or {})
    saved_freshness = dict(files.get("freshness", {}) or {})
    for relpath in files.get("hot", []) or []:
        relpath = str(relpath).strip()
        if not relpath:
            continue
        try:
            current = freshness(workspace.resolve_path(relpath))
        except Exception:
            stale.append(relpath)
            continue
        if saved_freshness.get(relpath) != current:
            stale.append(relpath)
    return stale
