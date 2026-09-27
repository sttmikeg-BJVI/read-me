from __future__ import annotations

import json

import pytest

from devin_worker_adapter import Job
from spool_relay_provider import SpoolRelayProvider, write_return
from worker_provider import (
    FORBIDDEN_PROVIDER_METHODS,
    WorkerProvider,
    devin_relay_descriptor,
)


def job(fence: str = "fence-1") -> Job:
    return Job(job_id="J1", fence_token=fence, instructions="do the thing")


def good_output(fence: str = "fence-1") -> dict:
    return {
        "job_id": "J1",
        "fence_token": fence,
        "outcome": "returned_complete",
        "summary": "done",
        "evidence": [{"kind": "url", "ref": "https://example.com"}],
        "tests": [{"name": "suite", "result": "passed"}],
        "blockers": [],
        "next_action": "none",
    }


def test_satisfies_provider_contract(tmp_path):
    provider = SpoolRelayProvider(root=tmp_path)
    assert isinstance(provider, WorkerProvider)
    for forbidden in FORBIDDEN_PROVIDER_METHODS:
        assert not hasattr(provider, forbidden)


def test_dispatch_writes_fenced_envelope_once(tmp_path):
    provider = SpoolRelayProvider(root=tmp_path)
    claim = provider.dispatch_once(job())
    path = provider.dispatch_path("J1", "fence-1")
    envelope = json.loads(path.read_text())

    assert envelope["fence_token"] == "fence-1"
    assert "fence:fence-1" in envelope["tags"]
    assert claim.job_id == "J1"

    first_write = path.stat().st_mtime_ns
    provider.dispatch_once(job())
    assert path.stat().st_mtime_ns == first_write


def test_pending_return_is_not_terminal(tmp_path):
    provider = SpoolRelayProvider(root=tmp_path)
    claim = provider.dispatch_once(job())
    pending = provider.poll(claim)
    assert not pending.terminal
    assert not pending.has_valid_return


def test_lease_expiry_without_return(tmp_path):
    ticks = iter([100.0, 100.0, 1000.0])
    provider = SpoolRelayProvider(root=tmp_path, clock=lambda: next(ticks))
    claim = provider.dispatch_once(job())
    expired = provider.poll(claim, lease_seconds=60.0)
    assert expired.lease_expired
    assert expired.needs_reroute


def test_valid_relayed_return_is_fenced_but_unverified(tmp_path):
    provider = SpoolRelayProvider(root=tmp_path)
    claim = provider.dispatch_once(job())
    write_return(tmp_path, "J1", "fence-1", good_output(), session_id="devin-abc")

    result = provider.wait_for_return(claim, poll_interval=0.0)
    assert result.terminal
    assert result.has_valid_return
    assert result.session_id == "devin-abc"
    assert result.structured_output["outcome"] == "returned_complete"


def test_return_from_superseded_fence_is_not_seen(tmp_path):
    provider = SpoolRelayProvider(root=tmp_path)
    claim = provider.dispatch_once(job("fence-2"))
    write_return(tmp_path, "J1", "fence-1", good_output("fence-1"))

    stale = provider.poll(claim)
    assert not stale.terminal
    assert not stale.has_valid_return


def test_return_echoing_wrong_fence_is_invalid(tmp_path):
    provider = SpoolRelayProvider(root=tmp_path)
    claim = provider.dispatch_once(job("fence-2"))
    write_return(tmp_path, "J1", "fence-2", good_output("fence-1"))

    result = provider.poll(claim)
    assert result.terminal
    assert not result.has_valid_return
    assert result.needs_reroute


def test_malformed_return_is_invalid_not_crashing(tmp_path):
    provider = SpoolRelayProvider(root=tmp_path)
    claim = provider.dispatch_once(job())
    bad = good_output()
    del bad["evidence"]
    write_return(tmp_path, "J1", "fence-1", bad)

    result = provider.poll(claim)
    assert not result.has_valid_return
    assert "missing required field: evidence" in result.schema_errors


def test_blocked_return_escalates(tmp_path):
    provider = SpoolRelayProvider(root=tmp_path)
    claim = provider.dispatch_once(job())
    write_return(tmp_path, "J1", "fence-1", good_output(), status_enum="blocked")

    result = provider.wait_for_return(claim, poll_interval=0.0)
    assert result.needs_human


def test_acknowledgement_attaches_session_without_completing(tmp_path):
    provider = SpoolRelayProvider(root=tmp_path)
    provider.dispatch_once(job())
    provider.acknowledge("J1", "fence-1", "devin-live", "https://app.devin.ai/sessions/live")

    claim = provider.dispatch_once(job())
    assert claim.session_id == "devin-live"
    assert provider.poll(claim).terminal is False


def test_nudge_is_recorded_for_the_relay(tmp_path):
    provider = SpoolRelayProvider(root=tmp_path)
    claim = provider.dispatch_once(job())
    provider.nudge(claim, "status?")
    path = tmp_path / "dispatch" / "J1.fence-1.nudges.jsonl"
    assert json.loads(path.read_text().strip())["message"] == "status?"


def test_timeout_returns_last_state_without_mutating(tmp_path):
    ticks = iter([0.0, 0.0, 0.0, 1.0, 2.0, 99.0])
    provider = SpoolRelayProvider(root=tmp_path, clock=lambda: next(ticks), sleep=lambda _: None)
    claim = provider.dispatch_once(job())
    result = provider.wait_for_return(claim, poll_interval=0.0, timeout=0.5)
    assert not result.terminal
    assert not result.lease_expired


def test_descriptor_ineligible_until_spool_exists():
    blocked = devin_relay_descriptor(spool_ready=False)
    ok, reason = blocked.eligible_for(["code"])
    assert not ok and "credentials" in reason
    assert devin_relay_descriptor(spool_ready=True).eligible_for(["code"])[0]


def test_corrupt_return_file_is_ignored(tmp_path):
    provider = SpoolRelayProvider(root=tmp_path)
    claim = provider.dispatch_once(job())
    provider.return_path("J1", "fence-1").write_text("{not json")
    assert not provider.poll(claim).terminal


@pytest.mark.parametrize("outcome", ["returned_partial", "refused"])
def test_non_complete_outcomes_stay_unverified(tmp_path, outcome):
    provider = SpoolRelayProvider(root=tmp_path)
    claim = provider.dispatch_once(job())
    output = good_output()
    output["outcome"] = outcome
    write_return(tmp_path, "J1", "fence-1", output)
    result = provider.poll(claim)
    assert result.has_valid_return
    assert result.structured_output["outcome"] == outcome


def test_runtime_records_the_session_the_worker_returned_with(tmp_path):
    """The relay learns the session id after dispatch, so the canonical
    attempt has to pick it up from the return rather than staying blank."""
    from conference_runtime import ConferenceRuntime
    from conference_store import ConferenceStore, connect_sqlite
    from conference_verification import PASSED, CheckOutcome
    from jarvis_contract import STATE_VERIFIED, submission_from_payload
    from worker_provider import ProviderRegistry

    class OkChecker:
        def _ok(self, ref):
            return CheckOutcome(PASSED)

        check_pull_request = check_commit = check_file = check_url = check_command_output = _ok

    store = ConferenceStore(connect_sqlite())
    store.migrate()
    job_id = store.submit_intent(
        submission_from_payload(
            {
                "intent_id": "intent-relay",
                "objective": "push a receipt commit",
                "workstream": "conference",
                "priority": "P0",
                "capability_required": ["code"],
                "expected_receipts": ["commit"],
            }
        ),
        now=1000.0,
        notify_on_complete=False,
    )
    fence = f"{job_id}:a0"

    provider = SpoolRelayProvider(root=tmp_path)
    registry = ProviderRegistry([devin_relay_descriptor(spool_ready=True)])
    registry.register_adapter("devin_relay", provider)

    output = good_output(fence)
    output["job_id"] = job_id
    output["evidence"] = [{"kind": "commit", "ref": "a" * 40}]
    write_return(
        tmp_path,
        job_id,
        fence,
        output,
        session_id="devin-real",
        session_url="https://app.devin.ai/sessions/real",
    )

    runtime = ConferenceRuntime(store, registry, OkChecker(), clock=lambda: 1000.0)
    result = runtime.run(job_id, required_evidence_kinds=("commit",))

    assert result.state == STATE_VERIFIED
    view = store.job_view(job_id)
    assert view.claim_session_url == "https://app.devin.ai/sessions/real"
