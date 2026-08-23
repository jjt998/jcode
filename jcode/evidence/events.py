from __future__ import annotations

from jcode.state.workspace import now_iso


def event_record(event: str, run_id: str, **payload) -> dict:
    return {"event": event, "created_at": now_iso(), "run_id": run_id, **payload}
