from __future__ import annotations

from pathlib import Path

from src.evidence.store import RunStore
from src.evidence.tool_artifacts import prepare_tool_result_observation
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


def test_generated_artifact_path_can_be_read_from_workspace(tmp_path: Path):
    workspace = Workspace(
        root=tmp_path,
        cwd=tmp_path,
        repo_root=tmp_path,
    )
    run_store = RunStore(tmp_path / ".jcode" / "runs", workspace_root=workspace.root)
    run_dir = run_store.run_dir("run-1")
    run_dir.mkdir(parents=True)
    full_text = "x" * 1201

    observed, metadata, artifacts = prepare_tool_result_observation(run_store, run_dir, "read_file", full_text)
    artifact_path = metadata["full_output_artifact"]

    assert artifact_path == ".jcode/runs/run-1/artifacts/read_file-output-" + metadata["content_sha256"][:12] + ".txt"
    assert artifact_path in artifacts
    assert observed.startswith(artifact_path + "\n")

    executor = ToolExecutor(
        workspace=workspace,
        registry=build_default_registry(),
        permissions=PermissionChecker("auto"),
        tool_policy=ToolPolicyChecker(workspace),
        sandbox=SandboxPolicy("disabled"),
        call_guard=CallGuard(),
        redactor=DummyRedactor(),
    )
    result = executor.execute("read_file", {"path": artifact_path}, working_memory=WorkingMemory.from_dict({}, tmp_path))

    assert result.status == "success"
    assert result.text == full_text
