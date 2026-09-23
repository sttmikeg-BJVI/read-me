from __future__ import annotations

import json
import time

from bjvi_handoff.models import redact
from bjvi_handoff.security import sign

from .conftest import PILOT_JOB_ID, WORKER, WORKER_SECRET, operator_headers, signed_post


def test_unsigned_callback_rejected(client, pilot_job):
    response = client.post(
        "/api/worker-events",
        json={
            "job_id": PILOT_JOB_ID,
            "worker": WORKER,
            "event": "COMPLETE",
            "artifact_reference": "gdrive:file/forged",
        },
    )
    assert response.status_code == 401
    assert client.get(
        f"/api/jobs/{PILOT_JOB_ID}", headers=operator_headers()
    ).json()["status"] == "QUEUED"


def test_wrong_secret_rejected(client, pilot_job):
    response = signed_post(
        client,
        {"job_id": PILOT_JOB_ID, "worker": WORKER, "event": "ACKNOWLEDGED"},
        secret="not-the-secret",
    )
    assert response.status_code == 401


def test_unknown_worker_rejected(client, pilot_job):
    response = signed_post(
        client,
        {"job_id": PILOT_JOB_ID, "worker": "rogue-bot", "event": "ACKNOWLEDGED"},
        worker="rogue-bot",
        secret="anything",
    )
    assert response.status_code == 401


def test_tampered_body_rejected(client, pilot_job):
    payload = {"job_id": PILOT_JOB_ID, "worker": WORKER, "event": "ACKNOWLEDGED"}
    body = json.dumps(payload, separators=(",", ":")).encode()
    stamp = str(int(time.time()))
    signature = sign(WORKER_SECRET, WORKER, stamp, body)
    tampered = json.dumps(
        {**payload, "event": "COMPLETE", "artifact_reference": "x"}, separators=(",", ":")
    ).encode()

    response = client.post(
        "/api/worker-events",
        content=tampered,
        headers={
            "content-type": "application/json",
            "x-bjvi-worker": WORKER,
            "x-bjvi-timestamp": stamp,
            "x-bjvi-signature": signature,
        },
    )
    assert response.status_code == 401


def test_stale_signature_rejected(client, pilot_job):
    response = signed_post(
        client,
        {"job_id": PILOT_JOB_ID, "worker": WORKER, "event": "ACKNOWLEDGED"},
        timestamp=int(time.time()) - 3600,
    )
    assert response.status_code == 401


def test_worker_cannot_report_on_another_workers_job(client, pilot_job, settings):
    settings.worker_secrets["codex"] = "codex-secret"
    response = signed_post(
        client,
        {"job_id": PILOT_JOB_ID, "worker": "codex", "event": "ACKNOWLEDGED"},
        worker="codex",
        secret="codex-secret",
    )
    assert response.status_code == 403


def test_operator_api_requires_token(client):
    assert client.get("/api/jobs").status_code == 401
    assert client.get("/dashboard").status_code == 401
    assert client.post("/api/jobs", json={}).status_code == 401


def test_sensitive_values_are_redacted_before_storage(client, slack, store, pilot_job):
    signed_post(client, {"job_id": PILOT_JOB_ID, "worker": WORKER, "event": "ACKNOWLEDGED"})
    signed_post(
        client,
        {
            "job_id": PILOT_JOB_ID,
            "worker": WORKER,
            "event": "BLOCKED",
            "blocker": "portal rejected SSN 123-45-6789 and api_key=sk-live-abcdefgh12345",
        },
    )
    stored = store.get_job(PILOT_JOB_ID).blocker
    assert "123-45-6789" not in stored
    assert "sk-live-abcdefgh12345" not in stored
    assert "[REDACTED_SSN]" in stored
    assert "123-45-6789" not in slack.messages[-1]["text"]


def test_redact_handles_account_numbers():
    assert "4111111111111111" not in redact("card 4111111111111111 on file")
