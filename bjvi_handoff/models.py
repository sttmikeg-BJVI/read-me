"""Normalized AI job schema for the Blessed Journey VI handoff layer."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


class JobStatus(str, Enum):
    QUEUED = "QUEUED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RUNNING = "RUNNING"
    BLOCKED = "BLOCKED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


class WorkerEventType(str, Enum):
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PROGRESS = "PROGRESS"
    BLOCKED = "BLOCKED"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


class ApprovalStatus(str, Enum):
    NOT_REQUIRED = "NOT_REQUIRED"
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class SourceSecurityLevel(str, Enum):
    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    CONFIDENTIAL = "CONFIDENTIAL"
    RESTRICTED = "RESTRICTED"


TERMINAL_STATUSES = {JobStatus.COMPLETE, JobStatus.FAILED}

# A job only leaves a state along an explicitly allowed edge. Existing in the
# database is never enough to call a job RUNNING.
ALLOWED_TRANSITIONS: dict[JobStatus, set[JobStatus]] = {
    JobStatus.QUEUED: {JobStatus.ACKNOWLEDGED, JobStatus.BLOCKED, JobStatus.FAILED},
    JobStatus.ACKNOWLEDGED: {
        JobStatus.RUNNING,
        JobStatus.BLOCKED,
        JobStatus.FAILED,
        JobStatus.AWAITING_APPROVAL,
        JobStatus.COMPLETE,
    },
    JobStatus.RUNNING: {
        JobStatus.RUNNING,
        JobStatus.BLOCKED,
        JobStatus.AWAITING_APPROVAL,
        JobStatus.COMPLETE,
        JobStatus.FAILED,
    },
    JobStatus.BLOCKED: {
        JobStatus.RUNNING,
        JobStatus.BLOCKED,
        JobStatus.COMPLETE,
        JobStatus.FAILED,
        JobStatus.AWAITING_APPROVAL,
    },
    JobStatus.AWAITING_APPROVAL: {
        JobStatus.RUNNING,
        JobStatus.COMPLETE,
        JobStatus.BLOCKED,
        JobStatus.FAILED,
    },
    JobStatus.COMPLETE: set(),
    JobStatus.FAILED: {JobStatus.RUNNING},
}

EVENT_TO_STATUS: dict[WorkerEventType, JobStatus] = {
    WorkerEventType.ACKNOWLEDGED: JobStatus.ACKNOWLEDGED,
    WorkerEventType.PROGRESS: JobStatus.RUNNING,
    WorkerEventType.BLOCKED: JobStatus.BLOCKED,
    WorkerEventType.COMPLETE: JobStatus.COMPLETE,
    WorkerEventType.FAILED: JobStatus.FAILED,
}


class TransitionError(ValueError):
    """Raised when a worker event would violate the job state machine."""


# Patterns that must never be persisted or relayed to Slack/Monday. Workers send
# free text, so redaction happens at the boundary rather than trusting callers.
_REDACTIONS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[REDACTED_SSN]"),
    (re.compile(r"\b(?:\d[ -]?){13,19}\b"), "[REDACTED_ACCOUNT_NUMBER]"),
    (
        re.compile(r"\b(?:sk|pk|xoxb|xoxp|ghp|gho|github_pat)[-_][A-Za-z0-9_-]{8,}\b"),
        "[REDACTED_KEY]",
    ),
    (
        re.compile(r"(?i)\b(password|passwd|secret|api[_-]?key|token)\b\s*[:=]\s*\S+"),
        r"\1=[REDACTED]",
    ),
]

MAX_TEXT_LENGTH = 2000


def redact(text: str | None) -> str | None:
    """Strip credential- and PII-shaped substrings out of worker-supplied text."""
    if text is None:
        return None
    cleaned = text
    for pattern, replacement in _REDACTIONS:
        cleaned = pattern.sub(replacement, cleaned)
    return cleaned[:MAX_TEXT_LENGTH]


@dataclass
class Job:
    job_id: str
    title: str
    worker: str
    status: JobStatus = JobStatus.QUEUED
    source_reference: str | None = None
    source_system: str | None = None
    source_security_level: SourceSecurityLevel = SourceSecurityLevel.INTERNAL
    created_at: datetime = field(default_factory=utcnow)
    started_at: datetime | None = None
    last_heartbeat_at: datetime | None = None
    completed_at: datetime | None = None
    blocker: str | None = None
    artifact_reference: str | None = None
    approval_required: bool = False
    approval_status: ApprovalStatus = ApprovalStatus.NOT_REQUIRED
    return_destination: str | None = None
    receipt_reference: str | None = None
    last_progress_message: str | None = None
    stale_flagged_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key, value in list(data.items()):
            if isinstance(value, datetime):
                data[key] = iso(value)
            elif isinstance(value, Enum):
                data[key] = value.value
        return data


@dataclass
class WorkerEvent:
    job_id: str
    worker: str
    event: WorkerEventType
    message: str | None = None
    artifact_reference: str | None = None
    blocker: str | None = None
    timestamp: datetime = field(default_factory=utcnow)
    metadata: dict[str, Any] = field(default_factory=dict)
    event_id: str | None = None
