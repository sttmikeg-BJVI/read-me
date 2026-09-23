"""Reference client an external worker (or a Zapier Code step) can copy."""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from .security import sign


def build_signed_request(
    base_url: str,
    worker: str,
    secret: str,
    payload: dict[str, Any],
    timestamp: float | None = None,
) -> tuple[str, bytes, dict[str, str]]:
    body = json.dumps(payload, separators=(",", ":"), default=str).encode()
    stamp = str(int(timestamp if timestamp is not None else time.time()))
    headers = {
        "content-type": "application/json",
        "x-bjvi-worker": worker,
        "x-bjvi-timestamp": stamp,
        "x-bjvi-signature": sign(secret, worker, stamp, body),
    }
    return f"{base_url.rstrip('/')}/api/worker-events", body, headers


def send_event(
    base_url: str,
    worker: str,
    secret: str,
    payload: dict[str, Any],
    client: httpx.Client | None = None,
) -> httpx.Response:
    url, body, headers = build_signed_request(base_url, worker, secret, payload)
    owned = client is None
    http = client or httpx.Client(timeout=20.0)
    try:
        return http.post(url, content=body, headers=headers)
    finally:
        if owned:
            http.close()
