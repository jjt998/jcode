from __future__ import annotations

import json
from pathlib import Path

from jcode.evidence.events import event_record


class RunStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def start_run(self, task_state) -> Path:
        run_dir = self.run_dir(task_state.run_id)
        (run_dir / "artifacts").mkdir(parents=True, exist_ok=True)
        self.write_task_state(run_dir, task_state)
        return run_dir

    def run_dir(self, run_id: str) -> Path:
        return self.root / str(run_id)

    def write_artifact(self, run_dir: Path, name: str, content: str) -> str:
        safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in name)
        path = run_dir / "artifacts" / safe
        path.write_text(content, encoding="utf-8")
        return f"artifacts/{safe}"

    def write_task_state(self, run_dir: Path, task_state) -> None:
        (run_dir / "task_state.json").write_text(json.dumps(task_state.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    def append_trace(self, run_dir: Path, event: str, run_id: str, **payload) -> None:
        with (run_dir / "trace.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event_record(event, run_id, **payload), ensure_ascii=False) + "\n")

    def read_trace(self, run_dir: Path) -> list[dict]:
        path = run_dir / "trace.jsonl"
        if not path.exists():
            return []
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return rows

    def write_report(self, run_dir: Path, report: dict) -> None:
        (run_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
