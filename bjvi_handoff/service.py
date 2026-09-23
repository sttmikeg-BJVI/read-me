"""Handoff orchestration: state machine, Monday writeback, Slack receipts."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .adapters.base import NOT_CONFIGURED, DeliveryResult, MondayGateway, SlackGateway
from .config import Settings
from .models import (
    ALLOWED_TRANSITIONS,
    EVENT_TO_STATUS,
    ApprovalStatus,
    Job,
    JobStatus,
    SourceSecurityLevel,
    TransitionError,
    WorkerEvent,
    WorkerEventType,
    redact,
    utcnow,
)
from .store import JobStore

MONDAY_SOURCE_SYSTEM = "monday"
HEARTBEAT_STATUSES = (JobStatus.ACKNOWLEDGED, JobStatus.RUNNING, JobStatus.AWAITING_APPROVAL)


class JobNotFound(LookupError):
    pass


class WorkerMismatch(PermissionError):
    """A worker may only report on the job it was assigned."""


class MissingArtifact(ValueError):
    """COMPLETE requires a deliverable reference."""


class DuplicateCompletion(ValueError):
    """A second, conflicting COMPLETE for an already completed job."""


class HandoffService:
    def __init__(
        self,
        store: JobStore,
        settings: Settings,
        monday: MondayGateway,
        slack: SlackGateway,
    ) -> None:
        self.store = store
        self.settings = settings
        self.monday = monday
        self.slack = slack

    # job lifecycle --------------------------------------------------------
    def create_job(
        self,
        job_id: str,
        title: str,
        worker: str,
        source_reference: str | None = None,
        source_system: str | None = MONDAY_SOURCE_SYSTEM,
        source_security_level: SourceSecurityLevel = SourceSecurityLevel.CONFIDENTIAL,
        approval_required: bool = False,
        return_destination: str | None = None,
    ) -> Job:
        if self.store.get_job(job_id):
            raise ValueError(f"job {job_id} already exists")
        job = Job(
            job_id=job_id,
            title=title,
            worker=worker.strip().lower(),
            status=JobStatus.QUEUED,
            source_reference=source_reference,
            source_system=source_system,
            source_security_level=source_security_level,
            approval_required=approval_required,
            approval_status=(
                ApprovalStatus.PENDING if approval_required else ApprovalStatus.NOT_REQUIRED
            ),
            return_destination=return_destination
            or (f"monday:item/{source_reference}" if source_reference else None),
        )
        self.store.create_job(job)
        return job

    def get_job(self, job_id: str) -> Job:
        job = self.store.get_job(job_id)
        if not job:
            raise JobNotFound(job_id)
        return job

    # worker callbacks -----------------------------------------------------
    def apply_event(self, event: WorkerEvent) -> dict[str, Any]:
        job = self.store.get_job(event.job_id)
        if not job:
            raise JobNotFound(event.job_id)
        if job.worker != event.worker.strip().lower():
            raise WorkerMismatch(f"{event.worker} is not assigned to {event.job_id}")

        if event.event_id and self.store.event_exists(event.event_id):
            return {"status": "duplicate_ignored", "job": job.to_dict(), "deliveries": []}

        event.message = redact(event.message)
        event.blocker = redact(event.blocker)

        if event.event is WorkerEventType.COMPLETE and not event.artifact_reference:
            raise MissingArtifact("COMPLETE requires artifact_reference")

        if job.status is JobStatus.COMPLETE:
            if event.event is not WorkerEventType.COMPLETE:
                raise TransitionError(f"job {job.job_id} is COMPLETE and cannot accept events")
            if event.artifact_reference != job.artifact_reference:
                raise DuplicateCompletion(
                    f"job {job.job_id} already completed with a different artifact"
                )
            self.store.record_event(event, utcnow())
            return {"status": "already_complete", "job": job.to_dict(), "deliveries": []}

        target = EVENT_TO_STATUS[event.event]
        if target not in ALLOWED_TRANSITIONS[job.status]:
            raise TransitionError(
                f"cannot move {job.job_id} from {job.status.value} to {target.value}"
            )

        now = utcnow()
        self.store.record_event(event, now)

        job.status = target
        job.last_heartbeat_at = now
        job.stale_flagged_at = None
        if event.message:
            job.last_progress_message = event.message
        if event.artifact_reference:
            job.artifact_reference = event.artifact_reference
        if target is JobStatus.ACKNOWLEDGED and not job.started_at:
            job.started_at = now
        if target is JobStatus.RUNNING and not job.started_at:
            job.started_at = now
        if target is JobStatus.BLOCKED:
            job.blocker = event.blocker or event.message or "unspecified blocker"
        else:
            job.blocker = None
        if target is JobStatus.COMPLETE:
            job.completed_at = now
            if job.approval_required and job.approval_status is ApprovalStatus.NOT_REQUIRED:
                job.approval_status = ApprovalStatus.PENDING

        self.store.update_job(job)

        deliveries: list[DeliveryResult] = []
        if target is JobStatus.COMPLETE:
            deliveries = self._deliver_completion(job)
        elif target is JobStatus.BLOCKED:
            deliveries = [self._notify_slack(job, self._blocked_receipt(job))]
        elif target is JobStatus.FAILED:
            deliveries = [self._notify_slack(job, self._failed_receipt(job))]

        job = self.store.get_job(job.job_id) or job
        return {
            "status": "accepted",
            "job": job.to_dict(),
            "deliveries": [delivery.__dict__ for delivery in deliveries],
        }

    # delivery -------------------------------------------------------------
    def _deliver_completion(self, job: Job) -> list[DeliveryResult]:
        monday_result = self._write_monday(job)
        if monday_result.delivered:
            job.receipt_reference = monday_result.reference
            self.store.update_job(job)
        slack_result = self._notify_slack(job, self._complete_receipt(job))
        if slack_result.delivered and not job.receipt_reference:
            job.receipt_reference = slack_result.reference
            self.store.update_job(job)
        return [monday_result, slack_result]

    def _write_monday(self, job: Job) -> DeliveryResult:
        item_id = (job.source_reference or "").strip()
        if not item_id or job.source_system != MONDAY_SOURCE_SYSTEM:
            result = DeliveryResult(
                destination="monday",
                status=NOT_CONFIGURED,
                detail="job has no monday source_reference",
                payload={"job_id": job.job_id},
            )
        else:
            summary = self._monday_summary(job)
            column_values = {
                self.settings.monday_status_column_id: {"label": job.status.value.title()}
            }
            result = self.monday.update_item(item_id, summary, column_values)
        self._record(job, result)
        return result

    def _notify_slack(self, job: Job, text: str) -> DeliveryResult:
        result = self.slack.post(self.settings.slack_channel, text)
        self._record(job, result)
        return result

    def _record(self, job: Job, result: DeliveryResult) -> None:
        self.store.record_receipt(
            job_id=job.job_id,
            destination=result.destination,
            status=result.status,
            reference=result.reference,
            detail=result.detail,
            payload=result.payload or {},
            created_at=utcnow(),
        )

    def _monday_summary(self, job: Job) -> str:
        lines = [
            f"STATUS: {job.status.value}",
            f"WORKER: {job.worker}",
            f"JOB_ID: {job.job_id}",
            f"COMPLETED_AT: {job.completed_at.isoformat() if job.completed_at else 'n/a'}",
            f"SUMMARY: {job.last_progress_message or 'no summary provided'}",
            f"ARTIFACT: {job.artifact_reference}",
            f"APPROVAL_REQUIRED: {'YES' if job.approval_required else 'NO'}",
            f"APPROVAL_STATUS: {job.approval_status.value}",
            f"SOURCE_SECURITY_LEVEL: {job.source_security_level.value}",
        ]
        return "\n".join(lines)

    def _complete_receipt(self, job: Job) -> str:
        return "\n".join(
            [
                f"{job.worker.upper()}_COMPLETE",
                f"JOB_ID: {job.job_id}",
                "STATUS: COMPLETE",
                f"MONDAY_ITEM: {job.source_reference or 'n/a'}",
                f"ARTIFACT: {job.artifact_reference}",
                f"APPROVAL_REQUIRED: {'YES' if job.approval_required else 'NO'}",
            ]
        )

    def _blocked_receipt(self, job: Job) -> str:
        return "\n".join(
            [
                f"{job.worker.upper()}_BLOCKED",
                f"JOB_ID: {job.job_id}",
                "STATUS: BLOCKED",
                f"MONDAY_ITEM: {job.source_reference or 'n/a'}",
                f"BLOCKER: {job.blocker}",
            ]
        )

    def _failed_receipt(self, job: Job) -> str:
        return "\n".join(
            [
                f"{job.worker.upper()}_FAILED",
                f"JOB_ID: {job.job_id}",
                "STATUS: FAILED",
                f"MONDAY_ITEM: {job.source_reference or 'n/a'}",
                f"DETAIL: {job.last_progress_message or 'no detail provided'}",
            ]
        )

    def _stale_receipt(self, job: Job, silent_for: timedelta) -> str:
        return "\n".join(
            [
                f"{job.worker.upper()}_STALE",
                f"JOB_ID: {job.job_id}",
                f"STATUS: {job.status.value} / INVESTIGATE",
                f"MONDAY_ITEM: {job.source_reference or 'n/a'}",
                f"NO_PROGRESS_FOR: {int(silent_for.total_seconds())}s",
            ]
        )

    # heartbeat ------------------------------------------------------------
    def sweep_stale(self, now: datetime | None = None) -> list[Job]:
        now = now or utcnow()
        threshold = timedelta(seconds=self.settings.stale_threshold_seconds)
        flagged: list[Job] = []
        for job in self.store.jobs_with_status(HEARTBEAT_STATUSES):
            if job.stale_flagged_at:
                continue
            last_seen = job.last_heartbeat_at or job.started_at or job.created_at
            silent_for = now - last_seen
            if silent_for <= threshold:
                continue
            job.stale_flagged_at = now
            self.store.update_job(job)
            self._notify_slack(job, self._stale_receipt(job, silent_for))
            flagged.append(job)
        return flagged

    def is_stale(self, job: Job, now: datetime | None = None) -> bool:
        if job.status not in HEARTBEAT_STATUSES:
            return False
        now = now or utcnow()
        last_seen = job.last_heartbeat_at or job.started_at or job.created_at
        return (now - last_seen).total_seconds() > self.settings.stale_threshold_seconds

    # receipts -------------------------------------------------------------
    def retry_undelivered(self) -> list[DeliveryResult]:
        """Re-attempt completion receipts for jobs that completed without one."""
        results: list[DeliveryResult] = []
        for job in self.store.jobs_with_status([JobStatus.COMPLETE]):
            if job.receipt_reference:
                continue
            results.extend(self._deliver_completion(job))
        return results
