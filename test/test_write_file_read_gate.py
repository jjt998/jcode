from __future__ import annotations

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
        resolved.relative_to(self.root)
        return resolved

    def relpath(self, path: str | Path) -> str:
        return str(Path(path).resolve().relative_to(self.root)).replace("\\", "/")

    def snapshot(self) -> dict:
        return {}


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
    return executor, WorkingMemory.from_dict({}, tmp_path), workspace


def test_write_file_allows_creating_new_file(tmp_path):
    executor, working_memory, _ = build_executor(tmp_path)

    result = executor.execute("write_file", {"path": "new.txt", "content": "created"}, working_memory=working_memory)

    assert result.status == "success"
    assert (tmp_path / "new.txt").read_text(encoding="utf-8") == "created"


def test_write_file_denies_existing_unread_file(tmp_path):
    (tmp_path / "existing.txt").write_text("old", encoding="utf-8")
    executor, working_memory, _ = build_executor(tmp_path)

    result = executor.execute("write_file", {"path": "existing.txt", "content": "new"}, working_memory=working_memory)

    assert result.status == "denied"
    assert result.error_type == "read_before_write"
    assert (tmp_path / "existing.txt").read_text(encoding="utf-8") == "old"


def test_write_file_allows_existing_file_after_read(tmp_path):
    target = tmp_path / "existing.txt"
    target.write_text("old", encoding="utf-8")
    executor, working_memory, _ = build_executor(tmp_path)

    assert executor.execute("read_file", {"path": "existing.txt"}, working_memory=working_memory).status == "success"
    result = executor.execute("write_file", {"path": "existing.txt", "content": "new"}, working_memory=working_memory)

    assert result.status == "success"
    assert target.read_text(encoding="utf-8") == "new"


def test_apply_patch_allows_absolute_path_after_relative_read(tmp_path):
    target = tmp_path / "existing.txt"
    target.write_text("old", encoding="utf-8")
    executor, working_memory, _ = build_executor(tmp_path)

    assert executor.execute("read_file", {"path": "existing.txt"}, working_memory=working_memory).status == "success"
    result = executor.execute(
        "apply_patch",
        {"path": str(target), "old_text": "old", "new_text": "new"},
        working_memory=working_memory,
    )

    assert result.status == "success"
    assert target.read_text(encoding="utf-8") == "new"


def test_read_file_and_apply_patch_preserve_crlf_for_multiline_old_text(tmp_path):
    """read_file 返回原始 CRLF 后，跨行 old_text 可以被精确替换。"""
    target = tmp_path / "crlf.txt"
    target.write_bytes(b"first\r\nsecond\r\nthird")
    executor, working_memory, _ = build_executor(tmp_path)

    read_result = executor.execute("read_file", {"path": "crlf.txt"}, working_memory=working_memory)
    assert read_result.metadata["newline"] == "crlf"
    assert "first\r\nsecond" in read_result.text

    result = executor.execute(
        "apply_patch",
        {"path": "crlf.txt", "old_text": "first\r\nsecond", "new_text": "first\r\nupdated"},
        working_memory=working_memory,
    )

    assert result.status == "success"
    assert target.read_bytes() == b"first\r\nupdated\r\nthird"


def test_apply_patch_mismatch_reports_newline_diagnostics(tmp_path):
    """old_text 未命中时返回文件换行和读取建议，便于定位协议不一致。"""
    target = tmp_path / "crlf.txt"
    target.write_bytes(b"first\r\nsecond")
    executor, working_memory, _ = build_executor(tmp_path)
    executor.execute("read_file", {"path": "crlf.txt"}, working_memory=working_memory)

    result = executor.execute(
        "apply_patch",
        {"path": "crlf.txt", "old_text": "first\nsecond", "new_text": "updated"},
        working_memory=working_memory,
    )

    assert result.status == "error"
    assert result.error_type == "patch_nonunique"
    assert "file_newline=crlf" in result.text
    assert "old_text_contains_newline=true" in result.text
    assert "reread the complete file" in result.text


def test_write_file_allows_absolute_path_after_relative_read(tmp_path):
    target = tmp_path / "existing.txt"
    target.write_text("old", encoding="utf-8")
    executor, working_memory, _ = build_executor(tmp_path)

    assert executor.execute("read_file", {"path": "existing.txt"}, working_memory=working_memory).status == "success"
    result = executor.execute("write_file", {"path": str(target), "content": "new"}, working_memory=working_memory)

    assert result.status == "success"
    assert target.read_text(encoding="utf-8") == "new"


def test_write_file_denies_after_external_change_with_freshness_conflict(tmp_path):
    target = tmp_path / "existing.txt"
    target.write_text("old", encoding="utf-8")
    executor, working_memory, _ = build_executor(tmp_path)

    assert executor.execute("read_file", {"path": "existing.txt"}, working_memory=working_memory).status == "success"
    target.write_text("changed externally", encoding="utf-8")
    result = executor.execute("write_file", {"path": "existing.txt", "content": "new"}, working_memory=working_memory)

    assert result.status == "denied"
    assert result.error_type == "write_conflict"
    assert target.read_text(encoding="utf-8") == "changed externally"


def test_jcode_own_patch_refreshes_freshness_for_followup_write(tmp_path):
    target = tmp_path / "existing.txt"
    target.write_text("old", encoding="utf-8")
    executor, working_memory, _ = build_executor(tmp_path)

    assert executor.execute("read_file", {"path": "existing.txt"}, working_memory=working_memory).status == "success"
    assert executor.execute("apply_patch", {"path": "existing.txt", "old_text": "old", "new_text": "patched"}, working_memory=working_memory).status == "success"
    result = executor.execute("write_file", {"path": "existing.txt", "content": "rewritten"}, working_memory=working_memory)

    assert result.status == "success"
    assert target.read_text(encoding="utf-8") == "rewritten"


def test_apply_patch_gate_remains_unchanged_for_unread_file(tmp_path):
    (tmp_path / "existing.txt").write_text("old", encoding="utf-8")
    executor, working_memory, _ = build_executor(tmp_path)

    result = executor.execute(
        "apply_patch",
        {"path": "existing.txt", "old_text": "old", "new_text": "new"},
        working_memory=working_memory,
    )

    assert result.status == "denied"
    assert result.error_type == "read_before_write"
