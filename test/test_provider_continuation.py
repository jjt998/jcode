from __future__ import annotations

import json

from src.providers.continuation import ProviderContinuation
from src.runtime.agent import JCodeAgent
from src.state.checkpoint import CheckpointManager
from src.state.task import TaskState
from src.state.workspace import Workspace


def test_provider_continuation_keeps_native_call_chain():
    continuation = ProviderContinuation(run_id="run-1")
    continuation.add_response_items(
        [
            {"type": "reasoning", "summary": [{"text": "inspect"}]},
            {"type": "function_call", "call_id": "call-1", "name": "read_file", "arguments": "{}"},
            {"type": "message", "content": [{"text": "ignored"}]},
        ]
    )
    continuation.add_tool_output("call-1", "file content")

    assert [item["type"] for item in continuation.items] == ["reasoning", "function_call", "function_call_output"]
    assert continuation.items[-1]["call_id"] == "call-1"


def test_checkpoint_persists_provider_continuation(tmp_path):
    workspace = Workspace.build(tmp_path)
    task_state = TaskState.create("test")
    task_state.provider_continuation = {"run_id": task_state.run_id, "items": [{"type": "reasoning"}]}
    checkpoint = CheckpointManager(tmp_path, workspace)

    checkpoint.create({"id": "session-1"}, task_state, type("Memory", (), {"task_goal": "test", "recent_files": [], "file_freshness": {}, "to_dict": lambda self: {}})())

    saved = json.loads((tmp_path / "checkpoint.json").read_text(encoding="utf-8"))
    assert saved["provider_continuation"] == task_state.provider_continuation


def test_incomplete_response_is_not_a_valid_final():
    assert JCodeAgent._is_completed_response("completed") is True
    assert JCodeAgent._is_completed_response("incomplete") is False
    assert JCodeAgent._is_completed_response("") is False
