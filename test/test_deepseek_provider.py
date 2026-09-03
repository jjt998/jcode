from __future__ import annotations

import json

from src.context.result import ContextResult, HistoryEvent, ToolDefinition
from src.memory.working import WorkingMemory
from src.providers.deepseek import DeepSeekClient
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
        id="deepseek",
        provider="deepseek",
        api_protocol="openai_responses",
        model="deepseek-v4-pro",
        api_key="key",
        base_url="https://api.deepseek.com",
        reasoning_mode="native",
        thinking_enabled=True,
        reasoning_effort="high",
        reasoning_effort_options=("low", "high", "max"),
    )


def _context(tmp_path) -> ContextResult:
    return ContextResult(
        prefix="stable prefix",
        skill="use repository evidence",
        history=[
            HistoryEvent("user", "event-1", "turn-1", "earlier request"),
            HistoryEvent("tool_call", "event-2", "turn-1", tool_name="read_file", call_id="call-1", arguments={"path": "a.py"}),
            HistoryEvent("tool_result", "event-3", "turn-1", "file content", tool_name="read_file", call_id="call-1"),
        ],
        working_memory=WorkingMemory(tmp_path, task_goal="current task"),
        current_request="do the task",
        tools=[ToolDefinition("read_file", "read", {"type": "object"}, True, False)],
        ctx_info={},
    )


def test_deepseek_responses_compiles_cache_order_and_native_tools(monkeypatch, tmp_path):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return FakeHttpResponse({"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}]})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    profile = _profile()
    response = DeepSeekClient(profile).complete(_context(tmp_path), model=profile.model, max_tokens=100, temperature=0.2)

    payload = captured["payload"]
    assert captured["url"].endswith("/responses")
    assert payload["instructions"] == "stable prefix"
    assert payload["reasoning"] == {"effort": "high"}
    assert "temperature" not in payload
    assert payload["input"][0]["content"].startswith("[JCode Skill Context]")
    assert payload["input"][-2]["content"].startswith("[JCode Working Memory]")
    assert payload["input"][-1] == {"role": "user", "content": "do the task"}
    assert payload["input"][2]["type"] == "function_call"
    assert payload["input"][3] == {"type": "function_call_output", "call_id": "call-1", "output": "file content"}
    assert response.text == "ok"


def test_deepseek_responses_parses_function_calls(monkeypatch, tmp_path):
    def fake_urlopen(request, timeout):
        return FakeHttpResponse({"status": "completed", "output": [{"type": "reasoning", "summary": [{"text": "inspect"}]}, {"type": "function_call", "call_id": "call-2", "name": "read_file", "arguments": '{"path":"a.py"}'}]})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    profile = _profile()
    response = DeepSeekClient(profile).complete(_context(tmp_path), model=profile.model, max_tokens=100, temperature=0.2)

    assert response.text == ""
    assert response.reasoning == "inspect"
    assert [(call.call_id, call.name, call.arguments) for call in response.tool_calls or []] == [("call-2", "read_file", {"path": "a.py"})]
