"""Conference -> Devin worker adapter (reference implementation).

Transport-injectable so it can be exercised without network access or a live
API key. All endpoint shapes below come from the official Devin API reference
(docs.devin.ai/api-reference); nothing here invents fields.

Endpoints used:
  POST /v1/sessions                 create a session (dispatch)
  GET  /v1/sessions/{session_id}    status_enum + structured_output (return)
  POST /v1/sessions/{session_id}/message   follow-up instruction

Contract boundaries this module deliberately enforces:
  * A provider return is NEVER marked verified here. Conference verifies.
  * One dispatch per job_id (idempotency + tag-based duplicate detection).
  * A lease/fence token is carried in the session tags so a late return from a
    superseded attempt can be rejected by Conference.
"""

from __future__ import annotations

import json
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol

DEFAULT_BASE_URL = "https://api.devin.ai"

TERMINAL_STATUSES = frozenset({"finished", "expired"})
BLOCKED_STATUSES = frozenset({"blocked"})


class Transport(Protocol):
    def request(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes | None
    ) -> tuple[int, bytes]: ...


class UrllibTransport:
    def request(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes | None
    ) -> tuple[int, bytes]:
        req = urllib.request.Request(url, data=body, method=method)
        for key, value in headers.items():
            req.add_header(key, value)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:  # type: ignore[attr-defined]
            return exc.code, exc.read()


ERROR_AUTH = "auth"
ERROR_RATE_LIMIT = "rate_limit"
ERROR_TRANSIENT = "transient"
ERROR_CLIENT = "client"
ERROR_UNKNOWN = "unknown"


def classify_status(status: int) -> str:
    if status in (401, 403):
        return ERROR_AUTH
    if status == 429:
        return ERROR_RATE_LIMIT
    if status >= 500:
        return ERROR_TRANSIENT
    if status >= 400:
        return ERROR_CLIENT
    return ERROR_UNKNOWN


class DevinApiError(RuntimeError):
    def __init__(self, status: int, payload: str) -> None:
        super().__init__(f"Devin API error {status}: {payload[:500]}")
        self.status = status
        self.payload = payload
        self.kind = classify_status(status)

    @property
    def retryable(self) -> bool:
        """Conference may retry the same attempt."""
        return self.kind in (ERROR_RATE_LIMIT, ERROR_TRANSIENT)

    @property
    def needs_human(self) -> bool:
        """A bad credential is a Michael gate, not something to retry forever."""
        return self.kind == ERROR_AUTH

    @property
    def needs_reroute(self) -> bool:
        """A malformed/rejected request will not succeed by repetition."""
        return self.kind == ERROR_CLIENT


# The schema Devin must fill in. This is the worker-return contract: Conference
# rejects a return that does not validate against it.
WORKER_RETURN_SCHEMA: dict[str, Any] = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "job_id",
        "fence_token",
        "outcome",
        "summary",
        "evidence",
        "tests",
        "blockers",
        "next_action",
    ],
    "properties": {
        "job_id": {"type": "string"},
        "fence_token": {"type": "string"},
        "outcome": {
            "type": "string",
            "enum": ["returned_complete", "returned_partial", "blocked", "refused"],
        },
        "summary": {"type": "string", "maxLength": 2000},
        "evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["kind", "ref"],
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["pull_request", "commit", "file", "command_output", "url"],
                    },
                    "ref": {"type": "string"},
                    "note": {"type": "string"},
                },
            },
        },
        "tests": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "result"],
                "properties": {
                    "name": {"type": "string"},
                    "result": {"type": "string", "enum": ["passed", "failed", "not_run"]},
                    "output_ref": {"type": "string"},
                },
            },
        },
        "blockers": {"type": "array", "items": {"type": "string"}},
        "next_action": {"type": "string"},
    },
}


_ALLOWED_OUTCOMES = frozenset(WORKER_RETURN_SCHEMA["properties"]["outcome"]["enum"])
_ALLOWED_EVIDENCE_KINDS = frozenset(
    WORKER_RETURN_SCHEMA["properties"]["evidence"]["items"]["properties"]["kind"]["enum"]
)
_ALLOWED_TEST_RESULTS = frozenset(
    WORKER_RETURN_SCHEMA["properties"]["tests"]["items"]["properties"]["result"]["enum"]
)


def validate_worker_return(output: Any) -> list[str]:
    """Structural validation of a worker return against the contract.

    Stdlib only, so Conference is not forced to take a jsonschema dependency to
    reject a malformed return. Returns a list of human-readable errors; empty
    means the return is well-formed. Well-formed is NOT verified.
    """
    errors: list[str] = []
    if not isinstance(output, dict):
        return ["structured_output is not an object"]

    for key in WORKER_RETURN_SCHEMA["required"]:
        if key not in output:
            errors.append(f"missing required field: {key}")
    for key in output:
        if key not in WORKER_RETURN_SCHEMA["properties"]:
            errors.append(f"unexpected field: {key}")

    for key in ("job_id", "fence_token", "summary", "next_action"):
        if key in output and not isinstance(output[key], str):
            errors.append(f"{key} must be a string")
    if isinstance(output.get("summary"), str) and len(output["summary"]) > 2000:
        errors.append("summary exceeds 2000 characters")
    if "outcome" in output and output["outcome"] not in _ALLOWED_OUTCOMES:
        errors.append(f"invalid outcome: {output['outcome']!r}")

    evidence = output.get("evidence")
    if "evidence" in output and not isinstance(evidence, list):
        errors.append("evidence must be an array")
    elif isinstance(evidence, list):
        for i, item in enumerate(evidence):
            if not isinstance(item, dict):
                errors.append(f"evidence[{i}] is not an object")
                continue
            if item.get("kind") not in _ALLOWED_EVIDENCE_KINDS:
                errors.append(f"evidence[{i}] invalid kind: {item.get('kind')!r}")
            if not isinstance(item.get("ref"), str) or not item.get("ref"):
                errors.append(f"evidence[{i}] missing ref")

    tests = output.get("tests")
    if "tests" in output and not isinstance(tests, list):
        errors.append("tests must be an array")
    elif isinstance(tests, list):
        for i, item in enumerate(tests):
            if not isinstance(item, dict):
                errors.append(f"tests[{i}] is not an object")
                continue
            if not isinstance(item.get("name"), str):
                errors.append(f"tests[{i}] missing name")
            if item.get("result") not in _ALLOWED_TEST_RESULTS:
                errors.append(f"tests[{i}] invalid result: {item.get('result')!r}")

    blockers = output.get("blockers")
    if "blockers" in output and (
        not isinstance(blockers, list) or any(not isinstance(b, str) for b in blockers)
    ):
        errors.append("blockers must be an array of strings")

    return errors


@dataclass(frozen=True)
class Job:
    job_id: str
    fence_token: str
    instructions: str
    repo: str | None = None
    max_acu_limit: int | None = None
    extra_tags: tuple[str, ...] = ()
    lease_seconds: float | None = None

    def tags(self) -> list[str]:
        return [
            "conference",
            f"job:{self.job_id}",
            f"fence:{self.fence_token}",
            *self.extra_tags,
        ]

    def prompt(self) -> str:
        repo_line = f"Repository: {self.repo}\n" if self.repo else ""
        return (
            "You are executing a Conference job as a worker.\n"
            f"job_id: {self.job_id}\n"
            f"fence_token: {self.fence_token}\n"
            f"{repo_line}"
            "\nWORK:\n"
            f"{self.instructions}\n"
            "\nRETURN CONTRACT:\n"
            "Emit structured output matching the provided schema. Echo job_id and "
            "fence_token exactly. Do not claim work is verified; Conference verifies "
            "independently. Report blockers instead of inventing evidence."
        )


@dataclass(frozen=True)
class Claim:
    job_id: str
    fence_token: str
    session_id: str
    session_url: str
    is_new_session: bool | None
    dispatched_at: float = field(default_factory=time.time)


@dataclass(frozen=True)
class WorkerReturn:
    job_id: str
    fence_token: str
    session_id: str
    status: str | None
    terminal: bool
    structured_output: dict[str, Any] | None
    pull_request_url: str | None
    raw: dict[str, Any]
    lease_expired: bool = False

    @property
    def schema_errors(self) -> list[str]:
        if self.structured_output is None:
            return ["no structured output"]
        return validate_worker_return(self.structured_output)

    @property
    def is_fenced_to_this_attempt(self) -> bool:
        out = self.structured_output
        return bool(
            isinstance(out, dict)
            and out.get("job_id") == self.job_id
            and out.get("fence_token") == self.fence_token
        )

    @property
    def has_valid_return(self) -> bool:
        """Structured output is well-formed and fenced to this job/attempt.

        This is NOT verification. It only says the worker returned something
        well-formed and addressed to this job and this attempt.
        """
        return self.is_fenced_to_this_attempt and not self.schema_errors

    @property
    def needs_reroute(self) -> bool:
        """Conference should fence off this attempt and reassign the job."""
        if self.lease_expired or self.status == "expired":
            return True
        return self.terminal and not self.has_valid_return

    @property
    def needs_human(self) -> bool:
        return self.status in BLOCKED_STATUSES


class DevinWorkerAdapter:
    def __init__(
        self,
        api_key: str,
        transport: Transport | None = None,
        base_url: str = DEFAULT_BASE_URL,
        clock: Callable[[], float] = time.time,
        org_id: str | None = None,
        max_retries: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key:
            raise ValueError("api_key is required")
        self._api_key = api_key
        self._transport = transport or UrllibTransport()
        self._base_url = base_url.rstrip("/")
        self._clock = clock
        self._org_id = org_id
        self._max_retries = max_retries
        self._sleep = sleep

    @property
    def _create_path(self) -> str:
        """v3 org-scoped endpoint when an org_id is configured, else legacy v1."""
        if self._org_id:
            return f"/v3/organizations/{self._org_id}/sessions"
        return "/v1/sessions"

    # --- HTTP -------------------------------------------------------------
    def _call(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = json.dumps(payload).encode() if payload is not None else None
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self._base_url}{path}"
        attempt = 0
        while True:
            status, raw = self._transport.request(method, url, headers, body)
            text = raw.decode("utf-8", "replace")
            if (status == 429 or status >= 500) and attempt < self._max_retries - 1:
                self._sleep(2.0**attempt)
                attempt += 1
                continue
            if status >= 400:
                raise DevinApiError(status, text)
            return json.loads(text) if text else {}

    # --- dispatch / poll --------------------------------------------------
    def dispatch(self, job: Job) -> Claim:
        payload: dict[str, Any] = {
            "prompt": job.prompt(),
            "idempotent": True,
            "title": f"Conference job {job.job_id}",
            "tags": job.tags(),
            "structured_output_schema": WORKER_RETURN_SCHEMA,
        }
        if job.max_acu_limit is not None:
            payload["max_acu_limit"] = job.max_acu_limit
        data = self._call("POST", self._create_path, payload)
        return Claim(
            job_id=job.job_id,
            fence_token=job.fence_token,
            session_id=data["session_id"],
            session_url=data["url"],
            is_new_session=data.get("is_new_session"),
            dispatched_at=self._clock(),
        )

    def find_existing_claim(self, job: Job) -> Claim | None:
        """Duplicate guard: a job tag must map to at most one live session."""
        data = self._call("GET", f"/v1/sessions?tags=job:{job.job_id}")
        sessions = data.get("sessions") or []
        for session in sessions:
            if session.get("status_enum") in TERMINAL_STATUSES:
                continue
            return Claim(
                job_id=job.job_id,
                fence_token=job.fence_token,
                session_id=session["session_id"],
                session_url=f"https://app.devin.ai/sessions/{session['session_id'].removeprefix('devin-')}",
                is_new_session=False,
                dispatched_at=self._clock(),
            )
        return None

    def dispatch_once(self, job: Job) -> Claim:
        return self.find_existing_claim(job) or self.dispatch(job)

    def poll(self, claim: Claim, lease_seconds: float | None = None) -> WorkerReturn:
        data = self._call("GET", f"/v1/sessions/{claim.session_id}")
        status = data.get("status_enum") or data.get("status")
        pr = data.get("pull_request") or {}
        terminal = status in TERMINAL_STATUSES
        lease_expired = bool(
            lease_seconds is not None
            and not terminal
            and self._clock() - claim.dispatched_at > lease_seconds
        )
        return WorkerReturn(
            job_id=claim.job_id,
            fence_token=claim.fence_token,
            session_id=claim.session_id,
            status=status,
            terminal=terminal,
            structured_output=data.get("structured_output"),
            pull_request_url=pr.get("url"),
            raw=data,
            lease_expired=lease_expired,
        )

    def wait_for_return(
        self,
        claim: Claim,
        lease_seconds: float | None = None,
        poll_interval: float = 15.0,
        timeout: float | None = None,
    ) -> WorkerReturn:
        """Poll until the attempt reaches a state Conference must act on.

        Returns as soon as the session is terminal, the lease expires, or the
        worker is blocked on human input. `timeout` is a caller-side guard and
        yields the last observed state; it never mutates the session, so the
        job stays reroutable rather than being silently lost.
        """
        started = self._clock()
        while True:
            worker_return = self.poll(claim, lease_seconds=lease_seconds)
            if worker_return.terminal or worker_return.lease_expired or worker_return.needs_human:
                return worker_return
            if timeout is not None and self._clock() - started >= timeout:
                return worker_return
            self._sleep(poll_interval)

    def nudge(self, claim: Claim, message: str) -> None:
        """Unblock / follow up on a session that is waiting on input."""
        self._call("POST", f"/v1/sessions/{claim.session_id}/message", {"message": message})


def build_receipt(worker_return: WorkerReturn, received_at: float) -> dict[str, Any]:
    """Persistable receipt of a provider return.

    verification_state is always 'unverified' here by design: Conference is the
    only component allowed to move a job to verified/DONE.
    """
    out = worker_return.structured_output or {}
    return {
        "job_id": worker_return.job_id,
        "fence_token": worker_return.fence_token,
        "worker": "devin",
        "session_id": worker_return.session_id,
        "session_status": worker_return.status,
        "received_at": received_at,
        "return_valid": worker_return.has_valid_return,
        "outcome": out.get("outcome") if worker_return.has_valid_return else None,
        "summary": out.get("summary") if worker_return.has_valid_return else None,
        "evidence": out.get("evidence", []) if worker_return.has_valid_return else [],
        "tests": out.get("tests", []) if worker_return.has_valid_return else [],
        "blockers": out.get("blockers", []) if worker_return.has_valid_return else [],
        "next_action": out.get("next_action") if worker_return.has_valid_return else None,
        "pull_request_url": worker_return.pull_request_url,
        "schema_errors": worker_return.schema_errors if not worker_return.has_valid_return else [],
        "lease_expired": worker_return.lease_expired,
        "needs_reroute": worker_return.needs_reroute,
        "verification_state": "unverified",
    }
