from __future__ import annotations

from pathlib import Path

from src.app.web_events import trace_events
from src.app.web_steps import build_reasoning_steps
from src.state.session import SessionStore


def build_session_turns(project_id: str, project_root: Path, session: dict) -> dict:
    runs_root = project_root / ".jcode" / "runs"
    history = list(session.get("history", []) or [])
    run_ids = _ordered_run_ids(session, history)
    turns = [_build_turn(run_id, runs_root, history) for run_id in run_ids]
    turns.extend(_orphan_history_turns(history))
    return {"project_id": project_id, "session_id": session.get("id", ""), "turns": turns}


def _ordered_run_ids(session: dict, history: list[dict]) -> list[str]:
    seen: set[str] = set()
    run_ids: list[str] = []
    for run_id in session.get("run_ids", []) or []:
        value = str(run_id or "").strip()
        if value and value not in seen:
            run_ids.append(value)
            seen.add(value)
    for item in history:
        value = str(item.get("run_id") or "").strip()
        if value and value not in seen:
            run_ids.append(value)
            seen.add(value)
    return run_ids


def _build_turn(run_id: str, runs_root: Path, history: list[dict]) -> dict:
    items = [item for item in history if item.get("run_id") == run_id]
    events = trace_events(runs_root / run_id)
    reasoning_steps, final_text = build_reasoning_steps(events, run_id=run_id)
    if not reasoning_steps:
        reasoning_steps = _fallback_steps(items, run_id)
    user_message = _first_content(items, "user")
    assistant_message = _last_content(items, "assistant")
    changed_files = _changed_files(events)
    if not final_text:
        final_text = assistant_message
    return {
        "run_id": run_id,
        "user_message": user_message,
        "assistant_message": assistant_message,
        "final_text": final_text,
        "reasoning_steps": reasoning_steps,
        "reasoning_text": reasoning_steps[0].get("reasoning_text", "") if reasoning_steps else "",
        "status": _status(events, assistant_message),
        "event_count": len(events),
        "step_count": len(reasoning_steps),
        "tool_count": sum(len(step.get("tool_calls", []) or []) for step in reasoning_steps),
        "changed_files": changed_files,
        "events": events,
    }


def _orphan_history_turns(history: list[dict]) -> list[dict]:
    turns: list[dict] = []
    for index, item in enumerate(history):
        if item.get("run_id") or item.get("role") == "tool":
            continue
        role = str(item.get("role") or "message")
        content = str(item.get("content") or "")
        turns.append(
            {
                "run_id": f"history-{index}",
                "user_message": content if role == "user" else "",
                "assistant_message": content if role == "assistant" else "",
                "final_text": content if role == "assistant" else "",
                "reasoning_steps": [],
                "reasoning_text": "",
                "status": "history",
                "event_count": 0,
                "step_count": 0,
                "tool_count": 0,
                "changed_files": [],
                "events": [],
            }
        )
    return turns


def _fallback_steps(items: list[dict], run_id: str) -> list[dict]:
    reasoning = ""
    context = ""
    for item in items:
        if item.get("role") == "assistant" and not reasoning:
            reasoning = str(item.get("reasoning") or "").strip()
            if not reasoning:
                content = str(item.get("content") or "")
                if "<reasoning>" in content and "</reasoning>" in content:
                    start = content.find("<reasoning>") + len("<reasoning>")
                    end = content.find("</reasoning>")
                    reasoning = content[start:end].strip()
        if item.get("kind") == "context_built":
            context = str(item.get("content") or "")
    if not reasoning and not context:
        return []
    return [
        {
            "step_id": f"{run_id}:1" if run_id else "step-1",
            "index": 1,
            "timestamp": items[0].get("created_at", "") if items else "",
            "end_timestamp": items[-1].get("created_at", "") if items else "",
            "status": "success",
            "reasoning_text": reasoning,
            "reasoning_summary": reasoning[:20] + ("…" if len(reasoning) > 20 else "") if reasoning else "",
            "context_text": context,
            "response_text": "",
            "error_text": "",
            "parsed_action": {},
            "tool_calls": [],
            "details": [],
            "duration_ms": None,
            "tool_count": 0,
        }
    ]


def _first_content(items: list[dict], role: str) -> str:
    for item in items:
        if item.get("role") == role:
            return str(item.get("content") or "")
    return ""


def _last_content(items: list[dict], role: str) -> str:
    for item in reversed(items):
        if item.get("role") == role:
            return str(item.get("content") or "")
    return ""


def _changed_files(events: list[dict]) -> list[str]:
    changed: list[str] = []
    known: set[str] = set()
    for event in events:
        for path in event.get("changed_files", []) or []:
            value = str(path)
            if value not in known:
                changed.append(value)
                known.add(value)
    return changed


def _status(events: list[dict], assistant_message: str) -> str:
    for event in reversed(events):
        if event.get("event") == "run_failed":
            return "failed"
        if event.get("event") == "run_aborted":
            return "stopped"
        if event.get("event") == "run_finished":
            status = str(event.get("status") or "")
            stop_reason = str(event.get("stop_reason") or "")
            if status == "completed":
                return "completed"
            if stop_reason:
                return "stopped"
    if assistant_message:
        return "completed"
    if events:
        return "incomplete"
    return "history"


def session_with_turns(project_id: str, project_root: Path, session_store: SessionStore, session_id: str) -> dict:
    session = session_store.load_requested(session_id, None, project_root)
    return build_session_turns(project_id, project_root, session)
