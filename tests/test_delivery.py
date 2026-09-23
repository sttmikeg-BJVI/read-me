from __future__ import annotations

import httpx
from fastapi.testclient import TestClient

from bjvi_handoff.adapters import MondayClient, SlackClient
from bjvi_handoff.adapters.base import DELIVERED, FAILED, NOT_CONFIGURED
from bjvi_handoff.api import create_app
from bjvi_handoff.config import Settings

from .conftest import (
    PILOT_ITEM_ID,
    PILOT_JOB_ID,
    WORKER,
    BrokenSlack,
    FakeMonday,
    operator_headers,
    signed_post,
)


def test_missing_credentials_never_fake_a_receipt():
    settings = Settings(worker_secrets={}, monday_api_token=None, slack_bot_token=None)
    monday = MondayClient(settings)
    slack = SlackClient(settings)

    monday_result = monday.update_item(PILOT_ITEM_ID, "summary", {})
    slack_result = slack.post("#claude-handoff", "text")

    assert monday_result.status == NOT_CONFIGURED
    assert slack_result.status == NOT_CONFIGURED
    assert monday_result.reference is None and slack_result.reference is None


def test_completion_without_delivered_receipt_leaves_receipt_pending(settings, store):
    monday = FakeMonday(configured=False)
    slack = BrokenSlack()
    app = create_app(settings=settings, store=store, monday=monday, slack=slack)
    client = TestClient(app)
    client.post(
        "/api/jobs",
        headers=operator_headers(),
        json={
            "job_id": PILOT_JOB_ID,
            "title": "pilot",
            "worker": WORKER,
            "source_reference": PILOT_ITEM_ID,
        },
    )
    signed_post(client, {"job_id": PILOT_JOB_ID, "worker": WORKER, "event": "ACKNOWLEDGED"})
    response = signed_post(
        client,
        {
            "job_id": PILOT_JOB_ID,
            "worker": WORKER,
            "event": "COMPLETE",
            "artifact_reference": "gdrive:file/x",
        },
    )
    job = response.json()["job"]
    assert job["status"] == "COMPLETE"
    assert job["receipt_reference"] is None

    statuses = {receipt["destination"]: receipt["status"] for receipt in store.list_receipts()}
    assert statuses == {"monday": NOT_CONFIGURED, "slack": FAILED}


def test_retry_undelivered_receipts_lands_once_credentials_work(settings, store):
    monday = FakeMonday(configured=False)
    slack = BrokenSlack()
    app = create_app(settings=settings, store=store, monday=monday, slack=slack)
    client = TestClient(app)
    client.post(
        "/api/jobs",
        headers=operator_headers(),
        json={
            "job_id": PILOT_JOB_ID,
            "title": "pilot",
            "worker": WORKER,
            "source_reference": PILOT_ITEM_ID,
        },
    )
    signed_post(client, {"job_id": PILOT_JOB_ID, "worker": WORKER, "event": "ACKNOWLEDGED"})
    signed_post(
        client,
        {
            "job_id": PILOT_JOB_ID,
            "worker": WORKER,
            "event": "COMPLETE",
            "artifact_reference": "gdrive:file/x",
        },
    )
    monday.configured = True

    results = app.state.service.retry_undelivered()
    assert any(result.status == DELIVERED for result in results)
    assert store.get_job(PILOT_JOB_ID).receipt_reference.startswith("monday:item/")


def test_monday_client_posts_graphql_mutations():
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read().decode()
        requests.append({"headers": dict(request.headers), "body": body})
        if "items(ids:" in body or "board {" in body:
            return httpx.Response(
                200, json={"data": {"items": [{"id": "1", "board": {"id": "77"}}]}}
            )
        if "change_multiple_column_values" in body:
            return httpx.Response(
                200, json={"data": {"change_multiple_column_values": {"id": "1"}}}
            )
        return httpx.Response(200, json={"data": {"create_update": {"id": "555"}}})

    transport = httpx.MockTransport(handler)
    settings = Settings(monday_api_token="token-abc")
    client = MondayClient(settings, client=httpx.Client(transport=transport))

    result = client.update_item(
        PILOT_ITEM_ID, "STATUS: COMPLETE", {"status": {"label": "Complete"}}
    )
    assert result.status == DELIVERED
    assert result.reference == f"monday:item/{PILOT_ITEM_ID}#update/555"
    assert all(entry["headers"]["authorization"] == "token-abc" for entry in requests)


def test_monday_api_error_is_reported_not_swallowed():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"errors": [{"message": "unauthorized"}]})
    )
    settings = Settings(monday_api_token="bad-token")
    client = MondayClient(settings, client=httpx.Client(transport=transport))
    result = client.update_item(PILOT_ITEM_ID, "summary", {})
    assert result.status == FAILED
    assert "unauthorized" in result.detail


def test_slack_client_posts_message():
    captured: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(
            {"auth": request.headers.get("authorization"), "body": request.read().decode()}
        )
        return httpx.Response(200, json={"ok": True, "channel": "C123", "ts": "1700000000.1"})

    settings = Settings(slack_bot_token="xoxb-test")
    client = SlackClient(settings, client=httpx.Client(transport=httpx.MockTransport(handler)))
    result = client.post("#claude-handoff", "CLAUDE_COMPLETE")
    assert result.status == DELIVERED
    assert result.reference == "slack:C123/1700000000.1"
    assert captured[0]["auth"] == "Bearer xoxb-test"


def test_slack_api_error_is_reported():
    settings = Settings(slack_bot_token="xoxb-test")
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"ok": False, "error": "channel_not_found"})
    )
    client = SlackClient(settings, client=httpx.Client(transport=transport))
    result = client.post("#claude-handoff", "text")
    assert result.status == FAILED
    assert "channel_not_found" in result.detail
