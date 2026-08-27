from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.app.web_events import event_name, indexed_events, sse_pack, trace_events
from src.app.web_runs import WebRunManager


class MessageRequest(BaseModel):
    message: str


class ApprovalRequest(BaseModel):
    answer: str


def create_app(manager: WebRunManager) -> FastAPI:
    app = FastAPI(title="JCode Web", version="0.1.0")
    static_dir = Path(__file__).parent / "web_static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/")
    def index():
        return FileResponse(static_dir / "index.html")

    @app.get("/api/sessions")
    def list_sessions():
        return manager.list_sessions()

    @app.post("/api/sessions")
    def create_session():
        return manager.create_session()

    @app.get("/api/sessions/{session_id}")
    def get_session(session_id: str):
        try:
            return manager.get_session(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="session not found") from exc

    @app.post("/api/sessions/{session_id}/messages")
    def send_message(session_id: str, request: MessageRequest):
        try:
            run = manager.start_run(session_id, request.message)
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
    def approve_run(run_id: str, request: ApprovalRequest):
        try:
            return manager.approve(run_id, request.answer).snapshot()
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/runs/{run_id}/abort")
    def abort_run(run_id: str):
        try:
            run = manager.abort(run_id, timeout_seconds=10.0)
            return run.snapshot()
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc

    @app.get("/api/runs/{run_id}/events")
    async def run_events(run_id: str):
        try:
            run = manager.get_run(run_id)
        except KeyError as exc:
            run_dir = manager.state_dir / "runs" / run_id
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
                run_dir = manager.run_dir(run)
                if run_dir is not None:
                    rows.extend(trace_events(run_dir))
                with run.lock:
                    rows.extend(dict(item, _source="web", _index=index) for index, item in enumerate(run.events))
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
