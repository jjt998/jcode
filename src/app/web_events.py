from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        row.setdefault("_source", path.name)
        row.setdefault("_index", index)
        rows.append(row)
    return rows


def trace_events(run_dir: Path) -> list[dict]:
    return load_jsonl(run_dir / "trace.jsonl")


def session_events(session_events_path: Path) -> list[dict]:
    return load_jsonl(session_events_path)


def sse_pack(event: str, data: dict, event_id: str | None = None) -> str:
    lines: list[str] = []
    if event_id:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    payload = json.dumps(data, ensure_ascii=False)
    for line in payload.splitlines() or ["{}"]:
        lines.append(f"data: {line}")
    return "\n".join(lines) + "\n\n"


def event_name(row: dict) -> str:
    return str(row.get("event") or "message")


def indexed_events(rows: Iterable[dict], *, prefix: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for index, row in enumerate(rows):
        source = str(row.get("_source") or "event")
        event_id = f"{prefix}:{source}:{row.get('_index', index)}"
        payload = dict(row)
        payload["event_id"] = event_id
        events.append((event_id, payload))
    return events
