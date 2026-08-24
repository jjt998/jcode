from __future__ import annotations

import hashlib
import json
import time
from dataclasses import replace
from pathlib import Path

from src.evidence.session_log import SessionEventBus
from src.memory.working import WorkingMemory
from src.state.workspace import now_iso
from src.workers.manager import WorkerManager


MAX_ENTRYPOINT_LINES = 200
DREAM_SESSION_CAP = 30
DREAM_MIN_NEW_TOKENS = 4096


def maintain_after_turn(store, working_memory, user_message: str, final_text: str, agent=None) -> dict:
    promotion = store.promote_from_turn(user_message, final_text)
    consolidation = store.consolidate_daily_logs()
    auto_dream = _maybe_run_auto_dream(store, agent)
    durable_memory = dict(promotion.get("durable_memory", {}))
    durable_memory["topic_consolidation"] = consolidation
    return {
        "daily_log": dict(promotion.get("daily_log", {"enabled": True, "source": "turn_summary", "count": 0, "paths": []})),
        "durable_memory": durable_memory,
        "auto_dream": auto_dream,
    }


def build_dream_prompt(memory_dir, session_ids: list[str] | None = None) -> str:
    memory_path = Path(memory_dir)
    session_ids = list(session_ids or [])
    total = len(session_ids)
    truncated = False
    if total > DREAM_SESSION_CAP:
        session_ids = session_ids[-DREAM_SESSION_CAP:]
        truncated = True
    session_section = "No specific session ids were provided."
    if session_ids:
        label = f"Recent sessions, showing {len(session_ids)} of {total}:" if truncated else "Recent sessions:"
        session_section = label + "\n" + "\n".join(f"- {session_id}" for session_id in session_ids)
    return f"""# Dream: Memory Consolidation

You are a restricted Dream sub-agent for JCode. Your only job is to turn process-layer Daily Log signal into conclusion-layer Durable Memory.

Memory directory: `{memory_path}`
Allowed writes: only inside `{memory_path}`.
Working_Memory is the current-turn scratchpad and is not your durable source of truth.
Daily Log files live under `logs/YYYY/MM/YYYY-MM-DD.md`; they are process logs, not final knowledge.
Durable Memory is the conclusion layer: `MEMORY.md`, `topics/*`, and future structured memory files.
The Durable Memory index is `MEMORY.md`.

## Read

- List the memory directory.
- Read `MEMORY.md` if it exists.
- Skim topic files under `topics/`.
- Review recent Daily Log entries as process input. Search narrowly if logs are large.

## Consolidate

- Merge duplicate or overlapping notes.
- Promote only stable conclusions into Durable Memory: project conventions, key decisions, dependency facts, and user preferences.
- Remove obvious noise and stale contradictions.
- Do not preserve secrets, credentials, raw command output, long logs, or transient task state.
- Convert relative dates to absolute dates when the surrounding evidence makes that possible.

## Write

- Update Durable Memory topic files under `topics/`.
- Update Durable Memory index `MEMORY.md`, keeping it under {MAX_ENTRYPOINT_LINES} lines.
- Do not write outside the memory directory.

Return a brief final summary of what changed. If nothing changed, say so.

## Session Inputs

{session_section}
"""


def run_dream(agent, quiet: bool = False, session_ids: list[str] | None = None) -> str:
    session_ids = list(session_ids or [])
    memory_dir = agent.memory_store.root
    memory_dir.mkdir(parents=True, exist_ok=True)
    before = _memory_snapshot(memory_dir)
    report_path = _dream_report_path(memory_dir)
    agent.session_events.emit("dream_started", quiet=quiet, session_ids=session_ids, memory_dir=str(memory_dir))
    try:
        child = _build_dream_agent(agent)
        child.set_tool_profile("dream")
        child.write_scope = [_memory_scope(agent)]
        prompt = build_dream_prompt(memory_dir, session_ids=session_ids)
        result = child.ask(prompt)
        after = _memory_snapshot(memory_dir)
        changed_files = _changed_files(before, after)
        report = {
            "status": "finished",
            "quiet": bool(quiet),
            "session_ids": session_ids,
            "dream_session_id": child.session.get("id", ""),
            "changed_files": changed_files,
            "result_preview": result[:1000],
            "updated_at": now_iso(),
        }
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        agent.session_events.emit(
            "dream_finished",
            quiet=quiet,
            session_ids=session_ids,
            dream_session_id=child.session.get("id", ""),
            changed_files=changed_files,
            report_path=str(report_path),
        )
        return result
    except Exception as exc:
        report = {
            "status": "failed",
            "quiet": bool(quiet),
            "session_ids": session_ids,
            "error_type": type(exc).__name__,
            "error": str(exc)[:1000],
            "updated_at": now_iso(),
        }
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        agent.session_events.emit(
            "dream_failed",
            quiet=quiet,
            session_ids=session_ids,
            error_type=type(exc).__name__,
            message=str(exc)[:500],
            report_path=str(report_path),
        )
        raise


def _build_dream_agent(agent):
    session = agent.session_store.load_requested(None, None, agent.workspace.root)
    working_memory = WorkingMemory.from_dict({}, agent.workspace.root)
    working_memory.task_goal = "Dream memory consolidation"
    session_events = SessionEventBus(agent.session_events.path.parent / f"{session['id']}.events.jsonl")
    dream_config = replace(
        agent.config,
        auto_dream=False,
        max_new_tokens=max(int(agent.config.max_new_tokens), DREAM_MIN_NEW_TOKENS),
    )
    worker_manager = WorkerManager(
        agent.workspace,
        agent.worker_manager.root,
        agent.tool_executor,
        agent.model_router,
        dream_config,
        session_events=session_events,
    )
    child = type(agent)(
        config=dream_config,
        workspace=agent.workspace,
        session=session,
        session_store=agent.session_store,
        run_store=agent.run_store,
        memory_store=agent.memory_store,
        session_events=session_events,
        working_memory=working_memory,
        context_builder=agent.context_builder,
        model_router=agent.model_router,
        tool_executor=agent.tool_executor,
        worker_manager=worker_manager,
        final_gate=agent.final_gate,
        redactor=agent.redactor,
        tool_profiles=agent.tool_profiles,
        active_tool_profile_name="dream",
        write_scope=[_memory_scope(agent)],
    )
    return child


def _maybe_run_auto_dream(store, agent) -> dict:
    enabled = bool(getattr(getattr(agent, "config", None), "auto_dream", False))
    result = {
        "enabled": enabled,
        "triggered": False,
        "skip_reason": "",
        "session_count": 0,
        "session_ids": [],
        "changed_files": [],
    }
    if agent is None or not enabled:
        result["skip_reason"] = "disabled"
        return result
    if getattr(agent, "active_tool_profile_name", "default") == "dream":
        result["skip_reason"] = "dream_run"
        return result
    session_ids = _recent_session_ids(agent)
    result["session_ids"] = session_ids
    result["session_count"] = len(session_ids)
    min_sessions = int(getattr(agent.config, "dream_min_sessions", 5))
    if len(session_ids) < min_sessions:
        result["skip_reason"] = "session_gate"
        return result
    interval_hours = float(getattr(agent.config, "dream_interval_hours", 24.0))
    if _hours_since_last_dream(store.root) < interval_hours:
        result["skip_reason"] = "interval_gate"
        return result
    before = _memory_snapshot(store.root)
    try:
        run_dream(agent, quiet=True, session_ids=session_ids)
        after = _memory_snapshot(store.root)
        result["triggered"] = True
        result["changed_files"] = _changed_files(before, after)
    except Exception as exc:
        result["skip_reason"] = "dream_failed"
        result["error_type"] = type(exc).__name__
        result["error"] = str(exc)[:500]
    return result


def _memory_scope(agent) -> str:
    try:
        return agent.workspace.relpath(agent.memory_store.root)
    except Exception:
        return ".jcode/memory"


def _recent_session_ids(agent) -> list[str]:
    paths = sorted(agent.session_store.root.glob("*.json"), key=lambda path: path.stat().st_mtime)
    return [path.stem for path in paths[-DREAM_SESSION_CAP:]]


def _hours_since_last_dream(memory_dir: Path) -> float:
    reports = sorted((memory_dir / "dream_reports").glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not reports:
        return float("inf")
    age_seconds = max(0.0, Path(reports[0]).stat().st_mtime)
    return (time.time() - age_seconds) / 3600


def _dream_report_path(memory_dir: Path) -> Path:
    reports_dir = memory_dir / "dream_reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    safe_ts = now_iso().replace(":", "").replace("-", "")
    return reports_dir / f"{safe_ts}.json"


def _memory_snapshot(memory_dir: Path) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    if not memory_dir.exists():
        return snapshot
    for path in memory_dir.rglob("*"):
        if not path.is_file() or "dream_reports" in path.relative_to(memory_dir).parts:
            continue
        try:
            snapshot[str(path.relative_to(memory_dir)).replace("\\", "/")] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            continue
    return snapshot


def _changed_files(before: dict[str, str], after: dict[str, str]) -> list[str]:
    return sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path))
