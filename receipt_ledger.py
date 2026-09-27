"""Append-only receipt ledger with duplicate-return protection.

Conference polls, so the same worker return can legitimately be observed more
than once (retries, overlapping pollers, restarts). Recording it twice would
inflate the evidence trail and could let one return be verified twice.

This is a reference in-memory implementation of the *semantics* only. The real
ledger lives in the existing Conference store (Postgres/Neon); when that source
arrives, these rules map onto it as a unique index on
(job_id, fence_token, session_id, return_digest) plus append-only inserts.
It is deliberately NOT another job database: it holds receipts, not jobs.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterator, Mapping

_DIGEST_FIELDS = (
    "outcome",
    "summary",
    "evidence",
    "tests",
    "blockers",
    "next_action",
    "return_valid",
    "session_status",
)


def return_digest(receipt: Mapping[str, Any]) -> str:
    """Stable digest of the *content* of a return, ignoring observation time."""
    payload = {key: receipt.get(key) for key in _DIGEST_FIELDS}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode()
    ).hexdigest()


@dataclass(frozen=True)
class RecordResult:
    stored: bool
    reason: str
    receipt_key: tuple[str, str, str, str]


class ReceiptLedger:
    def __init__(self) -> None:
        self._rows: list[dict[str, Any]] = []
        self._seen: set[tuple[str, str, str, str]] = set()

    def record(self, receipt: Mapping[str, Any]) -> RecordResult:
        """Append a receipt unless this exact return was already recorded.

        Returns `stored=False` for a duplicate. Never raises on duplicates:
        re-observing a return is normal in a polling architecture, it just must
        not produce a second receipt.
        """
        key = (
            str(receipt.get("job_id", "")),
            str(receipt.get("fence_token", "")),
            str(receipt.get("session_id", "")),
            return_digest(receipt),
        )
        if key in self._seen:
            return RecordResult(False, "duplicate return already recorded", key)
        self._seen.add(key)
        self._rows.append(dict(receipt))
        return RecordResult(True, "recorded", key)

    def for_job(self, job_id: str) -> list[dict[str, Any]]:
        """Every receipt for a job, superseded attempts included.

        Append-only: a rerouted attempt's receipt is retained so the evidence
        trail cannot be cleaned up to make a job look tidy.
        """
        return [r for r in self._rows if r.get("job_id") == job_id]

    def latest_valid_for_job(self, job_id: str) -> dict[str, Any] | None:
        """Most recently recorded receipt for a job that was a valid return."""
        for row in reversed(self._rows):
            if row.get("job_id") == job_id and row.get("return_valid"):
                return row
        return None

    def __iter__(self) -> Iterator[dict[str, Any]]:
        return iter(list(self._rows))

    def __len__(self) -> int:
        return len(self._rows)
