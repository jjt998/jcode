from pathlib import Path

from eval.loader import CaseLoader
from eval.protocol import EvalCase, RunRecord
from eval.scorer import OfflineScorer
from eval.workspace import WorkspaceSandbox


def test_case_loader_reads_versioned_cases(tmp_path: Path):
    case_dir = tmp_path / "cases"
    case_dir.mkdir()
    (case_dir / "case.json").write_text(
        '{"case_id":"case-1","eval_version":"1.0","category":"demo",'
        '"scenario":"D1","description":"demo","prompt":"run"}',
        encoding="utf-8",
    )
    case = CaseLoader(case_dir).find("case-1")
    assert case.category == "demo"


def test_workspace_sandbox_tracks_content_changes(tmp_path: Path):
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    (fixture / "main.py").write_text("old", encoding="utf-8")
    sandbox = WorkspaceSandbox(fixture, tmp_path / "run")
    sandbox.prepare()
    (sandbox.root / "main.py").write_text("new", encoding="utf-8")
    assert sandbox.changed_files() == ["main.py"]


def test_offline_scorer_evaluates_persisted_record():
    case = EvalCase.from_dict(
        {
            "case_id": "score-1",
            "eval_version": "1.0",
            "category": "demo",
            "scenario": "D1",
            "description": "demo",
            "prompt": "run",
            "acceptance_predicates": [{"final_status_is": "completed"}],
        }
    )
    record = RunRecord(
        case_id=case.case_id,
        eval_version=case.eval_version,
        runner_version="1.0",
        scorer_version="1.0",
        jcode_revision="test",
        run_id="run-1",
        started_at="now",
        finished_at="now",
        final_status="completed",
        process_exit_code=0,
        stdout="done",
    )
    result = OfflineScorer().score(case, record)
    assert result["passed"] is True
    assert result["agent_quality"]["level"] == "green"


def test_run_record_round_trip(tmp_path: Path):
    record = RunRecord("case", "1.0", "1", "1", "rev", "run", "s", "f", "completed")
    path = tmp_path / "run_record.json"
    record.write(path)
    assert RunRecord.load(path).run_id == "run"
