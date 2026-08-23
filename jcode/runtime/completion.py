from __future__ import annotations

from jcode.evidence.summaries import build_report
from jcode.memory.consolidation import maintain_after_turn
from jcode.runtime.transitions import VALID_FINAL


def finish_run(agent, task_state, run_dir, final_text: str, stop_reason: str = VALID_FINAL) -> str:
    task_state.finish("completed" if stop_reason == VALID_FINAL else "stopped", stop_reason, final_text)
    memory_audit = maintain_after_turn(agent.memory_store, agent.working_memory, task_state.user_request, final_text)
    agent.run_store.append_trace(
        run_dir,
        "memory_maintained",
        task_state.run_id,
        **memory_audit,
    )
    agent.run_store.append_trace(run_dir, "run_finished", task_state.run_id, status=task_state.status, stop_reason=stop_reason)
    agent.session_events.emit("turn_finished", run_id=task_state.run_id, status=task_state.status, stop_reason=stop_reason)
    trace = agent.run_store.read_trace(run_dir)
    agent.run_store.write_report(
        run_dir,
        build_report(
            task_state,
            stop_reason,
            final_text,
            trace=trace,
            session_id=agent.session.get("id", ""),
            workers=agent.worker_manager.worker_refs(),
            memory=memory_audit,
            resume=agent.working_memory.resume_context,
        ),
    )
    agent.session["working_memory"] = agent.working_memory.to_dict()
    agent.session.setdefault("run_ids", []).append(task_state.run_id)
    agent.session_store.save(agent.session)
    agent.run_store.write_task_state(run_dir, task_state)
    return final_text
