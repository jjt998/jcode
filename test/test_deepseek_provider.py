from __future__ import annotations

import json

from src.providers.deepseek import DeepSeekClient
from src.providers.profiles import ModelProfile


class FakeHttpResponse:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps({"choices": [{"message": {"content": "<final>ok</final>", "reasoning_content": "原生思考"}}], "usage": {"prompt_tokens": 3, "completion_tokens": 5}}).encode("utf-8")


def test_deepseek_thinking_request_uses_official_fields(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return FakeHttpResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    profile = ModelProfile(
        id="deepseek",
        provider="deepseek",
        model="deepseek-v4-pro",
        api_key="key",
        base_url="https://api.deepseek.com",
        reasoning_mode="native",
        thinking_enabled=True,
        reasoning_effort="high",
        reasoning_effort_options=("low", "high", "max"),
    )

    response = DeepSeekClient(profile).complete([{"role": "user", "content": "hi"}], model=profile.model, max_tokens=100, temperature=0.2)

    assert captured["payload"]["thinking"] == {"type": "enabled"}
    assert captured["payload"]["reasoning_effort"] == "high"
    assert "temperature" not in captured["payload"]
    assert response.text == "<final>ok</final>"
    assert response.reasoning == "原生思考"


def test_deepseek_disabled_thinking_keeps_temperature(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return FakeHttpResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    profile = ModelProfile("deepseek", "deepseek", "deepseek-v4-pro", "key", "https://api.deepseek.com", reasoning_mode="optional")

    DeepSeekClient(profile).complete([{"role": "user", "content": "hi"}], model=profile.model, max_tokens=100, temperature=0.2)

    assert captured["payload"]["thinking"] == {"type": "disabled"}
    assert captured["payload"]["temperature"] == 0.2
