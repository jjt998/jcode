from src.policy.final_gate import FinalGate
from src.runtime.final_readiness import build_final_readiness
from src.state.task import TaskState


class Result:
    def __init__(self, status, *, error_type=None, changed_files=None):
        self.status = status
        self.error_type = error_type
        self.changed_files = changed_files or []


def test_successful_write_resolves_same_path_patch_failure():
    task = TaskState.create("edit")
    task.record_tool("apply_patch", Result("error", error_type="patch_nonunique"), arguments={"path": "src/a.py"}, call_id="patch-1")
    task.record_tool("write_file", Result("success", changed_files=["src/a.py"]), arguments={"path": "src/a.py"}, call_id="write-1")

    decision = FinalGate().check("done", task, None, session={})

    assert not task.unresolved_tool_failures
    assert task.resolved_tool_failures[0]["resolved_by"] == "write_file"
    assert decision["allowed"] is False
    assert decision["reason"] == "changed_paths_without_verification"


def test_shell_failure_is_not_resolved_by_write_file():
    task = TaskState.create("edit")
    task.record_tool("run_shell", Result("error", error_type="tool_failed"), arguments={"command": "pytest"}, call_id="shell-1")
    task.record_tool("write_file", Result("success", changed_files=["src/a.py"]), arguments={"path": "src/a.py"}, call_id="write-1")

    decision = FinalGate().check("done", task, None, session={})

    assert decision["action"] == "block"
    assert decision["reason"] == "unresolved_tool_failure"


def test_soft_reason_blocks_after_one_notice_without_new_evidence():
    task = TaskState.create("edit")
    task.record_tool("write_file", Result("success", changed_files=["src/a.py"]), arguments={"path": "src/a.py"})
    gate = FinalGate()

    first = gate.check("done", task, None, session={})
    second = gate.check("done", task, None, session={})

    assert first["action"] == "runtime_notice"
    assert second["action"] == "block"


def test_explicit_required_artifact_blocks_until_file_exists(tmp_path):
    class Workspace:
        def resolve_path(self, path):
            return tmp_path / path
    task = TaskState.create("请创建文件 `output.md`")

    missing = build_final_readiness(task, {}, Workspace())
    (tmp_path / "output.md").write_text("done", encoding="utf-8")
    present = build_final_readiness(task, {}, Workspace())

    assert any(reason["code"] == "missing_required_artifact" for reason in missing.reasons)
    assert not present.reasons
