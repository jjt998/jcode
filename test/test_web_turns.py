from __future__ import annotations

import json

from src.app.web_turns import build_session_turns


def test_build_session_turns_extracts_first_reasoning_block(tmp_path):
    project_root = tmp_path
    run_dir = project_root / ".jcode" / "runs" / "run-1"
    run_dir.mkdir(parents=True, exist_ok=True)
    trace_rows = [
        {
            "event": "model_responded",
            "response_text": "<reasoning>第一段</reasoning>后面还有<reasoning>第二段</reasoning>",
        },
        {
            "event": "web_run_completed",
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

    assert turns[0]["reasoning_text"] == "第一段"
    assert turns[0]["assistant_message"] == "done"
    assert turns[0]["event_count"] == 2


def test_build_session_turns_leaves_reasoning_empty_when_missing(tmp_path):
    project_root = tmp_path
    run_dir = project_root / ".jcode" / "runs" / "run-1"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "trace.jsonl").write_text(
        json.dumps({"event": "web_run_completed", "final_text": "done"}, ensure_ascii=False),
        encoding="utf-8",
    )

    session = {
        "id": "session-1",
        "run_ids": ["run-1"],
        "history": [
            {"role": "user", "content": "帮我看一下", "run_id": "run-1"},
            {"role": "assistant", "content": "done", "run_id": "run-1"},
        ],
    }

    turns = build_session_turns("default", project_root, session)["turns"]

    assert turns[0]["reasoning_text"] == ""
