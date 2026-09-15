from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import uuid
import json
from pathlib import Path
from typing import Any

from eval.protocol import EvalCase, RunRecord, now_iso
from eval.workspace import WorkspaceSandbox


RUNNER_VERSION = "1.0"


class EvalRunner:
    """执行单个 Case 并收集原始运行证据。"""

    def __init__(self, project_root: str | Path, results_root: str | Path = "eval/reports"):
        self.project_root = Path(project_root).resolve()
        self.results_root = (self.project_root / results_root).resolve()
        self.results_root.mkdir(parents=True, exist_ok=True)

    def run(self, case: EvalCase, *, keep_workspace: bool = True) -> RunRecord:
        run_id = f"eval-{uuid.uuid4().hex[:10]}"
        started = time.perf_counter()
        started_at = now_iso()
        sandbox = WorkspaceSandbox(case.workspace_fixture)
        workspace = sandbox.prepare()
        initial_hash = sandbox.fingerprint()
        self._inject_fault(case, workspace)
        result_dir = self.results_root / case.case_id / run_id
        result_dir.mkdir(parents=True, exist_ok=True)
        command = self._command(case, workspace)
        timeout_seconds = int(case.config.get("timeout_seconds", 300))
        try:
            process = subprocess.run(
                command,
                cwd=workspace,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                env=self._environment(case),
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            # 超时必须落成结构化结果，不能让运行器异常退出而丢失现场。
            class TimeoutResult:
                returncode = None
                stdout = str(exc.stdout or "")
                stderr = str(exc.stderr or "")

            process = TimeoutResult()
        finished_at = now_iso()
        final_hash = sandbox.fingerprint()
        evidence = self._collect_evidence(workspace, result_dir)
        metadata = self._read_evidence_metadata(result_dir)
        record = RunRecord(
            case_id=case.case_id,
            eval_version=case.eval_version,
            runner_version=RUNNER_VERSION,
            scorer_version="1.0",
            jcode_revision=self._git_revision(),
            run_id=run_id,
            started_at=started_at,
            finished_at=finished_at,
            final_status="completed" if process.returncode == 0 else "timeout" if process.returncode is None else "process_failed",
            initial_workspace_hash=initial_hash,
            final_workspace_hash=final_hash,
            changed_files=sandbox.changed_files(),
            process_exit_code=process.returncode,
            latency_ms=int((time.perf_counter() - started) * 1000),
            evidence_paths=evidence,
            stdout=process.stdout[-12000:],
            stderr=process.stderr[-12000:],
            trace_events=metadata["trace_events"],
            report=metadata["report"],
            checkpoint=metadata["checkpoint"],
            checkpoint_status=str(metadata["checkpoint"].get("status") or metadata["checkpoint"].get("checkpoint_status") or ""),
            context_usage=dict(metadata["report"].get("context", {}) or metadata["report"].get("context_usage", {}) or {}),
            verification=dict(metadata["report"].get("verification", {}) or {}),
            tool_attempts=list(metadata["report"].get("tool_attempts", []) or []),
        )
        record.write(result_dir / "run_record.json")
        # 每次运行结束立即生成离线评分，保证原始证据和评分结果同目录归档。
        from eval.scorer import OfflineScorer
        score = OfflineScorer().score(case, record)
        (result_dir / "score.json").write_text(json.dumps(score, ensure_ascii=False, indent=2), encoding="utf-8")
        if not keep_workspace:
            sandbox.cleanup()
        return record

    @staticmethod
    def _inject_fault(case: EvalCase, workspace: Path) -> None:
        """按 Case 明确声明注入外部变化，不推断或扩展故障语义。"""
        injection = case.fault_injection
        kind = str(injection.get("type") or "")
        if kind == "external_file_change":
            path = str(injection.get("path") or "")
            if not path:
                raise ValueError("external_file_change requires path")
            target = (workspace / path).resolve()
            if workspace.resolve() not in target.parents:
                raise ValueError(f"fault path escapes workspace: {path}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(injection.get("content") or ""), encoding="utf-8")
        elif kind == "workspace_drift":
            path = str(injection.get("path") or ".eval-drift")
            target = (workspace / path).resolve()
            if workspace.resolve() not in target.parents:
                raise ValueError(f"fault path escapes workspace: {path}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(injection.get("content") or "drift"), encoding="utf-8")
        elif kind in {"", "none"}:
            return
        else:
            raise ValueError(f"unsupported fault injection: {kind}")

    def _command(self, case: EvalCase, workspace: Path) -> list[str]:
        command = [sys.executable, "-m", "src", "--cwd", str(workspace)]
        if case.model_profile:
            command.extend(["--model", case.model_profile])
        extra_args = case.config.get("cli_args", [])
        if not isinstance(extra_args, list) or any(not isinstance(item, str) for item in extra_args):
            raise ValueError("config.cli_args must be a list of strings")
        command.extend(extra_args)
        command.append(case.prompt)
        return command

    def _environment(self, case: EvalCase) -> dict[str, str]:
        env = dict(os.environ)
        env["JCODE_EVAL_CASE_ID"] = case.case_id
        # 子进程 cwd 是隔离 workspace，必须显式暴露 JCode 源码根目录。
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = str(self.project_root) + (os.pathsep + existing if existing else "")
        return env

    def _git_revision(self) -> str:
        try:
            value = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=self.project_root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=True,
            )
            return value.stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            return ""

    @staticmethod
    def _collect_evidence(workspace: Path, result_dir: Path) -> dict[str, str]:
        """复制 JCode 产生的证据到不可变的评测结果目录。"""
        paths: dict[str, str] = {}
        evidence_root = workspace / ".jcode"
        if not evidence_root.is_dir():
            return paths
        target = result_dir / "jcode"
        shutil.copytree(evidence_root, target, dirs_exist_ok=True)
        for name in ("trace.jsonl", "report.json", "checkpoint.json"):
            matches = list(target.rglob(name))
            if matches:
                paths[name] = str(matches[0].relative_to(result_dir)).replace("\\", "/")
        return paths

    @staticmethod
    def _read_evidence_metadata(result_dir: Path) -> dict[str, Any]:
        """读取复制后的证据快照，评分器只依赖这些持久化数据。"""
        metadata: dict[str, Any] = {"trace_events": [], "report": {}, "checkpoint": {}}
        trace_paths = list(result_dir.rglob("trace.jsonl"))
        if trace_paths:
            for line in trace_paths[0].read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    metadata["trace_events"].append(value)
        for key in ("report", "checkpoint"):
            paths = list(result_dir.rglob(f"{key}.json"))
            if paths:
                try:
                    value = json.loads(paths[0].read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    value = {}
                if isinstance(value, dict):
                    metadata[key] = value
        return metadata
