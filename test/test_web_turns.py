from __future__ import annotations

import threading
import time
from pathlib import Path

from src.app.web_runs import WebRun, WebRunManager
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
    assert all(detail["event"] != "native_tool_calls_received" for detail in steps[0]["details"])
    assert steps[0]["context_audit_ref"].endswith("context-0001.json")
    assert "reasoning_text" not in steps[0]
    assert steps[1]["response_text"] == "done"
    assert steps[1]["process_content"] == ""


def test_steps_keep_full_tool_result_summary():
    full_result = "x" * 1601
    events = [
        {"event": "model_responded", "created_at": "2026-08-30T15:23:41Z", "native_tool_calls": [{"call_id": "call-1", "name": "read_file", "arguments": {}}]},
        {"event": "tool_requested", "created_at": "2026-08-30T15:23:42Z", "call_id": "call-1", "name": "read_file", "args": {}},
        {"event": "tool_executed", "created_at": "2026-08-30T15:23:43Z", "call_id": "call-1", "name": "read_file", "status": "success", "result_summary": full_result},
    ]

    steps, _ = build_reasoning_steps(events, run_id="run-1")

    assert steps[0]["tool_calls"][0]["result_text"] == full_result


def test_active_run_snapshot_contains_current_steps_and_cursor():
    run = WebRun("web-run-1", "project-1", Path("."), "session-1", user_message="检查代码")
    run.reasoning_steps = [{"step_id": "run-1:1", "index": 1, "status": "running"}]
    run.emit("step_patch", step=run.reasoning_steps[0])

    snapshot = run.snapshot()

    assert snapshot["user_message"] == "检查代码"
    assert snapshot["reasoning_steps"] == [{"step_id": "run-1:1", "index": 1, "status": "running"}]
    assert snapshot["event_cursor"] == 1


def test_run_lookup_rejects_mismatched_project_or_session():
    manager = WebRunManager.__new__(WebRunManager)
    manager.lock = threading.RLock()
    run = WebRun("web-run-1", "project-1", Path("."), "session-1")
    manager.runs = {run.web_run_id: run}

    assert manager.get_run("web-run-1", project_id="project-1", session_id="session-1") is run

    try:
        manager.get_run("web-run-1", project_id="project-2")
    except KeyError:
        pass
    else:
        raise AssertionError("run lookup must reject another project")

    try:
        manager.get_run("web-run-1", session_id="session-2")
    except KeyError:
        pass
    else:
        raise AssertionError("run lookup must reject another session")


def test_abort_returns_aborting_without_waiting_for_run_thread():
    """abort 接口只登记请求，不能阻塞等待后台工具线程结束。"""
    manager = WebRunManager.__new__(WebRunManager)
    manager.lock = threading.RLock()
    run = WebRun("web-run-1", "project-1", Path("."), "session-1")

    class Agent:
        def __init__(self):
            self.aborted = False

        def abort(self):
            self.aborted = True

    agent = Agent()
    run.agent = agent
    run.status = "running"
    run.thread = threading.Thread(target=lambda: time.sleep(0.5), daemon=True)
    run.thread.start()
    manager.runs = {run.web_run_id: run}

    started = time.monotonic()
    result = manager.abort(run.web_run_id, timeout_seconds=10, project_id="project-1", session_id="session-1")

    assert time.monotonic() - started < 0.5
    assert result.status == "aborting"
    assert agent.aborted is True
    assert run.events[-1]["event"] == "run_abort_requested"
