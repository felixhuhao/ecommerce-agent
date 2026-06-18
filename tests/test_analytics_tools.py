from __future__ import annotations

import json

import pytest

from ecommerce_agent.tools.analytics import (
    CUSTOMER_SPEND_SUMMARY_TOOL_NAME,
    SALES_BY_CATEGORY_TOOL_NAME,
    build_customer_spend_summary_tool,
    build_sales_by_category_tool,
)


class _StatsTool:
    name = "get_statistics"

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls = 0

    async def ainvoke(self, args: dict) -> list[dict[str, str]]:
        self.calls += 1
        assert args == {}
        return [{"text": json.dumps(self.payload)}]


@pytest.mark.asyncio
async def test_customer_spend_summary_extracts_and_caps_top_customers() -> None:
    stats = _StatsTool(
        {
            "topCustomersBySpend": [
                {"customerId": 1, "customerName": "Small", "totalSpend": 20, "orders": 1},
                {"customerId": 2, "customerName": "Big", "totalSpend": 120, "orders": 4},
                {"customerId": 3, "customerName": "Mid", "totalSpend": 80, "orders": 2},
            ]
        }
    )
    tool = build_customer_spend_summary_tool(get_statistics=stats)  # type: ignore[arg-type]

    result = await tool.ainvoke({"limit": 2})

    assert stats.calls == 1
    assert result["kind"] == "analytics_result"
    assert result["metric"] == CUSTOMER_SPEND_SUMMARY_TOOL_NAME
    assert result["provenance"]["backing_key"] == "topCustomersBySpend"
    assert result["rows"] == [
        {"customerName": "Big", "revenue": 120.0, "customerId": 2, "orders": 4},
        {"customerName": "Mid", "revenue": 80.0, "customerId": 3, "orders": 2},
    ]


@pytest.mark.asyncio
async def test_sales_by_category_extracts_filters_and_caps_categories() -> None:
    stats = _StatsTool(
        {
            "salesByCategory": [
                {"category": "Unknown", "sales": 999},
                {"category": "Home", "sales": 40, "units": 3},
                {"category": "Electronics", "sales": 200, "unitsSold": 7},
                {"category": "Food", "sales": 10},
            ]
        }
    )
    tool = build_sales_by_category_tool(get_statistics=stats)  # type: ignore[arg-type]

    result = await tool.ainvoke({"limit": 2, "include_unknown": False})

    assert stats.calls == 1
    assert result["kind"] == "analytics_result"
    assert result["metric"] == SALES_BY_CATEGORY_TOOL_NAME
    assert result["provenance"]["backing_key"] == "salesByCategory"
    assert result["filters"] == {"limit": 2, "include_unknown": False}
    assert result["rows"] == [
        {"category": "Electronics", "revenue": 200.0, "units": 7},
        {"category": "Home", "revenue": 40.0, "units": 3},
    ]


@pytest.mark.asyncio
async def test_customer_spend_summary_fails_when_backing_key_is_missing() -> None:
    tool = build_customer_spend_summary_tool(get_statistics=_StatsTool({}))  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="topCustomersBySpend"):
        await tool.ainvoke({"limit": 5})
