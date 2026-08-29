from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from src.memory.working import WorkingMemory
from src.runtime.actions import parse_model_action
from src.runtime.agent import JCodeAgent
from src.state.task import TaskState
from src.tools.base import ToolResult


class DummySessionStore:
    def __init__(self, root: Path):
        self.root = root

    def save(self, session):
        self.last_saved = session


class DummyRunStore:
    def __init__(self):
        self.traces = []
        self.task_states = []

    def append_trace(self, run_dir, event, run_id, **payload):
        self.traces.append({"event": event, "run_id": run_id, **payload})

    def write_task_state(self, run_dir, task_state):
        self.task_states.append(task_state.to_dict())


class DummySessionEvents:
    def emit(self, *args, **kwargs):
        return None


class DummyRedactor:
    def redact(self, text: str) -> str:
        return text


class DummyFinalGate:
    def check(self, *args, **kwargs):
        return {"allowed": True, "reason": "ready", "message": ""}


class DummyCheckpoint:
    def __init__(self):
        self.calls = 0

    def create(self, *args, **kwargs):
        self.calls += 1


class DummyWorkerManager:
    def __init__(self):
        self.workers = {}

    def worker_refs(self):
        return []

    def spawn(self, *args, **kwargs):
        return ToolResult("success", "spawned")

    def send(self, *args, **kwargs):
        return ToolResult("success", "sent")

    def wait(self, *args, **kwargs):
        return ToolResult("success", "waited")


class DummyProfile:
    def __init__(self, name: str):
        self.name = name

    def allows(self, tool_name: str) -> bool:
        return True


class SequencedToolExecutor:
    def __init__(self, agent, results: dict[str, ToolResult], abort_after_first: bool = False):
        self.agent = agent
        self.results = results
        self.abort_after_first = abort_after_first
        self.calls: list[tuple[str, dict]] = []

    def execute(self, name: str, args: dict, **kwargs):
        self.calls.append((name, dict(args)))
        if self.abort_after_first and len(self.calls) == 1:
            kwargs["runtime"].abort()
        return self.results[name]


class FakeWorkspace:
    def __init__(self, root: Path):
        self.root = root

    def project_rules_text(self) -> str:
        return ""

    def stable_docs_text(self) -> str:
        return ""

    def runtime_text(self) -> str:
        return ""


class DummyConfig:
    max_steps = 3
    max_new_tokens = 32
    temperature = 0.0


def build_agent(tmp_path: Path, executor) -> JCodeAgent:
    session = {"id": "session-1"}
    workspace = FakeWorkspace(tmp_path)
    working_memory = WorkingMemory.from_dict({}, tmp_path)
    return JCodeAgent(
        config=DummyConfig(),
        workspace=workspace,
        session=session,
        session_store=DummySessionStore(tmp_path),
        run_store=DummyRunStore(),
        memory_store=object(),
        session_events=DummySessionEvents(),
        working_memory=working_memory,
        context_manager=object(),
        model_router=object(),
        tool_executor=executor,
        worker_manager=DummyWorkerManager(),
        final_gate=DummyFinalGate(),
        redactor=DummyRedactor(),
        tool_profiles={"default": DummyProfile("default"), "plan": DummyProfile("plan")},
    )


def test_parse_model_action_supports_tool_sequence():
    action = parse_model_action(
        '<tools>[{"name":"read_file","args":{"path":"a.txt"}},{"name":"write_file","args":{"path":"b.txt","content":"x"}}]</tools>'
    )

    assert action.kind == "tools"
    assert [call.name for call in action.tool_calls] == ["read_file", "write_file"]
    assert action.tool_calls[0].args == {"path": "a.txt"}


def test_parse_model_action_rejects_mixed_protocol():
    action = parse_model_action('<tools>[{"name":"read_file","args":{}}]</tools><final>done</final>')

    assert action.kind == "invalid"
    assert "exactly one" in action.content


def test_tool_sequence_executes_all_steps_even_after_failure(tmp_path):
    agent = build_agent(
        tmp_path,
        SequencedToolExecutor(
            None,
            {
                "read_file": ToolResult("success", "read ok"),
                "write_file": ToolResult("error", "write failed", error_type="tool_failed"),
            },
        ),
    )
    agent.tool_executor.agent = agent
    task_state = TaskState.create("do sequence")
    checkpoint = DummyCheckpoint()
    action = parse_model_action(
        '<tools>[{"name":"read_file","args":{"path":"a.txt"}},{"name":"write_file","args":{"path":"b.txt","content":"x"}}]</tools>'
    )

    agent._handle_tool_sequence_action(action, task_state, tmp_path, checkpoint)

    assert agent.tool_executor.calls == [("read_file", {"path": "a.txt"}), ("write_file", {"path": "b.txt", "content": "x"})]
    assert task_state.tool_steps == 2
    assert len([event for event in agent.run_store.traces if event["event"] == "tool_sequence_completed"]) == 1
    assert len([event for event in agent.run_store.traces if event["event"] == "tool_executed"]) == 2
    tool_history = [item for item in agent.session["history"] if item.get("role") == "tool"]
    assert len(tool_history) == 2
    assert tool_history[0]["sequence_index"] == 1
    assert tool_history[1]["sequence_index"] == 2
    assert checkpoint.calls == 2


def test_tool_sequence_stops_after_abort_request(tmp_path):
    agent = build_agent(
        tmp_path,
        SequencedToolExecutor(
            None,
            {
                "read_file": ToolResult("success", "read ok"),
                "write_file": ToolResult("success", "write ok"),
            },
            abort_after_first=True,
        ),
    )
    agent.tool_executor.agent = agent
    task_state = TaskState.create("do sequence")
    checkpoint = DummyCheckpoint()
    action = parse_model_action(
        '<tools>[{"name":"read_file","args":{"path":"a.txt"}},{"name":"write_file","args":{"path":"b.txt","content":"x"}}]</tools>'
    )

    agent._handle_tool_sequence_action(action, task_state, tmp_path, checkpoint)

    assert agent.abort_requested is True
    assert agent.tool_executor.calls == [("read_file", {"path": "a.txt"})]
    assert len([event for event in agent.run_store.traces if event["event"] == "tool_sequence_aborted"]) == 1
    assert task_state.tool_steps == 1
