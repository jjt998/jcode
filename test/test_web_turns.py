from __future__ import annotations

import json

from src.app.web_steps import StepTimelineBuilder, build_reasoning_steps
from src.app.web_turns import build_session_turns


def test_build_reasoning_steps_groups_tools_into_the_current_step():
    events = [
        {"event": "context_built", "created_at": "2026-08-30T15:23:40Z", "context": "ctx"},
        {
            "event": "model_responded",
            "created_at": "2026-08-30T15:23:41Z",
            "response_text": '<reasoning>第一步检查。</reasoning><tool name="read_file">{"path":"README.md"}</tool>',
        },
        {
            "event": "tool_requested",
            "created_at": "2026-08-30T15:23:42Z",
            "name": "read_file",
            "args": {"path": "README.md"},
        },
        {
            "event": "tool_executed",
            "created_at": "2026-08-30T15:23:43Z",
            "name": "read_file",
            "status": "success",
            "result": "read ok",
        },
        {
            "event": "model_responded",
            "created_at": "2026-08-30T15:23:44Z",
            "response_text": "<reasoning>第二步收尾。</reasoning><final>done</final>",
        },
        {
            "event": "web_run_completed",
            "created_at": "2026-08-30T15:23:45Z",
            "final_text": "done",
        },
    ]

    steps, final_text = build_reasoning_steps(events, run_id="run-1")

    assert final_text == "done"
    assert len(steps) == 2
    assert steps[0]["step_id"] == "run-1:1"
    assert steps[0]["reasoning_text"] == "第一步检查。"
    assert steps[0]["context_text"] == "ctx"
    assert steps[0]["tool_calls"][0]["name"] == "read_file"
    assert steps[0]["tool_calls"][0]["result_text"] == "read ok"
    assert steps[0]["tool_calls"][0]["status"] == "success"
    assert steps[0]["status"] == "success"
    assert steps[1]["reasoning_text"] == "第二步收尾。"
    assert steps[1]["tool_calls"] == []


def test_step_timeline_builder_updates_the_same_step_incrementally():
    builder = StepTimelineBuilder(run_id="run-2")
    builder.consume({"event": "context_built", "created_at": "2026-08-30T15:00:00Z", "context": "ctx"})
    first = builder.consume(
        {
            "event": "model_responded",
            "created_at": "2026-08-30T15:00:01Z",
            "response_text": "<reasoning>先看一下。</reasoning>",
        }
    )[0]
    running = builder.consume(
        {
            "event": "tool_requested",
            "created_at": "2026-08-30T15:00:02Z",
            "name": "list_files",
            "args": {"recursive": True},
        }
    )[0]
    success = builder.consume(
        {
            "event": "tool_executed",
            "created_at": "2026-08-30T15:00:03Z",
            "name": "list_files",
            "status": "success",
            "result": {"files": ["a.py", "b.py"]},
        }
    )[0]

    assert first["status"] == "pending"
    assert running["status"] == "running"
    assert success["status"] == "success"
    assert success["tool_calls"][0]["args_text"] == json.dumps({"recursive": True}, ensure_ascii=False, indent=2)


def test_build_session_turns_uses_step_list_and_falls_back_without_trace(tmp_path):
    project_root = tmp_path
    run_dir = project_root / ".jcode" / "runs" / "run-1"
    run_dir.mkdir(parents=True, exist_ok=True)
    trace_rows = [
        {
            "event": "model_responded",
            "created_at": "2026-08-30T15:23:41Z",
            "response_text": "<reasoning>第一段</reasoning><final>done</final>",
        },
        {
            "event": "web_run_completed",
            "created_at": "2026-08-30T15:23:45Z",
            "final_text": "done",
        },
    ]
    (run_dir / "trace.jsonl").write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in trace_rows), encoding="utf-8")

    session = {
        "id": "session-1",
        "run_ids": ["run-1"],
        "history": [
            {"role": "user", "content": "帮我看一下", "run_id": "run-1"},
            {"role": "assistant", "content": "done", "run_id": "run-1"},
        ],
    }

    turns = build_session_turns("default", project_root, session)["turns"]

    assert turns[0]["final_text"] == "done"
    assert turns[0]["reasoning_steps"][0]["reasoning_text"] == "第一段"
    assert turns[0]["step_count"] == 1
