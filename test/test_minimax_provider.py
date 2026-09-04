from __future__ import annotations

import json

from src.context.result import ContextResult, ToolDefinition
from src.memory.working import WorkingMemory
from src.providers.minimax import MiniMaxClient
from src.providers.profiles import ModelProfile


class FakeHttpResponse:
    def __init__(self, data: dict):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.data).encode("utf-8")


def _profile() -> ModelProfile:
    return ModelProfile(
        id="minimax-m3",
        provider="minimax",
        api_protocol="openai_responses",
        model="MiniMax-M3",
        api_key="key",
        base_url="https://minnimax.chat/v1",
        reasoning_mode="optional",
        thinking_enabled=False,
        reasoning_effort="medium",
        reasoning_effort_options=("minimal", "low", "medium", "high"),
    )


def _context(tmp_path) -> ContextResult:
    return ContextResult(
        prefix="stable prefix",
        skill="use repository evidence",
        history=[],
        working_memory=WorkingMemory(tmp_path, task_goal="current task"),
        current_request="do the task",
        tools=[ToolDefinition("read_file", "read", {"type": "object"}, True, False)],
        ctx_info={},
    )


def test_minimax_uses_proxy_url_and_omits_reasoning_when_disabled(monkeypatch, tmp_path):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return FakeHttpResponse({"status": "completed", "output_text": "ok", "output": []})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    response = MiniMaxClient(_profile()).complete(_context(tmp_path), model="MiniMax-M3", max_tokens=100, temperature=0.2)

    assert captured["url"] == "https://minnimax.chat/v1/responses"
    assert captured["payload"]["temperature"] == 0.2
    assert "reasoning" not in captured["payload"]
    assert response.text == "ok"


def test_minimax_sends_reasoning_and_parses_native_function_call(monkeypatch, tmp_path):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return FakeHttpResponse(
            {
                "status": "completed",
                "output": [
                    {"type": "reasoning", "summary": [{"text": "inspect"}]},
                    {"type": "function_call", "call_id": "call-1", "name": "read_file", "arguments": '{"path":"a.py"}'},
                ],
            }
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    response = MiniMaxClient(_profile()).complete(
        _context(tmp_path),
        model="MiniMax-M3",
        max_tokens=100,
        temperature=0.2,
        model_profile={"thinking_enabled": True, "reasoning_effort": "minimal"},
    )

    assert captured["payload"]["reasoning"] == {"effort": "minimal"}
    assert "temperature" not in captured["payload"]
    assert response.reasoning == "inspect"
    assert [(call.call_id, call.name, call.arguments) for call in response.tool_calls or []] == [("call-1", "read_file", {"path": "a.py"})]


def test_minimax_rejects_invalid_temperature_when_reasoning_is_disabled(tmp_path):
    try:
        MiniMaxClient(_profile()).request_preview(_context(tmp_path), model="MiniMax-M3", max_tokens=100, temperature=0)
    except ValueError as exc:
        assert "temperature" in str(exc)
    else:
        raise AssertionError("expected invalid temperature to be rejected")
