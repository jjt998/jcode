from __future__ import annotations


class FinalGate:
    def check(self, final_text: str, task_state, working_memory) -> dict:
        if not final_text.strip():
            return {"allowed": False, "reason": "empty_final", "message": "error: final answer is empty"}
        return {"allowed": True, "reason": "ready", "message": ""}
