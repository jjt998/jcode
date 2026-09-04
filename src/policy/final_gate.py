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
        pending_todos = _pending_todos(session)
        missing_todos = [todo_id for todo_id in pending_todos if todo_id.lower() not in lowered]
        if missing_todos:
            return {
                "allowed": False,
                "reason": "unaddressed_pending_todos",
                "message": "error: final answer must identify unfinished todos before finishing: " + ", ".join(missing_todos),
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


def _pending_todos(session: dict | None) -> list[str]:
    """提取 session 中尚未完成的 todo 标识。"""
    if not isinstance(session, dict):
        return []
    ledger = session.get("todo_ledger", {})
    items = ledger.get("items", []) if isinstance(ledger, dict) else []
    if not isinstance(items, list):
        return []
    return [
        str(item.get("todo_id", "")).strip()
        for item in items
        if isinstance(item, dict)
        and str(item.get("todo_id", "")).strip()
        and str(item.get("status", "pending")) != "completed"
    ]
