from pathlib import Path

from src.memory.working import WorkingMemory
from src.state.checkpoint import CheckpointManager, SCHEMA_VERSION, evaluate_checkpoint_data
from src.state.task import TaskState
from src.state.session import SessionStore
from src.state.workspace import Workspace
from src.state.resume import build_resume_context


def test_session_schema5_atomic_save_and_working_memory_v2(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    session = store.load_requested(None, None, tmp_path)
    memory = WorkingMemory(tmp_path, task_goal="goal")
    session["working_memory"] = memory.to_dict()
    store.save(session)
    loaded = store.load_requested(session["id"], None, tmp_path)
    assert loaded["schema_version"] == 5
    assert loaded["working_memory"]["schema"] == "jcode.layered_memory.v2"


def test_checkpoint_schema2_reads_hot_file_freshness(tmp_path):
    workspace = Workspace.build(tmp_path)
    data = {"schema_version": SCHEMA_VERSION, "resumable": True, "workspace_fingerprint": workspace.fingerprint(), "working_memory": {"files": {"hot": [], "freshness": {}}}}
    assert evaluate_checkpoint_data(data, workspace)[0] == "full_valid"


def test_checkpoint_persists_final_gate_dual_quality_snapshot(tmp_path):
    workspace = Workspace.build(tmp_path)
    task = TaskState.create("answer")
    task.agent_quality = {"level": "yellow", "reasons": [{"code": "unresolved_todo"}]}
    task.harness_quality = {"level": "green", "reasons": []}
    task.assurance = {"final_text": "verified", "execution_evidence": "verified", "runtime_state": "verified"}
    task.finalization = {"status": "committed"}
    checkpoint = CheckpointManager(tmp_path, workspace).create({"id": "session-1"}, task, WorkingMemory(tmp_path))

    assert checkpoint["agent_quality"]["level"] == "yellow"
    assert checkpoint["harness_quality"]["level"] == "green"
    assert checkpoint["finalization"]["status"] == "committed"


def test_resume_context_reports_external_workspace_changes(tmp_path):
    """会话延续评估发现外部修改时输出 changed_paths。"""
    workspace = Workspace.build(tmp_path)
    target = tmp_path / "src" / "a.py"
    target.parent.mkdir()
    target.write_text("old", encoding="utf-8")
    baseline = workspace.baseline()
    target.write_text("new", encoding="utf-8")
    store = SessionStore(tmp_path / "sessions")
    session = store.load_requested(None, None, tmp_path)
    session["run_ids"] = ["run-1"]
    run_store = type("Runs", (), {"run_dir": lambda self, run_id: tmp_path / "missing"})()
    checkpoint = {"schema_version": SCHEMA_VERSION, "resumable": True, "workspace_baseline": baseline, "workspace_fingerprint": "old", "working_memory": {"files": {"hot": [], "freshness": {}}}}
    (tmp_path / "missing").mkdir()
    (tmp_path / "missing" / "checkpoint.json").write_text(__import__("json").dumps(checkpoint), encoding="utf-8")

    context = build_resume_context(session=session, session_store=store, run_store=run_store, workspace=workspace, resume_requested=None, execution_fingerprint={})

    assert any(path.endswith("src/a.py") for path in context["changed_paths"])
    assert context["workspace_mismatch"] is True
