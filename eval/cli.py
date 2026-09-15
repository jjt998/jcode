from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from eval.loader import CaseLoader
from eval.runner import EvalRunner
from eval.scorer import OfflineScorer


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="JCode evaluation runner")
    parser.add_argument("--project-root", default=".")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    sub.add_parser("validate")
    run = sub.add_parser("run")
    run.add_argument("--case")
    run.add_argument("--category")
    run.add_argument("--all", action="store_true")
    run.add_argument("--manifest")
    run.add_argument("--keep-workspace", action="store_true")
    score = sub.add_parser("score")
    score.add_argument("--case-file", required=True)
    score.add_argument("--record-file", required=True)
    score.add_argument("--output", required=True)
    replay = sub.add_parser("replay")
    replay.add_argument("--result-dir", required=True)
    summary = sub.add_parser("summary")
    summary.add_argument("--score-file", action="append", required=True)
    summary.add_argument("--output")
    return parser


def main(argv: list[str] | None = None) -> int:
    # Windows 控制台默认可能是 GBK，评测输出必须稳定支持 UTF-8 证据摘要。
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    root = Path(args.project_root).resolve()
    loader = CaseLoader(root / "eval" / "cases")
    if args.command == "list":
        for case in loader.load_all():
            print(f"{case.case_id}\t{case.category}\t{case.scenario}")
        return 0
    if args.command == "validate":
        errors = loader.validate()
        for error in errors:
            print(error)
        return 1 if errors else 0
    if args.command == "run":
        if bool(args.case) + bool(args.category) + bool(args.all) + bool(args.manifest) != 1:
            raise SystemExit("run requires exactly one of --case, --category, --all, --manifest")
        cases = [loader.find(args.case)] if args.case else loader.load_manifest(args.manifest) if args.manifest else loader.load_all() if args.all else [case for case in loader.load_all() if case.category == args.category]
        if not cases:
            raise SystemExit("no matching eval cases")
        records = [EvalRunner(root).run(case, keep_workspace=args.keep_workspace) for case in cases]
        print(json.dumps([record.to_dict() for record in records], ensure_ascii=False, indent=2))
        return 0 if all(record.process_exit_code == 0 for record in records) else 1
    if args.command == "score":
        from eval.scorer import score_file
        result = score_file(args.case_file, args.record_file, args.output)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["passed"] else 1
    if args.command == "replay":
        from eval.replay import replay_summary
        print(json.dumps(replay_summary(args.result_dir), ensure_ascii=False, indent=2))
        return 0
    if args.command == "summary":
        from eval.summary import summarize_scores
        result = summarize_scores(args.score_file)
        payload = json.dumps(result, ensure_ascii=False, indent=2)
        if args.output:
            Path(args.output).write_text(payload, encoding="utf-8")
        print(payload)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
