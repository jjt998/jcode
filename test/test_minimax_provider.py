import json

from src.context.manager import ContextManager
from src.memory.working import WorkingMemory
from src.providers.minimax import MiniMaxClient
from src.providers.profiles import ModelProfile
from src.state.workspace import Workspace
from src.tools.registry import build_default_registry


class FakeHttpResponse:
    def __init__(self, data): self.data = data
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self): return json.dumps(self.data).encode("utf-8")


def profile():
    return ModelProfile("minimax-m3", "minimax", "openai_responses", "MiniMax-M3", "key", "https://minnimax.chat/v1", 1000000, 524288, "optional", False, "minimal", ("minimal", "low", "medium", "high"))


def test_minimax_payload_uses_snapshot(monkeypatch, tmp_path):
    captured = {}
    def fake_urlopen(request, timeout):
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return FakeHttpResponse({"status": "completed", "output_text": "ok", "output": []})
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    c = ContextManager(Workspace.build(tmp_path), object(), build_default_registry(), model_profile=profile(), actual_max_new_tokens=100).build({"history": []}, WorkingMemory(tmp_path), "task", allowed_tools=frozenset()).context_result
    MiniMaxClient(profile()).complete(c, model=profile().model, max_tokens=100, temperature=0.2)
    assert captured["payload"]["input"] == c.provider_input.input
    assert captured["payload"]["tools"] == c.provider_input.tools
