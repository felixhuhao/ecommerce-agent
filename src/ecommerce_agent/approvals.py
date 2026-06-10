from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import httpx

from ecommerce_agent.config import Settings


class ApprovalApiError(RuntimeError):
    """Raised when the Java approval API returns an unexpected error response."""

    def __init__(self, status_code: int, payload: Any) -> None:
        super().__init__(f"approval API returned {status_code}: {payload!r}")
        self.status_code = status_code
        self.payload = payload


class ApprovalClient:
    """Thin async client for the Java-owned approval REST API."""

    def __init__(
        self,
        *,
        base_url: str,
        service_token: str,
        user_id: str,
        session_id: str,
        timeout_seconds: float,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = {
            "X-Service-Token": service_token,
            "X-User-Id": user_id,
            "X-Session-Id": session_id,
        }
        self._timeout_seconds = timeout_seconds
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers=self._headers,
            timeout=self._timeout_seconds,
        )

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        session_id: str,
        user_id: str | None = None,
    ) -> ApprovalClient:
        return cls(
            base_url=settings.approval_api_base_url,
            service_token=settings.spring_mcp_service_token,
            user_id=user_id or settings.spring_mcp_user_id,
            session_id=session_id,
            timeout_seconds=settings.mcp_request_timeout_seconds,
        )

    async def get_approval(self, approval_id: str) -> dict[str, Any]:
        return await self._request_json("GET", f"/approvals/{approval_id}")

    async def approve(self, approval_id: str) -> dict[str, Any]:
        return await self._request_json(
            "POST",
            f"/approvals/{approval_id}/approve",
            allowed_error_statuses={409},
        )

    async def reject(self, approval_id: str, *, reason: str | None = None) -> dict[str, Any]:
        payload = {"reason": reason} if reason else None
        return await self._request_json(
            "POST",
            f"/approvals/{approval_id}/reject",
            json_payload=payload,
            allowed_error_statuses={409},
        )

    async def execute(self, approval_id: str) -> dict[str, Any]:
        return await self._request_json(
            "POST",
            f"/approvals/{approval_id}/execute",
            allowed_error_statuses={409, 503},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        json_payload: dict[str, Any] | None = None,
        allowed_error_statuses: set[int] | None = None,
    ) -> dict[str, Any]:
        allowed_error_statuses = allowed_error_statuses or set()
        request_kwargs = {"json": json_payload} if json_payload is not None else {}
        response = await self._client.request(method, path, **request_kwargs)

        try:
            payload: Any = response.json()
        except ValueError:
            payload = {"message": response.text}

        if response.is_error and response.status_code not in allowed_error_statuses:
            raise ApprovalApiError(response.status_code, payload)
        if not isinstance(payload, dict):
            payload = {"value": payload}
        payload.setdefault("_http_status_code", response.status_code)
        return payload


def make_approval_client(settings: Settings, *, session_id: str) -> ApprovalClient:
    return ApprovalClient.from_settings(settings, session_id=session_id)


def approval_card(approval: dict[str, Any]) -> dict[str, Any]:
    """Return the server-rendered operation detail as a stable card payload."""
    detail = _json_object(approval.get("operationDetail"))
    if detail is None:
        detail = {
            "operationDetail": approval.get("operationDetail"),
        }
    card = dict(detail)
    for key in ("approvalId", "toolName", "operationType", "status"):
        if approval.get(key) is not None:
            card.setdefault(key, approval[key])
    return card


def extract_approval_id(value: Any) -> str | None:
    if isinstance(value, dict):
        approval_id = value.get("approvalId") or value.get("approval_id")
        if approval_id:
            return str(approval_id)
        for nested in value.values():
            approval_id = extract_approval_id(nested)
            if approval_id:
                return approval_id
        return None
    if isinstance(value, list):
        for item in value:
            approval_id = extract_approval_id(item)
            if approval_id:
                return approval_id
        return None

    for attr in ("approvalId", "approval_id"):
        approval_id = getattr(value, attr, None)
        if approval_id:
            return str(approval_id)

    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return extract_approval_id(json.loads(text))
    except json.JSONDecodeError:
        pass

    match = re.search(r"approval[_-]?id['\"]?\s*[:=]\s*['\"]?([^,'\"\s})]+)", text, re.I)
    return match.group(1) if match else None


async def execute_with_retry(
    client: Any,
    approval_id: str,
    *,
    attempts: int = 2,
    delay_seconds: float = 0.1,
) -> dict[str, Any]:
    """Execute an approved operation, retrying Java-marked transient failures."""
    for attempt in range(attempts):
        result = await client.execute(approval_id)
        if not result.get("retryable") or attempt == attempts - 1:
            return result
        await asyncio.sleep(delay_seconds * (attempt + 1))
    raise AssertionError("unreachable")


def _json_object(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None
