from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from types import MethodType, SimpleNamespace
from typing import Any

from src.app.bootstrap import build_agent
from src.app.config import AppConfig, load_config
from src.state.session import SessionStore
from src.state.workspace import Workspace, now_iso


ACTIVE_STATUSES = {"running", "waiting_approval", "aborting"}


@dataclass
class WebRun:
    web_run_id: str
    session_id: str
    status: str = "idle"
    agent: Any | None = None
    thread: threading.Thread | None = None
    jcode_run_id: str = ""
    started_at: str = field(default_factory=now_iso)
    finished_at: str = ""
    final_text: str = ""
    error: str = ""
    pending_question: str | None = None
    pending_choices: list[str] = field(default_factory=list)
    approval_answer: str | None = None
    approval_event: threading.Event = field(default_factory=threading.Event)
    events: list[dict] = field(default_factory=list)
    lock: threading.RLock = field(default_factory=threading.RLock)

    def emit(self, event: str, **payload) -> None:
        with self.lock:
            self.events.append(
                {
                    "event": event,
                    "created_at": now_iso(),
                    "run_id": self.jcode_run_id or self.web_run_id,
                    "web_run_id": self.web_run_id,
                    **payload,
                }
            )

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "web_run_id": self.web_run_id,
                "run_id": self.jcode_run_id or self.web_run_id,
                "jcode_run_id": self.jcode_run_id,
                "session_id": self.session_id,
                "status": self.status,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "final_text": self.final_text,
                "error": self.error,
                "pending_question": self.pending_question,
                "pending_choices": list(self.pending_choices),
            }


class WebRunManager:
    config: AppConfig
    workspace: Workspace
    state_dir: Path
    runs: dict[str, WebRun]
    session_active: dict[str, str]
    lock: threading.RLock

    def __init__(self, config: AppConfig):
        self.config = config
        self.workspace = Workspace.build(config.cwd)
        self.state_dir = self.workspace.root / ".jcode"
        self.runs = {}
        self.session_active = {}
        self.lock = threading.RLock()

    @property
    def session_store(self) -> SessionStore:
        return SessionStore(self.state_dir / "sessions")

    def list_sessions(self) -> list[dict]:
        store = self.session_store
        sessions: list[dict] = []
        for path in sorted(store.root.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
            session = self._read_session(path)
            if not session:
                continue
            session_id = str(session.get("id") or path.stem)
            runtime_mode = session.get("runtime_mode", {})
            sessions.append(
                {
                    "id": session_id,
                    "created_at": session.get("created_at", ""),
                    "updated_at": session.get("updated_at", ""),
                    "workspace_root": session.get("workspace_root", ""),
                    "runtime_mode": runtime_mode.get("mode", "default") if isinstance(runtime_mode, dict) else "default",
                    "latest_run_id": store.latest_run_id(session),
                    "active_run_id": self.session_active.get(session_id, ""),
                    "active_status": self._active_status(session_id),
                }
            )
        return sessions

    def get_session(self, session_id: str) -> dict:
        path = self.session_store.root / f"{session_id}.json"
        session = self._read_session(path)
        if not session:
            raise KeyError(session_id)
        session["latest_run_id"] = self.session_store.latest_run_id(session)
        session["active_run_id"] = self.session_active.get(session_id, "")
        session["active_status"] = self._active_status(session_id)
        return session

    def create_session(self) -> dict:
        session = self.session_store.load_requested(None, None, self.workspace.root)
        self.session_store.save(session)
        return self.get_session(str(session["id"]))

    def start_run(self, session_id: str, message: str) -> WebRun:
        message = str(message or "").strip()
        if not message:
            raise ValueError("message is required")
        with self.lock:
            active_id = self.session_active.get(session_id)
            if active_id:
                active = self.runs.get(active_id)
                if active and active.status in ACTIVE_STATUSES:
                    raise RuntimeError(f"session already has active run: {active_id}")
            web_run = WebRun(web_run_id=f"web-run-{uuid.uuid4().hex[:10]}", session_id=session_id, status="running")
            self.runs[web_run.web_run_id] = web_run
            self.session_active[session_id] = web_run.web_run_id

        config = self._config_for_session(session_id)
        agent = build_agent(config)
        agent.ask_user_callback = self._approval_callback(web_run)
        self._bind_run_store(web_run, agent)
        web_run.agent = agent
        web_run.emit("web_run_started", session_id=session_id)
        thread = threading.Thread(target=self._run_agent, args=(web_run, message), name=web_run.web_run_id, daemon=True)
        web_run.thread = thread
        thread.start()
        return web_run

    def get_run(self, run_id: str) -> WebRun:
        with self.lock:
            if run_id in self.runs:
                return self.runs[run_id]
            for run in self.runs.values():
                if run.jcode_run_id == run_id:
                    return run
        raise KeyError(run_id)

    def approve(self, run_id: str, answer: str) -> WebRun:
        run = self.get_run(run_id)
        with run.lock:
            if run.status != "waiting_approval":
                raise RuntimeError("run is not waiting for approval")
            run.approval_answer = str(answer)
            run.pending_question = None
            run.pending_choices = []
            run.status = "running"
            run.approval_event.set()
            run.emit("approval_answered")
        return run

    def abort(self, run_id: str, timeout_seconds: float = 10.0) -> WebRun:
        run = self.get_run(run_id)
        with run.lock:
            if run.agent is not None and run.status in ACTIVE_STATUSES:
                run.status = "aborting"
                run.agent.abort()
                if run.pending_question is not None:
                    run.approval_answer = "Stopped by user."
                    run.pending_question = None
                    run.pending_choices = []
                    run.approval_event.set()
                run.emit("run_abort_requested", timeout_seconds=timeout_seconds)
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            thread = run.thread
            if thread is None or not thread.is_alive():
                return run
            time.sleep(0.1)
        return run

    def run_dir(self, run: WebRun) -> Path | None:
        if not run.jcode_run_id:
            return None
        return self.state_dir / "runs" / run.jcode_run_id

    def session_events_path(self, session_id: str) -> Path:
        return self.state_dir / "sessions" / f"{session_id}.events.jsonl"

    def _active_status(self, session_id: str) -> str:
        active_id = self.session_active.get(session_id, "")
        if not active_id:
            return ""
        run = self.runs.get(active_id)
        return run.status if run else ""

    def _config_for_session(self, session_id: str) -> AppConfig:
        args = SimpleNamespace(
            cwd=str(self.config.cwd),
            config=None,
            provider=self.config.provider,
            api_key=self.config.api_key,
            base_url=self.config.base_url,
            model=self.config.model,
            approval=self.config.approval,
            sandbox=self.config.sandbox,
            max_steps=self.config.max_steps,
            max_new_tokens=self.config.max_new_tokens,
            temperature=self.config.temperature,
            plan_topic=None,
            plan_path=None,
            session_id=session_id,
            resume=session_id,
        )
        return load_config(args)

    def _approval_callback(self, run: WebRun):
        def callback(question: str, choices: list[str]) -> str:
            with run.lock:
                run.status = "waiting_approval"
                run.pending_question = question
                run.pending_choices = list(choices or [])
                run.approval_answer = None
                run.approval_event.clear()
                run.emit("approval_required", question=question, choices=list(choices or []))
            run.approval_event.wait()
            with run.lock:
                return str(run.approval_answer or "")

        return callback

    def _bind_run_store(self, web_run: WebRun, agent) -> None:
        original_start_run = agent.run_store.start_run

        def start_run_wrapper(store_self, task_state):
            run_dir = original_start_run(task_state)
            with web_run.lock:
                web_run.jcode_run_id = str(task_state.run_id)
                web_run.emit("jcode_run_bound", jcode_run_id=web_run.jcode_run_id)
            return run_dir

        agent.run_store.start_run = MethodType(start_run_wrapper, agent.run_store)

    def _run_agent(self, run: WebRun, message: str) -> None:
        try:
            final_text = run.agent.ask(message) if run.agent is not None else ""
            with run.lock:
                run.final_text = final_text
                if run.status == "aborting":
                    run.status = "aborted"
                    run.emit("run_aborted")
                else:
                    run.status = "completed"
                    run.emit("web_run_completed", final_text=final_text)
        except Exception as exc:
            with run.lock:
                run.status = "failed"
                run.error = f"{type(exc).__name__}: {exc}"
                run.emit("run_failed", error=run.error)
        finally:
            with run.lock:
                run.finished_at = now_iso()
            with self.lock:
                if self.session_active.get(run.session_id) == run.web_run_id:
                    self.session_active.pop(run.session_id, None)

    @staticmethod
    def _read_session(path: Path) -> dict:
        if not path.exists():
            return {}
        import json

        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
