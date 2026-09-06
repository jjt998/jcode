from __future__ import annotations

import json
import pytest

from src.providers.continuation import ProviderContinuation
from src.runtime.errors import UnsupportedProviderContinuationItemError
from src.runtime.agent import JCodeAgent
from src.providers.base import ModelResponse, ProviderRequestError
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


def test_unknown_provider_output_item_stops_run():
    with pytest.raises(UnsupportedProviderContinuationItemError):
        ProviderContinuation().add_response_items([{"type": "unsupported_native_item"}])


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


def test_model_recovery_only_continues_token_limited_content():
    task_state = TaskState.create("test")
    response = ModelResponse(text="partial", finish_reason="incomplete", incomplete_reason="max_output_tokens")

    assert JCodeAgent._should_continue_incomplete(response, task_state) is True
    assert JCodeAgent._should_continue_incomplete(ModelResponse(text="partial", finish_reason="incomplete", incomplete_reason="content_filter"), task_state) is False

    task_state.output_continuation_count = 2
    assert JCodeAgent._should_continue_incomplete(response, task_state) is False


def test_model_recovery_retries_only_transient_provider_errors():
    assert JCodeAgent._is_retryable_provider_error(ProviderRequestError("limited", status_code=429)) is True
    assert JCodeAgent._is_retryable_provider_error(ProviderRequestError("server", status_code=503)) is True
    assert JCodeAgent._is_retryable_provider_error(ProviderRequestError("network", transport_error=True)) is True
    assert JCodeAgent._is_retryable_provider_error(ProviderRequestError("bad request", status_code=400)) is False
    assert JCodeAgent._is_retryable_provider_error(RuntimeError("unknown")) is False
    assert JCodeAgent._is_retryable_failed_response(ModelResponse("", finish_reason="failed", provider_error_code="server_error")) is True
    assert JCodeAgent._is_retryable_failed_response(ModelResponse("", finish_reason="failed", provider_error_code="invalid_request_error")) is False
