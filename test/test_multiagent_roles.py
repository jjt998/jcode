from __future__ import annotations

from src.policy.tool_profiles import build_tool_profiles
from src.policy.call_guard import CallGuard
from src.policy.permissions import PermissionChecker
from src.policy.sandbox import SandboxPolicy
from src.policy.tool_rules import ToolPolicyChecker
from src.providers.base import ModelResponse, ModelToolCall
from src.providers.profiles import ModelProfile
from src.providers.registry import ModelRegistry
from src.providers.router import ModelRouter
from src.state.workspace import Workspace
from src.tools.executor import ToolExecutor
from src.tools.registry import build_default_registry
from src.tools.schemas import SpawnSubagentArgs
from src.workers.manager import WorkerManager
from src.workers.roles import get_role_spec, plan_mode_allowed_roles


class _Redactor:
    def redact(self, text: str) -> str:
        return text


class _FakeClient:
    def __init__(self):
        self.calls = 0

    def complete(self, context, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return ModelResponse(
                text="",
                tool_calls=[ModelToolCall("call-1", "run_shell", {"command": "powershell -NoProfile -Command \"Set-Content -LiteralPath existing.txt -Value changed\"", "timeout": 60}, {})],
                finish_reason="completed",
                raw={"output": []},
            )
        return ModelResponse(text="验证完成", finish_reason="completed", raw={"output": []})


def _build_manager(tmp_path):
    workspace = Workspace(tmp_path, tmp_path, tmp_path)
    profile = ModelProfile("fake", "fake", "openai_responses", "fake", "", "https://fake", 100000, 10000)
    registry = ModelRegistry({"fake": profile}, "fake")
    client = _FakeClient()
    registry.register("fake", "openai_responses", lambda _: client)
    executor = ToolExecutor(
        workspace=workspace,
        registry=build_default_registry(),
        permissions=PermissionChecker("auto"),
        tool_policy=ToolPolicyChecker(workspace),
        sandbox=SandboxPolicy("disabled"),
        call_guard=CallGuard(),
        redactor=_Redactor(),
    )
    config = type("Config", (), {"default_model_profile": "fake", "max_new_tokens": 1000, "temperature": 0.2, "max_steps": 3, "compact_summary_timeout_seconds": 1, "compact_summary_retry_count": 0, "compact_summary_initial_retry_delay_seconds": 0, "compact_summary_retry_multiplier": 1, "compact_summary_rebuild_on": True})()
    return WorkerManager(workspace, tmp_path / ".jcode" / "workers", executor, ModelRouter(registry), config), profile


def test_fixed_roles_have_explicit_capabilities():
    explorer = get_role_spec("explorer")
    tester = get_role_spec("tester")
    worker = get_role_spec("worker")

    assert explorer.tool_profile == "readonly"
    assert explorer.allow_write is False
    assert tester.tool_profile == "tester"
    assert tester.allow_shell is True
    assert tester.allow_write is False
    assert worker.allow_write is True
    assert plan_mode_allowed_roles() == {"explorer", "planner", "reviewer"}


def test_tester_profile_allows_shell_but_not_direct_writes():
    profiles = build_tool_profiles(build_default_registry())

    assert profiles["tester"].allows("read_file")
    assert profiles["tester"].allows("run_shell")
    assert not profiles["tester"].allows("write_file")
    assert not profiles["tester"].allows("apply_patch")
    assert not profiles["tester"].allows("spawn_subagent")


def test_spawn_schema_requires_role_and_acceptance_criteria():
    parsed = SpawnSubagentArgs.model_validate({"prompt": "inspect", "role": "explorer", "acceptance_criteria": ["report paths"]})

    assert parsed.role == "explorer"
    assert parsed.acceptance_criteria == ["report paths"]


def test_old_subagent_type_is_not_accepted():
    try:
        SpawnSubagentArgs.model_validate({"prompt": "inspect", "subagent_type": "Explore"})
    except Exception as exc:
        assert "role" in str(exc) or "acceptance_criteria" in str(exc)
    else:
        raise AssertionError("old subagent_type protocol must be rejected")


def test_tester_runner_records_existing_file_shell_side_effect(tmp_path):
    (tmp_path / "existing.txt").write_text("before", encoding="utf-8")
    manager, profile = _build_manager(tmp_path)
    spawned = manager.spawn("run validation", role="tester", acceptance_criteria=["report the command"], model_profile=profile.snapshot())
    worker_id = spawned.metadata["worker_id"]

    result = manager.wait(worker_id)

    assert result.status == "success"
    assert "existing.txt" in result.changed_files
    assert result.metadata["role"] == "tester"
    assert manager.wait(worker_id).changed_files == result.changed_files
