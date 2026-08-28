from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess
import re
from types import SimpleNamespace

import pytest

from src.context.manager import ContextManager
from src.runtime.agent import JCodeAgent
from src.memory.working import WorkingMemory
from src.tools.registry import build_default_registry
from src.state.workspace import Workspace
from src.context.manager import ContextBuildResult


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


class FakeModelResponse:
    def __init__(self, text: str):
        self.text = text


class FakeModelClient:
    model = "fake-model"

    def __init__(self, api_key: str = "key"):
        self.api_key = api_key

    def complete(self, messages, *, model, max_tokens, temperature):
        return FakeModelResponse("## Goal\n- keep going\n\n## Constraints\n- none\n\n## Files Read\n- none\n\n## Files Modified\n- none\n\n## Key Decisions\n- none\n\n## Blockers\n- none\n\n## Next Steps\n- continue")


class FakeModelRouter:
    def __init__(self, api_key: str = "key", response_text: str = ""):
        self.client = FakeModelClient(api_key=api_key)
        self.response_text = response_text

    def complete(self, prompt: str, *, max_tokens: int, temperature: float):
        return FakeModelResponse(self.response_text or "## Goal\n- keep going\n\n## Constraints\n- none\n\n## Files Read\n- none\n\n## Files Modified\n- none\n\n## Key Decisions\n- none\n\n## Blockers\n- none\n\n## Next Steps\n- continue")


class RaisingModelRouter(FakeModelRouter):
    def complete(self, prompt: str, *, max_tokens: int, temperature: float):
        raise RuntimeError("boom")


class DummySessionStore:
    def __init__(self, root):
        self.root = root

    def save(self, session):
        self.last_saved = session


class DummyRunStore:
    def __init__(self):
        self.traces = []

    def append_trace(self, run_dir, event, run_id, **payload):
        self.traces.append({"event": event, "run_id": run_id, **payload})


class DummySessionEvents:
    def __init__(self):
        self.events = []

    def emit(self, *args, **kwargs):
        self.events.append({"args": args, "kwargs": kwargs})
        return None


class DummyRedactor:
    def redact(self, text: str) -> str:
        return text


class DummyFinalGate:
    def check(self, *args, **kwargs):
        return {"allowed": False, "message": "denied"}


class DummyToolExecutor:
    pass


class DummyWorkerManager:
    def __init__(self):
        self.workers = {}

    def worker_refs(self):
        return []


class DummyConfig:
    max_steps = 1
    max_new_tokens = 16
    temperature = 0.0


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


def test_default_registry_includes_subagent_tools():
    registry = build_default_registry()
    assert {"spawn_subagent", "send_subagent_message", "wait_subagent"}.issubset(set(registry.tools))


def test_context_manager_microcompresses_history_by_tool_name(tmp_path):
    workspace = FakeWorkspace(tmp_path)
    registry = DummyRegistry()
    manager = ContextManager(workspace=workspace, durable_memory=object(), registry=registry)
    session = {
        "history": [
            {"role": "user", "content": "turn 1", "run_id": "turn-1", "turn_id": "turn-1"},
            {"role": "tool", "name": "read_file", "args": {"path": "docs/a.txt"}, "content": "FIRST READ", "tool_status": "success", "run_id": "turn-1", "turn_id": "turn-1"},
            {"role": "assistant", "content": "turn 1 done", "run_id": "turn-1", "turn_id": "turn-1"},
            {"role": "user", "content": "turn 2", "run_id": "turn-2", "turn_id": "turn-2"},
            {"role": "tool", "name": "read_file", "args": {"path": "docs/a.txt"}, "content": "SECOND READ", "tool_status": "success", "run_id": "turn-2", "turn_id": "turn-2"},
            {"role": "assistant", "content": "turn 2 done", "run_id": "turn-2", "turn_id": "turn-2"},
            {"role": "user", "content": "turn 3", "run_id": "turn-3", "turn_id": "turn-3"},
            {
                "role": "tool",
                "name": "run_shell",
                "args": {"command": "echo hi"},
                "content": "stdout:\nline1\n\nline2\nline3\nline4\nstderr:\nerr1\nexit_code: 0",
                "tool_status": "success",
                "run_id": "turn-3",
                "turn_id": "turn-3",
            },
            {"role": "assistant", "content": "turn 3 done", "run_id": "turn-3", "turn_id": "turn-3"},
            {"role": "user", "content": "turn 4", "run_id": "turn-4", "turn_id": "turn-4"},
            {"role": "tool", "name": "search", "args": {"query": "foo"}, "content": "search result: " + ("x" * 160), "tool_status": "success", "run_id": "turn-4", "turn_id": "turn-4"},
            {"role": "tool", "name": "read_file", "args": {"path": "artifacts/result.txt"}, "content": "artifacts/result.txt\n" + ("z" * 1500), "tool_status": "success", "run_id": "turn-4", "turn_id": "turn-4", "artifacts": ["artifacts/result.txt"]},
            {"role": "assistant", "content": "turn 4 done", "run_id": "turn-4", "turn_id": "turn-4"},
            {"role": "user", "content": "turn 5", "run_id": "turn-5", "turn_id": "turn-5"},
            {"role": "tool", "name": "write_file", "args": {"path": "b.txt", "content": "written"}, "content": "wrote b.txt", "tool_status": "success", "run_id": "turn-5", "turn_id": "turn-5"},
            {"role": "assistant", "content": "turn 5 done", "run_id": "turn-5", "turn_id": "turn-5"},
            {"role": "user", "content": "turn 6", "run_id": "turn-6", "turn_id": "turn-6"},
            {"role": "assistant", "content": "turn 6 done", "run_id": "turn-6", "turn_id": "turn-6"},
        ],
        "event_seq": 6,
    }
    working_memory = WorkingMemory.from_dict({}, tmp_path)

    section_texts = manager._build_section_texts(session, working_memory, "new request")
    compressed_section_texts, info, compact_audit = manager._compress_section_texts_by_pressure(
        session=session,
        working_memory=working_memory,
        user_message="new request",
        section_texts=section_texts,
        pressure={"ratio": 0.72, "level": 2, "tier": "tier2", "range": "70-80", "source": "estimated", "input_tokens": 10, "budget_tokens": 10},
    )

    history = compressed_section_texts["history"]
    assert "FIRST READ" in history
    assert "SECOND READ" not in history
    assert "line1" in history and "line4" not in history
    assert "search result:" in history and ("x" * 120) not in history
    assert "artifacts/result.txt" in history and ("z" * 100) not in history
    assert "wrote b.txt" in history
    assert info["recent_turn_window"] == 2
    assert info["history_records"]
    assert compact_audit is None


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

    compact_info, audit = manager.compact_history(session, working_memory, retain_turns=1)
    assert compact_info["status"] == "applied"
    assert session["history"][0]["kind"] == "compact_summary"
    assert working_memory.compact_summary
    assert audit["source"] == "deterministic"

    result = manager.build(session, working_memory, "follow up")
    assert result.ctx_info["history"]["compact_summary"] == working_memory.compact_summary
    assert result.ctx_info["history"]["turn_count"] == 1
    assert "Turn compact-" not in result.context


def test_compact_history_uses_llm_summary_and_records_trace(tmp_path):
    workspace = FakeWorkspace(tmp_path)
    registry = DummyRegistry()
    manager = ContextManager(workspace=workspace, durable_memory=object(), registry=registry, model_router=FakeModelRouter())
    session = {
        "id": "session-1",
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
    run_store = DummyRunStore()
    session_store = DummySessionStore(tmp_path)
    session_events = DummySessionEvents()
    agent = JCodeAgent(
        config=DummyConfig(),
        workspace=workspace,
        session=session,
        session_store=session_store,
        run_store=run_store,
        memory_store=object(),
        session_events=session_events,
        working_memory=working_memory,
        context_manager=manager,
        model_router=FakeModelRouter(),
        tool_executor=DummyToolExecutor(),
        worker_manager=DummyWorkerManager(),
        final_gate=DummyFinalGate(),
        redactor=DummyRedactor(),
        tool_profiles={"default": object(), "plan": object()},
    )

    fake_result = ContextBuildResult(
        context="ctx",
        ctx_info={
            "history": {"compact_summary": ""},
            "compact": {"status": "applied", "should_compact": True, "trigger": "semantic_summary", "summary_source": "llm", "fallback_reason": "", "retain_turns": 2},
            "pressure": {"level": 4, "range": "95+"},
        },
        compact_audit={
            "source": "llm",
            "mode": "llm",
            "status": "success",
            "fallback_reason": "",
            "prompt": "prompt text",
            "response": "response text",
            "summary_text": "summary text",
        },
    )
    manager.build = lambda *args, **kwargs: fake_result

    result = agent._build_context("follow up", SimpleNamespace(run_id="run-99"), tmp_path)

    audit_events = [event for event in run_store.traces if event["event"] == "compact_history_audit"]
    assert audit_events
    compact_events = [event for event in run_store.traces if event["event"] in {"compact_evaluated", "compact_triggered", "compact_completed"}]
    assert [event["event"] for event in compact_events] == ["compact_evaluated", "compact_triggered", "compact_completed"]
    assert [event["args"][0] for event in session_events.events[:3]] == ["compact_evaluated", "compact_triggered", "compact_completed"]
    assert audit_events[0]["summary_source"] == "llm"
    assert audit_events[0]["summary_prompt"]
    assert audit_events[0]["summary_response"]
    assert result.compact_audit["source"] == "llm"


def test_compact_history_falls_back_when_llm_fails(tmp_path):
    workspace = FakeWorkspace(tmp_path)
    registry = DummyRegistry()
    manager = ContextManager(workspace=workspace, durable_memory=object(), registry=registry, model_router=RaisingModelRouter())
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

    compact_info, audit = manager.compact_history(session, working_memory, retain_turns=1, summary_mode="llm")

    assert compact_info["status"] == "applied"
    assert audit["status"] == "fallback"
    assert audit["fallback_reason"].startswith("RuntimeError")
    assert "## Goal" in session["history"][0]["content"]
