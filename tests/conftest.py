from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bjvi_handoff.adapters.base import DELIVERED, FAILED, NOT_CONFIGURED, DeliveryResult
from bjvi_handoff.api import create_app
from bjvi_handoff.config import Settings
from bjvi_handoff.security import sign
from bjvi_handoff.store import JobStore

WORKER = "claude"
WORKER_SECRET = "test-worker-secret"
OPERATOR_TOKEN = "test-operator-token"
PILOT_JOB_ID = "BJVI-CREDIT-PILOT-001"
PILOT_ITEM_ID = "12990994496"


@dataclass
class FakeMonday:
    configured: bool = True
    calls: list[dict[str, Any]] = field(default_factory=list)

    def update_item(self, item_id, summary, column_values) -> DeliveryResult:
        payload = {"item_id": item_id, "summary": summary, "column_values": column_values}
        self.calls.append(payload)
        if not self.configured:
            return DeliveryResult("monday", NOT_CONFIGURED, detail="no token", payload=payload)
        return DeliveryResult(
            "monday", DELIVERED, reference=f"monday:item/{item_id}#update/900", payload=payload
        )


@dataclass
class FakeSlack:
    configured: bool = True
    messages: list[dict[str, str]] = field(default_factory=list)

    def post(self, channel, text) -> DeliveryResult:
        payload = {"channel": channel, "text": text}
        self.messages.append(payload)
        if not self.configured:
            return DeliveryResult("slack", NOT_CONFIGURED, detail="no token", payload=payload)
        return DeliveryResult(
            "slack", DELIVERED, reference="slack:C123/1700000000.1", payload=payload
        )


@dataclass
class BrokenSlack:
    messages: list[dict[str, str]] = field(default_factory=list)

    def post(self, channel, text) -> DeliveryResult:
        payload = {"channel": channel, "text": text}
        self.messages.append(payload)
        return DeliveryResult(
            "slack", FAILED, detail="slack api error: channel_not_found", payload=payload
        )


@pytest.fixture
def settings() -> Settings:
    return Settings(
        database_path=":memory:",
        worker_secrets={WORKER: WORKER_SECRET},
        operator_token=OPERATOR_TOKEN,
        slack_channel="#claude-handoff",
        stale_threshold_seconds=60,
    )


@pytest.fixture
def store() -> JobStore:
    return JobStore(":memory:")


@pytest.fixture
def monday() -> FakeMonday:
    return FakeMonday()


@pytest.fixture
def slack() -> FakeSlack:
    return FakeSlack()


@pytest.fixture
def app(settings, store, monday, slack):
    return create_app(settings=settings, store=store, monday=monday, slack=slack)


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app)


@pytest.fixture
def service(app):
    return app.state.service


def operator_headers() -> dict[str, str]:
    return {"authorization": f"Bearer {OPERATOR_TOKEN}"}


def signed_post(
    client: TestClient,
    payload: dict[str, Any],
    *,
    worker: str = WORKER,
    secret: str = WORKER_SECRET,
    timestamp: int | None = None,
    signature: str | None = None,
):
    body = json.dumps(payload, separators=(",", ":"), default=str).encode()
    stamp = str(timestamp if timestamp is not None else int(time.time()))
    headers = {
        "content-type": "application/json",
        "x-bjvi-worker": worker,
        "x-bjvi-timestamp": stamp,
        "x-bjvi-signature": signature or sign(secret, worker, stamp, body),
    }
    return client.post("/api/worker-events", content=body, headers=headers)


@pytest.fixture
def pilot_job(client) -> dict[str, Any]:
    response = client.post(
        "/api/jobs",
        headers=operator_headers(),
        json={
            "job_id": PILOT_JOB_ID,
            "title": "P0 — Credit Reconstruction Forensic Matrix",
            "worker": WORKER,
            "source_reference": PILOT_ITEM_ID,
            "source_system": "monday",
            "source_security_level": "RESTRICTED",
            "approval_required": True,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()
