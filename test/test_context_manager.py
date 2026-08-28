from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess

import pytest

from src.context.manager import ContextManager
from src.memory.working import WorkingMemory
from src.state.workspace import Workspace


class DummySchema:
    @classmethod
    def model_json_schema(cls):
        return {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        }


class DummyTool:
    name = "dummy_tool"
    description = "Dummy tool"
    read_only = False
    risky = False
    schema = DummySchema


class DummyRegistry:
    def __init__(self):
        self.tools = {"dummy_tool": DummyTool()}


class FakeWorkspace:
    def __init__(self, root: Path):
        self.root = root
        self.cwd = root
        self.repo_root = root
        self.branch = "main"
        self.default_branch = "main"
        self.status = "clean"
        self.recent_commits = ["abc123 add docs"]
        self.project_docs = {"README.md": "readme snippet"}

    def project_rules_text(self) -> str:
        return "Project rules from JCODE.md:\n(none)"

    def stable_docs_text(self) -> str:
        return "Workspace docs:\n- README.md\n  readme snippet"

    def runtime_text(self) -> str:
        return "Workspace runtime:\n- cwd: test\n- repo_root: test"

    def workspace_hash(self) -> str:
        return "workspace-hash"


def test_workspace_build_collects_docs_and_git_facts(tmp_path, monkeypatch):
    (tmp_path / "README.md").write_text("readme body", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("agents body", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    (tmp_path / "package.json").write_text('{"name":"demo"}', encoding="utf-8")

    def fake_run(cmd, cwd=None, capture_output=None, text=None, check=None, timeout=None):
        key = tuple(cmd[1:])
        outputs = {
            ("rev-parse", "--show-toplevel"): str(tmp_path),
            ("branch", "--show-current"): "feature/test",
            ("symbolic-ref", "--short", "refs/remotes/origin/HEAD"): "origin/main",
            ("status", "--short"): " M src/app.py",
            ("log", "--oneline", "-5"): "abc123 first\nxyz999 second",
        }
        return CompletedProcess(cmd, 0, stdout=outputs.get(key, ""), stderr="")

    monkeypatch.setattr("src.state.workspace.subprocess.run", fake_run)

    workspace = Workspace.build(tmp_path)

    runtime_text = workspace.runtime_text()
    stable_docs_text = workspace.stable_docs_text()

    assert "cwd:" in runtime_text
    assert "repo_root:" in runtime_text
    assert "feature/test" in runtime_text
    assert "abc123 first" in runtime_text
    assert "README.md" in stable_docs_text
    assert "AGENTS.md" in stable_docs_text
    assert "pyproject.toml" in stable_docs_text
    assert "package.json" in stable_docs_text


def test_context_manager_builds_ctx_info_and_cache(tmp_path):
    workspace = FakeWorkspace(tmp_path)
    registry = DummyRegistry()
    manager = ContextManager(workspace=workspace, durable_memory=object(), registry=registry)
    session = {
        "history": [
            {"role": "user", "content": "Do the thing", "run_id": "run-1", "turn_id": "run-1"},
            {"role": "assistant", "content": "Okay", "run_id": "run-1", "turn_id": "run-1"},
        ]
    }
    working_memory = WorkingMemory.from_dict({}, tmp_path)

    result = manager.build(session, working_memory, "new request")

    assert result.ctx_info["cache"]["prompt_cache_key"] == result.ctx_info["cache"]["prefix_hash"]
    assert result.ctx_info["budget"]["section_budgets"]["current_request"] is None
    assert result.ctx_info["history"]["turn_count"] == 1
    assert "Workspace runtime" in result.context
    assert "Workspace docs" in result.context


def test_compact_history_inserts_summary_and_next_build_skips_it(tmp_path):
    workspace = FakeWorkspace(tmp_path)
    registry = DummyRegistry()
    manager = ContextManager(workspace=workspace, durable_memory=object(), registry=registry)
    session = {
        "history": [
            {"role": "user", "content": "Read README", "run_id": "run-1", "turn_id": "run-1"},
            {"role": "tool", "name": "read_file", "args": {"path": "README.md"}, "content": "readme content", "tool_status": "success", "run_id": "run-1", "turn_id": "run-1"},
            {"role": "assistant", "content": "Great", "run_id": "run-1", "turn_id": "run-1"},
            {"role": "user", "content": "Patch app.py", "run_id": "run-2", "turn_id": "run-2"},
            {"role": "tool", "name": "patch_file", "args": {"path": "src/app.py"}, "content": "patched", "tool_status": "success", "run_id": "run-2", "turn_id": "run-2"},
            {"role": "assistant", "content": "Done", "run_id": "run-2", "turn_id": "run-2"},
        ],
        "event_seq": 2,
    }
    working_memory = WorkingMemory.from_dict({}, tmp_path)

    compact_info = manager.compact_history(session, working_memory, retain_turns=1)
    assert compact_info["status"] == "applied"
    assert session["history"][0]["kind"] == "compact_summary"
    assert working_memory.compact_summary

    result = manager.build(session, working_memory, "follow up")
    assert result.ctx_info["history"]["compact_summary"] == working_memory.compact_summary
    assert result.ctx_info["history"]["turn_count"] == 1
    assert "Turn compact-" not in result.context
