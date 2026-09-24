"""Reroute / fallback and dependency-gating policy.

Pure decision functions. They hold no state, store no jobs, and dispatch
nothing — the existing Conference routing module calls them and remains the
component that actually moves work. Keeping the decisions pure is what makes
them testable offline and safe to map onto the recovered source.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

ACTION_DISPATCH = "dispatch"
ACTION_WAIT = "wait"
ACTION_REROUTE = "reroute"
ACTION_ESCALATE_HUMAN = "escalate_human"
ACTION_GIVE_UP = "give_up"


@dataclass(frozen=True)
class Decision:
    action: str
    reason: str
    provider: str | None = None
    next_fence_token: str | None = None
    attempt: int = 0


@dataclass(frozen=True)
class Attempt:
    provider: str
    fence_token: str
    outcome: str  # "returned_valid" | "no_valid_return" | "lease_expired" | "blocked" | "provider_error"


def dependency_gate(job: Mapping[str, Any], job_states: Mapping[str, str]) -> tuple[bool, tuple[str, ...]]:
    """A job may not be dispatched until every dependency is verified.

    Deliberately strict: a dependency that merely *returned* is not satisfied,
    because a provider return is not completion.
    """
    unmet = tuple(
        dep for dep in job.get("depends_on", ()) if job_states.get(dep) != "verified"
    )
    return (not unmet), unmet


def next_fence_token(job_id: str, attempt: int) -> str:
    """Every reroute mints a new fence, which is what makes a late return from
    the previous attempt harmless."""
    return f"{job_id}:a{attempt}"


def decide(
    job: Mapping[str, Any],
    attempts: Sequence[Attempt],
    eligible_providers: Sequence[str],
    job_states: Mapping[str, str] = {},
    max_attempts: int = 3,
) -> Decision:
    """What Conference should do next with this job.

    Rules:
      * unmet dependencies block dispatch entirely;
      * a valid return stops the loop — verification decides the rest, not this;
      * blocked means a human is needed, never a silent reroute;
      * a provider that already failed is not retried while another is eligible;
      * attempts are capped, and exhausting them escalates rather than looping.
    """
    ok, unmet = dependency_gate(job, job_states)
    if not ok:
        return Decision(ACTION_WAIT, f"unmet dependencies: {list(unmet)}")

    if not eligible_providers:
        return Decision(ACTION_ESCALATE_HUMAN, "no eligible provider (capabilities or credentials)")

    if attempts and attempts[-1].outcome == "returned_valid":
        return Decision(ACTION_WAIT, "valid return received; verification decides next")
    if attempts and attempts[-1].outcome == "blocked":
        return Decision(
            ACTION_ESCALATE_HUMAN, "worker is blocked on human input", provider=attempts[-1].provider
        )

    attempt_number = len(attempts)
    if attempt_number >= max_attempts:
        return Decision(
            ACTION_GIVE_UP,
            f"exhausted {max_attempts} attempts without a valid return",
            attempt=attempt_number,
        )

    failed_providers = {a.provider for a in attempts}
    unused = [p for p in eligible_providers if p not in failed_providers]
    provider = unused[0] if unused else eligible_providers[0]
    action = ACTION_DISPATCH if not attempts else ACTION_REROUTE
    reason = (
        "first dispatch"
        if not attempts
        else f"previous attempt {attempts[-1].outcome}; "
        + ("falling back to another provider" if unused else "retrying same provider, no alternative")
    )
    return Decision(
        action,
        reason,
        provider=provider,
        next_fence_token=next_fence_token(str(job.get("job_id", "job")), attempt_number),
        attempt=attempt_number,
    )
