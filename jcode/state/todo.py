from __future__ import annotations

from dataclasses import dataclass, field

from jcode.state.workspace import now_iso


@dataclass
class TodoItem:
    todo_id: str
    content: str
    status: str = "pending"
    priority: str = "normal"
    note: str = ""
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class TodoLedger:
    items: list[TodoItem] = field(default_factory=list)
    next_index: int = 1

    @classmethod
    def from_dict(cls, data: dict | None) -> "TodoLedger":
        payload = dict(data or {}) if isinstance(data, dict) else {}
        items = []
        raw_items = payload.get("items", [])
        if isinstance(raw_items, list):
            for raw in raw_items:
                if isinstance(raw, dict):
                    items.append(
                        TodoItem(
                            todo_id=str(raw.get("todo_id", raw.get("id", "")) or ""),
                            content=str(raw.get("content", "")),
                            status=str(raw.get("status", "pending")),
                            priority=str(raw.get("priority", "normal")),
                            note=str(raw.get("note", "")),
                            created_at=str(raw.get("created_at", now_iso())),
                            updated_at=str(raw.get("updated_at", now_iso())),
                        )
                    )
        next_index = int(payload.get("next_index", 1) or 1)
        if next_index < 1:
            next_index = 1
        max_index = 0
        for item in items:
            suffix = str(item.todo_id).split("_")[-1]
            if suffix.isdigit():
                max_index = max(max_index, int(suffix))
        if max_index >= next_index:
            next_index = max_index + 1
        return cls(items=items, next_index=next_index)

    def to_dict(self) -> dict:
        return {
            "schema": "jcode.todo_ledger.v1",
            "next_index": self.next_index,
            "items": [item.to_dict() for item in self.items],
        }

    def add(self, content: str, *, status: str = "pending", priority: str = "normal", note: str = "") -> TodoItem:
        text = str(content).strip()
        if not text:
            raise ValueError("content must not be empty")
        item = TodoItem(
            todo_id=f"todo_{self.next_index}",
            content=text,
            status=str(status or "pending"),
            priority=str(priority or "normal"),
            note=str(note or ""),
        )
        self.items.append(item)
        self.next_index += 1
        return item

    def update(
        self,
        todo_id: str,
        *,
        status: str | None = None,
        content: str | None = None,
        priority: str | None = None,
        note: str | None = None,
    ) -> TodoItem:
        item = self.get(todo_id)
        if status is not None:
            item.status = str(status)
        if content is not None:
            text = str(content).strip()
            if not text:
                raise ValueError("content must not be empty")
            item.content = text
        if priority is not None:
            item.priority = str(priority)
        if note is not None:
            item.note = str(note)
        item.updated_at = now_iso()
        return item

    def get(self, todo_id: str) -> TodoItem:
        for item in self.items:
            if item.todo_id == todo_id:
                return item
        raise KeyError(f"unknown todo {todo_id}")

    def list_items(self) -> list[TodoItem]:
        return list(self.items)

    def render_list(self) -> str:
        if not self.items:
            return "(empty)"
        lines = []
        for item in self.items:
            note = f" | note: {item.note}" if item.note else ""
            lines.append(f"{item.todo_id} [{item.status}] {item.priority} - {item.content}{note}")
        return "\n".join(lines)
