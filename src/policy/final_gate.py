from __future__ import annotations

from src.runtime.final_readiness import build_final_readiness


class FinalGate:
    """依据运行时结构化证据决定最终答案是否可以收口。"""

    def check(self, final_text: str, task_state, working_memory, *, session=None, workspace=None, context=None) -> dict:
        if not final_text.strip():
            decision = {"allowed": False, "action": "block", "reason": "empty_final", "reasons": [{"code": "empty_final", "severity": "hard"}], "message": "error: final answer is empty"}
            task_state.record_gate_decision(decision)
            return decision
        readiness = build_final_readiness(task_state, session, workspace, context=context)
        reasons = list(readiness.reasons)
        hard = [item for item in reasons if item.get("severity") == "hard"]
        if hard:
            decision = {"allowed": False, "action": "block", "reason": hard[0]["code"], "reasons": reasons, "message": "error: final gate blocked by unresolved runtime evidence"}
            task_state.record_gate_decision(decision)
            return decision
        soft = [item for item in reasons if item.get("severity") == "soft"]
        if soft:
            # 明确列出 todo 标识视为用户已收到未完成状态，不再重复打断收口。
            todo_ids = [str(item) for reason in soft if reason.get("code") == "unresolved_todo" for item in reason.get("evidence", {}).get("todo_ids", [])]
            if todo_ids and all(todo_id.lower() in final_text.lower() for todo_id in todo_ids):
                decision = {"allowed": True, "action": "allow_with_warning", "reason": "unresolved_todo_acknowledged", "reasons": reasons, "notice_count": 0, "message": ""}
                task_state.record_gate_decision(decision)
                return decision
            previous = getattr(task_state, "final_readiness_summary", {}) or {}
            previous_codes = {item.get("code") for item in previous.get("reasons", []) if isinstance(item, dict)}
            if previous.get("action") == "runtime_notice" and previous_codes.intersection({item.get("code") for item in soft}):
                decision = {"allowed": False, "action": "block", "reason": soft[0]["code"], "reasons": reasons, "notice_count": int(previous.get("notice_count", 1)) + 1, "message": "error: final gate requires runtime evidence before finishing"}
            else:
                decision = {"allowed": False, "action": "runtime_notice", "reason": soft[0]["code"], "reasons": reasons, "notice_count": 1, "message": "请先处理或说明以下运行状态：" + "；".join(item.get("message", "") for item in soft)}
            task_state.record_gate_decision(decision)
            return decision
        decision = {"allowed": True, "action": "allow", "reason": "ready", "reasons": [], "notice_count": 0, "message": ""}
        task_state.record_gate_decision(decision)
        return decision
