from __future__ import annotations

import json
from typing import Any, Protocol

from ecommerce_agent.monitoring.models import FindingEvidence

_SUMMARY_LIMIT = 500
_EVIDENCE_LIMIT = 2000


class MonitorReader(Protocol):
    async def inventory_low_stock(
        self,
        *,
        threshold: int,
    ) -> tuple[list[dict[str, Any]], FindingEvidence]:
        ...

    async def sales_drop_wow(self) -> tuple[list[dict[str, Any]], FindingEvidence]:
        ...

    async def stale_pending_order_candidates(
        self,
        *,
        older_than_hours: int,
    ) -> tuple[list[dict[str, Any]], FindingEvidence]:
        ...

    async def stale_paid_order_candidates(
        self,
        *,
        older_than_hours: int,
    ) -> tuple[list[dict[str, Any]], FindingEvidence]:
        ...


class InMemoryMonitorReader:
    def __init__(
        self,
        *,
        low_stock_rows: list[dict[str, Any]] | None = None,
        sales_drop_rows: list[dict[str, Any]] | None = None,
        stale_pending_rows: list[dict[str, Any]] | None = None,
        stale_paid_rows: list[dict[str, Any]] | None = None,
    ) -> None:
        self.low_stock_rows = low_stock_rows or []
        self.sales_drop_rows = sales_drop_rows or []
        self.stale_pending_rows = stale_pending_rows or []
        self.stale_paid_rows = stale_paid_rows or []

    async def inventory_low_stock(
        self,
        *,
        threshold: int,
    ) -> tuple[list[dict[str, Any]], FindingEvidence]:
        return self.low_stock_rows, FindingEvidence(
            source_id="detection:inventory_low_stock",
            tool_name="inventory_low_stock",
            args_summary=f"threshold={threshold}",
            result_summary=_summarize(self.low_stock_rows),
            evidence=_evidence(self.low_stock_rows),
        )

    async def sales_drop_wow(self) -> tuple[list[dict[str, Any]], FindingEvidence]:
        return self.sales_drop_rows, FindingEvidence(
            source_id="detection:get_statistics",
            tool_name="get_statistics",
            args_summary="metric=sales_drop_wow",
            result_summary=_summarize(self.sales_drop_rows),
            evidence=_evidence(self.sales_drop_rows),
        )

    async def stale_pending_order_candidates(
        self,
        *,
        older_than_hours: int,
    ) -> tuple[list[dict[str, Any]], FindingEvidence]:
        return self.stale_pending_rows, FindingEvidence(
            source_id="detection:order_query:pending",
            tool_name="order_query",
            args_summary=f"status=pending staleOlderThanHours={older_than_hours}",
            result_summary=_summarize(self.stale_pending_rows),
            evidence=_evidence(self.stale_pending_rows),
        )

    async def stale_paid_order_candidates(
        self,
        *,
        older_than_hours: int,
    ) -> tuple[list[dict[str, Any]], FindingEvidence]:
        return self.stale_paid_rows, FindingEvidence(
            source_id="detection:order_query:paid",
            tool_name="order_query",
            args_summary=f"status=paid staleOlderThanHours={older_than_hours}",
            result_summary=_summarize(self.stale_paid_rows),
            evidence=_evidence(self.stale_paid_rows),
        )


class McpMonitorReader:
    def __init__(self, tools: list[Any]) -> None:
        self._tools = {tool.name: tool for tool in tools}

    async def inventory_low_stock(
        self,
        *,
        threshold: int,
    ) -> tuple[list[dict[str, Any]], FindingEvidence]:
        args = {"threshold": threshold}
        result = await self._invoke("inventory_low_stock", args)
        return _records(result), FindingEvidence(
            source_id="detection:inventory_low_stock",
            tool_name="inventory_low_stock",
            args_summary=_summarize(args),
            result_summary=_summarize(result),
            evidence=_evidence(result),
        )

    async def sales_drop_wow(self) -> tuple[list[dict[str, Any]], FindingEvidence]:
        args: dict[str, Any] = {}
        result = await self._invoke("get_statistics", args)
        return _records(result), FindingEvidence(
            source_id="detection:get_statistics",
            tool_name="get_statistics",
            args_summary="aggregate=salesDropWow",
            result_summary=_summarize(result),
            evidence=_evidence(result),
        )

    async def stale_pending_order_candidates(
        self,
        *,
        older_than_hours: int,
    ) -> tuple[list[dict[str, Any]], FindingEvidence]:
        args = {
            "status": "pending",
            "staleOlderThanHours": older_than_hours,
            # Drain at most 50 oldest stale rows per monitor cycle.
            "limit": 50,
        }
        result = await self._invoke("order_query", args)
        return _records(result), FindingEvidence(
            source_id="detection:order_query:pending",
            tool_name="order_query",
            args_summary=_summarize(args),
            result_summary=_summarize(result),
            evidence=_evidence(result),
        )

    async def stale_paid_order_candidates(
        self,
        *,
        older_than_hours: int,
    ) -> tuple[list[dict[str, Any]], FindingEvidence]:
        args = {
            "status": "paid",
            "staleOlderThanHours": older_than_hours,
            # Drain at most 50 oldest stale rows per monitor cycle.
            "limit": 50,
        }
        result = await self._invoke("order_query", args)
        return _records(result), FindingEvidence(
            source_id="detection:order_query:paid",
            tool_name="order_query",
            args_summary=_summarize(args),
            result_summary=_summarize(result),
            evidence=_evidence(result),
        )

    async def _invoke(self, tool_name: str, args: dict[str, Any]) -> Any:
        tool = self._tools.get(tool_name)
        if tool is None:
            raise RuntimeError(f"required monitor tool {tool_name!r} is unavailable")
        return await tool.ainvoke(args)


def _summarize(value: Any) -> str:
    text = value if isinstance(value, str) else repr(value)
    suffix = "..." if len(text) > _SUMMARY_LIMIT else ""
    return f"{text[:_SUMMARY_LIMIT]}{suffix}"


def _evidence(value: Any) -> str:
    text = value if isinstance(value, str) else repr(value)
    return text[:_EVIDENCE_LIMIT]


def _records(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        content_records = _content_records(value)
        if content_records:
            return content_records
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        content = value.get("content")
        if isinstance(content, list):
            content_records = _content_records(content)
            if content_records:
                return content_records
        for key in (
            "items",
            "data",
            "results",
            "rows",
            "inventory",
            "products",
            "salesDropWow",
            "sales_drop_wow",
        ):
            nested = value.get(key)
            if isinstance(nested, list):
                return [item for item in nested if isinstance(item, dict)]
        return [value]
    return []


def _content_records(content: list[Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for item in content:
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            continue
        try:
            parsed = json.loads(item["text"])
        except json.JSONDecodeError:
            continue
        records.extend(_records(parsed))
    return records
