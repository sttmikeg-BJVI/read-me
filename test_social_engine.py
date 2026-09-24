"""Backend acceptance for the social publishing path.

No network, no platform credentials: these pin the decisions and the
verification rule, which are the parts that must not drift once real adapters
are written.
"""

from __future__ import annotations

import pytest

from social_engine import (
    ACTION_ESCALATE_HUMAN,
    ACTION_GIVE_UP,
    ACTION_PUBLISH,
    ACTION_RETRY,
    ACTION_WAIT,
    ERROR_AUTH,
    ERROR_RATE_LIMIT,
    ERROR_REJECTED,
    ERROR_TRANSIENT,
    INSTAGRAM,
    PUBLISH_PUBLISHED,
    SCOPE_PUBLISH,
    SCOPE_READ_INSIGHTS,
    MediaAsset,
    PublishAttempt,
    PublishJob,
    PublishResult,
    SocialAccount,
    SocialEngineError,
    due_jobs,
    next_action,
    scope_gate,
    verify_publish,
)

T0 = 10_000.0
ASSET = MediaAsset("asset-1", "video", "s3://bjvi/clip.mp4", duration_seconds=21.0)


def account(**overrides):
    base = dict(
        account_id="acct-1",
        platform=INSTAGRAM,
        handle="@blessedjourney",
        token_ref="secret://bjvi/instagram/blessedjourney",
        granted_scopes=frozenset({SCOPE_PUBLISH, SCOPE_READ_INSIGHTS}),
        token_expires_at=T0 + 86_400,
    )
    base.update(overrides)
    return SocialAccount(**base)


def job(**overrides):
    base = dict(
        job_id="pub-1",
        account_id="acct-1",
        asset_id=ASSET.asset_id,
        caption="New route this week.",
        scheduled_for=T0,
    )
    base.update(overrides)
    return PublishJob(**base)


class FakeAdapter:
    platform = INSTAGRAM

    def __init__(self, fetched=None):
        self._fetched = fetched

    def publish(self, account, asset, caption):
        return PublishResult("pub-1", "ig-123", None)

    def fetch_public_post(self, account, external_id):
        if self._fetched is None:
            return PublishResult("pub-1", external_id, None, "unknown", "404 not found")
        return self._fetched

    def fetch_metrics(self, account, external_id):
        return {}


def test_a_token_value_may_not_be_stored_in_place_of_a_reference():
    with pytest.raises(SocialEngineError):
        account(token_ref="eyJhbGciOiJIUzI1NiJ9.fake.token")


def test_missing_scope_is_refused_before_dispatch():
    restricted = account(granted_scopes=frozenset({SCOPE_READ_INSIGHTS}))
    ok, reason = scope_gate(job(), restricted, T0)
    assert ok is False and "missing scopes" in reason
    assert next_action(job(), restricted, T0).action == ACTION_ESCALATE_HUMAN


def test_expired_token_escalates_instead_of_attempting_a_publish():
    expired = account(token_expires_at=T0 - 1)
    assert next_action(job(), expired, T0).action == ACTION_ESCALATE_HUMAN


def test_a_scheduled_job_waits_until_it_is_due():
    decision = next_action(job(scheduled_for=T0 + 600), account(), T0)
    assert decision.action == ACTION_WAIT
    assert decision.not_before == T0 + 600


def test_a_due_authorized_job_publishes():
    assert next_action(job(), account(), T0).action == ACTION_PUBLISH


def test_transient_failure_backs_off_then_retries():
    attempted = job(attempts=(PublishAttempt(0, T0, ERROR_TRANSIENT, "502"),))
    assert next_action(attempted, account(), T0 + 5).action == ACTION_WAIT
    assert next_action(attempted, account(), T0 + 31).action == ACTION_RETRY


def test_rate_limit_backs_off_harder_than_a_transient_error():
    limited = job(attempts=(PublishAttempt(0, T0, ERROR_RATE_LIMIT, "429"),))
    assert next_action(limited, account(), T0 + 31).action == ACTION_WAIT
    assert next_action(limited, account(), T0 + 301).action == ACTION_RETRY


def test_auth_and_content_rejection_go_straight_to_michael():
    auth = job(attempts=(PublishAttempt(0, T0, ERROR_AUTH, "token revoked"),))
    rejected = job(attempts=(PublishAttempt(0, T0, ERROR_REJECTED, "music rights"),))
    assert next_action(auth, account(), T0 + 10_000).action == ACTION_ESCALATE_HUMAN
    assert next_action(rejected, account(), T0 + 10_000).action == ACTION_ESCALATE_HUMAN


def test_attempts_are_capped():
    burnt = job(
        attempts=tuple(PublishAttempt(i, T0 + i, ERROR_TRANSIENT, "502") for i in range(3))
    )
    assert next_action(burnt, account(), T0 + 10_000).action == ACTION_GIVE_UP


def test_adapter_success_alone_is_not_published():
    result = PublishResult("pub-1", "ig-123", "https://instagram.com/p/abc")
    receipt = verify_publish(result, account(), FakeAdapter(fetched=None))
    assert receipt.verification_state == "rejected"


def test_publish_is_verified_only_when_the_post_is_fetched_back():
    live = PublishResult("pub-1", "ig-123", "https://instagram.com/p/abc")
    receipt = verify_publish(live, account(), FakeAdapter(fetched=live))
    assert receipt.verification_state == "verified"
    assert receipt.public_url == "https://instagram.com/p/abc"


def test_queue_returns_only_actionable_jobs_in_schedule_order():
    accounts = {"acct-1": account()}
    later = job(job_id="pub-2", scheduled_for=T0 + 10)
    published = job(job_id="pub-3", scheduled_for=T0 - 10, state=PUBLISH_PUBLISHED)
    assert [j.job_id for j in due_jobs([later, published, job()], accounts, T0 + 20)] == [
        "pub-1",
        "pub-2",
    ]
