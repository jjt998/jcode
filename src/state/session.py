from __future__ import annotations

import json
import uuid
import os
from pathlib import Path

from src.state.workspace import now_iso
from src.runtime.errors import ArtifactWriteFailureError


class SessionStore:
    root: Path

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
                session = json.loads(path.read_text(encoding="utf-8"))
                if int(session.get("schema_version", 0) or 0) != 5:
                    raise ValueError("session schema is incompatible with native tool calling; start a new session")
                return session
        return {
            "schema_version": 5,
            "id": session_id or f"{now_iso().replace(':', '').replace('-', '')}-{uuid.uuid4().hex[:6]}",
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "workspace_root": str(workspace_root),
            "history": [],
            "working_memory": {},
            "run_ids": [],
            "event_seq": 0,
            "runtime_mode": {"mode": "default"},
            "active_model_profile": "",
            "model_switches": [],
        }

    def save(self, session: dict) -> Path:
        if int(session.get("schema_version", 0) or 0) != 5:
            raise ValueError("session schema must be 5")
        candidate = dict(session)
        candidate["updated_at"] = now_iso()
        path = self.root / f"{candidate['id']}.json"
        temp = path.with_suffix(path.suffix + f".{uuid.uuid4().hex}.tmp")
        try:
            with temp.open("w", encoding="utf-8") as fh:
                json.dump(candidate, fh, ensure_ascii=False, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(temp, path)
        except OSError as exc:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
            raise ArtifactWriteFailureError(str(exc)) from exc
        session.clear()
        session.update(candidate)
        return path
