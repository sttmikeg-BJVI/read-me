"""Worker identity verification for the callback endpoint."""

from __future__ import annotations

import hashlib
import hmac
import time

from .config import Settings

SIGNATURE_HEADER = "x-bjvi-signature"
WORKER_HEADER = "x-bjvi-worker"
TIMESTAMP_HEADER = "x-bjvi-timestamp"

SIGNATURE_VERSION = "v1"


class AuthError(Exception):
    """Raised when a callback cannot be attributed to a known worker."""


def signing_payload(worker: str, timestamp: str, body: bytes) -> bytes:
    return b".".join([SIGNATURE_VERSION.encode(), worker.encode(), timestamp.encode(), body])


def sign(secret: str, worker: str, timestamp: str, body: bytes) -> str:
    digest = hmac.new(
        secret.encode(), signing_payload(worker, timestamp, body), hashlib.sha256
    ).hexdigest()
    return f"{SIGNATURE_VERSION}={digest}"


def verify_worker(
    settings: Settings,
    worker: str | None,
    timestamp: str | None,
    signature: str | None,
    body: bytes,
    now: float | None = None,
) -> str:
    """Return the authenticated worker id or raise AuthError.

    Unauthenticated callers can never move a job to COMPLETE: every callback is
    signed with a per-worker shared secret over the exact request body, and
    stale signatures are rejected to stop replay of an old COMPLETE.
    """
    if not worker or not timestamp or not signature:
        raise AuthError("missing worker authentication headers")

    secret = settings.secret_for(worker)
    if not secret:
        raise AuthError("unknown worker")

    try:
        sent_at = float(timestamp)
    except ValueError as exc:
        raise AuthError("invalid timestamp header") from exc

    current = time.time() if now is None else now
    if abs(current - sent_at) > settings.signature_tolerance_seconds:
        raise AuthError("signature timestamp outside tolerance window")

    expected = sign(secret, worker, timestamp, body)
    if not hmac.compare_digest(expected, signature):
        raise AuthError("signature mismatch")

    return worker.strip().lower()
