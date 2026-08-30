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
        self.artifacts = []

    def append_trace(self, run_dir, event, run_id, **payload):
        self.traces.append({"event": event, "run_id": run_id, **payload})

    def write_task_state(self, run_dir, task_state):
        self.task_states.append(task_state.to_dict())

    def write_artifact(self, run_dir, name, content):
        safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in name)
        artifact_path = run_dir / "artifacts" / safe
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(content, encoding="utf-8")
        rel_path = f"artifacts/{safe}"
        self.artifacts.append(rel_path)
        return rel_path


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


def test_parse_model_action_supports_reasoning_with_tool():
    action = parse_model_action(
        '<reasoning>先检查文件。</reasoning><tool name="read_file">{"path":"a.txt"}</tool>'
    )

    assert action.kind == "tool"
    assert action.reasoning == "先检查文件。"
    assert action.content == '<tool name="read_file">{"path":"a.txt"}</tool>'
    assert action.tool_name == "read_file"
    assert action.tool_args == {"path": "a.txt"}


def test_parse_model_action_supports_reasoning_with_tools_and_final():
    tools_action = parse_model_action(
        '<reasoning>分两步执行。</reasoning><tools>[{"name":"read_file","args":{"path":"a.txt"}},{"name":"write_file","args":{"path":"b.txt","content":"x"}}]</tools>'
    )
    final_action = parse_model_action(
        "<reasoning>已经完成。</reasoning><final>done</final>"
    )

    assert tools_action.kind == "tools"
    assert tools_action.reasoning == "分两步执行。"
    assert tools_action.content.startswith("<tools>")
    assert [call.name for call in tools_action.tool_calls] == ["read_file", "write_file"]
    assert final_action.kind == "final"
    assert final_action.reasoning == "已经完成。"
    assert final_action.content == "done"


def test_parse_model_action_rejects_mixed_protocol():
    action = parse_model_action('<tools>[{"name":"read_file","args":{}}]</tools><final>done</final>')

    assert action.kind == "invalid"
    assert action.content.startswith("Your output protocol is invalid")
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


def test_long_read_file_result_is_saved_as_artifact(tmp_path):
    large_text = "a" * 1205
    agent = build_agent(
        tmp_path,
        SequencedToolExecutor(
            None,
            {
                "read_file": ToolResult("success", large_text),
            },
        ),
    )
    agent.tool_executor.agent = agent
    task_state = TaskState.create("read a large file")
    checkpoint = DummyCheckpoint()

    result = agent._execute_tool_call("read_file", {"path": "large.txt"}, task_state, tmp_path, checkpoint)

    tool_history = next(item for item in agent.session["history"] if item.get("role") == "tool")
    artifact_path = tool_history["artifacts"][0]

    assert result.text.startswith(f"{artifact_path}\n")
    assert result.text.splitlines()[1] == "a" * 1000
    assert (tmp_path / artifact_path).read_text(encoding="utf-8") == large_text
    assert tool_history["metadata"]["full_output_artifact"] == artifact_path
    assert tool_history["metadata"]["original_chars"] == len(large_text)
    assert tool_history["metadata"]["content_sha256"]
    assert any(event["event"] == "tool_executed" for event in agent.run_store.traces)


def test_long_non_read_file_result_is_saved_as_artifact(tmp_path):
    large_text = "b" * 1300
    agent = build_agent(
        tmp_path,
        SequencedToolExecutor(
            None,
            {
                "run_shell": ToolResult("success", large_text),
            },
        ),
    )
    agent.tool_executor.agent = agent
    task_state = TaskState.create("run a long command")
    checkpoint = DummyCheckpoint()

    result = agent._execute_tool_call("run_shell", {"command": "echo long"}, task_state, tmp_path, checkpoint)

    tool_history = next(item for item in agent.session["history"] if item.get("role") == "tool")
    artifact_path = tool_history["artifacts"][0]

    assert artifact_path.startswith("artifacts/run_shell-output-")
    assert result.text.startswith(f"{artifact_path}\n")
    assert result.text.splitlines()[1] == "b" * 1000
    assert (tmp_path / artifact_path).read_text(encoding="utf-8") == large_text
    assert tool_history["metadata"]["full_output_artifact"] == artifact_path
    assert agent.run_store.artifacts == [artifact_path]


def test_short_tool_result_stays_inline(tmp_path):
    agent = build_agent(
        tmp_path,
        SequencedToolExecutor(
            None,
            {
                "read_file": ToolResult("success", "short result"),
            },
        ),
    )
    agent.tool_executor.agent = agent
    task_state = TaskState.create("read a small file")
    checkpoint = DummyCheckpoint()

    result = agent._execute_tool_call("read_file", {"path": "small.txt"}, task_state, tmp_path, checkpoint)

    tool_history = next(item for item in agent.session["history"] if item.get("role") == "tool")
    assert result.text == "short result"
    assert tool_history["content"] == "short result"
    assert tool_history["artifacts"] == []
    assert tool_history["metadata"]["full_output_artifact"] == ""
    assert tool_history["metadata"]["original_chars"] == len("short result")
    assert agent.run_store.artifacts == []
