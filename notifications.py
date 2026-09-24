"""Delivery for the notifications Conference already decides to send.

`jarvis_contract.should_notify` / `notification_reason` decide *whether* and
*what*; the runtime calls a `Notifier`; until now the only notifier recorded
in memory. These deliver.

Two rules shape this file:

  * Nothing is silently dropped. Every notification is appended to a local
    JSONL audit log before any network attempt, and a failed delivery is
    recorded with its reason. A notifier that swallows a failure would make a
    blocked job look attended to, which is the exact failure this whole
    project exists to remove.
  * The payload carries state and references only — job id, surface status,
    worker, session URL, evidence refs. Never summaries of private material,
    never credentials. The same discipline the Slack receipts in the handoff
    service use.

`WebhookNotifier` signs with the scheme the handoff service already verifies
(`v1.<sender>.<timestamp>.<body>`, HMAC-SHA256), so pointing Conference at
that service is configuration, not code.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from jarvis_contract import JobView, surface_status

DEFAULT_SENDER = "conference"
SIGNATURE_VERSION = "v1"


def payload_for(event: str, view: JobView, message: str) -> dict:
    """Operational facts and references. No private content."""
    return {
        "event": event,
        "job_id": view.job_id,
        "status": surface_status(view),
        "state": view.state,
        "workstream": view.workstream,
        "priority": view.priority,
        "worker": view.assigned_worker,
        "session_url": view.claim_session_url,
        "blocker": view.blocker,
        "message": message,
        "evidence": [
            {"kind": item.get("kind"), "ref": item.get("ref")}
            for receipt in view.receipts
            for item in receipt.get("evidence", ())
        ],
    }


def sign(secret: str, sender: str, timestamp: int, body: bytes) -> str:
    material = f"{SIGNATURE_VERSION}.{sender}.{timestamp}.".encode() + body
    return hmac.new(secret.encode(), material, hashlib.sha256).hexdigest()


@dataclass
class AuditLogNotifier:
    """Append-only local record. The floor under every other notifier."""

    path: Path
    clock: object = time.time

    def notify(self, event: str, view: JobView, message: str) -> None:
        self.write(payload_for(event, view, message), delivered=False, detail="recorded only")

    def write(self, payload: dict, delivered: bool, detail: str) -> None:
        line = dict(payload, at=self.clock(), delivered=delivered, delivery_detail=detail)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(line, sort_keys=True) + "\n")


@dataclass
class WebhookNotifier:
    """Signed HTTPS delivery to whatever fans out to Slack/Monday."""

    url: str
    secret: str
    sender: str = DEFAULT_SENDER
    timeout: float = 10.0
    audit: AuditLogNotifier | None = None
    failures: list[tuple[str, str]] = field(default_factory=list)

    def notify(self, event: str, view: JobView, message: str) -> None:
        payload = payload_for(event, view, message)
        body = json.dumps(payload, sort_keys=True).encode()
        timestamp = int(time.time())
        request = urllib.request.Request(
            self.url,
            data=body,
            headers={
                "content-type": "application/json",
                "x-bjvi-sender": self.sender,
                "x-bjvi-timestamp": str(timestamp),
                "x-bjvi-signature": sign(self.secret, self.sender, timestamp, body),
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                delivered = 200 <= response.status < 300
                detail = f"HTTP {response.status}"
        except urllib.error.HTTPError as exc:
            delivered, detail = False, f"HTTP {exc.code}"
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            delivered, detail = False, f"{type(exc).__name__}: {exc}"

        if not delivered:
            self.failures.append((view.job_id, detail))
        if self.audit is not None:
            self.audit.write(payload, delivered=delivered, detail=detail)


def notifier_from_env(audit_path: str | os.PathLike = "conference-notifications.jsonl"):
    """Build the strongest notifier the environment actually supports.

    With no webhook configured this returns the audit log alone — visibly
    undelivered, rather than a notifier that appears to be sending.
    """
    audit = AuditLogNotifier(Path(audit_path))
    url = os.environ.get("CONFERENCE_NOTIFY_URL")
    secret = os.environ.get("CONFERENCE_NOTIFY_SECRET")
    if url and secret:
        return WebhookNotifier(url, secret, audit=audit)
    return audit
