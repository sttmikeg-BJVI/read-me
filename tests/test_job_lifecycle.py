from __future__ import annotations

from datetime import timedelta

from bjvi_handoff.models import utcnow

from .conftest import PILOT_ITEM_ID, PILOT_JOB_ID, WORKER, operator_headers, signed_post


def test_job_creation_starts_queued_not_running(client, pilot_job):
    assert pilot_job["status"] == "QUEUED"
    assert pilot_job["started_at"] is None
    assert pilot_job["last_heartbeat_at"] is None
    assert pilot_job["return_destination"] == f"monday:item/{PILOT_ITEM_ID}"
    assert pilot_job["approval_status"] == "PENDING"

    listed = client.get("/api/jobs", headers=operator_headers()).json()["jobs"]
    assert [job["job_id"] for job in listed] == [PILOT_JOB_ID]


def test_worker_acknowledgement(client, pilot_job):
    response = signed_post(
        client, {"job_id": PILOT_JOB_ID, "worker": WORKER, "event": "ACKNOWLEDGED"}
    )
    assert response.status_code == 202
    job = response.json()["job"]
    assert job["status"] == "ACKNOWLEDGED"
    assert job["started_at"] is not None
    assert job["last_heartbeat_at"] is not None


def test_progress_updates_heartbeat_and_message(client, pilot_job):
    signed_post(client, {"job_id": PILOT_JOB_ID, "worker": WORKER, "event": "ACKNOWLEDGED"})
    response = signed_post(
        client,
        {
            "job_id": PILOT_JOB_ID,
            "worker": WORKER,
            "event": "PROGRESS",
            "message": "parsed 3 of 4 bureau source pointers",
        },
    )
    job = response.json()["job"]
    assert job["status"] == "RUNNING"
    assert job["last_progress_message"] == "parsed 3 of 4 bureau source pointers"


def test_blocked_state_records_blocker_and_slack_notice(client, slack, pilot_job):
    signed_post(client, {"job_id": PILOT_JOB_ID, "worker": WORKER, "event": "ACKNOWLEDGED"})
    response = signed_post(
        client,
        {
            "job_id": PILOT_JOB_ID,
            "worker": WORKER,
            "event": "BLOCKED",
            "blocker": "missing TransUnion export permission",
        },
    )
    assert response.status_code == 202
    job = response.json()["job"]
    assert job["status"] == "BLOCKED"
    assert job["blocker"] == "missing TransUnion export permission"

    text = slack.messages[-1]["text"]
    assert "CLAUDE_BLOCKED" in text
    assert f"JOB_ID: {PILOT_JOB_ID}" in text
    assert "missing TransUnion export permission" in text


def test_blocked_job_can_resume_and_complete(client, pilot_job):
    signed_post(client, {"job_id": PILOT_JOB_ID, "worker": WORKER, "event": "ACKNOWLEDGED"})
    signed_post(
        client, {"job_id": PILOT_JOB_ID, "worker": WORKER, "event": "BLOCKED", "blocker": "waiting"}
    )
    resumed = signed_post(client, {"job_id": PILOT_JOB_ID, "worker": WORKER, "event": "PROGRESS"})
    assert resumed.json()["job"]["status"] == "RUNNING"
    assert resumed.json()["job"]["blocker"] is None


def test_completion_requires_artifact_reference(client, pilot_job):
    signed_post(client, {"job_id": PILOT_JOB_ID, "worker": WORKER, "event": "ACKNOWLEDGED"})
    response = signed_post(
        client, {"job_id": PILOT_JOB_ID, "worker": WORKER, "event": "COMPLETE"}
    )
    assert response.status_code == 422
    assert client.get(
        f"/api/jobs/{PILOT_JOB_ID}", headers=operator_headers()
    ).json()["status"] == "ACKNOWLEDGED"


def test_completion_writes_monday_and_slack_receipt(client, monday, slack, pilot_job):
    signed_post(client, {"job_id": PILOT_JOB_ID, "worker": WORKER, "event": "ACKNOWLEDGED"})
    response = signed_post(
        client,
        {
            "job_id": PILOT_JOB_ID,
            "worker": WORKER,
            "event": "COMPLETE",
            "message": "forensic matrix delivered",
            "artifact_reference": "gdrive:file/credit-matrix-v1",
        },
    )
    assert response.status_code == 202
    job = response.json()["job"]
    assert job["status"] == "COMPLETE"
    assert job["completed_at"] is not None
    assert job["artifact_reference"] == "gdrive:file/credit-matrix-v1"
    assert job["receipt_reference"].startswith(f"monday:item/{PILOT_ITEM_ID}")

    assert monday.calls[-1]["item_id"] == PILOT_ITEM_ID
    summary = monday.calls[-1]["summary"]
    assert "STATUS: COMPLETE" in summary
    assert "ARTIFACT: gdrive:file/credit-matrix-v1" in summary
    assert "APPROVAL_REQUIRED: YES" in summary

    receipt = slack.messages[-1]
    assert receipt["channel"] == "#claude-handoff"
    assert "CLAUDE_COMPLETE" in receipt["text"]
    assert f"MONDAY_ITEM: {PILOT_ITEM_ID}" in receipt["text"]


def test_duplicate_completion_is_idempotent(client, monday, slack, pilot_job):
    signed_post(client, {"job_id": PILOT_JOB_ID, "worker": WORKER, "event": "ACKNOWLEDGED"})
    payload = {
        "job_id": PILOT_JOB_ID,
        "worker": WORKER,
        "event": "COMPLETE",
        "artifact_reference": "gdrive:file/credit-matrix-v1",
    }
    signed_post(client, payload)
    monday_calls, slack_messages = len(monday.calls), len(slack.messages)

    repeat = signed_post(client, payload)
    assert repeat.status_code == 202
    assert repeat.json()["status"] == "already_complete"
    assert len(monday.calls) == monday_calls
    assert len(slack.messages) == slack_messages


def test_conflicting_second_completion_is_rejected(client, pilot_job):
    signed_post(client, {"job_id": PILOT_JOB_ID, "worker": WORKER, "event": "ACKNOWLEDGED"})
    signed_post(
        client,
        {
            "job_id": PILOT_JOB_ID,
            "worker": WORKER,
            "event": "COMPLETE",
            "artifact_reference": "gdrive:file/credit-matrix-v1",
        },
    )
    response = signed_post(
        client,
        {
            "job_id": PILOT_JOB_ID,
            "worker": WORKER,
            "event": "COMPLETE",
            "artifact_reference": "gdrive:file/someone-elses-file",
        },
    )
    assert response.status_code == 409


def test_replayed_event_id_is_ignored(client, pilot_job):
    payload = {
        "job_id": PILOT_JOB_ID,
        "worker": WORKER,
        "event": "ACKNOWLEDGED",
        "event_id": "evt-1",
    }
    assert signed_post(client, payload).json()["status"] == "accepted"
    assert signed_post(client, payload).json()["status"] == "duplicate_ignored"


def test_invalid_transition_rejected(client, pilot_job):
    response = signed_post(
        client,
        {
            "job_id": PILOT_JOB_ID,
            "worker": WORKER,
            "event": "COMPLETE",
            "artifact_reference": "gdrive:file/x",
        },
    )
    # A QUEUED job has never been acknowledged, so it cannot jump to COMPLETE.
    assert response.status_code == 409


def test_stale_worker_detection(client, service, store, slack, pilot_job):
    signed_post(client, {"job_id": PILOT_JOB_ID, "worker": WORKER, "event": "ACKNOWLEDGED"})
    signed_post(client, {"job_id": PILOT_JOB_ID, "worker": WORKER, "event": "PROGRESS"})
    job = store.get_job(PILOT_JOB_ID)
    job.last_heartbeat_at = utcnow() - timedelta(seconds=600)
    store.update_job(job)

    flagged = service.sweep_stale()
    assert [item.job_id for item in flagged] == [PILOT_JOB_ID]
    assert "CLAUDE_STALE" in slack.messages[-1]["text"]
    assert store.get_job(PILOT_JOB_ID).stale_flagged_at is not None

    # A flagged job is not re-announced on every sweep.
    assert service.sweep_stale() == []

    dashboard = client.get("/dashboard", headers=operator_headers()).text
    assert "STALE / INVESTIGATE" in dashboard


def test_fresh_running_job_is_not_stale(service, store, client, pilot_job):
    signed_post(client, {"job_id": PILOT_JOB_ID, "worker": WORKER, "event": "ACKNOWLEDGED"})
    signed_post(client, {"job_id": PILOT_JOB_ID, "worker": WORKER, "event": "PROGRESS"})
    assert service.sweep_stale() == []
    assert service.is_stale(store.get_job(PILOT_JOB_ID)) is False
