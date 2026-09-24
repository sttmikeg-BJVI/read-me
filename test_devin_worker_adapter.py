import json

from devin_worker_adapter import (
    DevinApiError,
    DevinWorkerAdapter,
    Job,
    WORKER_RETURN_SCHEMA,
    build_receipt,
)


class FakeTransport:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def request(self, method, url, headers, body):
        self.calls.append((method, url, json.loads(body) if body else None, dict(headers)))
        status, payload = self.responses.pop(0)
        return status, json.dumps(payload).encode()


JOB = Job(job_id="job-123", fence_token="fence-1", instructions="Add health endpoint.", repo="o/r")


def test_dispatch_sends_schema_tags_and_idempotency():
    t = FakeTransport([(200, {"session_id": "devin-abc", "url": "https://app.devin.ai/sessions/abc", "is_new_session": True})])
    claim = DevinWorkerAdapter("key", transport=t).dispatch(JOB)

    method, url, payload, headers = t.calls[0]
    assert (method, url) == ("POST", "https://api.devin.ai/v1/sessions")
    assert headers["Authorization"] == "Bearer key"
    assert payload["idempotent"] is True
    assert payload["structured_output_schema"] == WORKER_RETURN_SCHEMA
    assert "job:job-123" in payload["tags"] and "fence:fence-1" in payload["tags"]
    assert "job-123" in payload["prompt"] and "fence-1" in payload["prompt"]
    assert claim.session_id == "devin-abc"


def test_dispatch_once_reuses_live_session_and_does_not_create():
    t = FakeTransport([(200, {"sessions": [{"session_id": "devin-abc", "status_enum": "working"}]})])
    claim = DevinWorkerAdapter("key", transport=t).dispatch_once(JOB)
    assert claim.session_id == "devin-abc"
    assert len(t.calls) == 1 and t.calls[0][0] == "GET"


def test_dispatch_once_creates_when_only_finished_sessions_exist():
    t = FakeTransport([
        (200, {"sessions": [{"session_id": "devin-old", "status_enum": "finished"}]}),
        (200, {"session_id": "devin-new", "url": "u", "is_new_session": True}),
    ])
    assert DevinWorkerAdapter("key", transport=t).dispatch_once(JOB).session_id == "devin-new"


def _poll(session_payload):
    t = FakeTransport([
        (200, {"session_id": "devin-abc", "url": "u", "is_new_session": True}),
        (200, session_payload),
    ])
    adapter = DevinWorkerAdapter("key", transport=t)
    return adapter.poll(adapter.dispatch(JOB))


VALID_OUTPUT = {
    "job_id": "job-123",
    "fence_token": "fence-1",
    "outcome": "returned_complete",
    "summary": "Added endpoint.",
    "evidence": [{"kind": "pull_request", "ref": "https://github.com/o/r/pull/1"}],
    "tests": [{"name": "pytest", "result": "passed"}],
    "blockers": [],
    "next_action": "verify",
}


def test_poll_valid_return_builds_unverified_receipt():
    wr = _poll({
        "status_enum": "finished",
        "structured_output": VALID_OUTPUT,
        "pull_request": {"url": "https://github.com/o/r/pull/1"},
    })
    assert wr.terminal and wr.has_valid_return
    receipt = build_receipt(wr, received_at=1.0)
    assert receipt["verification_state"] == "unverified"
    assert receipt["outcome"] == "returned_complete"
    assert receipt["pull_request_url"].endswith("/pull/1")


def test_stale_fence_return_is_rejected_and_yields_empty_receipt():
    wr = _poll({"status_enum": "finished", "structured_output": dict(VALID_OUTPUT, fence_token="fence-0")})
    assert wr.has_valid_return is False
    receipt = build_receipt(wr, received_at=1.0)
    assert receipt["return_valid"] is False and receipt["outcome"] is None


def test_finished_without_structured_output_is_not_a_valid_return():
    wr = _poll({"status_enum": "finished", "structured_output": None})
    assert wr.terminal and wr.has_valid_return is False


def test_blocked_session_is_not_terminal():
    wr = _poll({"status_enum": "blocked", "structured_output": None})
    assert wr.status == "blocked" and wr.terminal is False


def test_v3_org_endpoint_used_when_org_id_configured():
    t = FakeTransport([(200, {"session_id": "devin-abc", "url": "u", "is_new_session": True})])
    DevinWorkerAdapter("key", transport=t, org_id="org-9").dispatch(JOB)
    assert t.calls[0][1] == "https://api.devin.ai/v3/organizations/org-9/sessions"


def test_lease_expiry_marks_reroute_without_touching_a_live_session():
    t = FakeTransport([
        (200, {"session_id": "devin-abc", "url": "u", "is_new_session": True}),
        (200, {"status_enum": "working", "structured_output": None}),
    ])
    clock = iter([100.0, 400.0])
    adapter = DevinWorkerAdapter("key", transport=t, clock=lambda: next(clock))
    claim = adapter.dispatch(JOB)
    wr = adapter.poll(claim, lease_seconds=60)
    assert wr.lease_expired and wr.needs_reroute and not wr.terminal
    assert build_receipt(wr, received_at=400.0)["verification_state"] == "unverified"


def test_blocked_session_needs_human_not_reroute():
    wr = _poll({"status_enum": "blocked", "structured_output": None})
    assert wr.needs_human and not wr.needs_reroute


def test_expired_session_needs_reroute():
    wr = _poll({"status_enum": "expired", "structured_output": None})
    assert wr.needs_reroute and not wr.needs_human


def test_retries_on_429_then_succeeds():
    t = FakeTransport([
        (429, {"detail": "slow down"}),
        (200, {"session_id": "devin-abc", "url": "u", "is_new_session": True}),
    ])
    slept = []
    adapter = DevinWorkerAdapter("key", transport=t, sleep=slept.append)
    assert adapter.dispatch(JOB).session_id == "devin-abc"
    assert slept == [1.0] and len(t.calls) == 2


def _adapter_with_claim(responses, clock_values, slept):
    t = FakeTransport(
        [(200, {"session_id": "devin-abc", "url": "u", "is_new_session": True})]
        + [(200, r) for r in responses]
    )
    clock = iter(clock_values)
    adapter = DevinWorkerAdapter(
        "key", transport=t, clock=lambda: next(clock), sleep=slept.append
    )
    return adapter, adapter.dispatch(JOB)


def test_wait_for_return_polls_until_terminal():
    slept = []
    adapter, claim = _adapter_with_claim(
        [
            {"status_enum": "working", "structured_output": None},
            {"status_enum": "working", "structured_output": None},
            {"status_enum": "finished", "structured_output": VALID_OUTPUT},
        ],
        [0.0, 0.0, 1.0, 2.0, 3.0],
        slept,
    )
    wr = adapter.wait_for_return(claim, poll_interval=5.0)
    assert wr.terminal and wr.has_valid_return
    assert slept == [5.0, 5.0]


def test_wait_for_return_stops_on_blocked_for_human_input():
    slept = []
    adapter, claim = _adapter_with_claim(
        [{"status_enum": "blocked", "structured_output": None}], [0.0, 0.0, 1.0], slept
    )
    wr = adapter.wait_for_return(claim, poll_interval=5.0)
    assert wr.needs_human and not wr.terminal and slept == []


def test_wait_for_return_timeout_returns_last_state_without_mutating_session():
    slept = []
    adapter, claim = _adapter_with_claim(
        [{"status_enum": "working", "structured_output": None}] * 2,
        [0.0, 0.0, 1.0, 100.0, 200.0],
        slept,
    )
    wr = adapter.wait_for_return(claim, poll_interval=5.0, timeout=30.0)
    assert wr.status == "working" and not wr.terminal and not wr.needs_reroute
    assert all(call[0] == "GET" for call in adapter._transport.calls[1:])


def test_api_error_surfaces():
    class ErrTransport(FakeTransport):
        def request(self, *a, **k):
            return 403, b'{"detail":"Unauthorized"}'

    try:
        DevinWorkerAdapter("bad", transport=ErrTransport([])).dispatch(JOB)
    except DevinApiError as exc:
        assert exc.status == 403
    else:
        raise AssertionError("expected DevinApiError")
