from pathlib import Path

import pytest

from src.memory.working import WorkingMemory
from src.state.todo import TodoLedger
from src.tools.registry import build_default_registry


def test_todo_status_is_strict_and_archive_is_not_status():
    ledger = TodoLedger()
    with pytest.raises(ValueError):
        ledger.add("任务", status="done")
    item = ledger.add("任务")
    archived = ledger.archive(item.todo_id)
    assert archived.archived is True
    assert ledger.list_items() == []
    assert "(empty)" == ledger.render_list()


def test_todo_delete_removes_only_current_ledger_item():
    ledger = TodoLedger()
    first = ledger.add("删除我")
    second = ledger.add("保留我")
    deleted = ledger.delete(first.todo_id)
    assert deleted.todo_id == first.todo_id
    assert [item.todo_id for item in ledger.items] == [second.todo_id]


def test_archived_todos_are_excluded_from_working_memory():
    ledger = TodoLedger()
    item = ledger.add("归档我")
    ledger.archive(item.todo_id)
    memory = WorkingMemory(Path("."))
    memory.sync_todos(ledger.to_dict())
    assert memory.todo_items == []


def test_todo_tool_schema_and_descriptions_are_explicit():
    registry = build_default_registry()
    assert {"todo_add", "todo_update", "todo_list", "todo_delete", "todo_archive"}.issubset(registry.tools)
    add_schema = registry.get("todo_add").schema.model_json_schema()
    status_schema = add_schema["properties"]["status"]
    assert set(status_schema["enum"]) == {"pending", "in_progress", "completed"}
    assert "对象" in registry.get("ask_user").schema.model_json_schema()["properties"]["choices"]["description"]
    assert "verbatim" in registry.get("apply_patch").description
