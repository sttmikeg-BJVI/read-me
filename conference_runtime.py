"""The Conference loop: the one place the existing pieces are wired together.

Until now the chain only existed inside a test. This runs it against the
canonical store:

    plan -> claim (fence) -> dispatch -> return -> receipt -> verify
    -> state -> reroute / escalate -> notification

It adds no policy of its own. Routing comes from `reroute_policy` through
`ConferenceStore.plan`, eligibility from `worker_provider.ProviderRegistry`,
verdicts from `conference_verification`, notification eligibility from
`jarvis_contract.should_notify`, and every state change goes through the store.
This is the orchestrator those modules were written for, not a second one.

Transports are injected. With the fake transport it is a rehearsal; with a real
adapter and credentials the same code is the live path, which is the point.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol, Sequence

from conference_store import ConferenceStore, StoreRejection
from conference_verification import EvidenceChecker, verify_receipt
from devin_worker_adapter import Job, build_receipt
from jarvis_contract import (
    STATE_BLOCKED_HUMAN,
    STATE_REJECTED,
    STATE_VERIFIED,
    JobView,
    notification_reason,
    should_notify,
)
from reroute_policy import (
    ACTION_DISPATCH,
    ACTION_ESCALATE_HUMAN,
    ACTION_GIVE_UP,
    ACTION_REROUTE,
    ACTION_WAIT,
)
from worker_provider import ProviderRegistry


class Notifier(Protocol):
    """Delivers a notification somewhere Michael actually looks."""

    def notify(self, event: str, view: JobView, message: str) -> None: ...


class CollectingNotifier:
    """Default notifier: records rather than delivers.

    Deliberate — no Slack or Monday delivery is wired up in this project, and a
    notifier that silently dropped events would make a job look attended to.
    """

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []

    def notify(self, event: str, view: JobView, message: str) -> None:
        self.sent.append((event, view.job_id, message))


@dataclass(frozen=True)
class CycleResult:
    job_id: str
    action: str
    state: str
    detail: str


class ConferenceRuntime:
    def __init__(
        self,
        store: ConferenceStore,
        registry: ProviderRegistry,
        checker: EvidenceChecker,
        notifier: Notifier | None = None,
        clock: Callable[[], float] = None,  # type: ignore[assignment]
        lease_seconds: float = 900.0,
    ) -> None:
        import time

        self.store = store
        self.registry = registry
        self.checker = checker
        self.notifier: Notifier = notifier or CollectingNotifier()
        self.clock = clock or time.time
        self.lease_seconds = lease_seconds

    # --- one job, one step ------------------------------------------------

    def advance(self, job_id: str, required_evidence_kinds: Sequence[str] = ()) -> CycleResult:
        """Move a job forward by exactly one step, or explain why it cannot."""
        now = self.clock()
        self.store.expire_leases(now)
        row = self.store.job_row(job_id)

        if row["state"] in (STATE_VERIFIED, STATE_BLOCKED_HUMAN):
            return CycleResult(job_id, ACTION_WAIT, row["state"], "terminal for this loop")

        eligible = [d.name for d in self.registry.eligible(_capabilities(row))]
        decision = self.store.plan(job_id, eligible)

        if decision.action in (ACTION_ESCALATE_HUMAN, ACTION_GIVE_UP):
            blocker = decision.reason
            if row["state"] != STATE_BLOCKED_HUMAN:
                self.store.block(job_id, blocker, now)
            event = (
                "job.rejected_after_max_reroutes"
                if decision.action == ACTION_GIVE_UP
                else "job.blocked_human"
            )
            self._maybe_notify(event, job_id)
            return CycleResult(job_id, decision.action, STATE_BLOCKED_HUMAN, blocker)

        if decision.action == ACTION_WAIT:
            return CycleResult(job_id, ACTION_WAIT, row["state"], decision.reason)

        if decision.action in (ACTION_DISPATCH, ACTION_REROUTE):
            return self._run_attempt(job_id, decision.provider or eligible[0], required_evidence_kinds)

        return CycleResult(job_id, decision.action, row["state"], decision.reason)

    def run(
        self,
        job_id: str,
        required_evidence_kinds: Sequence[str] = (),
        max_steps: int = 6,
    ) -> CycleResult:
        """Advance until the job is terminal or nothing more can be done."""
        result = CycleResult(job_id, ACTION_WAIT, self.store.job_row(job_id)["state"], "not started")
        for _ in range(max_steps):
            result = self.advance(job_id, required_evidence_kinds)
            if result.state in (STATE_VERIFIED, STATE_BLOCKED_HUMAN) or result.action == ACTION_WAIT:
                return result
        return result

    # --- internals --------------------------------------------------------

    def _run_attempt(
        self, job_id: str, provider_name: str, required_evidence_kinds: Sequence[str]
    ) -> CycleResult:
        now = self.clock()
        row = self.store.job_row(job_id)
        claim = self.store.claim(job_id, provider_name, now, self.lease_seconds)
        adapter = self.registry.adapter(provider_name)

        provider_claim = adapter.dispatch_once(
            Job(job_id=job_id, fence_token=claim.fence_token, instructions=row["objective"])
        )
        session_id = getattr(provider_claim, "session_id", None)
        session_url = getattr(provider_claim, "session_url", None)
        if session_id:
            self.store.attach_session(job_id, claim.fence_token, session_id, session_url or "")

        worker_return = adapter.wait_for_return(provider_claim, poll_interval=0.0)
        received_at = self.clock()

        if getattr(worker_return, "needs_human", False):
            blocker = getattr(worker_return, "blocker", None) or "worker is blocked on human input"
            self.store.record_blocked(job_id, claim.fence_token, blocker, received_at)
            self._maybe_notify("job.blocked_human", job_id)
            return CycleResult(job_id, ACTION_ESCALATE_HUMAN, STATE_BLOCKED_HUMAN, blocker)

        receipt = build_receipt(worker_return, received_at=received_at)
        try:
            record = self.store.record_return(receipt, received_at)
        except StoreRejection as exc:
            # A return that arrived against a superseded fence. Nothing to
            # verify, and the attempt stays failed rather than being believed.
            return CycleResult(job_id, ACTION_REROUTE, row["state"], str(exc))

        if not record.stored:
            return CycleResult(job_id, ACTION_WAIT, row["state"], record.reason)
        if not receipt.get("return_valid"):
            return CycleResult(job_id, ACTION_REROUTE, self.store.job_row(job_id)["state"],
                               "no valid fenced return")

        verdict = verify_receipt(receipt, self.checker, required_evidence_kinds)
        state = self.store.apply_verdict(verdict, self.clock())
        if state == STATE_VERIFIED:
            self._maybe_notify("job.verified", job_id)
            return CycleResult(job_id, ACTION_WAIT, state, "verified")
        if state == STATE_BLOCKED_HUMAN:
            self._maybe_notify("job.blocked_human", job_id)
            return CycleResult(job_id, ACTION_ESCALATE_HUMAN, state, "; ".join(verdict.reasons))
        return CycleResult(job_id, ACTION_REROUTE, STATE_REJECTED, "; ".join(verdict.reasons))

    def _maybe_notify(self, event: str, job_id: str) -> None:
        view = self.store.job_view(job_id)
        if not should_notify(event, view):
            return
        message = notification_reason(event, view)
        if message:
            self.notifier.notify(event, view, message)


def _capabilities(row: dict[str, Any]) -> list[str]:
    import json

    return list(json.loads(row["capability_required"]))
