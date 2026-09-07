from __future__ import annotations

import json
import sys
import threading
import traceback
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MethodType
from typing import Any

from src.app.bootstrap import build_agent
from src.app.config import AppConfig, compile_static_capacity_input
from src.app.web_projects import WebProject, WebProjectStore
from src.app.web_steps import StepTimelineBuilder
from src.app.web_turns import build_session_turns
from src.evidence.session_log import SessionEventBus
from src.state.session import SessionStore
from src.state.model_selection import resolve_model_snapshot, validate_model_options
from src.state.workspace import Workspace, now_iso
from src.context.budget import validate_static_model_capacity
from src.context.budget import TokenizerAdapter
from src.context.prefix import render_prefix
from src.tools.registry import build_default_registry


ACTIVE_STATUSES = {"running", "waiting_approval", "aborting"}


@dataclass
class WebRun:
    web_run_id: str
    project_id: str
    project_root: Path
    session_id: str
    user_message: str = ""
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
    event_seq: int = 0
    reasoning_steps: list[dict] = field(default_factory=list)
    lock: threading.RLock = field(default_factory=threading.RLock)

    def emit(self, event: str, **payload) -> None:
        with self.lock:
            self.event_seq += 1
            self.events.append(
                {
                    "event": event,
                    "created_at": now_iso(),
                    "run_id": self.jcode_run_id or self.web_run_id,
                    "web_run_id": self.web_run_id,
                    "project_id": self.project_id,
                    "session_id": self.session_id,
                    "event_cursor": self.event_seq,
                    **payload,
                }
            )

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "web_run_id": self.web_run_id,
                "run_id": self.jcode_run_id or self.web_run_id,
                "jcode_run_id": self.jcode_run_id,
                "project_id": self.project_id,
                "project_root": str(self.project_root),
                "session_id": self.session_id,
                "status": self.status,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "final_text": self.final_text,
                "error": self.error,
                "pending_question": self.pending_question,
                "pending_choices": list(self.pending_choices),
                "user_message": self.user_message,
                "reasoning_steps": [dict(step) for step in self.reasoning_steps],
                "event_cursor": self.event_seq,
            }


class WebRunManager:
    config: AppConfig
    project_store: WebProjectStore
    runs: dict[str, WebRun]
    session_active: dict[str, str]
    lock: threading.RLock

    def __init__(self, config: AppConfig):
        self.config = config
        base_workspace = Workspace.build(config.cwd)
        self.project_store = WebProjectStore(base_workspace.root / ".jcode" / "web_projects.json", base_workspace.root)
        self.runs = {}
        self.session_active = {}
        self.lock = threading.RLock()

    @property
    def state_dir(self) -> Path:
        return self.project_store.get("default").root / ".jcode"

    def list_projects(self) -> list[dict]:
        return [self.project_summary(project) for project in self.project_store.list_projects()]

    def create_project(self, root: str, name: str | None = None) -> dict:
        project = self.project_store.create(root, name=name)
        return self.project_summary(project)

    def project_summary(self, project: WebProject) -> dict:
        data = project.to_dict()
        data["session_count"] = len(self.list_sessions(project.id))
        data["active_runs"] = [
            run.snapshot()
            for run in self.runs.values()
            if run.project_id == project.id and run.status in ACTIVE_STATUSES
        ]
        return data

    def list_sessions(self, project_id: str = "default") -> list[dict]:
        project = self.project_store.get(project_id)
        store = self._session_store(project)
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
                    "project_id": project.id,
                    "created_at": session.get("created_at", ""),
                    "updated_at": session.get("updated_at", ""),
                    "workspace_root": session.get("workspace_root", ""),
                    "runtime_mode": runtime_mode.get("mode", "default") if isinstance(runtime_mode, dict) else "default",
                    "latest_run_id": store.latest_run_id(session),
                    "active_run_id": self.session_active.get(self._session_key(project.id, session_id), ""),
                    "active_status": self._active_status(project.id, session_id),
                    "active_model_profile": session.get("active_model_profile", ""),
                }
            )
        return sessions

    def get_session(self, session_id: str, project_id: str = "default") -> dict:
        project = self.project_store.get(project_id)
        path = self._session_store(project).root / f"{session_id}.json"
        session = self._read_session(path)
        if not session:
            raise KeyError(session_id)
        if not session.get("active_model_profile"):
            session["active_model_profile"] = self.config.default_model_profile
            self._session_store(project).save(session)
        session["project_id"] = project.id
        session["project_root"] = str(project.root)
        session["latest_run_id"] = self._session_store(project).latest_run_id(session)
        session["active_run_id"] = self.session_active.get(self._session_key(project.id, session_id), "")
        session["active_status"] = self._active_status(project.id, session_id)
        session["model_profiles"] = self.model_profiles(session)
        return session

    def model_profiles(self, session: dict | None = None) -> list[dict]:
        return [resolve_model_snapshot(session or {}, profile) for profile in self.config.model_profiles.values()]

    def switch_model(self, session_id: str, profile_id: str, reasoning_effort: str = "", project_id: str = "default", thinking_enabled: bool | None = None) -> dict:
        project = self.project_store.get(project_id)
        if profile_id not in self.config.model_profiles:
            raise ValueError(f"unknown model profile: {profile_id}")
        if self._active_status(project.id, session_id) in ACTIVE_STATUSES:
            raise RuntimeError("cannot switch model during an active run")
        store = self._session_store(project)
        session = store.load_requested(session_id, None, project.root)
        if str(session.get("id") or "") != session_id:
            raise KeyError(session_id)
        previous = str(session.get("active_model_profile") or "")
        profile = self.config.model_profiles[profile_id]
        registry = build_default_registry()
        mode = session.get("runtime_mode", {}) if isinstance(session.get("runtime_mode", {}), dict) else {}
        profile_name = "plan" if mode.get("mode") == "plan" else "default"
        from src.policy.tool_profiles import build_tool_profiles
        tool_profiles = build_tool_profiles(registry)
        static_tools = [
            {"type": "function", "name": item.name, "description": item.description, "parameters": item.parameters}
            for item in registry.definitions(tool_profiles[profile_name].allowed_tools)
        ]
        static_tokens = compile_static_capacity_input(render_prefix(Workspace.build(project.root), registry), static_tools, TokenizerAdapter())["total"]
        validate_static_model_capacity(profile, self.config.max_new_tokens, static_tokens)
        current = dict(session.get("model_options", {}).get(profile_id, {}) or {})
        enabled = profile.thinking_enabled if thinking_enabled is None else bool(thinking_enabled)
        if thinking_enabled is None and "thinking_enabled" in current:
            enabled = bool(current["thinking_enabled"])
        options = validate_model_options(profile, enabled, reasoning_effort)
        session["active_model_profile"] = profile_id
        if options:
            session.setdefault("model_options", {})[profile_id] = options
        session.setdefault("model_switches", []).append({"from": previous, "to": profile_id, "at": now_iso(), "source": "web"})
        store.save(session)
        # Web 切换没有常驻 Agent，直接写入同一份 session 事件流。
        SessionEventBus(store.root / f"{session_id}.events.jsonl").emit(
            "model_switched",
            previous_model_profile=previous,
            model_profile=resolve_model_snapshot(session, profile),
            source="web",
        )
        return self.get_session(session_id, project.id)

    def get_session_turns(self, session_id: str, project_id: str = "default") -> dict:
        project = self.project_store.get(project_id)
        session = self.get_session(session_id, project.id)
        turns = build_session_turns(project.id, project.root, session)
        active_id = self.session_active.get(self._session_key(project.id, session_id), "")
        if active_id and active_id in self.runs:
            turns["active_run"] = self.runs[active_id].snapshot()
        return turns

    def create_session(self, project_id: str = "default") -> dict:
        project = self.project_store.get(project_id)
        store = self._session_store(project)
        session = store.load_requested(None, None, project.root)
        store.save(session)
        self.project_store.touch(project.id)
        return self.get_session(str(session["id"]), project.id)

    def start_run(self, session_id: str, message: str, project_id: str = "default") -> WebRun:
        project = self.project_store.get(project_id)
        message = str(message or "").strip()
        if not message:
            raise ValueError("message is required")
        session_key = self._session_key(project.id, session_id)
        with self.lock:
            active_id = self.session_active.get(session_key)
            if active_id:
                active = self.runs.get(active_id)
                if active and active.status in ACTIVE_STATUSES:
                    raise RuntimeError(f"session already has active run: {active_id}")
            web_run = WebRun(
                web_run_id=f"web-run-{uuid.uuid4().hex[:10]}",
                project_id=project.id,
                project_root=project.root,
                session_id=session_id,
                user_message=message,
                status="running",
            )
            self.runs[web_run.web_run_id] = web_run
            self.session_active[session_key] = web_run.web_run_id

        config = self._config_for_session(project, session_id)
        agent = build_agent(config)
        agent.ask_user_callback = self._approval_callback(web_run)
        self._bind_run_store(web_run, agent)
        web_run.agent = agent
        web_run.emit("web_run_started")
        thread = threading.Thread(target=self._run_agent, args=(web_run, message), name=web_run.web_run_id, daemon=True)
        web_run.thread = thread
        thread.start()
        self.project_store.touch(project.id)
        return web_run

    def get_run(self, run_id: str, *, project_id: str | None = None, session_id: str | None = None) -> WebRun:
        with self.lock:
            if run_id in self.runs:
                run = self.runs[run_id]
            else:
                run = None
            for candidate in self.runs.values():
                if candidate.jcode_run_id == run_id:
                    run = candidate
                    break
            if run is None:
                raise KeyError(run_id)
            if project_id is not None and run.project_id != project_id:
                raise KeyError(run_id)
            if session_id is not None and run.session_id != session_id:
                raise KeyError(run_id)
            return run

    def approve(self, run_id: str, answer: str, *, project_id: str | None = None, session_id: str | None = None) -> WebRun:
        run = self.get_run(run_id, project_id=project_id, session_id=session_id)
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

    def abort(self, run_id: str, timeout_seconds: float = 10.0, *, project_id: str | None = None, session_id: str | None = None) -> WebRun:
        """登记中止请求并立即返回，实际终止由运行线程异步完成。"""
        run = self.get_run(run_id, project_id=project_id, session_id=session_id)
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
        return run

    def run_dir(self, run: WebRun) -> Path | None:
        if not run.jcode_run_id:
            return None
        return run.project_root / ".jcode" / "runs" / run.jcode_run_id

    def historical_run_dir(self, project_id: str, run_id: str) -> Path:
        project = self.project_store.get(project_id)
        return project.root / ".jcode" / "runs" / run_id

    def historical_run_belongs_to_session(self, project_id: str, session_id: str, run_id: str) -> bool:
        """校验已结束 run 是否属于指定 session。"""
        project = self.project_store.get(project_id)
        session = self._session_store(project).load_requested(session_id, None, project.root)
        if str(session.get("id") or "") != session_id:
            return False
        if run_id in {str(value) for value in session.get("run_ids", [])}:
            return True
        return any(str(item.get("run_id") or "") == run_id for item in session.get("history", []) if isinstance(item, dict))

    def _session_store(self, project: WebProject) -> SessionStore:
        return SessionStore(project.root / ".jcode" / "sessions")

    def _active_status(self, project_id: str, session_id: str) -> str:
        active_id = self.session_active.get(self._session_key(project_id, session_id), "")
        if not active_id:
            return ""
        run = self.runs.get(active_id)
        return run.status if run else ""

    def _config_for_session(self, project: WebProject, session_id: str) -> AppConfig:
        return replace(
            self.config,
            cwd=project.root,
            default_model_profile=self._session_model_profile(project, session_id),
            session_id=session_id,
            resume=session_id,
        )

    def _session_model_profile(self, project: WebProject, session_id: str) -> str:
        session = self._session_store(project).load_requested(session_id, None, project.root)
        return str(session.get("active_model_profile") or self.config.default_model_profile)

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
        original_append_trace = agent.run_store.append_trace
        step_builder = StepTimelineBuilder()

        def start_run_wrapper(store_self, task_state):
            run_dir = original_start_run(task_state)
            with web_run.lock:
                web_run.jcode_run_id = str(task_state.run_id)
                step_builder.run_id = web_run.jcode_run_id
                web_run.emit("jcode_run_bound", jcode_run_id=web_run.jcode_run_id)
            return run_dir

        def append_trace_wrapper(store_self, run_dir, event, run_id, **payload):
            original_append_trace(run_dir, event, run_id, **payload)
            row = {"event": event, "run_id": run_id, "created_at": now_iso(), **payload}
            patches = step_builder.consume(row)
            if not patches:
                return
            with web_run.lock:
                web_run.reasoning_steps = step_builder.steps_snapshot()
                for patch in patches:
                    web_run.emit("step_patch", step=patch)

        agent.run_store.start_run = MethodType(start_run_wrapper, agent.run_store)
        agent.run_store.append_trace = MethodType(append_trace_wrapper, agent.run_store)

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
            error_trace = "".join(traceback.format_exception(exc))
            redactor = getattr(run.agent, "redactor", None)
            if redactor is not None:
                error_trace = redactor.redact(error_trace)
            # Web 后台线程的异常不会自动显示，必须显式写入控制台。
            print(error_trace, file=sys.stderr, flush=True)
            with run.lock:
                run.status = "failed"
                run.error = f"{type(exc).__name__}: {exc}"
                run.emit("run_failed", error=run.error)
        finally:
            with run.lock:
                run.finished_at = now_iso()
            with self.lock:
                key = self._session_key(run.project_id, run.session_id)
                if self.session_active.get(key) == run.web_run_id:
                    self.session_active.pop(key, None)

    @staticmethod
    def _session_key(project_id: str, session_id: str) -> str:
        return f"{project_id}:{session_id}"

    @staticmethod
    def _read_session(path: Path) -> dict:
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
