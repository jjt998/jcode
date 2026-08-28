from __future__ import annotations

from src.state.workspace import now_iso

def append_history(session: dict, role: str, content: str, **extra) -> dict:
    session["event_seq"] = int(session.get("event_seq", 0)) + 1
    run_id = str(extra.pop("run_id", "")) if "run_id" in extra else ""
    turn_id = str(extra.pop("turn_id", "")) if "turn_id" in extra else ""
    if not turn_id:
        turn_id = run_id
    item = {
        "role": role,
        "content": content,
        "event_id": f"event-{session['event_seq']:06d}",
        "created_at": now_iso(),
        "run_id": run_id,
        "turn_id": turn_id,
        **extra,
    }
    session.setdefault("history", []).append(item)
    return item
