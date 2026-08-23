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

    def load_requested(self, session_id: str | None, resume: str | None, workspace_root: Path) -> dict:
        selected = session_id or resume
        if selected == "latest":
            selected = self.latest()
        if selected:
            path = self.root / f"{selected}.json"
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
        return {
            "id": session_id or f"{now_iso().replace(':', '').replace('-', '')}-{uuid.uuid4().hex[:6]}",
            "created_at": now_iso(),
            "workspace_root": str(workspace_root),
            "history": [],
            "working_memory": {},
        }

    def save(self, session: dict) -> Path:
        path = self.root / f"{session['id']}.json"
        path.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")
        return path
