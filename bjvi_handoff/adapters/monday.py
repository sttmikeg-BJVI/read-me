"""Monday.com writeback: Monday stays the authoritative job record."""

from __future__ import annotations

import json
from typing import Any

import httpx

from ..config import Settings
from .base import DELIVERED, FAILED, NOT_CONFIGURED, DeliveryResult

CHANGE_COLUMNS_MUTATION = """
mutation ($itemId: ID!, $boardId: ID!, $columnValues: JSON!) {
  change_multiple_column_values(
    item_id: $itemId, board_id: $boardId, column_values: $columnValues
  ) {
    id
  }
}
"""

CREATE_UPDATE_MUTATION = """
mutation ($itemId: ID!, $body: String!) {
  create_update(item_id: $itemId, body: $body) {
    id
  }
}
"""

ITEM_BOARD_QUERY = """
query ($itemId: [ID!]) {
  items(ids: $itemId) {
    id
    board { id }
  }
}
"""


class MondayClient:
    """Thin GraphQL client. Writes column values and posts a summary update.

    The summary is a pointer (job id, artifact reference, receipt) rather than a
    copy of the worker's source material, so sensitive source files are never
    duplicated into Monday.
    """

    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        self._settings = settings
        self._client = client

    def update_item(
        self, item_id: str, summary: str, column_values: dict[str, Any]
    ) -> DeliveryResult:
        payload = {"item_id": item_id, "summary": summary, "column_values": column_values}
        if not self._settings.monday_configured:
            return DeliveryResult(
                destination="monday",
                status=NOT_CONFIGURED,
                detail="MONDAY_API_TOKEN is not set; writeback withheld",
                payload=payload,
            )
        try:
            board_id = self._board_id(item_id)
            if column_values:
                self._graphql(
                    CHANGE_COLUMNS_MUTATION,
                    {
                        "itemId": str(item_id),
                        "boardId": str(board_id),
                        "columnValues": json.dumps(column_values),
                    },
                )
            data = self._graphql(CREATE_UPDATE_MUTATION, {"itemId": str(item_id), "body": summary})
            reference = data["create_update"]["id"]
        except Exception as exc:  # network/API failures must not fake success
            return DeliveryResult(
                destination="monday", status=FAILED, detail=str(exc)[:500], payload=payload
            )
        return DeliveryResult(
            destination="monday",
            status=DELIVERED,
            reference=f"monday:item/{item_id}#update/{reference}",
            payload=payload,
        )

    def _board_id(self, item_id: str) -> str:
        data = self._graphql(ITEM_BOARD_QUERY, {"itemId": [str(item_id)]})
        items = data.get("items") or []
        if not items:
            raise RuntimeError(f"monday item {item_id} not found")
        return items[0]["board"]["id"]

    def _graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        client = self._client or httpx.Client(timeout=20.0)
        try:
            response = client.post(
                self._settings.monday_api_url,
                json={"query": query, "variables": variables},
                headers={
                    "Authorization": self._settings.monday_api_token or "",
                    "Content-Type": "application/json",
                    "API-Version": "2024-01",
                },
            )
            response.raise_for_status()
            body = response.json()
        finally:
            if self._client is None:
                client.close()
        if body.get("errors"):
            raise RuntimeError(f"monday api error: {body['errors']}")
        return body["data"]
