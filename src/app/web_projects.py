from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path

from src.state.workspace import now_iso


@dataclass(frozen=True)
class WebProject:
    id: str
    name: str
    root: Path
    created_at: str
    updated_at: str

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "root": str(self.root),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "has_git": (self.root / ".git").exists(),
        }


class WebProjectStore:
    path: Path
    default_root: Path

    def __init__(self, path: Path, default_root: Path):
        self.path = path
        self.default_root = default_root.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_default_project()

    def list_projects(self) -> list[WebProject]:
        raw = self._read()
        projects = [self._from_dict(item) for item in raw.get("projects", []) if isinstance(item, dict)]
        return sorted(projects, key=lambda item: item.updated_at, reverse=True)

    def get(self, project_id: str) -> WebProject:
        for project in self.list_projects():
            if project.id == project_id:
                return project
        raise KeyError(project_id)

    def create(self, root: str, name: str | None = None) -> WebProject:
        project_root = Path(root).expanduser().resolve()
        if not project_root.exists() or not project_root.is_dir():
            raise ValueError(f"project root is not a directory: {root}")
        now = now_iso()
        project = WebProject(
            id=f"proj-{uuid.uuid4().hex[:10]}",
            name=str(name or project_root.name or "workspace"),
            root=project_root,
            created_at=now,
            updated_at=now,
        )
        raw = self._read()
        projects = [item for item in raw.get("projects", []) if item.get("root") != str(project_root)]
        projects.insert(0, project.to_dict())
        self._write({"projects": projects})
        return project

    def touch(self, project_id: str) -> None:
        raw = self._read()
        changed = False
        for item in raw.get("projects", []):
            if item.get("id") == project_id:
                item["updated_at"] = now_iso()
                changed = True
        if changed:
            self._write(raw)

    def _ensure_default_project(self) -> None:
        raw = self._read()
        projects = raw.get("projects", [])
        default_root = str(self.default_root)
        if any(item.get("id") == "default" for item in projects if isinstance(item, dict)):
            return
        for item in projects:
            if isinstance(item, dict) and item.get("root") == default_root:
                item["id"] = "default"
                item.setdefault("name", self.default_root.name or "workspace")
                item.setdefault("created_at", now_iso())
                item["updated_at"] = now_iso()
                self._write({"projects": projects})
                return
        now = now_iso()
        projects.insert(
            0,
            {
                "id": "default",
                "name": self.default_root.name or "workspace",
                "root": default_root,
                "created_at": now,
                "updated_at": now,
                "has_git": (self.default_root / ".git").exists(),
            },
        )
        self._write({"projects": projects})

    def _read(self) -> dict:
        if not self.path.exists():
            return {"projects": []}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"projects": []}

    def _write(self, raw: dict) -> None:
        self.path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _from_dict(raw: dict) -> WebProject:
        return WebProject(
            id=str(raw.get("id") or ""),
            name=str(raw.get("name") or "workspace"),
            root=Path(str(raw.get("root") or ".")).resolve(),
            created_at=str(raw.get("created_at") or ""),
            updated_at=str(raw.get("updated_at") or ""),
        )
