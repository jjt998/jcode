import json

from src.context.summary import build_deterministic_summary, build_summary_request, call_summary_model, validate_summary_payload
from src.context.budget import TokenizerAdapter
from src.providers.profiles import ModelProfile


def profile():
    return ModelProfile("m", "deepseek", "openai_responses", "m", "", "https://example.test", 1048576, 393216)


def test_summary_schema_and_deterministic_metadata_only():
    payload = build_deterministic_summary({}, [{"tool_name": "read_file", "arguments": {"path": "src/a.py"}, "content": "assistant prose ignored"}])
    assert payload.summary_version == "9.6"
    assert "task_goal" not in payload.model_dump()
    assert payload.files_read == [{"path": "src/a.py", "tool": "read_file"}]
    assert payload.key_findings == []


def test_summary_request_has_no_tools_or_working_memory():
    request = build_summary_request("summary instructions", {"summary_version": "9.6"}, [{"event_id": "e"}])
    assert request["tools"] == []
    assert set(request) == {"instructions", "input", "tools"}


def test_summary_model_retries_twice_then_fallback(monkeypatch):
    class Router:
        def __init__(self): self.calls = 0
        def complete_summary(self, **kwargs):
            self.calls += 1
            raise RuntimeError("timeout")
    router = Router()
    monkeypatch.setattr("src.context.summary.time.sleep", lambda _: None)
    result, audit = call_summary_model(router, build_summary_request("i", None, []), profile_id="m", profile=profile(), actual_max_new_tokens=8192, tokenizer=TokenizerAdapter(), initial_retry_delay_seconds=0)
    assert result is None
    assert router.calls == 3
    assert audit["status"] == "fallback"


def test_invalid_summary_json_is_rejected():
    try:
        validate_summary_payload("not-json")
    except (ValueError, json.JSONDecodeError):
        pass
    else:
        raise AssertionError("invalid summary must fail")


def test_old_summary_schema_is_rejected():
    """旧摘要不得绕过版本校验重新进入压缩历史。"""
    try:
        validate_summary_payload({"summary_version": "9.5"})
    except ValueError as exc:
        assert "schema mismatch" in str(exc)
    else:
        raise AssertionError("old compact summary schema must fail")
