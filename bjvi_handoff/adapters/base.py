"""Delivery contracts for the two systems ChatGPT can already inspect."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

DELIVERED = "DELIVERED"
NOT_CONFIGURED = "NOT_CONFIGURED"
FAILED = "FAILED"


@dataclass
class DeliveryResult:
    """Outcome of one outbound write.

    `status` is never DELIVERED unless the remote system actually accepted the
    write, so a job is only marked as receipted on real evidence.
    """

    destination: str
    status: str
    reference: str | None = None
    detail: str | None = None
    payload: dict[str, Any] | None = None

    @property
    def delivered(self) -> bool:
        return self.status == DELIVERED


class MondayGateway(Protocol):
    def update_item(
        self, item_id: str, summary: str, column_values: dict[str, Any]
    ) -> DeliveryResult:
        ...


class SlackGateway(Protocol):
    def post(self, channel: str, text: str) -> DeliveryResult:
        ...
