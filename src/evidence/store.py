from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
from src.state.workspace import now_iso
from src.evidence.events import event_record
from src.runtime.errors import ArtifactIntegrityError, ArtifactUnavailableError, ArtifactWriteFailureError


class RunStore:
    root: Path
    workspace_root: Path

    def __init__(self, root: Path, workspace_root: Path | None = None):
        self.root = Path(root).resolve()
        # artifact 引用要由 workspace 根目录解析，不能以单次 run 目录为相对基准。
        self.workspace_root = (Path(workspace_root).resolve() if workspace_root else self.root.parent.parent)
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
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        try:
            with temp.open("w", encoding="utf-8") as fh:
                fh.write(content)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(temp, path)
        except OSError as exc:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
            raise ArtifactWriteFailureError(str(exc)) from exc
        # 返回 workspace-relative 路径，模型后续可直接交给 read_file。
        return str(path.resolve().relative_to(self.workspace_root)).replace("\\", "/")

    def write_history_artifact(self, run_dir: Path, sequence: int, history: list[dict]) -> dict:
        """写入不可覆盖的压缩前 History 副本并返回校验引用。"""
        content = json.dumps(history, ensure_ascii=False, indent=2)
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        name = f"history-before-compact-{int(sequence):06d}-{digest[:12]}.json"
        path = run_dir / "artifacts" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if path.exists() and path.read_text(encoding="utf-8") != content:
                raise ArtifactWriteFailureError("history artifact collision")
            if not path.exists():
                temp = path.with_suffix(path.suffix + ".tmp")
                with temp.open("w", encoding="utf-8") as fh:
                    fh.write(content)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(temp, path)
        except ArtifactWriteFailureError:
            raise
        except OSError as exc:
            raise ArtifactWriteFailureError(str(exc)) from exc
        return {"path": str(path.resolve().relative_to(self.workspace_root)).replace("\\", "/"), "sha256": digest, "bytes": len(content.encode("utf-8")), "sequence": int(sequence), "created_at": now_iso()}

    def read_verified_artifact(self, ref: dict) -> list[dict]:
        path = self.workspace_root / str(ref.get("path", ""))
        if not path.exists():
            raise ArtifactUnavailableError(str(path))
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ArtifactUnavailableError(str(path)) from exc
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if digest != str(ref.get("sha256", "")):
            raise ArtifactIntegrityError("artifact sha256 mismatch")
        try:
            value = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ArtifactIntegrityError("artifact JSON is invalid") from exc
        if not isinstance(value, list):
            raise ArtifactIntegrityError("history artifact must contain an array")
        return value

    def write_audit(self, run_dir: Path, name: str, payload: dict) -> str:
        """写入单份权威审计快照，并返回 workspace 相对引用。"""
        return self.write_artifact(run_dir, name, json.dumps(payload, ensure_ascii=False, indent=2))

    def write_task_state(self, run_dir: Path, task_state) -> None:
        self._write_text_atomic(run_dir / "task_state.json", json.dumps(task_state.to_dict(), ensure_ascii=False, indent=2))

    def append_trace(self, run_dir: Path, event: str, run_id: str, **payload) -> None:
        try:
            with (run_dir / "trace.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(event_record(event, run_id, **payload), ensure_ascii=False) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
        except OSError as exc:
            raise ArtifactWriteFailureError(str(exc)) from exc

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
        self._write_text_atomic(run_dir / "report.json", json.dumps(report, ensure_ascii=False, indent=2))

    @staticmethod
    def _write_text_atomic(path: Path, content: str) -> None:
        temp = path.with_suffix(path.suffix + ".tmp")
        try:
            with temp.open("w", encoding="utf-8") as fh:
                fh.write(content)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(temp, path)
        except OSError as exc:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
            raise ArtifactWriteFailureError(str(exc)) from exc
