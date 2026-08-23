from __future__ import annotations

import json
import uuid
from pathlib import Path

from jcode.state.workspace import now_iso


class SessionStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def latest(self) -> str | None:
        files = sorted(self.root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        return files[0].stem if files else None

    def run_ids(self, session: dict) -> list[str]:
        return [str(run_id) for run_id in session.get("run_ids", []) if str(run_id).strip()]

    def latest_run_id(self, session: dict) -> str:
        run_ids = self.run_ids(session)
        return run_ids[-1] if run_ids else ""

    def load_requested(self, session_id: str | None, resume: str | None, workspace_root: Path) -> dict:
        selected = session_id or resume
        if selected == "latest":
            selected = self.latest()
        if selected:
            path = self.root / f"{selected}.json"
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
        return {
            "schema_version": 2,
            "id": session_id or f"{now_iso().replace(':', '').replace('-', '')}-{uuid.uuid4().hex[:6]}",
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "workspace_root": str(workspace_root),
            "history": [],
            "working_memory": {},
            "run_ids": [],
            "event_seq": 0,
        }

    def save(self, session: dict) -> Path:
        session.setdefault("schema_version", 2)
        session["updated_at"] = now_iso()
        path = self.root / f"{session['id']}.json"
        path.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")
        return path
