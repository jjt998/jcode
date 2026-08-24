from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class Workspace:
    root: Path

    @classmethod
    def build(cls, cwd: str | Path) -> "Workspace":
        return cls(Path(cwd).resolve())

    def resolve_path(self, value: str | Path) -> Path:
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = self.root / candidate
        resolved = candidate.resolve()
        if os.path.commonpath([str(self.root), str(resolved)]) != str(self.root):
            raise ValueError(f"path escapes workspace: {value}")
        return resolved

    def relpath(self, path: str | Path) -> str:
        return str(Path(path).resolve().relative_to(self.root)).replace("\\", "/")

    def fingerprint(self) -> str:
        h = hashlib.sha256()
        h.update(str(self.root).encode("utf-8"))
        for path in sorted(self.root.glob("*"))[:200]:
            if path.name == ".jcode":
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            h.update(f"{path.name}:{int(stat.st_mtime)}:{stat.st_size}".encode("utf-8"))
        return h.hexdigest()[:16]

    def snapshot(self) -> dict[str, tuple[int, int]]:
        items: dict[str, tuple[int, int]] = {}
        for path in self.root.rglob("*"):
            if ".jcode" in path.parts or not path.is_file():
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            items[self.relpath(path)] = (int(stat.st_mtime_ns), int(stat.st_size))
        return items
