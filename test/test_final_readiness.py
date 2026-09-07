from src.policy.final_gate import FinalGate
from src.runtime.final_readiness import build_final_readiness
from src.state.task import TaskState


class Result:
    def __init__(self, status, *, error_type=None, changed_files=None):
        self.status = status
        self.error_type = error_type
        self.changed_files = changed_files or []


def test_successful_write_closes_same_path_patch_failure_then_requests_verification():
    task = TaskState.create("edit")
    task.record_tool("apply_patch", Result("error", error_type="patch_nonunique"), arguments={"path": "src/a.py"}, call_id="patch-1")
    task.record_tool("write_file", Result("success", changed_files=["src/a.py"]), arguments={"path": "src/a.py"}, call_id="write-1")

    decision = FinalGate().check("done", task, None, session={})

    assert not task.unresolved_tool_failures
    assert task.resolved_tool_failures[0]["resolved_by"] == "write_file"
    assert decision["action"] == "rerun_agent"
    assert decision["reason"] == "changed_paths_without_verification"


def test_shell_inspection_failure_is_yellow_and_never_blocks_final():
    task = TaskState.create("inspect")
    task.record_tool("run_shell", Result("error", error_type="tool_failed"), arguments={"command": "wc -l missing.txt"}, call_id="shell-1")

    decision = FinalGate().check("done", task, None, session={})

    assert decision["action"] == "safe_finalize"
    assert decision["agent_quality"]["level"] == "yellow"
    assert decision["finalization"]["final_content_status"] == "present"


def test_hard_failure_reruns_once_then_finalizes_red_when_budget_is_exhausted():
    task = TaskState.create("edit")
    task.record_tool("write_file", Result("error", error_type="tool_failed"), arguments={"path": "src/a.py"})
    gate = FinalGate()

    first = gate.check("done", task, None, session={})
    task.agent_rerun_count = task.agent_rerun_budget
    second = gate.check("done", task, None, session={})

    assert first["action"] == "rerun_agent"
    assert second["action"] == "safe_finalize"
    assert second["agent_quality"]["level"] == "red"


def test_empty_final_is_recorded_and_finalized_after_its_single_intervention():
    task = TaskState.create("answer")
    gate = FinalGate()

    first = gate.check("", task, None, session={})
    second = gate.check("", task, None, session={})

    assert first["action"] == "rerun_agent"
    assert second["action"] == "safe_finalize"
    assert second["finalization"]["final_content_status"] == "empty"
    assert second["assurance"]["final_text"] == "unverified"


def test_final_context_overflow_is_harness_red_and_does_not_rerun_agent():
    task = TaskState.create("answer")
    decision = FinalGate().check("done", task, None, session={}, context={"final_capacity_status": {"can_send": False}})

    assert decision["action"] == "safe_finalize"
    assert decision["harness_quality"]["level"] == "red"
    assert decision["agent_quality"]["level"] == "green"


def test_explicit_required_artifact_generates_agent_rerun_until_file_exists(tmp_path):
    class Workspace:
        def resolve_path(self, path):
            return tmp_path / path

    task = TaskState.create("请创建文件 `output.md`")
    missing = build_final_readiness(task, {}, Workspace())
    (tmp_path / "output.md").write_text("done", encoding="utf-8")
    present = build_final_readiness(task, {}, Workspace())

    assert any(reason["code"] == "missing_required_artifact" for reason in missing.reasons)
    assert not present.reasons
