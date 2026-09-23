"""SQLite persistence for jobs, worker events and outbound receipts."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable

from .models import (
    ApprovalStatus,
    Job,
    JobStatus,
    SourceSecurityLevel,
    WorkerEvent,
    WorkerEventType,
    iso,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    worker TEXT NOT NULL,
    status TEXT NOT NULL,
    source_reference TEXT,
    source_system TEXT,
    source_security_level TEXT NOT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    last_heartbeat_at TEXT,
    completed_at TEXT,
    blocker TEXT,
    artifact_reference TEXT,
    approval_required INTEGER NOT NULL DEFAULT 0,
    approval_status TEXT NOT NULL,
    return_destination TEXT,
    receipt_reference TEXT,
    last_progress_message TEXT,
    stale_flagged_at TEXT
);

CREATE TABLE IF NOT EXISTS job_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT UNIQUE,
    job_id TEXT NOT NULL,
    worker TEXT NOT NULL,
    event TEXT NOT NULL,
    message TEXT,
    artifact_reference TEXT,
    blocker TEXT,
    timestamp TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}',
    received_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_job_events_job ON job_events(job_id);

CREATE TABLE IF NOT EXISTS receipts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    destination TEXT NOT NULL,
    status TEXT NOT NULL,
    reference TEXT,
    detail TEXT,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_receipts_job ON receipts(job_id);
"""


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class JobStore:
    def __init__(self, database_path: str = ":memory:") -> None:
        self._conn = sqlite3.connect(database_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # jobs -----------------------------------------------------------------
    def create_job(self, job: Job) -> Job:
        self._conn.execute(
            """
            INSERT INTO jobs (
                job_id, title, worker, status, source_reference, source_system,
                source_security_level, created_at, started_at, last_heartbeat_at,
                completed_at, blocker, artifact_reference, approval_required,
                approval_status, return_destination, receipt_reference,
                last_progress_message, stale_flagged_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                job.job_id,
                job.title,
                job.worker,
                job.status.value,
                job.source_reference,
                job.source_system,
                job.source_security_level.value,
                iso(job.created_at),
                iso(job.started_at),
                iso(job.last_heartbeat_at),
                iso(job.completed_at),
                job.blocker,
                job.artifact_reference,
                int(job.approval_required),
                job.approval_status.value,
                job.return_destination,
                job.receipt_reference,
                job.last_progress_message,
                iso(job.stale_flagged_at),
            ),
        )
        self._conn.commit()
        return job

    def update_job(self, job: Job) -> Job:
        self._conn.execute(
            """
            UPDATE jobs SET
                title=?, worker=?, status=?, source_reference=?, source_system=?,
                source_security_level=?, started_at=?, last_heartbeat_at=?,
                completed_at=?, blocker=?, artifact_reference=?, approval_required=?,
                approval_status=?, return_destination=?, receipt_reference=?,
                last_progress_message=?, stale_flagged_at=?
            WHERE job_id=?
            """,
            (
                job.title,
                job.worker,
                job.status.value,
                job.source_reference,
                job.source_system,
                job.source_security_level.value,
                iso(job.started_at),
                iso(job.last_heartbeat_at),
                iso(job.completed_at),
                job.blocker,
                job.artifact_reference,
                int(job.approval_required),
                job.approval_status.value,
                job.return_destination,
                job.receipt_reference,
                job.last_progress_message,
                iso(job.stale_flagged_at),
                job.job_id,
            ),
        )
        self._conn.commit()
        return job

    def get_job(self, job_id: str) -> Job | None:
        row = self._conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        return self._row_to_job(row) if row else None

    def list_jobs(self) -> list[Job]:
        rows = self._conn.execute("SELECT * FROM jobs ORDER BY created_at DESC").fetchall()
        return [self._row_to_job(row) for row in rows]

    def jobs_with_status(self, statuses: Iterable[JobStatus]) -> list[Job]:
        wanted = {status.value for status in statuses}
        return [job for job in self.list_jobs() if job.status.value in wanted]

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> Job:
        return Job(
            job_id=row["job_id"],
            title=row["title"],
            worker=row["worker"],
            status=JobStatus(row["status"]),
            source_reference=row["source_reference"],
            source_system=row["source_system"],
            source_security_level=SourceSecurityLevel(row["source_security_level"]),
            created_at=_parse_dt(row["created_at"]),
            started_at=_parse_dt(row["started_at"]),
            last_heartbeat_at=_parse_dt(row["last_heartbeat_at"]),
            completed_at=_parse_dt(row["completed_at"]),
            blocker=row["blocker"],
            artifact_reference=row["artifact_reference"],
            approval_required=bool(row["approval_required"]),
            approval_status=ApprovalStatus(row["approval_status"]),
            return_destination=row["return_destination"],
            receipt_reference=row["receipt_reference"],
            last_progress_message=row["last_progress_message"],
            stale_flagged_at=_parse_dt(row["stale_flagged_at"]),
        )

    # events ---------------------------------------------------------------
    def event_exists(self, event_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM job_events WHERE event_id=?", (event_id,)
        ).fetchone()
        return row is not None

    def record_event(self, event: WorkerEvent, received_at: datetime) -> None:
        self._conn.execute(
            """
            INSERT INTO job_events (
                event_id, job_id, worker, event, message, artifact_reference,
                blocker, timestamp, metadata, received_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                event.event_id,
                event.job_id,
                event.worker,
                event.event.value,
                event.message,
                event.artifact_reference,
                event.blocker,
                iso(event.timestamp),
                json.dumps(event.metadata, default=str),
                iso(received_at),
            ),
        )
        self._conn.commit()

    def list_events(self, job_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM job_events WHERE job_id=? ORDER BY id ASC", (job_id,)
        ).fetchall()
        return [
            {
                "event": WorkerEventType(row["event"]).value,
                "worker": row["worker"],
                "message": row["message"],
                "artifact_reference": row["artifact_reference"],
                "blocker": row["blocker"],
                "timestamp": row["timestamp"],
                "metadata": json.loads(row["metadata"]),
            }
            for row in rows
        ]

    # receipts -------------------------------------------------------------
    def record_receipt(
        self,
        job_id: str,
        destination: str,
        status: str,
        reference: str | None,
        detail: str | None,
        payload: dict[str, Any],
        created_at: datetime,
    ) -> int:
        cursor = self._conn.execute(
            """
            INSERT INTO receipts
                (job_id, destination, status, reference, detail, payload, created_at)
            VALUES (?,?,?,?,?,?,?)
            """,
            (
                job_id,
                destination,
                status,
                reference,
                detail,
                json.dumps(payload, default=str),
                iso(created_at),
            ),
        )
        self._conn.commit()
        return int(cursor.lastrowid)

    def list_receipts(self, job_id: str | None = None) -> list[dict[str, Any]]:
        if job_id:
            rows = self._conn.execute(
                "SELECT * FROM receipts WHERE job_id=? ORDER BY id ASC", (job_id,)
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM receipts ORDER BY id ASC").fetchall()
        return [
            {
                "id": row["id"],
                "job_id": row["job_id"],
                "destination": row["destination"],
                "status": row["status"],
                "reference": row["reference"],
                "detail": row["detail"],
                "payload": json.loads(row["payload"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]
