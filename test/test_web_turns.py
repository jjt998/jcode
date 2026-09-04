from __future__ import annotations

from src.app.web_steps import build_reasoning_steps


def test_steps_show_process_content_without_reasoning():
    events = [
        {"event": "context_built", "created_at": "2026-08-30T15:23:40Z", "context_audit_ref": ".jcode/runs/run-1/artifacts/context-0001.json"},
        {"event": "model_responded", "created_at": "2026-08-30T15:23:41Z", "response_text": "I will inspect the file.", "reasoning_text": "inspect", "native_tool_calls": [{"call_id": "call-1", "name": "read_file", "arguments": {"path": "README.md"}}]},
        {"event": "tool_requested", "created_at": "2026-08-30T15:23:42Z", "call_id": "call-1", "name": "read_file", "args": {"path": "README.md"}},
        {"event": "tool_executed", "created_at": "2026-08-30T15:23:43Z", "call_id": "call-1", "name": "read_file", "status": "success", "result_summary": "read ok", "artifact_ref": ".jcode/runs/run-1/artifacts/read_file-output.txt"},
        {"event": "model_responded", "created_at": "2026-08-30T15:23:44Z", "response_text": "done", "reasoning_text": "finish", "native_tool_calls": []},
        {"event": "web_run_completed", "created_at": "2026-08-30T15:23:45Z", "final_text": "done"},
    ]

    steps, final_text = build_reasoning_steps(events, run_id="run-1")

    assert final_text == "done"
    assert len(steps) == 2
    assert steps[0]["tool_calls"][0]["name"] == "read_file"
    assert steps[0]["tool_calls"][0]["result_text"] == "read ok"
    assert steps[0]["tool_calls"][0]["artifact_ref"].endswith("read_file-output.txt")
    assert steps[0]["process_content"] == "I will inspect the file."
    assert steps[0]["context_audit_ref"].endswith("context-0001.json")
    assert "reasoning_text" not in steps[0]
    assert steps[1]["response_text"] == "done"
    assert steps[1]["process_content"] == ""
