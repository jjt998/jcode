from __future__ import annotations

from src.context.manager import ContextManager
from src.memory.working import WorkingMemory
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
