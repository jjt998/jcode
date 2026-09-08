from __future__ import annotations

from pathlib import Path
import hashlib
import json

from src.state.checkpoint import evaluate_checkpoint_path
from src.runtime.plan import runtime_mode_name, runtime_mode_plan_path, runtime_mode_state


def build_execution_fingerprint(model_snapshot: dict, tool_definitions: list[dict] | None = None) -> dict:
    """生成当前 Provider、模型、推理配置和工具协议的可比较指纹。"""
    tools = list(tool_definitions or [])
    tool_schema_hash = hashlib.sha256(json.dumps(tools, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]
    return {**dict(model_snapshot or {}), "tool_schema_hash": tool_schema_hash, "runtime_protocol_version": "jcode-runtime-v1", "context_policy_version": "9.5"}


def _changed_paths(saved: dict, current: dict) -> list[str]:
    saved_files = dict(saved.get("files", {}) or {}) if isinstance(saved, dict) else {}
    current_files = dict(current.get("files", {}) or {}) if isinstance(current, dict) else {}
    return sorted(path for path in set(saved_files) | set(current_files) if saved_files.get(path) != current_files.get(path))


def build_resume_context(*, session: dict, session_store, run_store, workspace, resume_requested: str | None, execution_fingerprint: dict | None = None) -> dict:
    run_id = session_store.latest_run_id(session)
    run_dir = run_store.run_dir(run_id) if run_id else Path()
    checkpoint_path = run_dir / "checkpoint.json" if run_id else Path()
    status, checkpoint = evaluate_checkpoint_path(checkpoint_path, workspace) if run_id else ("no_checkpoint", {})
    current_baseline = workspace.baseline()
    saved_baseline = checkpoint.get("workspace_baseline", {}) if isinstance(checkpoint, dict) else {}
    changed_paths = _changed_paths(saved_baseline, current_baseline) if saved_baseline else ([] if str(checkpoint.get("workspace_fingerprint", "")) == workspace.fingerprint() else ["."])
    saved_execution = dict(checkpoint.get("execution_fingerprint", {}) or {}) if isinstance(checkpoint, dict) else {}
    current_execution = dict(execution_fingerprint or {})
    execution_changes = {key: {"checkpoint": saved_execution.get(key), "current": current_execution.get(key)} for key in set(saved_execution) | set(current_execution) if saved_execution.get(key) != current_execution.get(key)}
    continuation_items = list((checkpoint.get("provider_continuation", {}) or {}).get("items", []) or []) if isinstance(checkpoint, dict) else []
    continuation_present = bool(continuation_items)
    continuation_compatible = continuation_present and not execution_changes and not changed_paths
    return {
        "session_id": str(session.get("id", "")),
        "resume_requested": str(resume_requested or ""),
        "history_items": len(session.get("history", [])),
        "run_ids": session_store.run_ids(session),
        "latest_run_id": run_id,
        "checkpoint_status": status,
        "checkpoint_path": str(checkpoint_path) if run_id else "",
        "checkpoint_created_at": str(checkpoint.get("created_at", "")),
        "checkpoint_step_index": int(checkpoint.get("step_index", 0) or 0),
        "checkpoint_stop_reason": str(checkpoint.get("stop_reason", "")),
        "changed_files": list(checkpoint.get("changed_files", []) or []),
        "workspace_fingerprint": workspace.fingerprint(),
        "workspace_baseline": current_baseline,
        "checkpoint_workspace_fingerprint": str(checkpoint.get("workspace_fingerprint", "")),
        "workspace_mismatch": bool(changed_paths),
        "changed_paths": changed_paths,
        "stale_paths": sorted(set(checkpoint.get("stale_paths", []) or []) | set(changed_paths)),
        "execution_fingerprint": current_execution,
        "checkpoint_execution_fingerprint": saved_execution,
        "execution_changes": execution_changes,
        "execution_mismatch": bool(execution_changes),
        "continuation": {"present": continuation_present, "compatible": continuation_compatible},
        "action": "continuation_incompatible" if continuation_present and not continuation_compatible else ("start_new_run_with_reread" if changed_paths else "start_new_run"),
        "session_continuation": bool(run_id),
        "runtime_mode": runtime_mode_name(session),
        "plan_topic": str(runtime_mode_state(session).get("topic", "") or ""),
        "plan_path": runtime_mode_plan_path(session),
    }
