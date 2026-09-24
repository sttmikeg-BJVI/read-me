"""Jarvis <-> Conference interface types.

Jarvis is Michael's private control layer. Conference is the authoritative
ledger. This module is the boundary between them, and it exists mainly to make
two mistakes impossible:

  1. Jarvis minting canonical identity (job_id, fence_token, state,
     verification_state). Conference mints those, always.
  2. Jarvis inventing its own status vocabulary that drifts from canonical
     state. The surface Michael sees is *projected* from canonical state here,
     in one place, so there is only ever one definition of DONE.

No job storage lives here. This is a contract, not a second job database.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

# --- what Michael/Jarvis is allowed to say -----------------------------------

PRIORITIES = ("P0", "P1", "P2", "P3")

# --- canonical states Conference owns ----------------------------------------

STATE_QUEUED = "queued"
STATE_CLAIMED = "claimed"
STATE_RUNNING = "running"
STATE_RETURNED_UNVERIFIED = "returned_unverified"
STATE_VERIFYING = "verifying"
STATE_VERIFIED = "verified"
STATE_REJECTED = "rejected"
STATE_BLOCKED_HUMAN = "blocked_human"
STATE_CANCELLED = "cancelled"

CANONICAL_STATES = frozenset(
    {
        STATE_QUEUED,
        STATE_CLAIMED,
        STATE_RUNNING,
        STATE_RETURNED_UNVERIFIED,
        STATE_VERIFYING,
        STATE_VERIFIED,
        STATE_REJECTED,
        STATE_BLOCKED_HUMAN,
        STATE_CANCELLED,
    }
)

# --- what Michael is shown ----------------------------------------------------

SURFACE_READY = "READY"
SURFACE_RUNNING = "RUNNING"
SURFACE_BLOCKED = "BLOCKED"
SURFACE_VERIFYING = "VERIFYING"
SURFACE_DONE = "DONE"
SURFACE_MICHAEL_ACTION_REQUIRED = "MICHAEL ACTION REQUIRED"

# Fields only Conference may set. Jarvis supplying any of them is a contract
# violation, not a convenience.
CONFERENCE_OWNED_FIELDS = frozenset(
    {
        "job_id",
        "fence_token",
        "state",
        "verification_state",
        "assigned_worker",
        "claim",
        "lease_expires_at",
        "receipts",
    }
)


class ContractViolation(ValueError):
    pass


@dataclass(frozen=True)
class JobSubmission:
    """What Jarvis sends. Deliberately contains no canonical identity."""

    intent_id: str
    objective: str
    workstream: str
    priority: str = "P1"
    depends_on: tuple[str, ...] = ()
    capability_required: tuple[str, ...] = ()
    michael_only_gate: bool = False
    expected_receipts: tuple[str, ...] = ()
    context_refs: tuple[Mapping[str, str], ...] = ()

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.intent_id:
            errors.append("intent_id is required as the idempotency key")
        if not self.objective.strip():
            errors.append("objective is required")
        if not self.workstream.strip():
            errors.append("workstream is required")
        if self.priority not in PRIORITIES:
            errors.append(f"priority must be one of {PRIORITIES}")
        return errors


def submission_from_payload(payload: Mapping[str, Any]) -> JobSubmission:
    """Build a submission from raw Jarvis input, refusing canonical fields."""
    intruding = sorted(CONFERENCE_OWNED_FIELDS & set(payload))
    if intruding:
        raise ContractViolation(
            "Jarvis may not set Conference-owned fields: " + ", ".join(intruding)
        )
    submission = JobSubmission(
        intent_id=str(payload.get("intent_id", "")),
        objective=str(payload.get("objective", "")),
        workstream=str(payload.get("workstream", "")),
        priority=str(payload.get("priority", "P1")),
        depends_on=tuple(payload.get("depends_on", ()) or ()),
        capability_required=tuple(payload.get("capability_required", ()) or ()),
        michael_only_gate=bool(payload.get("michael_only_gate", False)),
        expected_receipts=tuple(payload.get("expected_receipts", ()) or ()),
        context_refs=tuple(payload.get("context_refs", ()) or ()),
    )
    errors = submission.validate()
    if errors:
        raise ContractViolation("; ".join(errors))
    return submission


@dataclass(frozen=True)
class JobView:
    """The read model Conference exposes to Jarvis and BJVI OS. Read-only."""

    job_id: str
    state: str
    objective: str
    workstream: str
    priority: str
    owner: str = "conference"
    assigned_worker: str | None = None
    claim_session_url: str | None = None
    lease_expires_at: float | None = None
    blocker: str | None = None
    michael_only_gate: bool = False
    receipts: tuple[Mapping[str, Any], ...] = ()
    verification_state: str = "unverified"
    next_action: str | None = None
    depends_on: tuple[str, ...] = ()
    unmet_dependencies: tuple[str, ...] = ()
    notify_on_complete: bool = False

    def __post_init__(self) -> None:
        if self.state not in CANONICAL_STATES:
            raise ContractViolation(f"unknown canonical state: {self.state!r}")


def surface_status(view: JobView) -> str:
    """Project canonical state onto the six things Michael is shown.

    A worker's return alone never projects to DONE: only a Conference
    verification does. `returned_unverified` deliberately shows as VERIFYING,
    not DONE.
    """
    if view.state == STATE_BLOCKED_HUMAN or (
        view.michael_only_gate and view.state in (STATE_QUEUED, STATE_CLAIMED)
    ):
        return SURFACE_MICHAEL_ACTION_REQUIRED
    if view.state == STATE_VERIFIED:
        return SURFACE_DONE
    if view.state in (STATE_RETURNED_UNVERIFIED, STATE_VERIFYING):
        return SURFACE_VERIFYING
    if view.state in (STATE_CLAIMED, STATE_RUNNING):
        return SURFACE_RUNNING
    if view.state == STATE_REJECTED:
        return SURFACE_BLOCKED
    if view.state == STATE_QUEUED:
        return SURFACE_BLOCKED if view.unmet_dependencies else SURFACE_READY
    if view.state == STATE_CANCELLED:
        return SURFACE_BLOCKED
    raise ContractViolation(f"unmapped state: {view.state!r}")


# Events worth interrupting Michael for. Everything else stays silent: dispatch,
# claiming, polling, a provider returning, and a receipt being written are all
# routine and none of them mean the work is done.
NOTIFIABLE_EVENTS = (
    "job.blocked_human",
    "job.rejected_after_max_reroutes",
    "intent.clarification_required",
    "job.verified",  # only when the submission asked to be told
)


def should_notify(event: str, view: JobView) -> bool:
    if event == "job.verified":
        return view.notify_on_complete
    return event in NOTIFIABLE_EVENTS


def notification_reason(event: str, view: JobView) -> str | None:
    """One line Jarvis can say to Michael, or None to stay silent."""
    if event == "job.blocked_human":
        return f"{view.job_id} needs you: {view.blocker or 'human input required'}"
    if event == "job.rejected_after_max_reroutes":
        return f"{view.job_id} is not converging and has been rerouted too many times"
    if event == "intent.clarification_required":
        return f"{view.job_id} cannot be interpreted safely and needs one clarification"
    if event == "job.verified":
        return f"{view.job_id} is verified complete"
    return None
