from __future__ import annotations

from pathlib import Path

from jcode.state.checkpoint import evaluate_checkpoint_path


def build_resume_context(*, session: dict, session_store, run_store, workspace, resume_requested: str | None) -> dict:
    run_id = session_store.latest_run_id(session)
    run_dir = run_store.run_dir(run_id) if run_id else Path()
    checkpoint_path = run_dir / "checkpoint.json" if run_id else Path()
    status, checkpoint = evaluate_checkpoint_path(checkpoint_path, workspace) if run_id else ("no_checkpoint", {})
    changed_files = list(checkpoint.get("changed_files", []) or [])
    checkpoint_fingerprint = str(checkpoint.get("workspace_fingerprint", ""))
    current_fingerprint = workspace.fingerprint()
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
        "changed_files": changed_files,
        "workspace_fingerprint": current_fingerprint,
        "checkpoint_workspace_fingerprint": checkpoint_fingerprint,
        "workspace_mismatch": bool(checkpoint_fingerprint and checkpoint_fingerprint != current_fingerprint),
    }
