"""Backend architecture for owned social publishing.

Scope: the backend only. The browser-driven campaign work in Zeely stays with
Codex; nothing here copies or wraps it. What this defines is the permanent path
that eventually replaces dependence on any third-party campaign tool:

    OAuth/delegated authorization -> account + scopes -> media asset
    -> campaign/job -> platform adapter -> queue -> scheduler -> publish
    -> public URL receipt -> analytics/error -> retry/escalation

It reuses Conference's discipline rather than inventing a second one:

  * An adapter reporting success is testimony. A publish is only `published`
    once the platform's own public URL has been independently fetched back —
    `RETURNED != VERIFIED` applies to posts exactly as it does to jobs.
  * Credentials are never held here. An account carries a *reference* to a
    token in the secret store plus its scopes and expiry, so this module can be
    read, logged and tested without touching a secret.
  * Missing scope or an expired token is a refusal to dispatch, never an
    attempt that fails at the platform.
  * Retries are capped and classified; an auth failure escalates to Michael
    immediately instead of burning attempts.

Pure functions and records only: no scheduler process, no queue implementation,
no second job database. When this is wired up, jobs live in the existing
Conference store and the queue is that table plus these decisions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence

# --- platforms and scopes ----------------------------------------------------

INSTAGRAM = "instagram"
TIKTOK = "tiktok"
FACEBOOK = "facebook"
YOUTUBE = "youtube"
X = "x"

PLATFORMS = frozenset({INSTAGRAM, TIKTOK, FACEBOOK, YOUTUBE, X})

# Scope names are platform-specific strings; the engine only cares that the
# scopes a job needs are a subset of the scopes the account actually granted.
SCOPE_PUBLISH = "publish"
SCOPE_READ_INSIGHTS = "read_insights"
SCOPE_MANAGE_COMMENTS = "manage_comments"

# --- job states --------------------------------------------------------------

PUBLISH_DRAFT = "draft"
PUBLISH_SCHEDULED = "scheduled"
PUBLISH_QUEUED = "queued"
PUBLISH_PUBLISHING = "publishing"
PUBLISH_RETURNED_UNVERIFIED = "returned_unverified"
PUBLISH_PUBLISHED = "published"
PUBLISH_FAILED = "failed"
PUBLISH_BLOCKED_HUMAN = "blocked_human"

PUBLISH_STATES = frozenset(
    {
        PUBLISH_DRAFT,
        PUBLISH_SCHEDULED,
        PUBLISH_QUEUED,
        PUBLISH_PUBLISHING,
        PUBLISH_RETURNED_UNVERIFIED,
        PUBLISH_PUBLISHED,
        PUBLISH_FAILED,
        PUBLISH_BLOCKED_HUMAN,
    }
)

# --- error classes an adapter may report -------------------------------------

ERROR_AUTH = "auth"  # token revoked/expired/insufficient scope
ERROR_RATE_LIMIT = "rate_limit"
ERROR_TRANSIENT = "transient"  # 5xx, timeout, connection reset
ERROR_REJECTED = "rejected"  # platform refused the content itself
ERROR_UNKNOWN = "unknown"

ACTION_PUBLISH = "publish"
ACTION_WAIT = "wait"
ACTION_RETRY = "retry"
ACTION_ESCALATE_HUMAN = "escalate_human"
ACTION_GIVE_UP = "give_up"


class SocialEngineError(ValueError):
    pass


@dataclass(frozen=True)
class SocialAccount:
    """A connected account. Holds no token, only a pointer to one."""

    account_id: str
    platform: str
    handle: str
    token_ref: str  # e.g. "secret://bjvi/instagram/blessedjourney"
    granted_scopes: frozenset[str]
    token_expires_at: float | None = None
    connected_by: str = "michael"

    def __post_init__(self) -> None:
        if self.platform not in PLATFORMS:
            raise SocialEngineError(f"unknown platform: {self.platform!r}")
        if self.token_ref.startswith(("ey", "Bearer ")) or len(self.token_ref) > 200:
            raise SocialEngineError("token_ref must be a reference, not a credential")

    def token_valid_at(self, now: float) -> bool:
        return self.token_expires_at is None or self.token_expires_at > now


@dataclass(frozen=True)
class MediaAsset:
    """Content to publish, referenced rather than embedded."""

    asset_id: str
    kind: str  # "image" | "video"
    uri: str
    duration_seconds: float | None = None
    checksum: str | None = None


@dataclass(frozen=True)
class PublishJob:
    job_id: str
    account_id: str
    asset_id: str
    caption: str
    scheduled_for: float
    required_scopes: frozenset[str] = frozenset({SCOPE_PUBLISH})
    campaign_id: str | None = None
    state: str = PUBLISH_DRAFT
    attempts: tuple["PublishAttempt", ...] = ()

    def __post_init__(self) -> None:
        if self.state not in PUBLISH_STATES:
            raise SocialEngineError(f"unknown publish state: {self.state!r}")


@dataclass(frozen=True)
class PublishAttempt:
    attempt_no: int
    started_at: float
    error_class: str | None = None
    detail: str = ""


@dataclass(frozen=True)
class PublishResult:
    """What an adapter returns. Unverified by construction."""

    job_id: str
    external_id: str | None
    public_url: str | None
    error_class: str | None = None
    detail: str = ""
    raw: Mapping[str, Any] = field(default_factory=dict)

    @property
    def claims_success(self) -> bool:
        return self.error_class is None and bool(self.external_id)


@dataclass(frozen=True)
class PublishReceipt:
    job_id: str
    account_id: str
    external_id: str
    public_url: str
    verification_state: str  # "unverified" | "verified" | "rejected"
    detail: str = ""


class PlatformAdapter(Protocol):
    """One per platform. Knows an API; knows nothing about scheduling or state."""

    platform: str

    def publish(self, account: SocialAccount, asset: MediaAsset, caption: str) -> PublishResult: ...

    def fetch_public_post(self, account: SocialAccount, external_id: str) -> PublishResult: ...

    def fetch_metrics(self, account: SocialAccount, external_id: str) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class Decision:
    action: str
    reason: str
    not_before: float | None = None


def scope_gate(job: PublishJob, account: SocialAccount, now: float) -> tuple[bool, str]:
    """Refuse before dispatch when the authorization cannot support the job."""
    if job.account_id != account.account_id:
        return False, "job/account mismatch"
    if not account.token_valid_at(now):
        return False, f"{account.handle}: token expired, reauthorization required"
    missing = sorted(job.required_scopes - account.granted_scopes)
    if missing:
        return False, f"{account.handle}: missing scopes {missing}"
    return True, "authorized"


# Backoff per attempt, in seconds. Rate limits back off harder than transient
# errors because retrying into a rate limit is what gets an account restricted.
_BACKOFF = {
    ERROR_TRANSIENT: (30.0, 120.0, 600.0),
    ERROR_RATE_LIMIT: (300.0, 1800.0, 3600.0),
    ERROR_UNKNOWN: (60.0, 300.0, 900.0),
}


def next_action(
    job: PublishJob,
    account: SocialAccount,
    now: float,
    max_attempts: int = 3,
) -> Decision:
    """What the scheduler should do with this job right now."""
    if job.state in (PUBLISH_PUBLISHED, PUBLISH_BLOCKED_HUMAN):
        return Decision(ACTION_WAIT, f"job is {job.state}")

    authorized, reason = scope_gate(job, account, now)
    if not authorized:
        return Decision(ACTION_ESCALATE_HUMAN, reason)

    if job.scheduled_for > now:
        return Decision(ACTION_WAIT, "not due yet", not_before=job.scheduled_for)

    if not job.attempts:
        return Decision(ACTION_PUBLISH, "due and authorized")

    last = job.attempts[-1]
    if last.error_class is None:
        return Decision(ACTION_WAIT, "attempt returned; verification decides next")
    if last.error_class == ERROR_AUTH:
        return Decision(ACTION_ESCALATE_HUMAN, f"authorization failed: {last.detail}")
    if last.error_class == ERROR_REJECTED:
        return Decision(ACTION_ESCALATE_HUMAN, f"platform rejected the content: {last.detail}")
    if len(job.attempts) >= max_attempts:
        return Decision(ACTION_GIVE_UP, f"exhausted {max_attempts} attempts: {last.error_class}")

    schedule = _BACKOFF.get(last.error_class, _BACKOFF[ERROR_UNKNOWN])
    delay = schedule[min(len(job.attempts) - 1, len(schedule) - 1)]
    retry_at = last.started_at + delay
    if retry_at > now:
        return Decision(ACTION_WAIT, f"backing off after {last.error_class}", not_before=retry_at)
    return Decision(ACTION_RETRY, f"retrying after {last.error_class}")


def verify_publish(
    result: PublishResult,
    account: SocialAccount,
    adapter: PlatformAdapter,
) -> PublishReceipt:
    """Confirm a post exists publicly before anything is called published.

    The adapter's own success response is not enough: the post is fetched back
    and must carry a public URL. A post that cannot be fetched is `rejected`,
    which keeps a silently dropped upload from being reported as shipped.
    """
    if not result.claims_success:
        return PublishReceipt(
            result.job_id,
            account.account_id,
            result.external_id or "",
            result.public_url or "",
            "rejected",
            result.detail or f"adapter reported {result.error_class}",
        )

    external_id = result.external_id or ""
    fetched = adapter.fetch_public_post(account, external_id)
    if fetched.error_class is not None or not fetched.public_url:
        return PublishReceipt(
            result.job_id,
            account.account_id,
            external_id,
            result.public_url or "",
            "rejected",
            fetched.detail or "post could not be fetched back publicly",
        )
    return PublishReceipt(
        result.job_id,
        account.account_id,
        external_id,
        fetched.public_url,
        "verified",
    )


def due_jobs(jobs: Sequence[PublishJob], accounts: Mapping[str, SocialAccount], now: float) -> list[PublishJob]:
    """The queue: jobs the scheduler should act on, in schedule order."""
    actionable = []
    for job in jobs:
        account = accounts.get(job.account_id)
        if account is None:
            continue
        if next_action(job, account, now).action in (ACTION_PUBLISH, ACTION_RETRY):
            actionable.append(job)
    return sorted(actionable, key=lambda j: j.scheduled_for)
