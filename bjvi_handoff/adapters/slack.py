"""Slack receipts: short operational notices, never private job content."""

from __future__ import annotations

import httpx

from ..config import Settings
from .base import DELIVERED, FAILED, NOT_CONFIGURED, DeliveryResult

SLACK_POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"


class SlackClient:
    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        self._settings = settings
        self._client = client

    def post(self, channel: str, text: str) -> DeliveryResult:
        payload = {"channel": channel, "text": text}
        if not self._settings.slack_configured:
            return DeliveryResult(
                destination="slack",
                status=NOT_CONFIGURED,
                detail="neither SLACK_BOT_TOKEN nor SLACK_WEBHOOK_URL is set; receipt withheld",
                payload=payload,
            )
        client = self._client or httpx.Client(timeout=20.0)
        try:
            if self._settings.slack_bot_token:
                response = client.post(
                    SLACK_POST_MESSAGE_URL,
                    json=payload,
                    headers={"Authorization": f"Bearer {self._settings.slack_bot_token}"},
                )
                response.raise_for_status()
                body = response.json()
                if not body.get("ok"):
                    return DeliveryResult(
                        destination="slack",
                        status=FAILED,
                        detail=f"slack api error: {body.get('error')}",
                        payload=payload,
                    )
                reference = f"slack:{body.get('channel')}/{body.get('ts')}"
            else:
                response = client.post(self._settings.slack_webhook_url or "", json=payload)
                response.raise_for_status()
                reference = f"slack:webhook/{channel}"
        except Exception as exc:
            return DeliveryResult(
                destination="slack", status=FAILED, detail=str(exc)[:500], payload=payload
            )
        finally:
            if self._client is None:
                client.close()
        return DeliveryResult(
            destination="slack", status=DELIVERED, reference=reference, payload=payload
        )
