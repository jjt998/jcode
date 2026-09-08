from __future__ import annotations

from dataclasses import dataclass, field

from src.state.workspace import now_iso

TODO_STATUSES = frozenset({"pending", "in_progress", "completed"})


@dataclass
class TodoItem:
    todo_id: str
    content: str
    status: str = "pending"
    priority: str = "normal"
    note: str = ""
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    archived: bool = False  # 是否已归档并从当前工作列表排除
    archived_at: str = ""  # 归档时间

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
                    status = str(raw.get("status", "pending"))
                    if status not in TODO_STATUSES:
                        raise ValueError(f"invalid todo status: {status}")
                    items.append(
                        TodoItem(
                            todo_id=str(raw.get("todo_id", raw.get("id", "")) or ""),
                            content=str(raw.get("content", "")),
                            status=status,
                            priority=str(raw.get("priority", "normal")),
                            note=str(raw.get("note", "")),
                            created_at=str(raw.get("created_at", now_iso())),
                            updated_at=str(raw.get("updated_at", now_iso())),
                            archived=bool(raw.get("archived", False)),
                            archived_at=str(raw.get("archived_at", "")),
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
        if status not in TODO_STATUSES:
            raise ValueError(f"status must be one of: {', '.join(sorted(TODO_STATUSES))}")
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
            if status not in TODO_STATUSES:
                raise ValueError(f"status must be one of: {', '.join(sorted(TODO_STATUSES))}")
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

    def delete(self, todo_id: str) -> TodoItem:
        item = self.get(todo_id)
        self.items.remove(item)
        return item

    def archive(self, todo_id: str) -> TodoItem:
        item = self.get(todo_id)
        if item.archived:
            raise ValueError(f"todo already archived: {todo_id}")
        item.archived = True
        item.archived_at = now_iso()
        item.updated_at = item.archived_at
        return item

    def get(self, todo_id: str) -> TodoItem:
        for item in self.items:
            if item.todo_id == todo_id:
                return item
        raise KeyError(f"unknown todo {todo_id}")

    def list_items(self) -> list[TodoItem]:
        return [item for item in self.items if not item.archived]

    def render_list(self) -> str:
        active_items = self.list_items()
        if not active_items:
            return "(empty)"
        completed = sum(1 for item in active_items if item.status == "completed")
        in_progress = sum(1 for item in active_items if item.status == "in_progress")
        pending = len(active_items) - completed - in_progress
        percent = int(completed * 100 / len(active_items))
        lines = [f"Todo progress: {completed}/{len(active_items)} completed ({percent}%), {in_progress} in progress, {pending} pending"]
        for item in active_items:
            note = f" | note: {item.note}" if item.note else ""
            lines.append(f"{item.todo_id} [{item.status}] {item.priority} - {item.content}{note}")
        return "\n".join(lines)
