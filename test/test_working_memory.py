from __future__ import annotations

from src.memory.working import WorkingMemory


def test_tool_observation_keeps_summary_and_artifact_only(tmp_path):
    memory = WorkingMemory(tmp_path)
    memory.observe_tool("run_shell", "success", "x" * 500, ".jcode/runs/run-1/artifacts/output.txt")

    observation = memory.to_dict()["tools"]["observations"][0]
    assert observation == {
        "tool": "run_shell",
        "status": "success",
        "summary": "x" * 300,
        "artifact": ".jcode/runs/run-1/artifacts/output.txt",
    }
