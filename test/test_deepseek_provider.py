import json

from src.context.manager import ContextManager
from src.memory.working import WorkingMemory
from src.providers.deepseek import DeepSeekClient
from src.providers.profiles import ModelProfile
from src.state.workspace import Workspace
from src.tools.registry import build_default_registry


class FakeHttpResponse:
    def __init__(self, data): self.data = data
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self): return json.dumps(self.data).encode("utf-8")


def profile(key="key"):
    return ModelProfile("deepseek", "deepseek", "openai_responses", "deepseek-v4-pro", key, "https://api.deepseek.com", 1048576, 393216, "native", True, "high", ("low", "high", "max"))


def context(tmp_path):
    return ContextManager(Workspace.build(tmp_path), object(), build_default_registry(), model_profile=profile(), actual_max_new_tokens=100).build({"history": []}, WorkingMemory(tmp_path), "do the task", allowed_tools=frozenset({"read_file"})).context_result


def test_preview_and_payload_share_provider_snapshot(monkeypatch, tmp_path):
    captured = {}
    def fake_urlopen(request, timeout):
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return FakeHttpResponse({"status": "completed", "output_text": "ok", "output": []})
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    c = context(tmp_path)
    client = DeepSeekClient(profile())
    preview = client.request_preview(c, model=c.provider_input.instructions and profile().model, max_tokens=100, temperature=0.2)
    client.complete(c, model=profile().model, max_tokens=100, temperature=0.2)
    assert preview["instructions"] == captured["payload"]["instructions"] == c.provider_input.instructions
    assert preview["input"] == captured["payload"]["input"] == c.provider_input.input
    assert preview["tools"] == captured["payload"]["tools"] == c.provider_input.tools


def test_missing_api_key_uses_local_token_count(tmp_path):
    c = context(tmp_path)
    response = DeepSeekClient(profile(key="")).complete(c, model=profile().model, max_tokens=100, temperature=0.2)
    assert response.input_tokens == c.provider_input.serialized_input_tokens


def test_summary_request_uses_schema_and_reads_message_content(monkeypatch):
    captured = {}
    summary = {
        "summary_version": "9.6",
        "decisions": [],
        "files_read": [],
        "files_modified": [],
        "key_findings": [],
        "tool_failures": [],
        "freshness_events": [],
        "unresolved_blockers": [],
        "next_steps": [],
        "artifact_paths": [],
    }

    def fake_urlopen(request, timeout):
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return FakeHttpResponse({"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(summary)}]}]})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    result = DeepSeekClient(profile()).complete_summary(
        {"instructions": "Return JSON", "input": [{"role": "user", "content": "{}"}], "tools": []},
        profile_id="deepseek",
        max_output_tokens=2048,
    )

    assert json.loads(result) == summary
    assert captured["payload"]["text"]["format"]["type"] == "json_schema"
    assert captured["payload"]["text"]["format"]["name"] == "compact_summary"
