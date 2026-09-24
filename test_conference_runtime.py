"""Conference acceptance, end to end, against the persisted store.

Rehearsal, not live: the Devin transport and the evidence checker are fakes,
and every provider still has `credentials_available=False`. What is real here
is the loop, the fencing, the persistence and the verification gate — the first
live run replaces two injected objects and nothing else.
"""

from __future__ import annotations

import json

import pytest

from conference_runtime import CollectingNotifier, ConferenceRuntime
from conference_store import ConferenceStore, connect_sqlite
from conference_verification import CheckOutcome, PASSED
from devin_worker_adapter import DevinWorkerAdapter
from jarvis_contract import (
    STATE_BLOCKED_HUMAN,
    STATE_REJECTED,
    STATE_VERIFIED,
    SURFACE_DONE,
    SURFACE_MICHAEL_ACTION_REQUIRED,
    submission_from_payload,
    surface_status,
)
from worker_provider import CLAUDE, CODEX, DEVIN, ProviderRegistry
from dataclasses import replace

T0 = 5_000.0


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, headers, body):
        self.calls.append((method, url))
        status, payload = self.responses.pop(0)
        return status, json.dumps(payload).encode()


class OkChecker:
    def _ok(self, ref):
        return CheckOutcome(PASSED)

    check_pull_request = check_commit = check_file = check_url = check_command_output = _ok


class MissingPRChecker(OkChecker):
    def check_pull_request(self, ref):
        return CheckOutcome("failed", "404 not found")


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


# dispatch_once looks for a live session on the job tag before creating one,
# so every attempt costs a lookup response as well as a create response.
NO_EXISTING_SESSION = (200, {"sessions": []})


def session_response(session_id="devin-abc"):
    return (200, {"session_id": session_id, "url": f"https://app.devin.ai/sessions/{session_id}",
                  "is_new_session": True})


def attempt_script(poll, session_id="devin-abc"):
    return [NO_EXISTING_SESSION, session_response(session_id), poll]


def finished_response(output):
    return (200, {"status_enum": "finished", "structured_output": output,
                  "pull_request": {"url": "https://github.com/o/r/pull/7"}})


class ScriptedDevin(DevinWorkerAdapter):
    """Devin adapter whose transport answers a pre-written script per attempt.

    Each dispatch consumes one session response and one poll response, so an
    attempt's return can be made to depend on the fence it was dispatched with.
    """

    def __init__(self, script):
        super().__init__("test-key", transport=FakeTransport(script))


@pytest.fixture()
def store():
    store = ConferenceStore(connect_sqlite())
    store.migrate()
    return store


def credentialed_registry(adapter, names=("devin",)):
    descriptors = [
        replace(d, credentials_available=d.name in names) for d in (DEVIN, CODEX, CLAUDE)
    ]
    registry = ProviderRegistry(descriptors)
    for name in names:
        registry.register_adapter(name, adapter)
    return registry


def submit(store, **overrides):
    payload = {
        "intent_id": "intent-1",
        "objective": "Add a health endpoint to Conference",
        "workstream": "conference",
        "priority": "P0",
        "capability_required": ["code", "github_pr"],
        "expected_receipts": ["pull_request"],
    }
    payload.update(overrides)
    return store.submit_intent(submission_from_payload(payload), now=T0, notify_on_complete=True)


def test_intent_to_verified_and_notified(store):
    job_id = submit(store)
    fence = f"{job_id}:a0"
    adapter = ScriptedDevin(attempt_script(finished_response(worker_output(job_id, fence))))
    notifier = CollectingNotifier()
    runtime = ConferenceRuntime(
        store, credentialed_registry(adapter), OkChecker(), notifier, clock=lambda: T0
    )

    result = runtime.run(job_id, required_evidence_kinds=("pull_request",))

    assert result.state == STATE_VERIFIED
    view = store.job_view(job_id)
    assert surface_status(view) == SURFACE_DONE
    assert view.claim_session_url == "https://app.devin.ai/sessions/devin-abc"
    assert len(store.receipts(job_id)) == 1
    assert [event for event, _, _ in notifier.sent] == ["job.verified"]


def test_unverifiable_evidence_reroutes_to_the_other_provider(store):
    job_id = submit(store)
    adapter = ScriptedDevin(
        attempt_script(finished_response(worker_output(job_id, f"{job_id}:a0")), "devin-1")
        + attempt_script(finished_response(worker_output(job_id, f"{job_id}:a1")), "devin-2")
    )
    registry = credentialed_registry(adapter, names=("devin", "codex"))
    runtime = ConferenceRuntime(store, registry, MissingPRChecker(), clock=lambda: T0)

    first = runtime.advance(job_id)
    assert first.state == STATE_REJECTED

    plan = store.plan(job_id, ["devin", "codex"])
    assert plan.action == "reroute" and plan.provider == "codex"

    second = runtime.advance(job_id)
    assert second.state == STATE_REJECTED
    assert store.job_view(job_id).state == STATE_REJECTED
    # Both attempts are retained; neither was cleaned up to look tidy.
    assert len(store.receipts(job_id)) == 2


def test_attempts_are_capped_and_escalate_to_michael(store):
    job_id = submit(store)
    script = []
    for attempt in range(3):
        script += attempt_script(
            finished_response(worker_output(job_id, f"{job_id}:a{attempt}")),
            f"devin-{attempt}",
        )
    runtime = ConferenceRuntime(
        store,
        credentialed_registry(ScriptedDevin(script), names=("devin", "codex")),
        MissingPRChecker(),
        CollectingNotifier(),
        clock=lambda: T0,
    )

    result = runtime.run(job_id)

    assert result.state == STATE_BLOCKED_HUMAN
    assert surface_status(store.job_view(job_id)) == SURFACE_MICHAEL_ACTION_REQUIRED
    assert len(store.attempts(job_id)) == 3


def test_blocked_worker_stops_the_loop_and_reaches_michael(store):
    job_id = submit(store)
    adapter = ScriptedDevin(
        attempt_script((200, {"status_enum": "blocked", "structured_output": None}))
    )
    notifier = CollectingNotifier()
    runtime = ConferenceRuntime(
        store, credentialed_registry(adapter), OkChecker(), notifier, clock=lambda: T0
    )

    result = runtime.run(job_id)

    assert result.state == STATE_BLOCKED_HUMAN
    assert [event for event, _, _ in notifier.sent] == ["job.blocked_human"]
    assert store.job_view(job_id).blocker


def test_no_credentialed_provider_escalates_instead_of_dispatching(store):
    job_id = submit(store)
    registry = ProviderRegistry()  # every declared provider has no credentials
    runtime = ConferenceRuntime(store, registry, OkChecker(), CollectingNotifier(), clock=lambda: T0)

    result = runtime.run(job_id)

    assert result.state == STATE_BLOCKED_HUMAN
    assert "no eligible provider" in store.job_view(job_id).blocker
    assert store.attempts(job_id) == []


def test_loop_resumes_correctly_after_a_restart(tmp_path):
    db = str(tmp_path / "conference.db")
    store = ConferenceStore(connect_sqlite(db))
    store.migrate()
    job_id = submit(store)
    fence = f"{job_id}:a0"
    adapter = ScriptedDevin(attempt_script(finished_response(worker_output(job_id, fence))))
    ConferenceRuntime(store, credentialed_registry(adapter), OkChecker(), clock=lambda: T0).run(job_id)

    # Restart: a fresh store and a fresh runtime see the job as already done
    # and do not dispatch it a second time.
    reopened = ConferenceStore(connect_sqlite(db))
    idle = ScriptedDevin([])
    result = ConferenceRuntime(
        reopened, credentialed_registry(idle), OkChecker(), clock=lambda: T0 + 100
    ).run(job_id)

    assert result.state == STATE_VERIFIED
    assert idle._transport.calls == []
