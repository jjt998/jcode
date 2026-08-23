from __future__ import annotations

import json
from pathlib import Path

from jcode.evidence.events import event_record


class SessionEventBus:
    def __init__(self, path: Path, run_id: str = ""):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id

    def emit(self, event: str, **payload) -> None:
        run_id = str(payload.pop("run_id", self.run_id) or self.run_id)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event_record(event, run_id, **payload), ensure_ascii=False) + "\n")
