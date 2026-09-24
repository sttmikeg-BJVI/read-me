"""Offline rehearsal of the full chain.

MICHAEL -> JARVIS -> CONFERENCE -> WORKER -> RETURN -> RECEIPT -> VERIFICATION
-> CANONICAL STATE -> NOTIFICATION

This is a REHEARSAL, not live acceptance: the Devin transport and the evidence
checker are fakes. Its value is that the first live run replays exactly this
script with the fakes swapped for a real API key and a real GitHub checker, so
any divergence is in the transport, not in the logic.
"""

import json

from conference_verification import PASSED, VERIFIED, CheckOutcome, verify_receipt
from devin_worker_adapter import DevinWorkerAdapter, Job, build_receipt
from jarvis_contract import (
    SURFACE_DONE,
    SURFACE_MICHAEL_ACTION_REQUIRED,
    SURFACE_VERIFYING,
    JobView,
    should_notify,
    submission_from_payload,
    surface_status,
)
from receipt_ledger import ReceiptLedger
from reroute_policy import ACTION_REROUTE, Attempt, decide
from worker_provider import provider_neutral_job


class FakeTransport:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def request(self, method, url, headers, body):
        self.calls.append((method, url))
        status, payload = self.responses.pop(0)
        return status, json.dumps(payload).encode()


class OkChecker:
    def _ok(self, ref):
        return CheckOutcome(PASSED)

    check_pull_request = check_commit = check_file = check_url = check_command_output = _ok


def worker_output(job_id, fence_token, **overrides):
    base = {
        "job_id": job_id,
        "fence_token": fence_token,
        "outcome": "returned_complete",
        "summary": "Added the health endpoint.",
        "evidence": [{"kind": "pull_request", "ref": "https://github.com/o/r/pull/7"}],
        "tests": [{"name": "pytest", "result": PASSED}],
        "blockers": [],
        "next_action": "review",
    }
    base.update(overrides)
    return base


def test_happy_path_michael_to_notification():
    # MICHAEL -> JARVIS: an intent, with no canonical identity attached.
    submission = submission_from_payload(
        {
            "intent_id": "intent-1",
            "objective": "Add a health endpoint to Conference",
            "workstream": "conference",
            "priority": "P0",
            "capability_required": ["code", "github_pr"],
            "expected_receipts": ["pull_request", "tests"],
        }
    )

    # CONFERENCE mints identity. Jarvis never does.
    job_id, fence_token = "job-1", "job-1:a0"
    neutral = provider_neutral_job(submission.__dict__, job_id, fence_token)
    assert neutral["job_id"] == job_id

    # CONFERENCE -> WORKER
    output = worker_output(job_id, fence_token)
    transport = FakeTransport([
        (200, {"session_id": "devin-abc", "url": "https://app.devin.ai/sessions/abc", "is_new_session": True}),
        (200, {"status_enum": "finished", "structured_output": output,
               "pull_request": {"url": "https://github.com/o/r/pull/7"}}),
    ])
    adapter = DevinWorkerAdapter("key", transport=transport)
    claim = adapter.dispatch(Job(job_id=job_id, fence_token=fence_token, instructions=submission.objective))
    worker_return = adapter.wait_for_return(claim, poll_interval=0.0)

    # RETURN -> RECEIPT (always unverified at this point)
    receipt = build_receipt(worker_return, received_at=100.0)
    assert receipt["verification_state"] == "unverified"

    ledger = ReceiptLedger()
    assert ledger.record(receipt).stored is True
    # Re-observing the same return (polling overlap) must not duplicate it.
    assert ledger.record(build_receipt(worker_return, received_at=101.0)).stored is False
    assert len(ledger) == 1

    # Michael sees VERIFYING, never DONE, purely because a worker returned.
    view = JobView(job_id=job_id, state="returned_unverified", objective=submission.objective,
                   workstream="conference", priority="P0", receipts=(receipt,))
    assert surface_status(view) == SURFACE_VERIFYING
    assert should_notify("receipt.written", view) is False

    # VERIFICATION -> CANONICAL STATE
    verdict = verify_receipt(receipt, OkChecker(), required_evidence_kinds=("pull_request",))
    assert verdict.verification_state == VERIFIED

    verified_view = JobView(job_id=job_id, state="verified", objective=submission.objective,
                            workstream="conference", priority="P0", receipts=(receipt,),
                            verification_state="verified", notify_on_complete=True)
    assert surface_status(verified_view) == SURFACE_DONE
    assert should_notify("job.verified", verified_view) is True
    # The receipt itself is untouched by verification.
    assert receipt["verification_state"] == "unverified"


def test_unverifiable_evidence_does_not_reach_done_and_reroutes():
    job_id, fence_token = "job-2", "job-2:a0"
    output = worker_output(job_id, fence_token)
    transport = FakeTransport([
        (200, {"session_id": "devin-abc", "url": "u", "is_new_session": True}),
        (200, {"status_enum": "finished", "structured_output": output}),
    ])
    adapter = DevinWorkerAdapter("key", transport=transport)
    claim = adapter.dispatch(Job(job_id=job_id, fence_token=fence_token, instructions="x"))
    receipt = build_receipt(adapter.poll(claim), received_at=1.0)

    class MissingPR(OkChecker):
        def check_pull_request(self, ref):
            return CheckOutcome("failed", "404 not found")

    verdict = verify_receipt(receipt, MissingPR())
    assert verdict.verification_state == "rejected"

    # Conference reroutes with a NEW fence, so a late return from the old
    # attempt can no longer be accepted.
    decision = decide({"job_id": job_id}, [Attempt("devin", fence_token, "no_valid_return")],
                      ["devin", "codex"])
    assert decision.action == ACTION_REROUTE
    assert decision.next_fence_token != fence_token

    stale = worker_output(job_id, fence_token)  # old attempt reports in late
    transport2 = FakeTransport([
        (200, {"session_id": "devin-new", "url": "u", "is_new_session": True}),
        (200, {"status_enum": "finished", "structured_output": stale}),
    ])
    adapter2 = DevinWorkerAdapter("key", transport=transport2)
    claim2 = adapter2.dispatch(
        Job(job_id=job_id, fence_token=decision.next_fence_token, instructions="x")
    )
    late = adapter2.poll(claim2)
    assert late.has_valid_return is False
    assert verify_receipt(build_receipt(late, received_at=2.0), OkChecker()).verification_state == "rejected"


def test_blocked_worker_reaches_michael_and_nothing_else():
    job_id, fence_token = "job-3", "job-3:a0"
    transport = FakeTransport([
        (200, {"session_id": "devin-abc", "url": "u", "is_new_session": True}),
        (200, {"status_enum": "blocked", "structured_output": None}),
    ])
    adapter = DevinWorkerAdapter("key", transport=transport)
    claim = adapter.dispatch(Job(job_id=job_id, fence_token=fence_token, instructions="x"))
    worker_return = adapter.wait_for_return(claim, poll_interval=0.0)
    assert worker_return.needs_human and not worker_return.needs_reroute

    decision = decide({"job_id": job_id}, [Attempt("devin", fence_token, "blocked")], ["devin", "codex"])
    assert decision.action == "escalate_human"

    view = JobView(job_id=job_id, state="blocked_human", objective="x", workstream="conference",
                   priority="P0", blocker="worker needs a credential")
    assert surface_status(view) == SURFACE_MICHAEL_ACTION_REQUIRED
    assert should_notify("job.blocked_human", view) is True
