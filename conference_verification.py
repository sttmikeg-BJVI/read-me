"""Independent verification of a worker receipt.

Conference — not the worker, not the adapter — decides whether a job is done.
This module takes a receipt produced by `build_receipt` and turns it into a
verdict by independently checking the evidence the worker cited.

Design rules it enforces:
  * A provider saying `returned_complete` is testimony, not proof.
  * Absent or unverifiable evidence is a rejection, never a pass.
  * A check that cannot be performed is `not_run`, and `not_run` never counts
    as success.
  * Verification is read-only. It never mutates the job, the session, or the
    receipt; it returns a verdict that Conference persists alongside them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol, Sequence

VERIFIED = "verified"
REJECTED = "rejected"
NEEDS_HUMAN = "needs_human"

PASSED = "passed"
FAILED = "failed"
NOT_RUN = "not_run"


class EvidenceChecker(Protocol):
    """Reads the outside world to confirm one piece of cited evidence.

    Implementations are injected so verification is testable without network
    access and so Conference can swap in its own GitHub/CI clients.
    """

    def check_pull_request(self, ref: str) -> "CheckOutcome": ...

    def check_commit(self, ref: str) -> "CheckOutcome": ...

    def check_file(self, ref: str) -> "CheckOutcome": ...

    def check_url(self, ref: str) -> "CheckOutcome": ...

    def check_command_output(self, ref: str) -> "CheckOutcome": ...


@dataclass(frozen=True)
class CheckOutcome:
    result: str
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.result == PASSED


@dataclass(frozen=True)
class Check:
    name: str
    result: str
    detail: str = ""
    evidence_ref: str | None = None


@dataclass(frozen=True)
class Verdict:
    job_id: str
    fence_token: str
    verification_state: str
    checks: tuple[Check, ...] = ()
    reasons: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "fence_token": self.fence_token,
            "verification_state": self.verification_state,
            "checks": [
                {
                    "name": c.name,
                    "result": c.result,
                    "detail": c.detail,
                    "evidence_ref": c.evidence_ref,
                }
                for c in self.checks
            ],
            "reasons": list(self.reasons),
        }


@dataclass
class _Accumulator:
    checks: list[Check] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    def add(self, check: Check, reason_if_bad: str | None = None) -> None:
        self.checks.append(check)
        if check.result != PASSED and reason_if_bad:
            self.reasons.append(reason_if_bad)


_CHECKER_BY_KIND: Mapping[str, str] = {
    "pull_request": "check_pull_request",
    "commit": "check_commit",
    "file": "check_file",
    "url": "check_url",
    "command_output": "check_command_output",
}


def verify_receipt(
    receipt: Mapping[str, Any],
    checker: EvidenceChecker,
    required_evidence_kinds: Sequence[str] = (),
    rerun_tests: Callable[[Sequence[Mapping[str, Any]]], Sequence[Check]] | None = None,
) -> Verdict:
    """Independently verify one receipt.

    `required_evidence_kinds` lets Conference demand, per job type, that certain
    evidence exists at all — e.g. a code job must cite a pull_request. Missing
    required evidence is a rejection even if the worker claims completion.
    """
    acc = _Accumulator()
    job_id = receipt.get("job_id", "")
    fence_token = receipt.get("fence_token", "")

    if not receipt.get("return_valid"):
        acc.add(
            Check("return_fenced", FAILED, "receipt is not a valid fenced return"),
            "no valid fenced return to verify",
        )
        return Verdict(job_id, fence_token, REJECTED, tuple(acc.checks), tuple(acc.reasons))
    acc.add(Check("return_fenced", PASSED))

    outcome = receipt.get("outcome")
    if outcome == "blocked":
        acc.add(Check("worker_outcome", NOT_RUN, "worker reported blocked"))
        return Verdict(job_id, fence_token, NEEDS_HUMAN, tuple(acc.checks),
                       ("worker is blocked and needs human input",))
    if outcome in {"refused", "returned_partial"}:
        acc.add(
            Check("worker_outcome", FAILED, f"worker outcome: {outcome}"),
            f"worker did not complete the job (outcome={outcome})",
        )
        return Verdict(job_id, fence_token, REJECTED, tuple(acc.checks), tuple(acc.reasons))
    acc.add(Check("worker_outcome", PASSED, str(outcome)))

    evidence = receipt.get("evidence") or []
    present_kinds = {e.get("kind") for e in evidence}
    for kind in required_evidence_kinds:
        if kind in present_kinds:
            acc.add(Check(f"evidence_required:{kind}", PASSED))
        else:
            acc.add(
                Check(f"evidence_required:{kind}", FAILED, "no evidence of this kind cited"),
                f"required evidence missing: {kind}",
            )

    if not evidence:
        acc.add(
            Check("evidence_present", FAILED, "worker cited no evidence"),
            "completion claimed with zero evidence",
        )

    for item in evidence:
        kind = item.get("kind")
        ref = item.get("ref") or ""
        method = _CHECKER_BY_KIND.get(kind or "")
        if method is None:
            acc.add(
                Check(f"evidence:{kind}", FAILED, "unknown evidence kind", ref),
                f"unknown evidence kind: {kind}",
            )
            continue
        outcome_ = getattr(checker, method)(ref)
        acc.add(
            Check(f"evidence:{kind}", outcome_.result, outcome_.detail, ref),
            f"evidence {kind} {ref} did not verify: {outcome_.detail}",
        )

    reported_tests = receipt.get("tests") or []
    if any(t.get("result") == FAILED for t in reported_tests):
        acc.add(
            Check("reported_tests", FAILED, "worker reported a failing test"),
            "worker reported a failing test but claimed completion",
        )
    if rerun_tests is not None:
        for check in rerun_tests(reported_tests):
            acc.add(check, f"independent test rerun did not pass: {check.name}")

    state = VERIFIED if all(c.result == PASSED for c in acc.checks) else REJECTED
    return Verdict(job_id, fence_token, state, tuple(acc.checks), tuple(acc.reasons))
