from conference_verification import (
    Check,
    CheckOutcome,
    FAILED,
    NEEDS_HUMAN,
    NOT_RUN,
    PASSED,
    REJECTED,
    VERIFIED,
    verify_receipt,
)


class Checker:
    """Injected evidence checker. Every ref maps to a canned outcome."""

    def __init__(self, outcomes=None, default=CheckOutcome(PASSED)):
        self.outcomes = outcomes or {}
        self.default = default
        self.seen = []

    def _get(self, ref):
        self.seen.append(ref)
        return self.outcomes.get(ref, self.default)

    check_pull_request = _get
    check_commit = _get
    check_file = _get
    check_url = _get
    check_command_output = _get


def receipt(**overrides):
    base = {
        "job_id": "job-123",
        "fence_token": "fence-1",
        "return_valid": True,
        "outcome": "returned_complete",
        "evidence": [{"kind": "pull_request", "ref": "https://github.com/o/r/pull/7"}],
        "tests": [{"name": "pytest", "result": PASSED}],
        "verification_state": "unverified",
    }
    base.update(overrides)
    return base


def test_verified_when_every_cited_evidence_checks_out():
    v = verify_receipt(receipt(), Checker())
    assert v.verification_state == VERIFIED and v.reasons == ()


def test_unverifiable_pull_request_rejects_even_though_worker_claimed_complete():
    checker = Checker({"https://github.com/o/r/pull/7": CheckOutcome(FAILED, "404")})
    v = verify_receipt(receipt(), checker)
    assert v.verification_state == REJECTED
    assert any("did not verify" in r for r in v.reasons)


def test_completion_with_zero_evidence_is_rejected():
    v = verify_receipt(receipt(evidence=[]), Checker())
    assert v.verification_state == REJECTED
    assert "completion claimed with zero evidence" in v.reasons


def test_missing_required_evidence_kind_is_rejected():
    r = receipt(evidence=[{"kind": "url", "ref": "https://example.com"}])
    v = verify_receipt(r, Checker(), required_evidence_kinds=("pull_request",))
    assert v.verification_state == REJECTED
    assert "required evidence missing: pull_request" in v.reasons


def test_stale_or_invalid_return_is_rejected_without_checking_evidence():
    checker = Checker()
    v = verify_receipt(receipt(return_valid=False), checker)
    assert v.verification_state == REJECTED and checker.seen == []


def test_blocked_worker_needs_human_not_rejection():
    v = verify_receipt(receipt(outcome="blocked"), Checker())
    assert v.verification_state == NEEDS_HUMAN
    assert v.checks[-1].result == NOT_RUN


def test_partial_and_refused_outcomes_are_rejected():
    for outcome in ("returned_partial", "refused"):
        v = verify_receipt(receipt(outcome=outcome), Checker())
        assert v.verification_state == REJECTED


def test_worker_reported_failing_test_blocks_verification():
    r = receipt(tests=[{"name": "pytest", "result": FAILED}])
    v = verify_receipt(r, Checker())
    assert v.verification_state == REJECTED


def test_independent_rerun_overrides_worker_testimony():
    def rerun(_tests):
        return [Check("pytest", FAILED, "2 failed on rerun")]

    v = verify_receipt(receipt(), Checker(), rerun_tests=rerun)
    assert v.verification_state == REJECTED
    assert any("independent test rerun" in r for r in v.reasons)


def test_unknown_evidence_kind_is_rejected():
    r = receipt(evidence=[{"kind": "vibes", "ref": "trust me"}])
    v = verify_receipt(r, Checker())
    assert v.verification_state == REJECTED
    assert "unknown evidence kind: vibes" in v.reasons


def test_adapter_receipt_flows_into_verification_offline():
    """dispatch -> poll -> build_receipt -> verify_receipt, end to end, no network."""
    import json

    from devin_worker_adapter import DevinWorkerAdapter, Job, build_receipt

    class FakeTransport:
        def __init__(self, responses):
            self.responses = responses

        def request(self, method, url, headers, body):
            status, payload = self.responses.pop(0)
            return status, json.dumps(payload).encode()

    job = Job(job_id="job-123", fence_token="fence-1", instructions="do it")
    output = {
        "job_id": "job-123",
        "fence_token": "fence-1",
        "outcome": "returned_complete",
        "summary": "done",
        "evidence": [{"kind": "pull_request", "ref": "https://github.com/o/r/pull/7"}],
        "tests": [{"name": "pytest", "result": PASSED}],
        "blockers": [],
        "next_action": "review",
    }
    transport = FakeTransport([
        (200, {"session_id": "devin-abc", "url": "u", "is_new_session": True}),
        (200, {"status_enum": "finished", "structured_output": output}),
    ])
    adapter = DevinWorkerAdapter("key", transport=transport)
    claim = adapter.dispatch(job)
    worker_return = adapter.poll(claim)
    r = build_receipt(worker_return, received_at=1.0)

    assert r["verification_state"] == "unverified"
    verdict = verify_receipt(r, Checker(), required_evidence_kinds=("pull_request",))
    assert verdict.verification_state == VERIFIED
    assert r["verification_state"] == "unverified"


def test_verdict_is_serializable_and_never_mutates_the_receipt():
    r = receipt()
    snapshot = dict(r)
    v = verify_receipt(r, Checker())
    assert r == snapshot
    assert v.as_dict()["verification_state"] == VERIFIED
