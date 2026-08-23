from __future__ import annotations


def build_report(task_state, stop_reason: str, final_text: str, *, trace=None, session_id="", workers=None, memory=None, resume=None) -> dict:
    trace = list(trace or [])
    event_counts: dict[str, int] = {}
    for item in trace:
        event = str(item.get("event", ""))
        event_counts[event] = event_counts.get(event, 0) + 1
    return {
        "run_id": task_state.run_id,
        "task_id": task_state.task_id,
        "session_id": session_id,
        "status": task_state.status,
        "stop_reason": stop_reason,
        "steps": task_state.step_index,
        "tool_steps": task_state.tool_steps,
        "changed_files": list(task_state.changed_files),
        "failed_tools": list(task_state.failed_tools),
        "final_chars": len(final_text),
        "event_counts": event_counts,
        "workers": list(workers or []),
        "memory": dict(memory or {}),
        "resume": dict(resume or {}),
    }
