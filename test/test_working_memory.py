from __future__ import annotations

from src.memory.working import WorkingMemory
from src.policy.final_gate import FinalGate
from src.state.todo import TodoLedger
from src.state.task import TaskState


def test_tool_observation_keeps_summary_and_artifact_only(tmp_path):
    memory = WorkingMemory(tmp_path)
    memory.observe_tool("run_shell", "success", "x" * 1200, ".jcode/runs/run-1/artifacts/output.txt")

    observation = memory.to_dict()["tools"]["observations"][0]
    assert observation == {
        "tool": "run_shell",
        "status": "success",
        "summary": "x" * 500 + "\n[...middle omitted...]\n" + "x" * 500,
        "artifact": ".jcode/runs/run-1/artifacts/output.txt",
    }


def test_file_read_projection_shows_current_range(tmp_path):
    memory = WorkingMemory(tmp_path)
    memory.note_file_read("note.txt", {"path": "note.txt", "start": 0, "end": None, "max_chars": 20}, "1:5", {
        "file_size": 5, "returned_chars": 5, "missing_chars": 0, "complete": True,
    })

    rendered = memory.render()

    assert "file_reads:" in rendered
    assert "range: 0..EOF" in rendered
    assert "complete: true" in rendered


def test_todo_projection_renders_items_and_progress(tmp_path):
    memory = WorkingMemory(tmp_path)
    memory.sync_todos({
        "items": [
            {"todo_id": "todo_1", "content": "完成分析", "status": "completed", "priority": "high"},
            {"todo_id": "todo_2", "content": "补测试", "status": "in_progress", "priority": "normal"},
            {"todo_id": "todo_3", "content": "写文档", "status": "pending", "priority": "normal"},
        ]
    })

    rendered = memory.render()

    assert "todo_progress: 1/3 completed (33%), 1 in progress, 1 pending" in rendered
    assert "todo_1 [completed] high - 完成分析" in rendered
    assert "todo_2 [in_progress] normal - 补测试" in rendered


def test_todo_list_renders_progress(tmp_path):
    ledger = TodoLedger()
    ledger.add("完成分析", status="completed")
    ledger.add("补测试", status="in_progress")

    rendered = ledger.render_list()

    assert rendered.splitlines()[0] == "Todo progress: 1/2 completed (50%), 1 in progress, 0 pending"


def test_final_gate_requires_pending_todo_in_final_answer(tmp_path):
    task = TaskState.create("完成任务")
    session = {"todo_ledger": {"items": [{"todo_id": "todo_1", "status": "pending"}]}}

    denied = FinalGate().check("任务处理完成。", task, WorkingMemory(tmp_path), session=session)
    allowed = FinalGate().check("任务暂未完成，剩余 todo_1。", task, WorkingMemory(tmp_path), session=session)

    assert denied["reason"] == "unaddressed_pending_todos"
    assert allowed["allowed"] is True
