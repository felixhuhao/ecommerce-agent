from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from ecommerce_agent.tools.staging import (
    ORDERS_RAW_PATH,
    PRODUCTS_RAW_PATH,
    STAGE_SALES_ANALYSIS_TOOL_NAME,
    build_sales_analysis_staging_tool,
)


class FakeTool:
    def __init__(self, name: str, result: object) -> None:
        self.name = name
        self.result = result
        self.calls: list[dict] = []

    async def ainvoke(self, payload: dict) -> object:
        self.calls.append(payload)
        return self.result


class FakeSandbox:
    def __init__(self) -> None:
        self.uploaded: list[tuple[str, bytes]] = []

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[SimpleNamespace]:
        self.uploaded.extend(files)
        return [SimpleNamespace(path=path, error=None) for path, _ in files]


@pytest.mark.asyncio
async def test_stage_sales_analysis_inputs_writes_raw_payloads_without_returning_them() -> None:
    orders = [
        {"orderId": 1, "items": []},
        {"orderId": 2, "items": []},
    ]
    products = [{"productId": 1, "category": "Electronics"}]
    order_query = FakeTool("order_query", [{"type": "text", "text": json.dumps(orders)}])
    product_query = FakeTool("product_query", products)
    sandbox = FakeSandbox()
    tool = build_sales_analysis_staging_tool(
        spring_read_tools=[order_query, product_query],  # type: ignore[list-item]
        backend=sandbox,
    )

    result = await tool.ainvoke({"order_limit": 2, "product_limit": 3})

    assert tool.name == STAGE_SALES_ANALYSIS_TOOL_NAME
    assert order_query.calls == [{"limit": 2}]
    assert product_query.calls == [{"limit": 3}]
    assert [path for path, _ in sandbox.uploaded] == [ORDERS_RAW_PATH, PRODUCTS_RAW_PATH]
    assert json.loads(sandbox.uploaded[0][1]) == [{"type": "text", "text": json.dumps(orders)}]
    assert json.loads(sandbox.uploaded[1][1]) == products
    assert result["orders_path"] == ORDERS_RAW_PATH
    assert result["products_path"] == PRODUCTS_RAW_PATH
    assert result["order_count"] == 2
    assert result["product_count"] == 1
    assert "orderId" not in json.dumps(result)


@pytest.mark.asyncio
async def test_stage_sales_analysis_inputs_surfaces_upload_failures() -> None:
    class FailingSandbox(FakeSandbox):
        def upload_files(self, files: list[tuple[str, bytes]]) -> list[SimpleNamespace]:
            return [SimpleNamespace(path=files[0][0], error="invalid_path")]

    tool = build_sales_analysis_staging_tool(
        spring_read_tools=[
            FakeTool("order_query", []),  # type: ignore[list-item]
            FakeTool("product_query", []),  # type: ignore[list-item]
        ],
        backend=FailingSandbox(),
    )

    with pytest.raises(RuntimeError, match="failed to stage sandbox files"):
        await tool.ainvoke({})

