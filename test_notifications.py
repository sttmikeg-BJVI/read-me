"""Notification delivery, exercised over a real HTTP socket.

The server here is a real `http.server` bound to a real port: the request is
serialised, signed, sent over TCP and verified by the receiver. That proves
the transport and the signature, and nothing more — it is not evidence that
Slack or Monday have been reached.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from jarvis_contract import JobView
from notifications import AuditLogNotifier, WebhookNotifier, notifier_from_env, sign

SECRET = "test-secret-not-a-credential"


@pytest.fixture()
def view():
    return JobView(
        job_id="job-1",
        state="verified",
        objective="ship the thing",
        workstream="engineering",
        priority="P0",
        assigned_worker="devin",
        claim_session_url="https://app.devin.ai/sessions/devin-abc",
        receipts=({"evidence": ({"kind": "pull_request", "ref": "https://example.com/pr/1"},)},),
    )


class _Receiver(BaseHTTPRequestHandler):
    received: list[dict] = []
    status = 200

    def do_POST(self):  # noqa: N802
        body = self.rfile.read(int(self.headers["content-length"]))
        _Receiver.received.append(
            {
                "body": body,
                "sender": self.headers["x-bjvi-sender"],
                "timestamp": self.headers["x-bjvi-timestamp"],
                "signature": self.headers["x-bjvi-signature"],
            }
        )
        self.send_response(_Receiver.status)
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture()
def receiver():
    _Receiver.received = []
    _Receiver.status = 200
    server = HTTPServer(("127.0.0.1", 0), _Receiver)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()


def test_a_notification_is_delivered_over_http_and_signed(receiver, tmp_path, view):
    audit = AuditLogNotifier(tmp_path / "audit.jsonl")
    url = f"http://127.0.0.1:{receiver.server_port}/events"
    notifier = WebhookNotifier(url, SECRET, audit=audit)

    notifier.notify("verified", view, "job-1 is done")

    assert notifier.failures == []
    got = _Receiver.received[0]
    expected = sign(SECRET, "conference", int(got["timestamp"]), got["body"])
    assert got["signature"] == expected

    payload = json.loads(got["body"])
    assert payload["status"] == "DONE"
    assert payload["job_id"] == "job-1"
    assert payload["evidence"] == [{"kind": "pull_request", "ref": "https://example.com/pr/1"}]

    logged = json.loads((tmp_path / "audit.jsonl").read_text().splitlines()[0])
    assert logged["delivered"] is True


def test_a_rejected_delivery_is_recorded_as_undelivered(receiver, tmp_path, view):
    _Receiver.status = 500
    audit = AuditLogNotifier(tmp_path / "audit.jsonl")
    notifier = WebhookNotifier(
        f"http://127.0.0.1:{receiver.server_port}/events", SECRET, audit=audit
    )

    notifier.notify("blocked", view, "needs Michael")

    assert notifier.failures == [("job-1", "HTTP 500")]
    logged = json.loads((tmp_path / "audit.jsonl").read_text().splitlines()[0])
    assert logged["delivered"] is False


def test_an_unreachable_destination_is_recorded_not_swallowed(tmp_path, view):
    audit = AuditLogNotifier(tmp_path / "audit.jsonl")
    notifier = WebhookNotifier("http://127.0.0.1:1/events", SECRET, audit=audit, timeout=2.0)

    notifier.notify("blocked", view, "needs Michael")

    assert notifier.failures and notifier.failures[0][0] == "job-1"
    assert json.loads((tmp_path / "audit.jsonl").read_text())["delivered"] is False


def test_without_a_configured_webhook_notifications_are_visibly_undelivered(
    tmp_path, view, monkeypatch
):
    monkeypatch.delenv("CONFERENCE_NOTIFY_URL", raising=False)
    monkeypatch.delenv("CONFERENCE_NOTIFY_SECRET", raising=False)
    notifier = notifier_from_env(tmp_path / "audit.jsonl")

    notifier.notify("verified", view, "done")

    logged = json.loads((tmp_path / "audit.jsonl").read_text())
    assert logged["delivered"] is False
    assert logged["delivery_detail"] == "recorded only"


def test_notifications_carry_references_not_private_content(view):
    from notifications import payload_for

    payload = payload_for("verified", view, "job-1 is done")
    assert set(payload) == {
        "event",
        "job_id",
        "status",
        "state",
        "workstream",
        "priority",
        "worker",
        "session_url",
        "blocker",
        "message",
        "evidence",
    }
