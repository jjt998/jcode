from __future__ import annotations

from types import SimpleNamespace

from src.policy.secrets import SecretRedactor
from src.runtime.agent import JCodeAgent


class EventRecorder:
    def __init__(self):
        self.rows = []

    def emit(self, event: str, **payload) -> None:
        self.rows.append({"event": event, **payload})


def test_context_compression_event_does_not_pass_run_id_twice():
    """压缩对比事件的 run_id 只由 emit 的显式参数传入。"""
    agent = JCodeAgent.__new__(JCodeAgent)
    event_recorder = EventRecorder()
    agent.session_events = event_recorder
    agent._record_trace = lambda *args, **kwargs: None
    task_state = SimpleNamespace(run_id="run-1")
    context_result = SimpleNamespace(
        ctx_info={
            "pressure": {"level": 1, "range": "high"},
            "compression_comparison": {"before_tokens": 100, "after_tokens": 80, "content_changes": [{"change": "removed"}]},
        },
        compact_audit={"status": "fallback", "source": "rule", "summary_text": '{"summary":"规则摘要"}', "artifact_ref": "audit.json"},
    )

    agent._emit_compact_context_events(
        "unused-run-dir",
        task_state,
        context_result,
        {"should_compact": False, "status": "idle"},
    )

    comparison_event = next(row for row in event_recorder.rows if row["event"] == "context_compression_compared")
    assert comparison_event["run_id"] == "run-1"
    assert comparison_event["content_changes"] == [{"change": "removed"}]
    assert comparison_event["result"]["status"] == "fallback"
    assert comparison_event["result"]["label"] == "降级为规则压缩"
    assert comparison_event["result"]["summary_text"] == '{"summary":"规则摘要"}'


def test_unexpected_runtime_error_is_printed_to_stderr(capsys):
    """未处理异常需要输出完整且脱敏后的控制台诊断信息。"""
    agent = JCodeAgent.__new__(JCodeAgent)
    agent.redactor = SecretRedactor({"secret-value"})

    def fail(_user_message: str) -> str:
        raise RuntimeError("request failed: secret-value")

    agent._ask_loop = fail

    result = agent.ask("test")

    captured = capsys.readouterr()
    assert result == "运行时错误: request failed: secret-value"
    assert "RuntimeError: request failed: [REDACTED]" in captured.err
    assert "secret-value" not in captured.err
