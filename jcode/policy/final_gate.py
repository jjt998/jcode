from __future__ import annotations


class FinalGate:
    def check(self, final_text: str, task_state, working_memory) -> dict:
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
        return {"allowed": True, "reason": "ready", "message": ""}
