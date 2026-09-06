from pathlib import Path

from src.memory.working import WorkingMemory
from src.state.checkpoint import SCHEMA_VERSION, evaluate_checkpoint_data
from src.state.session import SessionStore
from src.state.workspace import Workspace


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
