"""Jarvis acceptance: the assistant surface, and the limits on it.

The point of most of these is what Jarvis *cannot* do. It cannot mint a job
id, cannot verify, cannot mark anything done, and cannot report DONE for work
that a worker merely returned.
"""

from __future__ import annotations

import pytest

from conference_runtime import ConferenceRuntime
from conference_store import ConferenceStore, connect_sqlite
from jarvis import Jarvis, brief, parse_instruction
from jarvis_contract import (
    CONFERENCE_OWNED_FIELDS,
    STATE_RETURNED_UNVERIFIED,
    SURFACE_DONE,
    SURFACE_MICHAEL_ACTION_REQUIRED,
    ContractViolation,
    submission_from_payload,
    surface_status,
)
from test_conference_runtime import (
    OkChecker,
    ScriptedDevin,
    attempt_script,
    credentialed_registry,
    finished_response,
    worker_output,
)

T0 = 7_000.0


@pytest.fixture()
def store():
    store = ConferenceStore(connect_sqlite())
    store.migrate()
    return store


@pytest.fixture()
def jarvis(store):
    return Jarvis(store, clock=lambda: T0)


def test_instruction_becomes_a_submission_with_capabilities_and_priority():
    submission = parse_instruction(
        "P0 - fix the billing bug and open a pull request", workstream="platform"
    )
    assert submission.priority == "P0"
    assert set(submission.capability_required) == {"code", "github_pr"}
    assert submission.expected_receipts == ("pull_request",)
    assert submission.michael_only_gate is False


def test_repeating_the_same_instruction_does_not_create_a_second_job(jarvis, store):
    first = jarvis.instruct("Fix the billing bug", workstream="platform")
    second = jarvis.instruct("fix   the billing   bug", workstream="platform")
    assert first.job_id == second.job_id
    assert len(store.jobs()) == 1


def test_instructions_touching_credentials_are_held_for_michael(jarvis):
    reply = jarvis.instruct("Rotate the Monday API key", workstream="ops")
    assert reply.job_id is not None
    assert surface_status(reply.views[0]) == SURFACE_MICHAEL_ACTION_REQUIRED
    assert "authorization" in reply.text


def test_jarvis_may_not_set_conference_owned_fields():
    for field in sorted(CONFERENCE_OWNED_FIELDS):
        with pytest.raises(ContractViolation):
            submission_from_payload(
                {
                    "intent_id": "i-1",
                    "objective": "do the thing",
                    "workstream": "ops",
                    field: "forged",
                }
            )


def test_a_returned_job_reads_as_verifying_not_done(store, jarvis):
    job_id = jarvis.instruct("Add a health endpoint and open a pull request").job_id
    claim = store.claim(job_id, "devin", T0, session_id="devin-1")
    store.record_return(
        {
            "job_id": job_id,
            "fence_token": claim.fence_token,
            "session_id": "devin-1",
            "return_valid": True,
            "outcome": "returned_complete",
            "summary": "done",
            "evidence": [{"kind": "pull_request", "ref": "https://github.com/o/r/pull/7"}],
            "tests": [],
            "received_at": T0 + 10,
        },
        T0 + 10,
    )
    assert store.job_row(job_id)["state"] == STATE_RETURNED_UNVERIFIED
    assert "[VERIFYING]" in jarvis.status(job_id).text
    assert "[DONE]" not in jarvis.status(job_id).text


def test_jarvis_reports_done_only_after_conference_verifies(store):
    jarvis = Jarvis(store, clock=lambda: T0)
    job_id = jarvis.instruct("Add a health endpoint and open a pull request").job_id
    fence = f"{job_id}:a0"
    runtime = ConferenceRuntime(
        store,
        credentialed_registry(
            ScriptedDevin(attempt_script(finished_response(worker_output(job_id, fence))))
        ),
        OkChecker(),
        clock=lambda: T0,
    )
    jarvis.runtime = runtime
    runtime.run(job_id, ("pull_request",))
    assert f"[{SURFACE_DONE}]" in jarvis.status(job_id).text


def test_attention_lists_only_genuine_michael_gates(store, jarvis):
    jarvis.instruct("Research competitor pricing", workstream="growth")
    gated = jarvis.instruct("Approve the vendor payment", workstream="ops").job_id
    assert [v.job_id for v in jarvis.attention().views] == [gated]


def test_escalation_can_only_ever_ask_for_a_human(store, jarvis):
    job_id = jarvis.instruct("Add a health endpoint").job_id
    jarvis.escalate(job_id, "I need Michael's call on scope")
    view = store.job_view(job_id)
    assert surface_status(view) == SURFACE_MICHAEL_ACTION_REQUIRED
    assert view.verification_state != "verified"


def test_follow_up_nudges_the_worker_without_changing_state(store):
    job_id = Jarvis(store, clock=lambda: T0).instruct("Add a health endpoint").job_id
    store.claim(job_id, "devin", T0, session_id="devin-1")
    adapter = ScriptedDevin([(200, {"ok": True})])
    runtime = ConferenceRuntime(
        store, credentialed_registry(adapter), OkChecker(), clock=lambda: T0
    )
    jarvis = Jarvis(store, runtime, clock=lambda: T0)

    before = store.job_row(job_id)["state"]
    reply = jarvis.follow_up(job_id, "Any progress?")

    assert "devin" in reply.text
    assert store.job_row(job_id)["state"] == before
    assert store.job_row(job_id)["verification_state"] != "verified"
    assert adapter._transport.calls[0][1].endswith("/v1/sessions/devin-1/message")


def test_recall_finds_prior_work_and_its_receipts(store):
    jarvis = Jarvis(store, clock=lambda: T0)
    job_id = jarvis.instruct("Add a health endpoint and open a pull request").job_id
    fence = f"{job_id}:a0"
    runtime = ConferenceRuntime(
        store,
        credentialed_registry(
            ScriptedDevin(attempt_script(finished_response(worker_output(job_id, fence))))
        ),
        OkChecker(),
        clock=lambda: T0,
    )
    runtime.run(job_id, ("pull_request",))

    reply = jarvis.recall("health endpoint")
    assert job_id in reply.text
    assert "https://github.com/o/r/pull/7" in reply.text
    assert jarvis.recall("something never asked for").views == ()


def test_brief_groups_work_by_what_michael_would_do(store, jarvis):
    jarvis.instruct("Research competitor pricing", workstream="growth")
    jarvis.instruct("Approve the vendor payment", workstream="ops")
    summary = brief([store.job_view(row["job_id"]) for row in store.jobs()])
    assert summary.splitlines()[0].startswith(SURFACE_MICHAEL_ACTION_REQUIRED)
