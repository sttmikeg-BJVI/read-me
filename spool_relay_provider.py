"""A provider whose transport is a filesystem spool.

Why this exists: Conference already has a complete Devin adapter, but it needs
`DEVIN_API_KEY` to reach api.devin.ai, and no key is provisioned. The spool is
the transport that *is* available today — a dispatch envelope is written to
disk, an authorized relay (an operator or an agent that already holds Devin
session permissions) starts the real worker and writes the worker's structured
return back, and Conference continues unchanged.

What is real here and what is not, stated plainly:

  * The worker execution is real. The relay is not allowed to author a return;
    it may only copy what the worker produced.
  * The evidence in that return is verified independently by Conference against
    GitHub/git/HTTP, so a lying relay or a lying worker fails verification.
  * The *message transport* is a file, not an API call. It is not a webhook and
    must never be described as one.

Fencing is enforced on both sides: the dispatch envelope carries the fence
token, and a return whose fence does not match the attempt is surfaced as an
invalid return rather than being believed. This module writes no canonical
state and exposes no way to mark anything verified.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from devin_worker_adapter import (
    WORKER_RETURN_SCHEMA,
    Claim,
    Job,
    WorkerReturn,
)

DISPATCH_DIR = "dispatch"
RETURN_DIR = "returns"
ACK_DIR = "ack"

STATUS_FINISHED = "finished"
STATUS_BLOCKED = "blocked"
STATUS_RUNNING = "running"


def envelope_name(job_id: str, fence_token: str) -> str:
    return f"{job_id}.{fence_token}.json"


@dataclass
class SpoolRelayProvider:
    """Dispatch by writing a file; return by reading one.

    `worker` names the real executor the relay is required to use (for example
    "devin"), so a receipt never implies the spool itself did the work.
    """

    root: Path
    worker: str = "devin"
    clock: Callable[[], float] = time.time
    sleep: Callable[[float], None] = time.sleep

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        for sub in (DISPATCH_DIR, RETURN_DIR, ACK_DIR):
            (self.root / sub).mkdir(parents=True, exist_ok=True)

    # --- paths ------------------------------------------------------------

    def dispatch_path(self, job_id: str, fence_token: str) -> Path:
        return self.root / DISPATCH_DIR / envelope_name(job_id, fence_token)

    def return_path(self, job_id: str, fence_token: str) -> Path:
        return self.root / RETURN_DIR / envelope_name(job_id, fence_token)

    def ack_path(self, job_id: str, fence_token: str) -> Path:
        return self.root / ACK_DIR / envelope_name(job_id, fence_token)

    # --- dispatch ---------------------------------------------------------

    def dispatch_once(self, job: Job) -> Claim:
        """Write the dispatch envelope at most once per (job, fence).

        Re-dispatching the same attempt is a no-op that returns the existing
        claim, so a retried Conference step cannot start a second worker.
        """
        path = self.dispatch_path(job.job_id, job.fence_token)
        if not path.exists():
            envelope = {
                "job_id": job.job_id,
                "fence_token": job.fence_token,
                "worker": self.worker,
                "tags": job.tags(),
                "prompt": job.prompt(),
                "return_schema": WORKER_RETURN_SCHEMA,
                "return_path": str(self.return_path(job.job_id, job.fence_token)),
                "dispatched_at": self.clock(),
            }
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(envelope, indent=2))
            tmp.rename(path)

        ack = self._read_json(self.ack_path(job.job_id, job.fence_token)) or {}
        return Claim(
            job_id=job.job_id,
            fence_token=job.fence_token,
            session_id=str(ack.get("session_id") or f"spool:{job.job_id}:{job.fence_token}"),
            session_url=str(ack.get("session_url") or ""),
            is_new_session=None,
            dispatched_at=self.clock(),
        )

    def acknowledge(self, job_id: str, fence_token: str, session_id: str, session_url: str) -> None:
        """Relay records which real worker session picked the attempt up.

        Acknowledgement is not a return: it proves a worker started, nothing
        about the outcome.
        """
        path = self.ack_path(job_id, fence_token)
        path.write_text(
            json.dumps(
                {
                    "session_id": session_id,
                    "session_url": session_url,
                    "acknowledged_at": self.clock(),
                },
                indent=2,
            )
        )

    # --- return -----------------------------------------------------------

    def poll(self, claim: Claim, lease_seconds: float | None = None) -> WorkerReturn:
        payload = self._read_json(self.return_path(claim.job_id, claim.fence_token))
        ack = self._read_json(self.ack_path(claim.job_id, claim.fence_token)) or {}
        session_id = str(ack.get("session_id") or claim.session_id)

        if payload is None:
            status = STATUS_RUNNING
            terminal = False
            structured: dict[str, Any] | None = None
            raw: dict[str, Any] = {}
        else:
            raw = payload
            structured = payload.get("structured_output")
            status = str(payload.get("status_enum") or STATUS_FINISHED)
            terminal = status == STATUS_FINISHED
            session_id = str(payload.get("session_id") or session_id)

        lease_expired = bool(
            lease_seconds is not None
            and not terminal
            and self.clock() - claim.dispatched_at > lease_seconds
        )
        return WorkerReturn(
            job_id=claim.job_id,
            fence_token=claim.fence_token,
            session_id=session_id,
            status=status,
            terminal=terminal,
            structured_output=structured,
            pull_request_url=(raw.get("pull_request") or {}).get("url") if raw else None,
            raw=raw,
            lease_expired=lease_expired,
        )

    def wait_for_return(
        self,
        claim: Claim,
        lease_seconds: float | None = None,
        poll_interval: float = 5.0,
        timeout: float | None = None,
    ) -> WorkerReturn:
        started = self.clock()
        while True:
            worker_return = self.poll(claim, lease_seconds=lease_seconds)
            if worker_return.terminal or worker_return.lease_expired or worker_return.needs_human:
                return worker_return
            if timeout is not None and self.clock() - started >= timeout:
                return worker_return
            self.sleep(poll_interval)

    def nudge(self, claim: Claim, message: str) -> None:
        """Append a follow-up the relay must deliver to the live session."""
        path = self.root / DISPATCH_DIR / f"{claim.job_id}.{claim.fence_token}.nudges.jsonl"
        with open(path, "a") as handle:
            handle.write(json.dumps({"at": self.clock(), "message": message}) + "\n")

    # --- helpers ----------------------------------------------------------

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            return None


def write_return(
    root: Path,
    job_id: str,
    fence_token: str,
    structured_output: dict[str, Any],
    session_id: str = "",
    session_url: str = "",
    status_enum: str = STATUS_FINISHED,
) -> Path:
    """Relay-side helper: deposit exactly what the worker returned.

    Kept deliberately dumb. It performs no validation and no interpretation —
    Conference validates the schema, the fence, and the evidence.
    """
    path = Path(root) / RETURN_DIR / envelope_name(job_id, fence_token)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "job_id": job_id,
                "fence_token": fence_token,
                "session_id": session_id,
                "session_url": session_url,
                "status_enum": status_enum,
                "structured_output": structured_output,
            },
            indent=2,
        )
    )
    return path
