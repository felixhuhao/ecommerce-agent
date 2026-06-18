from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

CUSTOMER_SPEND_SUMMARY_TOOL_NAME = "customer_spend_summary"
SALES_BY_CATEGORY_TOOL_NAME = "sales_by_category"
MAX_ANALYTICS_ROWS = 20


class CustomerSpendSummaryInput(BaseModel):
    """Return top customers by spend from backend aggregate statistics."""

    limit: int = Field(default=10, ge=1, le=MAX_ANALYTICS_ROWS)


class SalesByCategoryInput(BaseModel):
    """Return sales totals by category from backend aggregate statistics."""

    limit: int = Field(default=10, ge=1, le=MAX_ANALYTICS_ROWS)
    include_unknown: bool = True


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, list | tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}

    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return _jsonable(model_dump())

    content = getattr(value, "content", None)
    if content is not None:
        return _jsonable(content)

    return repr(value)


def _parse_payload(value: Any) -> dict[str, Any]:
    payload = _jsonable(value)
    if isinstance(payload, dict) and isinstance(payload.get("content"), list):
        payload = payload["content"]
    if isinstance(payload, list):
        texts = [
            item["text"]
            for item in payload
            if isinstance(item, dict) and isinstance(item.get("text"), str)
        ]
        if texts:
            joined = "\n".join(texts)
            try:
                decoded = json.loads(joined)
            except json.JSONDecodeError:
                return {}
            payload = decoded
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return {}
    return payload if isinstance(payload, dict) else {}


def _get_any(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload:
            return payload[key]
    return None


def _number(value: Any) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        cleaned = value.replace("$", "").replace(",", "").strip()
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _sort_rows(rows: list[dict[str, Any]], metric: str) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: _number(row.get(metric)) or 0,
        reverse=True,
    )


async def _statistics_payload(get_statistics: BaseTool) -> dict[str, Any]:
    return _parse_payload(await get_statistics.ainvoke({}))


def _analytics_result(
    *,
    metric: str,
    filters: dict[str, Any],
    rows: list[dict[str, Any]],
    backing_key: str,
) -> dict[str, Any]:
    return {
        "kind": "analytics_result",
        "metric": metric,
        "source": "spring",
        "filters": filters,
        "rows": rows,
        "summary": {
            "row_count": len(rows),
            "currency": "USD",
        },
        "provenance": {
            "system": "spring-mcp",
            "backing_tool": "get_statistics",
            "backing_key": backing_key,
        },
    }


def _customer_rows(raw: Any, limit: int) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        revenue = _number(
            _get_any(
                item,
                "revenue",
                "totalSpend",
                "total_spend",
                "spend",
                "sales",
                "totalSales",
            )
        )
        if revenue is None:
            continue
        row: dict[str, Any] = {
            "customerName": _get_any(
                item, "customerName", "customer_name", "name", "username", "customer"
            )
            or "Unknown customer",
            "revenue": round(revenue, 2),
        }
        customer_id = _get_any(item, "customerId", "customer_id", "userId", "user_id", "id")
        orders = _get_any(item, "orders", "orderCount", "order_count", "count")
        if customer_id is not None:
            row["customerId"] = customer_id
        if orders is not None:
            row["orders"] = orders
        rows.append(row)
    return _sort_rows(rows, "revenue")[:limit]


def _category_rows(raw: Any, *, limit: int, include_unknown: bool) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        category = str(_get_any(item, "category", "name", "categoryName") or "Unknown").strip()
        if not category:
            category = "Unknown"
        if not include_unknown and category.lower().startswith("unknown"):
            continue
        revenue = _number(
            _get_any(item, "revenue", "sales", "totalSales", "total_sales", "amount")
        )
        if revenue is None:
            continue
        row: dict[str, Any] = {
            "category": category,
            "revenue": round(revenue, 2),
        }
        units = _get_any(item, "units", "unitsSold", "units_sold", "quantity")
        share = _get_any(item, "share", "percentShare", "percent_share")
        if units is not None:
            row["units"] = units
        if share is not None:
            row["share"] = share
        rows.append(row)
    return _sort_rows(rows, "revenue")[:limit]


def build_customer_spend_summary_tool(*, get_statistics: BaseTool) -> BaseTool:
    async def customer_spend_summary(limit: int = 10) -> dict[str, Any]:
        """Return top customers by spend from authoritative backend statistics."""
        payload = await _statistics_payload(get_statistics)
        raw = _get_any(payload, "topCustomersBySpend", "top_customers_by_spend")
        if raw is None:
            raise ValueError("get_statistics did not return topCustomersBySpend")
        rows = _customer_rows(raw, limit)
        return _analytics_result(
            metric=CUSTOMER_SPEND_SUMMARY_TOOL_NAME,
            filters={"limit": limit},
            rows=rows,
            backing_key="topCustomersBySpend",
        )

    return StructuredTool.from_function(
        coroutine=customer_spend_summary,
        name=CUSTOMER_SPEND_SUMMARY_TOOL_NAME,
        description=(
            "Return authoritative top customers by spend from backend aggregate "
            "statistics. Use for customer spend rankings and top-N customer revenue; "
            "do not recompute this by looping over order_query."
        ),
        args_schema=CustomerSpendSummaryInput,
    )


def build_sales_by_category_tool(*, get_statistics: BaseTool) -> BaseTool:
    async def sales_by_category(
        limit: int = 10,
        include_unknown: bool = True,
    ) -> dict[str, Any]:
        """Return sales totals by category from authoritative backend statistics."""
        payload = await _statistics_payload(get_statistics)
        raw = _get_any(payload, "salesByCategory", "sales_by_category")
        if raw is None:
            raise ValueError("get_statistics did not return salesByCategory")
        rows = _category_rows(raw, limit=limit, include_unknown=include_unknown)
        return _analytics_result(
            metric=SALES_BY_CATEGORY_TOOL_NAME,
            filters={"limit": limit, "include_unknown": include_unknown},
            rows=rows,
            backing_key="salesByCategory",
        )

    return StructuredTool.from_function(
        coroutine=sales_by_category,
        name=SALES_BY_CATEGORY_TOOL_NAME,
        description=(
            "Return authoritative sales totals by category from backend aggregate "
            "statistics. Use for sales-by-category comparisons and category charts; "
            "do not use sandbox or warehouse SQL for this direct aggregate."
        ),
        args_schema=SalesByCategoryInput,
    )
