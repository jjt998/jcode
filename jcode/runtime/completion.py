from __future__ import annotations

from jcode.evidence.summaries import build_report
from jcode.runtime.transitions import VALID_FINAL


def finish_run(agent, task_state, run_dir, final_text: str, stop_reason: str = VALID_FINAL) -> str:
    task_state.status = "completed" if stop_reason == VALID_FINAL else "stopped"
    task_state.stop_reason = stop_reason
    agent.run_store.append_trace(run_dir, "run_finished", task_state.run_id, status=task_state.status, stop_reason=stop_reason)
    agent.run_store.write_report(run_dir, build_report(task_state, stop_reason, final_text))
    agent.session["working_memory"] = agent.working_memory.to_dict()
    agent.session_store.save(agent.session)
    return final_text
