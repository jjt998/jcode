from __future__ import annotations

from src.context.manager import ContextManager
from src.context.result import HistoryEvent
from src.memory.working import WorkingMemory
from src.providers.deepseek import DeepSeekClient
from src.providers.profiles import ModelProfile
from src.state.workspace import Workspace
from src.tools.registry import build_default_registry


def test_context_result_keeps_sections_and_structured_history(tmp_path):
    workspace = Workspace.build(tmp_path)
    manager = ContextManager(workspace, object(), build_default_registry(), total_budget=400000)
    memory = WorkingMemory(tmp_path, task_goal="old task")
    session = {
        "history": [
            {"kind": "user", "event_id": "event-1", "turn_id": "turn-1", "content": "current request"},
            {"kind": "tool_call", "event_id": "event-2", "turn_id": "turn-1", "tool_name": "read_file", "call_id": "call-1", "arguments": {"path": "a.py"}},
            {"kind": "tool_result", "event_id": "event-3", "turn_id": "turn-1", "tool_name": "read_file", "call_id": "call-1", "content": "content"},
        ],
        "event_seq": 3,
    }

    result = manager.build(session, memory, "current request", allowed_tools=frozenset({"read_file"}))

    assert result.prefix
    assert result.skill
    assert result.current_request == "current request"
    assert [event.kind for event in result.history] == ["tool_call", "tool_result"]
    assert result.history[-1].call_id == "call-1"
    assert [tool.name for tool in result.tools] == ["read_file"]
    assert result.working_memory is not memory
    assert result.ctx_info["context_result"]["history_is_text_clipped"] is False


def test_context_budget_includes_provider_continuation(tmp_path):
    workspace = Workspace.build(tmp_path)
    manager = ContextManager(workspace, object(), build_default_registry(), total_budget=400000)
    session = {"history": [], "event_seq": 0}
    memory = WorkingMemory(tmp_path)

    without_continuation = manager.build(session, memory, "continue", allowed_tools=frozenset({"read_file"}))
    with_continuation = manager.build(
        session,
        memory,
        "continue",
        allowed_tools=frozenset({"read_file"}),
        provider_continuation={"run_id": "run-1", "items": [{"type": "reasoning", "summary": [{"text": "x" * 4000}]}]},
    )

    assert with_continuation.ctx_info["budget"]["total_chars"] > without_continuation.ctx_info["budget"]["total_chars"] + 4000


def test_pressure_windows_and_threshold_are_relaxed(tmp_path):
    manager = ContextManager(Workspace.build(tmp_path), object(), build_default_registry())

    assert [manager._history_window_for_level(level) for level in range(5)] == [7, 6, 5, 4, 4]
    assert manager._pressure_level(0.899)[0] == 3
    assert manager._pressure_level(0.90)[0] == 4


def test_request_compression_does_not_modify_session_history(tmp_path):
    manager = ContextManager(Workspace.build(tmp_path), object(), build_default_registry())
    history = []
    for number in range(8):
        history.extend([
            {"kind": "user", "event_id": f"user-{number}", "turn_id": f"turn-{number}", "content": f"request {number}"},
            {"kind": "tool_call", "event_id": f"call-{number}", "turn_id": f"turn-{number}", "tool_name": "run_shell", "call_id": f"call-{number}", "arguments": {"command": "dir"}},
            {"kind": "tool_result", "event_id": f"result-{number}", "turn_id": f"turn-{number}", "tool_name": "run_shell", "call_id": f"call-{number}", "content": "old output\n" * 50},
        ])
    session = {"history": history, "event_seq": len(history)}

    context_history, _ = manager._build_structured_history(session, pressure_level=1, current_request="new request")

    assert session["history"][2]["content"] == "old output\n" * 50
    assert next(event for event in context_history if event.event_id == "result-0").metadata["compressed"] is True
    assert next(event for event in context_history if event.event_id == "result-7").content == "old output\n" * 50


def test_request_compression_keeps_only_artifact_path(tmp_path):
    manager = ContextManager(Workspace.build(tmp_path), object(), build_default_registry())
    event = HistoryEvent(
        "tool_result",
        "result-1",
        "turn-1",
        "large output body",
        "run_shell",
        "call-1",
        {"command": "dir"},
        {"full_output_artifact": ".jcode/runs/run-1/artifacts/output.txt"},
    )

    compressed, record = manager._compress_structured_tool_event(event, set())

    assert compressed.content == "Large tool output stored at: .jcode/runs/run-1/artifacts/output.txt"
    assert record["rule"] == "artifact_path_only"


def test_compact_summary_is_sent_to_provider(tmp_path):
    profile = ModelProfile("test", "deepseek", "openai_responses", "model", "", "https://example.test")
    client = DeepSeekClient(profile)
    event = HistoryEvent("compact_summary", "summary-1", "compact-1", "old task facts")

    compiled = client._compile_history_event(event)

    assert compiled == [{"role": "user", "content": "[JCode Compact Summary]\nold task facts"}]


def test_session_compaction_keeps_four_latest_turns(tmp_path):
    manager = ContextManager(Workspace.build(tmp_path), object(), build_default_registry())
    memory = WorkingMemory(tmp_path)
    session = {
        "event_seq": 6,
        "history": [
            {"kind": "user", "event_id": f"event-{number}", "turn_id": f"turn-{number}", "content": f"request {number}"}
            for number in range(6)
        ],
    }

    compact, _ = manager.compact_history(session, memory)

    assert compact["retain_turns"] == 4
    assert session["history"][0]["kind"] == "compact_summary"
    assert [item["turn_id"] for item in session["history"][1:]] == ["turn-2", "turn-3", "turn-4", "turn-5"]


def test_pressure_memory_keeps_only_active_state(tmp_path):
    manager = ContextManager(Workspace.build(tmp_path), object(), build_default_registry())
    memory = WorkingMemory(
        tmp_path,
        recent_files=[f"file-{number}.py" for number in range(10)],
        file_freshness={f"file-{number}.py": str(number) for number in range(10)},
        retrieved_memory=["old retrieval"],
        last_retrieval_query="old query",
        subagent_results=["old worker result"],
        compact_summary="old summary",
        todo_items=[
            {"content": "done", "status": "completed"},
            {"content": "continue", "status": "in_progress"},
        ],
    )

    manager._reduce_working_memory_for_pressure(memory)

    assert memory.recent_files == [f"file-{number}.py" for number in range(3, 10)]
    assert set(memory.file_freshness) == set(memory.recent_files)
    assert not memory.retrieved_memory and not memory.last_retrieval_query
    assert not memory.subagent_results and not memory.compact_summary
    assert memory.todo_items == [{"content": "continue", "status": "in_progress"}]
