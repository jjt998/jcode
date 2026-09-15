import json
from pathlib import Path

from eval.protocol import EvalCase, RunRecord
from eval.runner import EvalRunner
from eval.scorer import OfflineScorer


def test_external_file_fault_injection_is_explicit(tmp_path: Path):
    case = EvalCase.from_dict({
        "case_id": "fault-1", "eval_version": "1.0", "category": "checkpoint_resume",
        "scenario": "R2", "description": "stale", "prompt": "run",
        "fault_injection": {"type": "external_file_change", "path": "src/main.py", "content": "new"},
    })
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    EvalRunner._inject_fault(case, workspace)
    assert (workspace / "src/main.py").read_text(encoding="utf-8") == "new"


def test_trace_and_checkpoint_predicates_use_persisted_metadata():
    case = EvalCase.from_dict({
        "case_id": "score-2", "eval_version": "1.0", "category": "checkpoint_resume",
        "scenario": "R3", "description": "drift", "prompt": "run",
        "acceptance_predicates": [
            {"trace_event_exists": "workspace_mismatch"},
            {"checkpoint_status_is": "workspace_mismatch"},
        ],
    })
    record = RunRecord(
        case_id=case.case_id, eval_version="1.0", runner_version="1", scorer_version="1",
        jcode_revision="test", run_id="run-1", started_at="s", finished_at="f",
        final_status="completed", trace_events=[{"event": "workspace_mismatch"}],
        checkpoint={"status": "workspace_mismatch"},
    )
    result = OfflineScorer().score(case, record)
    assert result["passed"] is True


def test_summary_uses_category_field(tmp_path: Path):
    score_path = tmp_path / "score.json"
    score_path.write_text(json.dumps({"case_id": "odd-id", "category": "memory", "passed": True, "redlines": [], "agent_quality": {"level": "green"}, "harness_quality": {"level": "green"}}), encoding="utf-8")
    from eval.summary import summarize_scores
    assert summarize_scores([score_path])["categories"]["memory"]["passed"] == 1
