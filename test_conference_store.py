"""Persistence acceptance for the canonical store.

Covers the chain the offline rehearsal could only hold in memory:

  create job -> persist -> claim/lease -> worker return -> independent
  verification -> state transition -> restart/reconnect -> state still correct

Every test runs against a real SQL engine (SQLite on disk where restart
matters), not a fake, so the guarantees are enforced by the schema and the
queries rather than by test doubles.
"""

from __future__ import annotations

import os

import pytest

from conference_store import POSTGRES, ConferenceStore, StoreRejection, connect_sqlite
from conference_verification import CheckOutcome, PASSED, Verdict, verify_receipt
from jarvis_contract import (
    STATE_BLOCKED_HUMAN,
    STATE_QUEUED,
    STATE_REJECTED,
    STATE_RETURNED_UNVERIFIED,
    STATE_VERIFIED,
    SURFACE_DONE,
    SURFACE_MICHAEL_ACTION_REQUIRED,
    SURFACE_VERIFYING,
    submission_from_payload,
    surface_status,
)
from reroute_policy import ACTION_DISPATCH, ACTION_REROUTE, ACTION_WAIT

T0 = 1_000.0


class OkChecker:
    def _ok(self, ref):
        return CheckOutcome(PASSED)

    check_pull_request = check_commit = check_file = check_url = check_command_output = _ok


def payload(intent_id="intent-1", **overrides):
    base = {
        "intent_id": intent_id,
        "objective": "Add a health endpoint to Conference",
        "workstream": "conference",
        "priority": "P0",
        "capability_required": ["code", "github_pr"],
        "expected_receipts": ["pull_request"],
    }
    base.update(overrides)
    return base


def receipt_for(job_id, fence_token, *, valid=True, outcome="returned_complete", session_id="devin-abc"):
    return {
        "job_id": job_id,
        "fence_token": fence_token,
        "session_id": session_id,
        "return_valid": valid,
        "outcome": outcome,
        "summary": "Added the health endpoint.",
        "evidence": [{"kind": "pull_request", "ref": "https://github.com/o/r/pull/7"}],
        "tests": [{"name": "pytest", "result": PASSED}],
        "blockers": [],
        "next_action": "review",
        "verification_state": "unverified",
        "session_status": "finished",
    }


# Set CONFERENCE_TEST_DSN to run the same acceptance against a real Postgres
# (a local container, or Neon once a DSN exists). Without it the Postgres
# parameter is skipped rather than silently passing on SQLite alone.
POSTGRES_DSN = os.environ.get("CONFERENCE_TEST_DSN")


def _postgres_store():
    psycopg = pytest.importorskip("psycopg")
    connection = psycopg.connect(POSTGRES_DSN)
    with connection.cursor() as cursor:
        cursor.execute("DROP TABLE IF EXISTS receipts, attempts, jobs")
    connection.commit()
    store = ConferenceStore(connection, POSTGRES)
    store.migrate()
    return store


@pytest.fixture(params=["sqlite", "postgres"])
def store(request):
    if request.param == "postgres":
        if not POSTGRES_DSN:
            pytest.skip("CONFERENCE_TEST_DSN is not set; no live Postgres to verify against")
        return _postgres_store()
    store = ConferenceStore(connect_sqlite())
    store.migrate()
    return store


def test_intent_is_persisted_and_identity_is_minted_by_conference(store):
    job_id = store.submit_intent(submission_from_payload(payload()), now=T0)
    assert job_id.startswith("job-")
    view = store.job_view(job_id)
    assert view.state == STATE_QUEUED
    assert view.objective == "Add a health endpoint to Conference"


def test_resubmitting_the_same_intent_does_not_create_a_second_job(store):
    first = store.submit_intent(submission_from_payload(payload()), now=T0)
    second = store.submit_intent(submission_from_payload(payload()), now=T0 + 5)
    assert first == second


def test_claim_takes_a_lease_and_blocks_a_concurrent_claim(store):
    job_id = store.submit_intent(submission_from_payload(payload()), now=T0)
    claim = store.claim(job_id, "devin", now=T0, lease_seconds=60)
    assert claim.fence_token == f"{job_id}:a0"
    assert store.job_view(job_id).lease_expires_at == T0 + 60

    with pytest.raises(StoreRejection):
        store.claim(job_id, "codex", now=T0 + 1, lease_seconds=60)


def test_expired_lease_releases_the_job_and_mints_a_new_fence(store):
    job_id = store.submit_intent(submission_from_payload(payload()), now=T0)
    first = store.claim(job_id, "devin", now=T0, lease_seconds=60)

    assert store.expire_leases(now=T0 + 61) == [job_id]
    assert store.job_view(job_id).state == STATE_QUEUED

    second = store.claim(job_id, "codex", now=T0 + 62, lease_seconds=60)
    assert second.fence_token != first.fence_token


def test_heartbeat_extends_the_lease_but_only_for_the_current_fence(store):
    job_id = store.submit_intent(submission_from_payload(payload()), now=T0)
    claim = store.claim(job_id, "devin", now=T0, lease_seconds=60)

    assert store.heartbeat(job_id, claim.fence_token, now=T0 + 30, lease_seconds=60) == T0 + 90
    assert store.expire_leases(now=T0 + 61) == []

    with pytest.raises(StoreRejection):
        store.heartbeat(job_id, f"{job_id}:a9", now=T0 + 31)


def test_return_is_stored_unverified_and_never_reaches_done_on_its_own(store):
    job_id = store.submit_intent(submission_from_payload(payload()), now=T0)
    claim = store.claim(job_id, "devin", now=T0)

    assert store.record_return(receipt_for(job_id, claim.fence_token), now=T0 + 10).stored

    view = store.job_view(job_id)
    assert view.state == STATE_RETURNED_UNVERIFIED
    assert view.verification_state == "unverified"
    assert surface_status(view) == SURFACE_VERIFYING


def test_duplicate_return_records_one_receipt(store):
    job_id = store.submit_intent(submission_from_payload(payload()), now=T0)
    claim = store.claim(job_id, "devin", now=T0)

    assert store.record_return(receipt_for(job_id, claim.fence_token), now=T0 + 10).stored is True
    again = store.record_return(receipt_for(job_id, claim.fence_token), now=T0 + 11)
    assert again.stored is False
    assert len(store.receipts(job_id)) == 1


def test_stale_fence_return_is_rejected_after_reroute(store):
    job_id = store.submit_intent(submission_from_payload(payload()), now=T0)
    first = store.claim(job_id, "devin", now=T0, lease_seconds=60)
    store.expire_leases(now=T0 + 61)
    store.claim(job_id, "codex", now=T0 + 62, lease_seconds=60)

    with pytest.raises(StoreRejection):
        store.record_return(receipt_for(job_id, first.fence_token), now=T0 + 63)


def test_verification_is_the_only_path_to_done(store):
    job_id = store.submit_intent(submission_from_payload(payload(), ), now=T0)
    claim = store.claim(job_id, "devin", now=T0)
    receipt = receipt_for(job_id, claim.fence_token)
    store.record_return(receipt, now=T0 + 10)

    verdict = verify_receipt(receipt, OkChecker(), required_evidence_kinds=("pull_request",))
    assert store.apply_verdict(verdict, now=T0 + 20) == STATE_VERIFIED

    view = store.job_view(job_id)
    assert view.verification_state == "verified"
    assert surface_status(view) == SURFACE_DONE


def test_verdict_without_a_stored_valid_return_is_refused(store):
    job_id = store.submit_intent(submission_from_payload(payload()), now=T0)
    claim = store.claim(job_id, "devin", now=T0)

    with pytest.raises(StoreRejection):
        store.apply_verdict(Verdict(job_id, claim.fence_token, "verified"), now=T0 + 20)


def test_rejected_verdict_leaves_the_job_reroutable(store):
    job_id = store.submit_intent(submission_from_payload(payload()), now=T0)
    claim = store.claim(job_id, "devin", now=T0)
    receipt = receipt_for(job_id, claim.fence_token)
    store.record_return(receipt, now=T0 + 10)

    class MissingPR(OkChecker):
        def check_pull_request(self, ref):
            return CheckOutcome("failed", "404 not found")

    verdict = verify_receipt(receipt, MissingPR())
    assert store.apply_verdict(verdict, now=T0 + 20) == STATE_REJECTED

    decision = store.plan(job_id, ["devin", "codex"])
    assert decision.action == ACTION_REROUTE
    assert decision.provider == "codex"


def test_blocked_worker_surfaces_to_michael(store):
    job_id = store.submit_intent(submission_from_payload(payload()), now=T0)
    claim = store.claim(job_id, "devin", now=T0)
    store.record_blocked(job_id, claim.fence_token, "needs a Monday API token", now=T0 + 5)

    view = store.job_view(job_id)
    assert view.state == STATE_BLOCKED_HUMAN
    assert surface_status(view) == SURFACE_MICHAEL_ACTION_REQUIRED
    assert store.plan(job_id, ["devin", "codex"]).action == "escalate_human"


def test_dependency_must_be_verified_before_a_dependent_job_is_claimable(store):
    upstream = store.submit_intent(submission_from_payload(payload("intent-up")), now=T0)
    downstream = store.submit_intent(
        submission_from_payload(payload("intent-down", depends_on=[upstream])), now=T0
    )

    assert store.plan(downstream, ["devin"]).action == ACTION_WAIT
    with pytest.raises(StoreRejection):
        store.claim(downstream, "devin", now=T0)

    claim = store.claim(upstream, "devin", now=T0)
    receipt = receipt_for(upstream, claim.fence_token)
    store.record_return(receipt, now=T0 + 10)
    # A return alone must not unblock the dependent job.
    assert store.plan(downstream, ["devin"]).action == ACTION_WAIT

    store.apply_verdict(verify_receipt(receipt, OkChecker()), now=T0 + 20)
    assert store.plan(downstream, ["devin"]).action == ACTION_DISPATCH
    store.claim(downstream, "devin", now=T0 + 21)


def test_state_survives_restart_and_reconnect(tmp_path):
    db = str(tmp_path / "conference.db")
    store = ConferenceStore(connect_sqlite(db))
    store.migrate()
    job_id = store.submit_intent(submission_from_payload(payload()), now=T0)
    claim = store.claim(job_id, "devin", now=T0, lease_seconds=600)
    receipt = receipt_for(job_id, claim.fence_token)
    store.record_return(receipt, now=T0 + 10)

    # Conference process dies here.
    del store

    reopened = ConferenceStore(connect_sqlite(db))
    view = reopened.job_view(job_id)
    assert view.state == STATE_RETURNED_UNVERIFIED
    assert view.lease_expires_at == T0 + 600
    assert len(reopened.receipts(job_id)) == 1
    # And the duplicate-return guard is still enforced across the restart.
    assert reopened.record_return(receipt, now=T0 + 30).stored is False

    reopened.apply_verdict(verify_receipt(receipt, OkChecker()), now=T0 + 40)

    again = ConferenceStore(connect_sqlite(db))
    assert again.job_view(job_id).state == STATE_VERIFIED
    assert surface_status(again.job_view(job_id)) == SURFACE_DONE


@pytest.mark.skipif(not POSTGRES_DSN, reason="CONFERENCE_TEST_DSN is not set")
def test_state_survives_reconnect_on_postgres():
    import psycopg

    store = _postgres_store()
    job_id = store.submit_intent(submission_from_payload(payload()), now=T0)
    claim = store.claim(job_id, "devin", now=T0, lease_seconds=600)
    receipt = receipt_for(job_id, claim.fence_token)
    store.record_return(receipt, now=T0 + 10)

    reconnected = ConferenceStore(psycopg.connect(POSTGRES_DSN), POSTGRES)
    view = reconnected.job_view(job_id)
    assert view.state == STATE_RETURNED_UNVERIFIED
    assert view.lease_expires_at == T0 + 600
    assert reconnected.record_return(receipt, now=T0 + 30).stored is False

    reconnected.apply_verdict(verify_receipt(receipt, OkChecker()), now=T0 + 40)
    again = ConferenceStore(psycopg.connect(POSTGRES_DSN), POSTGRES)
    assert again.job_view(job_id).state == STATE_VERIFIED


def test_verified_job_cannot_be_reclaimed(store):
    job_id = store.submit_intent(submission_from_payload(payload()), now=T0)
    claim = store.claim(job_id, "devin", now=T0)
    receipt = receipt_for(job_id, claim.fence_token)
    store.record_return(receipt, now=T0 + 10)
    store.apply_verdict(verify_receipt(receipt, OkChecker()), now=T0 + 20)

    with pytest.raises(StoreRejection):
        store.claim(job_id, "codex", now=T0 + 30)
