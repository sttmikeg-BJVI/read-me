"""Environment-driven configuration. No credential is ever hard-coded here."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

WORKER_SECRET_PREFIX = "BJVI_WORKER_SECRET_"

DEFAULT_STALE_THRESHOLD_SECONDS = 1800
DEFAULT_SIGNATURE_TOLERANCE_SECONDS = 300


def _worker_key(worker: str) -> str:
    return WORKER_SECRET_PREFIX + worker.strip().upper().replace("-", "_")


@dataclass
class Settings:
    database_path: str = "bjvi_handoff.db"
    worker_secrets: dict[str, str] = field(default_factory=dict)
    operator_token: str | None = None
    monday_api_token: str | None = None
    monday_api_url: str = "https://api.monday.com/v2"
    monday_status_column_id: str = "status"
    slack_bot_token: str | None = None
    slack_webhook_url: str | None = None
    slack_channel: str = "#claude-handoff"
    stale_threshold_seconds: int = DEFAULT_STALE_THRESHOLD_SECONDS
    signature_tolerance_seconds: int = DEFAULT_SIGNATURE_TOLERANCE_SECONDS

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> "Settings":
        env = dict(environ if environ is not None else os.environ)
        secrets = {
            key[len(WORKER_SECRET_PREFIX) :].lower().replace("_", "-"): value
            for key, value in env.items()
            if key.startswith(WORKER_SECRET_PREFIX) and value
        }
        return cls(
            database_path=env.get("BJVI_DATABASE_PATH", "bjvi_handoff.db"),
            worker_secrets=secrets,
            operator_token=env.get("BJVI_OPERATOR_TOKEN") or None,
            monday_api_token=env.get("MONDAY_API_TOKEN") or None,
            monday_api_url=env.get("MONDAY_API_URL", "https://api.monday.com/v2"),
            monday_status_column_id=env.get("MONDAY_STATUS_COLUMN_ID", "status"),
            slack_bot_token=env.get("SLACK_BOT_TOKEN") or None,
            slack_webhook_url=env.get("SLACK_WEBHOOK_URL") or None,
            slack_channel=env.get("SLACK_CHANNEL", "#claude-handoff"),
            stale_threshold_seconds=int(
                env.get("BJVI_STALE_THRESHOLD_SECONDS", DEFAULT_STALE_THRESHOLD_SECONDS)
            ),
            signature_tolerance_seconds=int(
                env.get("BJVI_SIGNATURE_TOLERANCE_SECONDS", DEFAULT_SIGNATURE_TOLERANCE_SECONDS)
            ),
        )

    def secret_for(self, worker: str) -> str | None:
        return self.worker_secrets.get(worker.strip().lower())

    @property
    def monday_configured(self) -> bool:
        return bool(self.monday_api_token)

    @property
    def slack_configured(self) -> bool:
        return bool(self.slack_bot_token or self.slack_webhook_url)
