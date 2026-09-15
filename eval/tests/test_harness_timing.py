from __future__ import annotations

import json

from src.evidence.timing import HarnessTiming


def test_harness_timing_records_component_and_prints_console(capsys):
    timing = HarnessTiming("run-1")
    span = timing.start("provider_request", "complete", metadata={"retry_index": 0})
    record = timing.finish(span)

    captured = capsys.readouterr()
    assert record["event"] == "harness_component_finished"
    assert record["run_id"] == "run-1"
    assert record["component"] == "provider_request"
    assert record["duration_ms"] >= 0
    assert "[harness-timing]" in captured.err
    assert "component=provider_request" in captured.err
    assert "level=phase" in captured.err


def test_harness_timing_summary_aggregates_components():
    timing = HarnessTiming("run-1")
    timing.finish(timing.start("tool_call", "read_file"))
    timing.finish(timing.start("tool_call", "run_shell"))
    timing.finish(timing.start("context_build", "build"))

    summary = timing.summary(42)

    assert summary["duration_ms"] == 42
    assert summary["span_count"] == 3
    assert set(summary["components"]) == {"tool_call", "context_build"}
    assert summary["components"]["tool_call"] >= 0
