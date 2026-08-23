from __future__ import annotations


def build_report(task_state, stop_reason: str, final_text: str) -> dict:
    return {
        "run_id": task_state.run_id,
        "task_id": task_state.task_id,
        "status": task_state.status,
        "stop_reason": stop_reason,
        "steps": task_state.step_index,
        "tool_steps": task_state.tool_steps,
        "final_chars": len(final_text),
    }
