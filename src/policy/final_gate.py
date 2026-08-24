from __future__ import annotations

from src.runtime.plan import plan_artifact_ready, runtime_mode_name, runtime_mode_plan_path


class FinalGate:
    def check(self, final_text: str, task_state, working_memory, *, session=None, workspace=None) -> dict:
        if not final_text.strip():
            return {"allowed": False, "reason": "empty_final", "message": "error: final answer is empty"}
        lowered = final_text.strip().lower()
        failure_terms = ("fail", "error", "blocked", "unable", "could not", "partial", "失败", "错误", "无法", "阻塞", "部分")
        if task_state.failed_tools and not any(term in lowered for term in failure_terms):
            return {
                "allowed": False,
                "reason": "unaddressed_failed_tools",
                "message": "error: final answer must mention unresolved tool failures before finishing",
            }
        if session is not None and workspace is not None and runtime_mode_name(session) == "plan":
            if not plan_artifact_ready(session, workspace, runtime_mode_plan_path(session)):
                plan_path = runtime_mode_plan_path(session)
                return {
                    "allowed": False,
                    "reason": "plan_artifact_not_ready",
                    "message": f"error: plan mode requires a written, non-empty plan artifact before final answer: {plan_path}",
                }
        return {"allowed": True, "reason": "ready", "message": ""}
