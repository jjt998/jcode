from __future__ import annotations

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
