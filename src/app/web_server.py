from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.app.web_events import event_name, indexed_events, sse_pack, trace_events
from src.app.web_runs import WebRunManager


class MessageRequest(BaseModel):
    message: str


class ModelSwitchRequest(BaseModel):
    model_profile: str
    reasoning_effort: str = ""
    thinking_enabled: bool | None = None


class ApprovalRequest(BaseModel):
    answer: str


class ProjectRequest(BaseModel):
    root: str
    name: str | None = None


def create_app(manager: WebRunManager) -> FastAPI:
    app = FastAPI(title="JCode Web", version="0.1.0")
    static_dir = Path(__file__).parent / "web_static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/")
    def index():
        return FileResponse(static_dir / "index.html")

    @app.get("/api/model-configuration")
    def get_model_configuration():
        """为尚未创建 session 的前端返回全局模型配置。"""
        return manager.model_configuration()

    @app.get("/api/projects")
    def list_projects():
        return manager.list_projects()

    @app.post("/api/projects")
    def create_project(request: ProjectRequest):
        try:
            return manager.create_project(request.root, name=request.name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/projects/{project_id}")
    def get_project(project_id: str):
        try:
            return manager.project_summary(manager.project_store.get(project_id))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="project not found") from exc

    @app.get("/api/projects/{project_id}/sessions")
    def list_project_sessions(project_id: str):
        try:
            return manager.list_sessions(project_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="project not found") from exc

    @app.post("/api/projects/{project_id}/sessions")
    def create_project_session(project_id: str):
        try:
            return manager.create_session(project_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="project not found") from exc

    @app.get("/api/projects/{project_id}/sessions/{session_id}")
    def get_project_session(project_id: str, session_id: str):
        try:
            return manager.get_session(session_id, project_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="session not found") from exc

    @app.get("/api/projects/{project_id}/sessions/{session_id}/turns")
    def get_project_session_turns(project_id: str, session_id: str):
        try:
            return manager.get_session_turns(session_id, project_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="session not found") from exc

    @app.post("/api/projects/{project_id}/sessions/{session_id}/messages")
    def send_project_message(project_id: str, session_id: str, request: MessageRequest):
        try:
            run = manager.start_run(session_id, request.message, project_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="project or session not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return run.snapshot()

    @app.post("/api/projects/{project_id}/sessions/{session_id}/model")
    def switch_project_session_model(project_id: str, session_id: str, request: ModelSwitchRequest):
        try:
            return manager.switch_model(session_id, request.model_profile, request.reasoning_effort, project_id, request.thinking_enabled)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="project or session not found") from exc
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/projects/{project_id}/runs/{run_id}/events")
    async def project_run_events(project_id: str, run_id: str, session_id: str | None = None, after: int = 0):
        return await stream_run_events(project_id, run_id, session_id=session_id, after=after)

    @app.get("/api/projects/{project_id}/context-audit")
    def get_context_audit(project_id: str, ref: str):
        """按 workspace 相对引用读取单次 Context 审计快照。"""
        try:
            project = manager.project_store.get(project_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="project not found") from exc
        normalized = Path(ref.replace("\\", "/"))
        if normalized.is_absolute() or not str(normalized).replace("\\", "/").startswith(".jcode/runs/"):
            raise HTTPException(status_code=400, detail="invalid context audit reference")
        path = (project.root / normalized).resolve()
        runs_root = (project.root / ".jcode" / "runs").resolve()
        if runs_root not in path.parents or not path.is_file():
            raise HTTPException(status_code=404, detail="context audit not found")
        return json.loads(path.read_text(encoding="utf-8"))

    @app.get("/api/projects/{project_id}/tool-artifact")
    def get_tool_artifact(project_id: str, ref: str):
        """读取当前项目某次运行外置的完整工具输出。"""
        try:
            project = manager.project_store.get(project_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="project not found") from exc
        normalized = Path(ref.replace("\\", "/"))
        parts = normalized.parts
        if normalized.is_absolute() or len(parts) < 5 or parts[:2] != (".jcode", "runs") or "artifacts" not in parts:
            raise HTTPException(status_code=400, detail="invalid tool artifact reference")
        path = (project.root / normalized).resolve()
        runs_root = (project.root / ".jcode" / "runs").resolve()
        relative_parts = path.relative_to(runs_root).parts if runs_root in path.parents else ()
        if len(relative_parts) < 3 or relative_parts[1] != "artifacts" or not path.is_file():
            raise HTTPException(status_code=404, detail="tool artifact not found")
        return {"ref": str(normalized).replace("\\", "/"), "content": path.read_text(encoding="utf-8")}

    @app.get("/api/sessions")
    def list_sessions():
        return manager.list_sessions("default")

    @app.post("/api/sessions")
    def create_session():
        return manager.create_session("default")

    @app.get("/api/sessions/{session_id}")
    def get_session(session_id: str):
        try:
            return manager.get_session(session_id, "default")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="session not found") from exc

    @app.post("/api/sessions/{session_id}/messages")
    def send_message(session_id: str, request: MessageRequest):
        try:
            run = manager.start_run(session_id, request.message, "default")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return run.snapshot()

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str):
        try:
            return manager.get_run(run_id).snapshot()
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc

    @app.post("/api/runs/{run_id}/approval")
    def approve_run(run_id: str, request: ApprovalRequest, project_id: str = "default", session_id: str | None = None):
        try:
            return manager.approve(run_id, request.answer, project_id=project_id, session_id=session_id).snapshot()
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/runs/{run_id}/abort")
    def abort_run(run_id: str, project_id: str = "default", session_id: str | None = None):
        try:
            run = manager.abort(run_id, timeout_seconds=10.0, project_id=project_id, session_id=session_id)
            return run.snapshot()
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc

    @app.get("/api/runs/{run_id}/events")
    async def run_events(run_id: str, session_id: str | None = None, after: int = 0):
        return await stream_run_events("default", run_id, session_id=session_id, after=after)

    async def stream_run_events(project_id: str, run_id: str, *, session_id: str | None = None, after: int = 0):
        try:
            run = manager.get_run(run_id)
            if run.project_id != project_id:
                raise KeyError(run_id)
            if session_id is not None and run.session_id != session_id:
                raise KeyError(run_id)
        except KeyError as exc:
            try:
                run_dir = manager.historical_run_dir(project_id, run_id)
            except KeyError:
                raise HTTPException(status_code=404, detail="project not found") from exc
            if session_id is not None and not manager.historical_run_belongs_to_session(project_id, session_id, run_id):
                raise HTTPException(status_code=404, detail="run not found") from exc
            if not run_dir.exists():
                raise HTTPException(status_code=404, detail="run not found") from exc

            async def historical_stream():
                for event_id, payload in indexed_events(trace_events(run_dir), prefix=run_id):
                    yield sse_pack(event_name(payload), payload, event_id=event_id)
                yield sse_pack("stream_closed", {"run_id": run_id, "status": "completed"})

            return StreamingResponse(historical_stream(), media_type="text/event-stream")

        async def stream():
            sent: set[str] = set()
            while True:
                rows: list[dict] = []
                with run.lock:
                    rows.extend(
                        dict(item, _source="web", _index=index)
                        for index, item in enumerate(run.events)
                        if int(item.get("event_cursor", 0) or 0) > after
                    )
                    status = run.status
                for event_id, payload in indexed_events(rows, prefix=run.web_run_id):
                    if event_id in sent:
                        continue
                    sent.add(event_id)
                    yield sse_pack(event_name(payload), payload, event_id=event_id)
                if status not in {"running", "waiting_approval", "aborting"}:
                    yield sse_pack("stream_closed", {"web_run_id": run.web_run_id, "status": status})
                    break
                await asyncio.sleep(0.5)

        return StreamingResponse(stream(), media_type="text/event-stream")

    return app
