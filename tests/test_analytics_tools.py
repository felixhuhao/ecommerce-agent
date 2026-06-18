from __future__ import annotations

import json

import pytest
from deepagents.backends.protocol import ExecuteResponse

from ecommerce_agent.tools.analytics import (
    CUSTOMER_SPEND_SUMMARY_TOOL_NAME,
    SALES_BY_CATEGORY_TOOL_NAME,
    build_customer_spend_summary_tool,
    build_sales_by_category_tool,
)
from ecommerce_agent.tools.forecasting import (
    SALES_FORECAST_TOOL_NAME,
    build_sales_forecast_tool,
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


class _ReadTool:
    def __init__(self, name: str, payload: list[dict]) -> None:
        self.name = name
        self.payload = payload
        self.calls: list[dict] = []

    async def ainvoke(self, args: dict) -> list[dict[str, str]]:
        self.calls.append(args)
        return [{"text": json.dumps(self.payload)}]


class _Backend:
    def __init__(self, output: str) -> None:
        self.output = output
        self.uploads: list[list[tuple[str, bytes]]] = []
        self.commands: list[str] = []

    def upload_files(self, files: list[tuple[str, bytes]]) -> list:
        self.uploads.append(files)
        return [type("Upload", (), {"path": path, "error": None})() for path, _ in files]

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        self.commands.append(command)
        return ExecuteResponse(output=self.output, exit_code=0, truncated=False)


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


@pytest.mark.asyncio
async def test_sales_forecast_stages_inputs_and_returns_chart_ready_rows() -> None:
    order_query = _ReadTool("order_query", [{"orderId": 1}])
    product_query = _ReadTool("product_query", [{"productId": 3}])
    backend = _Backend(
        json.dumps(
            {
                "status": "ok",
                "rows": [
                    {
                        "time": "2026-05",
                        "value": 79.0,
                        "group": "Fast Charger actual",
                        "is_forecast": False,
                    },
                    {
                        "time": "2026-06",
                        "value": 80.0,
                        "group": "Fast Charger forecast",
                        "is_forecast": True,
                    },
                ],
                "summary": {
                    "actual_months": ["2026-05"],
                    "forecast_months": ["2026-06"],
                    "subjects": ["Fast Charger"],
                },
            }
        )
    )
    tool = build_sales_forecast_tool(
        spring_read_tools=[order_query, product_query],  # type: ignore[list-item]
        backend=backend,
    )

    result = await tool.ainvoke({"sku": "SKU-LOW-003", "label": "Fast Charger"})

    assert order_query.calls == [{"limit": 100}]
    assert product_query.calls == [{"limit": 100}]
    assert len(backend.uploads[-1]) == 2
    assert "monthly_sales_by_product" in backend.commands[-1]
    assert result["kind"] == "analytics_result"
    assert result["metric"] == SALES_FORECAST_TOOL_NAME
    assert result["source"] == "sandbox"
    assert result["status"] == "ok"
    assert result["rows"][1]["is_forecast"] is True
    assert result["summary"]["row_count"] == 2


@pytest.mark.asyncio
async def test_sales_forecast_returns_no_data_without_raising() -> None:
    backend = _Backend(json.dumps({"status": "no_data", "reason": "forecast is empty"}))
    tool = build_sales_forecast_tool(
        spring_read_tools=[
            _ReadTool("order_query", []),  # type: ignore[list-item]
            _ReadTool("product_query", []),  # type: ignore[list-item]
        ],
        backend=backend,
    )

    result = await tool.ainvoke({"sku": "SKU-NOPE-999"})

    assert result["status"] == "no_data"
    assert result["rows"] == []
    assert result["summary"]["reason"] == "forecast is empty"
