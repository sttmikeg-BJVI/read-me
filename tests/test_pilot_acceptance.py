"""Acceptance test for BJVI-CREDIT-PILOT-001.

Drives the full flow the way an external Claude worker would: signed callbacks
through the reference client, ending with a Monday writeback and a Slack
receipt that ChatGPT can inspect in a connected source.
"""

from __future__ import annotations

from bjvi_handoff.client import build_signed_request

from .conftest import (
    PILOT_ITEM_ID,
    PILOT_JOB_ID,
    WORKER,
    WORKER_SECRET,
    operator_headers,
)


def worker_callback(client, payload):
    url, body, headers = build_signed_request(
        "http://testserver", WORKER, WORKER_SECRET, payload
    )
    return client.post(url, content=body, headers=headers)


def test_pilot_end_to_end(client, monday, slack, pilot_job):
    assert pilot_job["job_id"] == PILOT_JOB_ID
    assert pilot_job["source_reference"] == PILOT_ITEM_ID
    assert pilot_job["source_security_level"] == "RESTRICTED"

    assert worker_callback(
        client, {"job_id": PILOT_JOB_ID, "worker": WORKER, "event": "ACKNOWLEDGED"}
    ).status_code == 202
    assert worker_callback(
        client,
        {
            "job_id": PILOT_JOB_ID,
            "worker": WORKER,
            "event": "PROGRESS",
            "message": "matrix rows reconstructed from source pointers",
        },
    ).status_code == 202

    completion = worker_callback(
        client,
        {
            "job_id": PILOT_JOB_ID,
            "worker": WORKER,
            "event": "COMPLETE",
            "message": "forensic matrix complete; approval needed before dispute filing",
            "artifact_reference": "gdrive:file/bjvi-credit-matrix-v1",
            "metadata": {"rows": 42},
        },
    )
    assert completion.status_code == 202

    detail = client.get(f"/api/jobs/{PILOT_JOB_ID}", headers=operator_headers()).json()
    assert detail["status"] == "COMPLETE"
    assert detail["approval_required"] is True
    assert detail["approval_status"] == "PENDING"
    assert detail["artifact_reference"] == "gdrive:file/bjvi-credit-matrix-v1"
    assert detail["receipt_reference"].startswith(f"monday:item/{PILOT_ITEM_ID}")
    assert [event["event"] for event in detail["events"]] == [
        "ACKNOWLEDGED",
        "PROGRESS",
        "COMPLETE",
    ]

    # Monday keeps the authoritative record, pointing at the artifact only.
    summary = monday.calls[-1]["summary"]
    assert monday.calls[-1]["item_id"] == PILOT_ITEM_ID
    assert "ARTIFACT: gdrive:file/bjvi-credit-matrix-v1" in summary
    assert "APPROVAL_REQUIRED: YES" in summary

    # Slack carries an operational receipt, not report contents.
    receipt = slack.messages[-1]["text"]
    assert receipt.splitlines() == [
        "CLAUDE_COMPLETE",
        f"JOB_ID: {PILOT_JOB_ID}",
        "STATUS: COMPLETE",
        f"MONDAY_ITEM: {PILOT_ITEM_ID}",
        "ARTIFACT: gdrive:file/bjvi-credit-matrix-v1",
        "APPROVAL_REQUIRED: YES",
    ]

    dashboard = client.get("/dashboard", headers=operator_headers()).text
    assert PILOT_JOB_ID in dashboard
    assert "COMPLETE" in dashboard
