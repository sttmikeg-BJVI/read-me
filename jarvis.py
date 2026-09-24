"""Jarvis: Michael's private assistant surface over Conference.

Jarvis is not the operating system, not a router and not a dashboard. It is the
one place Michael talks to, and everything it does is one of five things:

    instruct   - turn an instruction into a Conference intent
    status     - say where work stands, in Michael's words
    attention  - list what genuinely needs Michael
    follow_up  - nudge a worker that is sitting still
    recall     - find prior work and its receipts

It deliberately owns no job state. Canonical IDs, fences, verification and
state transitions belong to Conference; `jarvis_contract` already refuses a
submission that tries to set them, and this class never calls a store writer
other than `submit_intent`. That is why Jarvis can never report DONE for work
that was merely returned — it has no way to say so.

Instruction parsing here is deterministic keyword extraction, not a model. It
produces a submission Michael can see and correct before it is sent; a language
model can replace `parse_instruction` later without touching the boundary.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Sequence

from conference_store import ConferenceStore
from jarvis_contract import (
    STATE_BLOCKED_HUMAN,
    SURFACE_DONE,
    SURFACE_MICHAEL_ACTION_REQUIRED,
    JobSubmission,
    JobView,
    submission_from_payload,
    surface_status,
)

# Capability keywords. Conservative on purpose: an unrecognized instruction
# asks for nothing special rather than guessing a capability that silently
# narrows which workers are eligible.
_CAPABILITY_HINTS = {
    "code": ("code", "implement", "build", "fix", "refactor", "endpoint", "bug"),
    "github_pr": ("pr", "pull request", "merge", "branch"),
    "browser": ("browser", "website", "log in", "click", "screenshot"),
    "research": ("research", "investigate", "compare", "find out", "look into"),
    "long_running": ("overnight", "long running", "large migration"),
}

_PRIORITY_HINTS = {
    "P0": ("p0", "urgent", "today", "right now", "drop everything"),
    "P1": ("p1", "this week", "soon"),
    "P2": ("p2", "when you can", "eventually", "backlog"),
}

_MICHAEL_ONLY_HINTS = (
    "password",
    "credential",
    "api key",
    "payment",
    "sign the",
    "approve",
    "legal",
    "bank",
)


@dataclass(frozen=True)
class Reply:
    """What Jarvis says back. `job_id` is set only when work was submitted."""

    text: str
    job_id: str | None = None
    views: tuple[JobView, ...] = ()


def parse_instruction(text: str, workstream: str = "general") -> JobSubmission:
    """Turn typed or transcribed speech into a Conference submission.

    The intent_id is derived from the instruction itself, so repeating the same
    instruction twice does not create a second job — Conference treats it as the
    same intent.
    """
    cleaned = " ".join(text.split())
    if not cleaned:
        raise ValueError("empty instruction")
    lowered = cleaned.lower()

    capabilities = tuple(
        name for name, hints in _CAPABILITY_HINTS.items() if any(h in lowered for h in hints)
    )
    priority = next(
        (p for p, hints in _PRIORITY_HINTS.items() if any(h in lowered for h in hints)), "P1"
    )
    michael_only = any(hint in lowered for hint in _MICHAEL_ONLY_HINTS)
    expected = ("pull_request",) if "github_pr" in capabilities else ()

    digest = hashlib.sha256(f"{workstream}:{lowered}".encode()).hexdigest()[:12]
    return submission_from_payload(
        {
            "intent_id": f"jarvis-{digest}",
            "objective": cleaned,
            "workstream": workstream,
            "priority": priority,
            "capability_required": capabilities,
            "michael_only_gate": michael_only,
            "expected_receipts": expected,
        }
    )


class Jarvis:
    def __init__(self, store: ConferenceStore, runtime: Any = None, clock=None) -> None:
        import time

        self.store = store
        self.runtime = runtime
        self.clock = clock or time.time

    # --- sending work -----------------------------------------------------

    def instruct(self, text: str, workstream: str = "general", run: bool = False) -> Reply:
        submission = parse_instruction(text, workstream)
        job_id = self.store.submit_intent(submission, self.clock(), notify_on_complete=True)
        if run and self.runtime is not None:
            self.runtime.run(job_id, submission.expected_receipts)
        view = self.store.job_view(job_id)
        if submission.michael_only_gate:
            return Reply(
                f"{job_id}: held for you — this looks like it needs your authorization, "
                "so I have not sent it to a worker.",
                job_id,
                (view,),
            )
        return Reply(f"{job_id}: {self._line(view)}", job_id, (view,))

    # --- reading state ----------------------------------------------------

    def status(self, job_id: str | None = None, workstream: str | None = None) -> Reply:
        views = self._views(job_id=job_id, workstream=workstream)
        if not views:
            return Reply("Nothing matching that.")
        return Reply("\n".join(self._line(v) for v in views), views=tuple(views))

    def attention(self) -> Reply:
        """Only genuine Michael gates — not everything that is merely stuck."""
        views = [v for v in self._views() if surface_status(v) == SURFACE_MICHAEL_ACTION_REQUIRED]
        if not views:
            return Reply("Nothing needs you right now.")
        return Reply("\n".join(self._line(v) for v in views), views=tuple(views))

    def recall(self, query: str, limit: int = 5) -> Reply:
        """Find prior work by objective, with whatever receipts it produced."""
        pattern = re.compile(re.escape(query.strip()), re.IGNORECASE)
        matched = [v for v in self._views() if pattern.search(v.objective)][:limit]
        if not matched:
            return Reply(f"I have no prior work matching {query!r}.")
        lines = []
        for view in matched:
            refs = [
                item.get("ref", "")
                for receipt in view.receipts
                for item in receipt.get("evidence", ())
                if item.get("ref")
            ]
            suffix = f" — {', '.join(refs)}" if refs else ""
            lines.append(self._line(view) + suffix)
        return Reply("\n".join(lines), views=tuple(matched))

    # --- supervising ------------------------------------------------------

    def follow_up(self, job_id: str, message: str) -> Reply:
        """Nudge the worker holding the current attempt. Changes no state."""
        view = self.store.job_view(job_id)
        attempt = self.store.active_attempt(job_id)
        if attempt is None or self.runtime is None:
            return Reply(f"{job_id}: no worker is holding this right now — {self._line(view)}")
        provider = str(attempt["provider"])
        adapter = self.runtime.registry.adapter(provider)
        from devin_worker_adapter import Claim

        adapter.nudge(
            Claim(
                job_id=job_id,
                fence_token=str(attempt["fence_token"]),
                session_id=str(attempt["session_id"] or ""),
                session_url=str(attempt["session_url"] or ""),
                is_new_session=False,
            ),
            message,
        )
        return Reply(f"{job_id}: passed that to {provider}.", job_id, (view,))

    def escalate(self, job_id: str, reason: str) -> Reply:
        """Pull a job back to Michael. The one state change Jarvis may cause,
        and it can only ever make a job need a human — never finish one."""
        self.store.block(job_id, reason, self.clock())
        view = self.store.job_view(job_id)
        return Reply(f"{job_id}: brought back to you — {reason}", job_id, (view,))

    # --- internals --------------------------------------------------------

    def _views(self, job_id: str | None = None, workstream: str | None = None) -> list[JobView]:
        if job_id is not None:
            return [self.store.job_view(job_id)]
        views = [self.store.job_view(row["job_id"]) for row in self.store.jobs()]
        if workstream is not None:
            views = [v for v in views if v.workstream == workstream]
        return views

    def _line(self, view: JobView) -> str:
        surface = surface_status(view)
        parts = [f"{view.job_id} [{surface}] {view.objective}"]
        if surface == SURFACE_DONE and view.claim_session_url:
            parts.append(view.claim_session_url)
        elif view.state == STATE_BLOCKED_HUMAN and view.blocker:
            parts.append(view.blocker)
        elif view.unmet_dependencies:
            parts.append("waiting on " + ", ".join(view.unmet_dependencies))
        elif view.assigned_worker:
            parts.append(f"with {view.assigned_worker}")
        return " — ".join(parts)


def brief(views: Sequence[JobView]) -> str:
    """One-glance summary, grouped by what Michael would do about it."""
    buckets: dict[str, list[str]] = {}
    for view in views:
        buckets.setdefault(surface_status(view), []).append(view.job_id)
    order = [SURFACE_MICHAEL_ACTION_REQUIRED, "BLOCKED", "VERIFYING", "RUNNING", "READY", SURFACE_DONE]
    return "\n".join(
        f"{name}: {len(buckets[name])} ({', '.join(buckets[name])})"
        for name in order
        if name in buckets
    )
