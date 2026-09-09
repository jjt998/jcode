from __future__ import annotations

from src.state.checkpoint import CheckpointManager
from src.state.task import TaskState
from src.state.workspace import Workspace
from src.memory.working import WorkingMemory


def test_checkpoint_create_scans_workspace_once(tmp_path, monkeypatch):
    """Checkpoint 生成基线和指纹时不能重复递归扫描工作区。"""
    workspace = Workspace.build(tmp_path)
    calls = {"baseline": 0}
    original = workspace.baseline

    def counted_baseline():
        calls["baseline"] += 1
        return original()

    monkeypatch.setattr(workspace, "baseline", counted_baseline)
    task = TaskState.create("test")
    CheckpointManager(tmp_path, workspace).create({"id": "session-1"}, task, WorkingMemory(tmp_path))

    assert calls["baseline"] == 1
