"""The provider boundary Conference depends on.

Conference must not import Devin specifics. It depends on this protocol plus a
capability declaration, so Codex, Claude, or any future worker can be added
without touching routing, fencing, receipts, or verification.

`DevinWorkerAdapter` already satisfies `WorkerProvider` structurally; no changes
to it were needed. A conformance check lives in the tests so a future provider
cannot silently drift from the contract.

This module declares capabilities and eligibility only. It does NOT decide
worker assignment policy — the existing Conference routing module owns that, and
this is the input it consumes. Nothing here is a second scheduler.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

# Capability vocabulary. Extend deliberately: an unknown capability must fail
# eligibility rather than being silently ignored.
CAP_CODE = "code"
CAP_GITHUB_PR = "github_pr"
CAP_LONG_RUNNING = "long_running"
CAP_BROWSER = "browser"
CAP_SHELL = "shell"
CAP_RESEARCH = "research"
CAP_STRUCTURED_RETURN = "structured_return"

KNOWN_CAPABILITIES = frozenset(
    {
        CAP_CODE,
        CAP_GITHUB_PR,
        CAP_LONG_RUNNING,
        CAP_BROWSER,
        CAP_SHELL,
        CAP_RESEARCH,
        CAP_STRUCTURED_RETURN,
    }
)


@runtime_checkable
class WorkerProvider(Protocol):
    """Minimum surface a worker provider must expose to Conference.

    Deliberately small. A provider may not expose anything that lets it mark its
    own work verified, and it may not write canonical state.
    """

    def dispatch_once(self, job: Any) -> Any:
        """Claim the job at most once. Must be safe to call repeatedly."""

    def wait_for_return(
        self,
        claim: Any,
        lease_seconds: float | None = ...,
        poll_interval: float = ...,
        timeout: float | None = ...,
    ) -> Any:
        """Block until the attempt is terminal, lease-expired, or human-blocked."""

    def nudge(self, claim: Any, message: str) -> None:
        """Follow up on an attempt that is waiting on input."""


FORBIDDEN_PROVIDER_METHODS = ("verify", "mark_done", "set_state", "write_canonical_state")


@dataclass(frozen=True)
class ProviderDescriptor:
    """What Conference knows about a provider without importing it.

    `return_mode` records how the provider's result actually reaches Conference.
    Devin is 'poll' — EXTERNALLY-VERIFIED: Devin exposes no outbound per-session
    completion webhook, so anything claiming a Devin callback is false. A
    provider may only be registered as 'callback' once that callback has been
    observed working, never on documentation alone.
    """

    name: str
    capabilities: frozenset[str]
    return_mode: str  # "poll" | "callback"
    credentials_available: bool
    live_verified: bool = False
    notes: str = ""

    def __post_init__(self) -> None:
        unknown = self.capabilities - KNOWN_CAPABILITIES
        if unknown:
            raise ValueError(f"unknown capabilities: {sorted(unknown)}")
        if self.return_mode not in ("poll", "callback"):
            raise ValueError(f"unknown return_mode: {self.return_mode!r}")

    def can_satisfy(self, required: Sequence[str]) -> bool:
        return set(required).issubset(self.capabilities)

    def eligible_for(self, required: Sequence[str]) -> tuple[bool, str]:
        """Eligibility with a reason, so Conference can report why not."""
        unknown = set(required) - KNOWN_CAPABILITIES
        if unknown:
            return False, f"unknown capability requested: {sorted(unknown)}"
        if not self.credentials_available:
            return False, f"{self.name} has no credentials provisioned"
        if not self.can_satisfy(required):
            missing = sorted(set(required) - self.capabilities)
            return False, f"{self.name} lacks: {missing}"
        return True, "eligible"


# Declared providers. `live_verified=False` everywhere is the honest current
# state: no provider in this project has completed a live dispatch->return cycle
# yet. Flipping any of these to True requires a real observed run.
DEVIN = ProviderDescriptor(
    name="devin",
    capabilities=frozenset(
        {CAP_CODE, CAP_GITHUB_PR, CAP_SHELL, CAP_BROWSER, CAP_LONG_RUNNING, CAP_STRUCTURED_RETURN}
    ),
    return_mode="poll",
    credentials_available=False,
    live_verified=False,
    notes="No outbound completion webhook exists; Conference must poll.",
)

CODEX = ProviderDescriptor(
    name="codex",
    capabilities=frozenset({CAP_CODE, CAP_SHELL}),
    return_mode="poll",
    credentials_available=False,
    live_verified=False,
    notes="Transport not inspected by Devin; capabilities listed are UNVERIFIED.",
)

CLAUDE = ProviderDescriptor(
    name="claude",
    capabilities=frozenset({CAP_CODE, CAP_RESEARCH, CAP_STRUCTURED_RETURN}),
    return_mode="poll",
    credentials_available=False,
    live_verified=False,
    notes="Transport not inspected by Devin; capabilities listed are UNVERIFIED.",
)


def devin_relay_descriptor(spool_ready: bool) -> ProviderDescriptor:
    """Devin reached through the filesystem spool instead of the HTTP API.

    Same executor as `DEVIN`, different transport: the relay carries the
    dispatch envelope to a real Devin session and copies the worker's return
    back. Eligible only when a spool directory is actually present, so a
    missing spool blocks dispatch rather than silently faking one.
    """
    return ProviderDescriptor(
        name="devin_relay",
        capabilities=frozenset(
            {CAP_CODE, CAP_GITHUB_PR, CAP_SHELL, CAP_LONG_RUNNING, CAP_STRUCTURED_RETURN}
        ),
        return_mode="poll",
        credentials_available=spool_ready,
        live_verified=False,
        notes="Transport is a file spool relayed by an authorized operator; not a webhook.",
    )


class ProviderRegistry:
    """Lookup of declared providers. Holds no jobs and no state."""

    def __init__(self, descriptors: Sequence[ProviderDescriptor] = (DEVIN, CODEX, CLAUDE)) -> None:
        self._by_name: dict[str, ProviderDescriptor] = {d.name: d for d in descriptors}
        self._adapters: dict[str, WorkerProvider] = {}

    def register_adapter(self, name: str, adapter: WorkerProvider) -> None:
        if name not in self._by_name:
            raise KeyError(f"undeclared provider: {name}")
        if not isinstance(adapter, WorkerProvider):
            raise TypeError(f"{name} adapter does not satisfy WorkerProvider")
        for forbidden in FORBIDDEN_PROVIDER_METHODS:
            if hasattr(adapter, forbidden):
                raise TypeError(
                    f"{name} adapter exposes {forbidden!r}; providers may not decide completion"
                )
        self._adapters[name] = adapter

    def adapter(self, name: str) -> WorkerProvider:
        return self._adapters[name]

    def descriptor(self, name: str) -> ProviderDescriptor:
        return self._by_name[name]

    def eligible(self, required: Sequence[str]) -> list[ProviderDescriptor]:
        """Providers that could take this work, ordered by declaration order.

        Ordering here is not a scheduling decision: Conference's existing
        routing module chooses among eligible providers.
        """
        return [d for d in self._by_name.values() if d.eligible_for(required)[0]]

    def explain(self, required: Sequence[str]) -> dict[str, str]:
        return {name: d.eligible_for(required)[1] for name, d in self._by_name.items()}


def provider_neutral_job(submission: Mapping[str, Any], job_id: str, fence_token: str) -> dict[str, Any]:
    """Canonical job payload every provider adapter receives.

    Conference mints job_id and fence_token; the provider only ever sees them,
    never invents them.
    """
    return {
        "job_id": job_id,
        "fence_token": fence_token,
        "objective": submission.get("objective"),
        "workstream": submission.get("workstream"),
        "capability_required": list(submission.get("capability_required", ())),
        "expected_receipts": list(submission.get("expected_receipts", ())),
        "lease_seconds": submission.get("lease_seconds"),
    }
