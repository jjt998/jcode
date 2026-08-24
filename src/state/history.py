from __future__ import annotations

from src.state.workspace import now_iso

def append_history(session: dict, role: str, content: str, **extra) -> dict:
    session["event_seq"] = int(session.get("event_seq", 0)) + 1
    item = {
        "role": role,
        "content": content,
        "event_id": f"event-{session['event_seq']:06d}",
        "created_at": now_iso(),
        **extra,
    }
    session.setdefault("history", []).append(item)
    return item
