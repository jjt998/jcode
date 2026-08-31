from __future__ import annotations

import os
from pathlib import Path

from src.memory.working import WorkingMemory
from src.policy.call_guard import CallGuard
from src.policy.permissions import PermissionChecker
from src.policy.sandbox import SandboxPolicy
from src.policy.tool_rules import ToolPolicyChecker
from src.tools.executor import ToolExecutor
from src.tools.registry import build_default_registry


class DummyRedactor:
    def redact(self, text: str) -> str:
        return text


class FakeWorkspace:
    def __init__(self, root: Path):
        self.root = root.resolve()

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


def build_executor(tmp_path: Path) -> tuple[ToolExecutor, WorkingMemory, FakeWorkspace]:
    workspace = FakeWorkspace(tmp_path)
    executor = ToolExecutor(
        workspace=workspace,
        registry=build_default_registry(),
        permissions=PermissionChecker("auto"),
        tool_policy=ToolPolicyChecker(workspace),
        sandbox=SandboxPolicy("disabled"),
        call_guard=CallGuard(),
        redactor=DummyRedactor(),
    )
    working_memory = WorkingMemory.from_dict({}, tmp_path)
    return executor, working_memory, workspace


def test_read_file_allows_three_reads_then_denies_fourth(tmp_path):
    (tmp_path / "docs").mkdir()
    target = tmp_path / "docs" / "note.txt"
    target.write_text("alpha", encoding="utf-8")
    executor, working_memory, _ = build_executor(tmp_path)

    args = {"path": "docs/note.txt", "max_chars": 10, "start": 0, "end": 4}
    first = executor.execute("read_file", args, working_memory=working_memory)
    second = executor.execute("read_file", args, working_memory=working_memory)
    third = executor.execute("read_file", args, working_memory=working_memory)
    fourth = executor.execute("read_file", args, working_memory=working_memory)

    freshness_key = f"{int(target.stat().st_mtime_ns)}:{target.stat().st_size}"
    assert first.status == "success"
    assert second.status == "success"
    assert third.status == "success"
    assert fourth.status == "denied"
    assert "You've already reread this unchanged file three times." in fourth.text
    assert working_memory.read_file_count("docs/note.txt", args, freshness_key) == 3


def test_read_file_different_ranges_count_separately(tmp_path):
    target = tmp_path / "docs" / "note.txt"
    target.parent.mkdir()
    target.write_text("alpha beta gamma", encoding="utf-8")
    executor, working_memory, _ = build_executor(tmp_path)

    first_range = {"path": "docs/note.txt", "max_chars": 10, "start": 0, "end": 5}
    second_range = {"path": "docs/note.txt", "max_chars": 10, "start": 6, "end": 11}

    for _ in range(3):
        assert executor.execute("read_file", first_range, working_memory=working_memory).status == "success"

    blocked = executor.execute("read_file", first_range, working_memory=working_memory)
    allowed = executor.execute("read_file", second_range, working_memory=working_memory)
    freshness_key = f"{int(target.stat().st_mtime_ns)}:{target.stat().st_size}"

    assert blocked.status == "denied"
    assert allowed.status == "success"
    assert working_memory.read_file_count("docs/note.txt", first_range, freshness_key) == 3
    assert working_memory.read_file_count("docs/note.txt", second_range, freshness_key) == 1


def test_read_file_count_resets_when_file_changes(tmp_path):
    target = tmp_path / "docs" / "note.txt"
    target.parent.mkdir()
    target.write_text("alpha", encoding="utf-8")
    executor, working_memory, _ = build_executor(tmp_path)
    args = {"path": "docs/note.txt", "max_chars": 10, "start": 0, "end": 4}

    for _ in range(3):
        result = executor.execute("read_file", args, working_memory=working_memory)
        assert result.status == "success"

    first_freshness = f"{int(target.stat().st_mtime_ns)}:{target.stat().st_size}"
    target.write_text("alpha beta", encoding="utf-8")
    second = executor.execute("read_file", args, working_memory=working_memory)

    second_freshness = f"{int(target.stat().st_mtime_ns)}:{target.stat().st_size}"
    assert second.status == "success"
    assert working_memory.read_file_count("docs/note.txt", args, first_freshness) == 3
    assert working_memory.read_file_count("docs/note.txt", args, second_freshness) == 1


def test_read_file_failure_does_not_increment_count(tmp_path):
    executor, working_memory, _ = build_executor(tmp_path)
    missing = executor.execute("read_file", {"path": "missing.txt", "start": 0, "end": 5}, working_memory=working_memory)

    assert missing.status == "error"
    assert working_memory.read_file_counts == {}


def test_working_memory_round_trip_keeps_read_file_counts(tmp_path):
    working_memory = WorkingMemory.from_dict({}, tmp_path)
    first_args = {"path": "docs/note.txt", "max_chars": 10, "start": 0, "end": 5}
    second_args = {"path": "docs/note.txt", "max_chars": 10, "start": 6, "end": 11}
    working_memory.note_file_read("docs/note.txt", first_args, "1:10")
    working_memory.note_file_read("docs/note.txt", first_args, "1:10")
    working_memory.note_file_read("docs/note.txt", second_args, "2:11")

    restored = WorkingMemory.from_dict(working_memory.to_dict(), tmp_path)

    assert restored.read_file_count("docs/note.txt", first_args, "1:10") == 2
    assert restored.read_file_count("docs/note.txt", second_args, "2:11") == 1


def test_read_file_result_records_source_freshness(tmp_path):
    target = tmp_path / "note.txt"
    target.write_text("alpha", encoding="utf-8")
    executor, working_memory, _ = build_executor(tmp_path)

    result = executor.execute("read_file", {"path": "note.txt"}, working_memory=working_memory)

    assert result.status == "success"
    assert result.metadata["source_files"] == [{
        "path": "note.txt",
        "freshness": f"{int(target.stat().st_mtime_ns)}:{target.stat().st_size}",
    }]
