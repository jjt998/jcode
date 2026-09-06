from __future__ import annotations

import threading
from pathlib import Path

from src.memory.working import WorkingMemory
from src.policy.call_guard import CallGuard
from src.policy.permissions import PermissionChecker
from src.policy.sandbox import SandboxPolicy
from src.policy.tool_rules import ToolPolicyChecker
from src.state.workspace import Workspace
from src.tools.executor import ToolExecutor
from src.tools.registry import build_default_registry


class DummyRedactor:
    def redact(self, text: str) -> str:
        return text


class TodoRuntime:
    def todo_list(self, args: dict) -> str:
        return "(empty)"


def build_executor(workspace: Workspace) -> ToolExecutor:
    return ToolExecutor(
        workspace=workspace,
        registry=build_default_registry(),
        permissions=PermissionChecker("auto"),
        tool_policy=ToolPolicyChecker(workspace),
        sandbox=SandboxPolicy("disabled"),
        call_guard=CallGuard(),
        redactor=DummyRedactor(),
    )


def test_call_guard_allows_three_identical_calls_then_denies_fourth():
    guard = CallGuard()

    assert guard.repeated("list_files", {"path": "."}) is False
    assert guard.repeated("list_files", {"path": "."}) is False
    assert guard.repeated("list_files", {"path": "."}) is False
    assert guard.repeated("list_files", {"path": "."}) is True


def test_run_shell_retries_after_workspace_fingerprint_changes(tmp_path: Path):
    workspace = Workspace(root=tmp_path, cwd=tmp_path, repo_root=tmp_path)
    executor = build_executor(workspace)
    working_memory = WorkingMemory.from_dict({}, tmp_path)
    args = {"command": "echo ok"}

    assert executor.execute("run_shell", args, working_memory=working_memory).status == "success"
    assert executor.execute("run_shell", args, working_memory=working_memory).status == "success"
    assert executor.execute("run_shell", args, working_memory=working_memory).status == "success"
    assert executor.execute("run_shell", args, working_memory=working_memory).status == "denied"

    (tmp_path / "changed.py").write_text("changed", encoding="utf-8")

    assert executor.execute("run_shell", args, working_memory=working_memory).status == "success"


def test_run_shell_stops_when_abort_is_requested(tmp_path: Path):
    workspace = Workspace(root=tmp_path, cwd=tmp_path, repo_root=tmp_path)
    executor = build_executor(workspace)
    working_memory = WorkingMemory.from_dict({}, tmp_path)
    abort_event = threading.Event()
    timer = threading.Timer(0.2, abort_event.set)

    timer.start()
    try:
        result = executor.execute(
            "run_shell",
            {"command": 'powershell -NoProfile -Command "Start-Sleep -Seconds 5"'},
            working_memory=working_memory,
            abort_requested=abort_event.is_set,
        )
    finally:
        timer.cancel()

    assert result.status == "interrupted"
    assert result.error_type == "user_abort"


def test_todo_tools_skip_repeated_call_guard(tmp_path: Path):
    workspace = Workspace(root=tmp_path, cwd=tmp_path, repo_root=tmp_path)
    executor = build_executor(workspace)
    working_memory = WorkingMemory.from_dict({}, tmp_path)
    runtime = TodoRuntime()

    for _ in range(4):
        result = executor.execute("todo_list", {}, working_memory=working_memory, runtime=runtime)
        assert result.status == "success"
