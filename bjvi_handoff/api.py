"""HTTP surface: worker callbacks, operator job API, operator dashboard."""

from __future__ import annotations

import hmac
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from .adapters import MondayClient, SlackClient
from .adapters.base import MondayGateway, SlackGateway
from .config import Settings
from .dashboard import render_dashboard
from .models import (
    SourceSecurityLevel,
    TransitionError,
    WorkerEvent,
    WorkerEventType,
    utcnow,
)
from .security import (
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    WORKER_HEADER,
    AuthError,
    verify_worker,
)
from .service import (
    DuplicateCompletion,
    HandoffService,
    JobNotFound,
    MissingArtifact,
    WorkerMismatch,
)
from .store import JobStore


class CreateJobRequest(BaseModel):
    job_id: str = Field(min_length=3, max_length=120)
    title: str = Field(min_length=1, max_length=300)
    worker: str = Field(min_length=1, max_length=60)
    source_reference: str | None = None
    source_system: str | None = "monday"
    source_security_level: SourceSecurityLevel = SourceSecurityLevel.CONFIDENTIAL
    approval_required: bool = False
    return_destination: str | None = None


class WorkerEventRequest(BaseModel):
    job_id: str
    worker: str
    event: WorkerEventType
    message: str | None = None
    artifact_reference: str | None = None
    blocker: str | None = None
    timestamp: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    event_id: str | None = None


def create_app(
    settings: Settings | None = None,
    store: JobStore | None = None,
    monday: MondayGateway | None = None,
    slack: SlackGateway | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    store = store or JobStore(settings.database_path)
    service = HandoffService(
        store=store,
        settings=settings,
        monday=monday or MondayClient(settings),
        slack=slack or SlackClient(settings),
    )

    app = FastAPI(title="BJVI AI Job Handoff Layer", version="1.0.0")
    app.state.service = service
    app.state.settings = settings

    def require_operator(authorization: str | None = Header(default=None)) -> None:
        if not settings.operator_token:
            raise HTTPException(status_code=503, detail="BJVI_OPERATOR_TOKEN is not configured")
        expected = f"Bearer {settings.operator_token}"
        if not authorization or not hmac.compare_digest(authorization, expected):
            raise HTTPException(status_code=401, detail="operator token required")

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        return {
            "ok": True,
            "monday_configured": settings.monday_configured,
            "slack_configured": settings.slack_configured,
            "known_workers": sorted(settings.worker_secrets),
        }

    @app.post("/api/jobs", status_code=201, dependencies=[Depends(require_operator)])
    def create_job(payload: CreateJobRequest) -> dict[str, Any]:
        try:
            job = service.create_job(**payload.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return job.to_dict()

    @app.get("/api/jobs", dependencies=[Depends(require_operator)])
    def list_jobs() -> dict[str, Any]:
        now = utcnow()
        return {
            "jobs": [
                {**job.to_dict(), "stale": service.is_stale(job, now)}
                for job in store.list_jobs()
            ]
        }

    @app.get("/api/jobs/{job_id}", dependencies=[Depends(require_operator)])
    def get_job(job_id: str) -> dict[str, Any]:
        try:
            job = service.get_job(job_id)
        except JobNotFound as exc:
            raise HTTPException(status_code=404, detail="job not found") from exc
        return {
            **job.to_dict(),
            "stale": service.is_stale(job),
            "events": store.list_events(job_id),
            "receipts": store.list_receipts(job_id),
        }

    @app.post("/api/maintenance/sweep-stale", dependencies=[Depends(require_operator)])
    def sweep_stale() -> dict[str, Any]:
        flagged = service.sweep_stale()
        return {"flagged": [job.job_id for job in flagged]}

    @app.post("/api/worker-events")
    async def worker_events(
        request: Request,
        x_bjvi_worker: str | None = Header(default=None, alias=WORKER_HEADER),
        x_bjvi_timestamp: str | None = Header(default=None, alias=TIMESTAMP_HEADER),
        x_bjvi_signature: str | None = Header(default=None, alias=SIGNATURE_HEADER),
    ) -> JSONResponse:
        body = await request.body()
        try:
            worker = verify_worker(
                settings, x_bjvi_worker, x_bjvi_timestamp, x_bjvi_signature, body
            )
        except AuthError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

        try:
            payload = WorkerEventRequest.model_validate_json(body)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="invalid worker event payload") from exc

        if payload.worker.strip().lower() != worker:
            raise HTTPException(
                status_code=403, detail="signed worker does not match payload worker"
            )

        event = WorkerEvent(
            job_id=payload.job_id,
            worker=worker,
            event=payload.event,
            message=payload.message,
            artifact_reference=payload.artifact_reference,
            blocker=payload.blocker,
            timestamp=payload.timestamp or datetime.now(timezone.utc),
            metadata=payload.metadata,
            event_id=payload.event_id,
        )

        try:
            result = service.apply_event(event)
        except JobNotFound as exc:
            raise HTTPException(status_code=404, detail="job not found") from exc
        except WorkerMismatch as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except MissingArtifact as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except DuplicateCompletion as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except TransitionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        return JSONResponse(status_code=202, content=result)

    @app.get("/dashboard", response_class=HTMLResponse)
    def dashboard(authorization: str | None = Header(default=None)) -> HTMLResponse:
        require_operator(authorization)
        now = utcnow()
        rows = [
            {**job.to_dict(), "stale": service.is_stale(job, now)} for job in store.list_jobs()
        ]
        return HTMLResponse(render_dashboard(rows, now))

    return app
