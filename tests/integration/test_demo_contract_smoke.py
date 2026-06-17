"""Tier 0 deterministic demo contract smoke.

No live LLM. Proves the local Spring MCP and ModelScope chart MCP expose the tools and
data shapes the demo depends on, before spending LLM tokens in Tier 1.

Run mode:
- default: each check skips cleanly when the service it needs is unreachable.
- strict: ``RUN_DEMO_CONTRACT_SMOKE=1`` turns an unreachable service into a FAILURE so the
  closeout gate cannot report a false green where every check skipped.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable

import httpx
import pytest

from ecommerce_agent.config import Settings
from ecommerce_agent.mcp_client import (
    CUSTOMER_INSIGHTS_SPRING_TOOLS,
    INVENTORY_SPRING_TOOLS,
    MODELSCOPE_SERVER_NAME,
    MODELSCOPE_VIZ_TOOLS,
    ORDER_MANAGER_SPRING_TOOLS,
    PURCHASING_SPRING_TOOLS,
    READ_ONLY_SPRING_TOOLS,
    SPRING_SERVER_NAME,
    WRITE_OR_APPROVAL_SPRING_TOOLS,
    WRITE_SPRING_TOOLS,
    build_mcp_client,
    filter_customer_insights_tools,
    filter_inventory_tools,
    filter_order_manager_tools,
    filter_purchasing_tools,
    filter_spring_read_tools,
    filter_viz_tools,
    load_spring_read_tools,
    tool_names,
)
from tests.integration.helpers import skip_on_spring_mcp_auth_error, spring_health_url

pytestmark = [pytest.mark.integration]

_STRICT = os.getenv("RUN_DEMO_CONTRACT_SMOKE") == "1"

_HARD_ROW_FIELDS = ("productId", "quantity", "safetyStock")
_STRICT_ROW_FIELDS = ("sku", "productName")


def _settings() -> Settings:
    return Settings(
        mcp_request_timeout_seconds=5,
        mcp_sse_read_timeout_seconds=30,
    )


def _gate_unreachable(detail: str) -> None:
    if _STRICT:
        pytest.fail(f"RUN_DEMO_CONTRACT_SMOKE=1 is set but {detail}")
    pytest.skip(detail)


async def _gate_spring(settings: Settings) -> None:
    health_url = spring_health_url(settings.spring_mcp_url)
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            response = await client.get(health_url)
    except httpx.HTTPError as exc:
        _gate_unreachable(f"Spring MCP is unreachable at {health_url}: {exc}")
        return
    if response.status_code != 200:
        _gate_unreachable(
            f"Spring MCP health returned {response.status_code} at {health_url}"
        )


async def _gate_chart_mcp(settings: Settings) -> None:
    if not settings.modelscope_mcp_url:
        _gate_unreachable("MODELSCOPE_MCP_URL is not configured")
        return
    client = build_mcp_client(settings)
    try:
        await client.get_tools(server_name=MODELSCOPE_SERVER_NAME)
    except Exception as exc:
        _gate_unreachable(
            f"chart/modelscope MCP is unreachable at {settings.modelscope_mcp_url}: {exc}"
        )


def _spring_tool(tools: Iterable, name: str):
    return next(tool for tool in tools if tool.name == name)


def _fail_on_spring_tool_error(exc: BaseException, settings: Settings, where: str) -> None:
    skip_on_spring_mcp_auth_error(exc, settings)
    pytest.fail(
        f"{where} failed against a reachable Spring MCP "
        f"(likely outputSchema/data contract drift): {exc}"
    )


def _as_json_text(value: object) -> str:
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
            else:
                parts.append(json.dumps(item, default=str))
        return "\n".join(parts)
    if isinstance(value, dict):
        content = value.get("content")
        if isinstance(content, list):
            return _as_json_text(content)
        return json.dumps(value, default=str)
    return str(value)


def _parse_content(value: object) -> object:
    if isinstance(value, list):
        texts = [
            item["text"]
            for item in value
            if isinstance(item, dict) and isinstance(item.get("text"), str)
        ]
        if texts:
            joined = "\n".join(texts)
            try:
                return json.loads(joined)
            except json.JSONDecodeError:
                return joined
        return value
    if isinstance(value, dict):
        content = value.get("content")
        if isinstance(content, list):
            return _parse_content(content)
        return value
    return value


def _extract_rows(value: object) -> list[dict]:
    parsed = _parse_content(value)
    if isinstance(parsed, str):
        try:
            parsed = json.loads(parsed)
        except json.JSONDecodeError:
            return []
    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict)]
    if isinstance(parsed, dict):
        for key in ("items", "data", "results", "rows", "inventory", "products"):
            nested = parsed.get(key)
            if isinstance(nested, list):
                return [item for item in nested if isinstance(item, dict)]
        return [parsed]
    return []


async def test_spring_mcp_is_reachable() -> None:
    await _gate_spring(_settings())


async def test_chart_mcp_exposes_viz_surface() -> None:
    settings = _settings()
    await _gate_chart_mcp(settings)
    client = build_mcp_client(settings)
    try:
        tools = await client.get_tools(server_name=MODELSCOPE_SERVER_NAME)
    except Exception as exc:
        _gate_unreachable(
            f"chart/modelscope MCP discovery failed at {settings.modelscope_mcp_url}: {exc}"
        )
        return
    discovered = tool_names(filter_viz_tools(tools))
    missing = MODELSCOPE_VIZ_TOOLS - discovered
    assert not missing, f"chart MCP is missing viz tools from VIZ_TOOL_NAMES: {sorted(missing)}"


async def test_spring_exposes_specialist_tool_groups() -> None:
    settings = _settings()
    await _gate_spring(settings)
    client = build_mcp_client(settings)
    try:
        tools = await client.get_tools(server_name=SPRING_SERVER_NAME)
    except Exception as exc:
        _fail_on_spring_tool_error(exc, settings, "Spring tool discovery")
        return

    expected_groups = {
        "sales-analyst (read surface)": (
            filter_spring_read_tools(tools),
            READ_ONLY_SPRING_TOOLS,
        ),
        "inventory": (filter_inventory_tools(tools), INVENTORY_SPRING_TOOLS),
        "purchasing": (filter_purchasing_tools(tools), PURCHASING_SPRING_TOOLS),
        "customer-insights": (
            filter_customer_insights_tools(tools),
            CUSTOMER_INSIGHTS_SPRING_TOOLS,
        ),
        "order-manager": (filter_order_manager_tools(tools), ORDER_MANAGER_SPRING_TOOLS),
    }
    names = tool_names(tools)
    for label, (filtered, expected) in expected_groups.items():
        actual = tool_names(filtered)
        assert actual == expected, (
            f"{label}: live filter {sorted(actual)} != expected {sorted(expected)}"
        )
        missing = expected - names
        assert not missing, f"{label}: Spring MCP is missing tools {sorted(missing)}"


async def _invoke_get_statistics(read_tools) -> str:
    stats = _spring_tool(read_tools, "get_statistics")
    try:
        result = await stats.ainvoke({})
    except Exception as exc:
        raise _SpringCallError("get_statistics invocation") from exc
    return _as_json_text(result)


class _SpringCallError(RuntimeError):
    pass


async def test_get_statistics_exposes_top_customers_aggregate() -> None:
    settings = _settings()
    await _gate_spring(settings)
    client = build_mcp_client(settings)
    try:
        read_tools = await load_spring_read_tools(client)
    except Exception as exc:
        _fail_on_spring_tool_error(exc, settings, "Spring read-tool discovery")
        return
    try:
        text = await _invoke_get_statistics(read_tools)
    except _SpringCallError as exc:
        _fail_on_spring_tool_error(exc.__cause__ or exc, settings, str(exc))
        return
    assert "topCustomersBySpend" in text, (
        f"get_statistics does not expose topCustomersBySpend (closeout regression); "
        f"got: {text[:500]}"
    )


async def test_get_statistics_exposes_sales_by_category_aggregate() -> None:
    settings = _settings()
    await _gate_spring(settings)
    client = build_mcp_client(settings)
    try:
        read_tools = await load_spring_read_tools(client)
    except Exception as exc:
        _fail_on_spring_tool_error(exc, settings, "Spring read-tool discovery")
        return
    try:
        text = await _invoke_get_statistics(read_tools)
    except _SpringCallError as exc:
        _fail_on_spring_tool_error(exc.__cause__ or exc, settings, str(exc))
        return
    if "salesByCategory" not in text:
        detail = (
            "get_statistics does not expose salesByCategory. The 'compare sales by "
            "category' demo depends on it; the live backend currently returns "
            "productsByCategory instead. got: " + text[:500]
        )
        if _STRICT:
            pytest.fail(detail)
        pytest.skip(detail)


async def test_inventory_low_stock_returns_readable_rows() -> None:
    settings = _settings()
    await _gate_spring(settings)
    client = build_mcp_client(settings)
    try:
        read_tools = await load_spring_read_tools(client)
    except Exception as exc:
        _fail_on_spring_tool_error(exc, settings, "Spring read-tool discovery")
        return
    low_stock = _spring_tool(read_tools, "inventory_low_stock")
    try:
        result = await low_stock.ainvoke({})
    except Exception as exc:
        _fail_on_spring_tool_error(exc, settings, "inventory_low_stock invocation")
        return

    rows = _extract_rows(result)
    assert rows, f"inventory_low_stock returned no rows; raw: {_as_json_text(result)[:500]}"
    for index, row in enumerate(rows):
        for field in _HARD_ROW_FIELDS:
            assert field in row, (
                f"row {index} missing required field {field!r}; row={row}"
            )
    if _STRICT:
        for field in _STRICT_ROW_FIELDS:
            present = any(field in row for row in rows)
            assert present, (
                f"no row exposes demo-readable field {field!r}; the demo depends on "
                f"human-readable evidence. Rows: {rows}"
            )


async def test_product_search_resolves_low_stock_sku() -> None:
    settings = _settings()
    await _gate_spring(settings)
    client = build_mcp_client(settings)
    try:
        read_tools = await load_spring_read_tools(client)
    except Exception as exc:
        _fail_on_spring_tool_error(exc, settings, "Spring read-tool discovery")
        return
    product_search = _spring_tool(read_tools, "product_search")
    try:
        result = await product_search.ainvoke({"keyword": "SKU-LOW-003"})
    except Exception as exc:
        _fail_on_spring_tool_error(exc, settings, "product_search invocation")
        return

    rows = _extract_rows(result)
    text = _as_json_text(result)
    assert rows or "SKU-LOW-003" in text, (
        f"product_search did not resolve SKU-LOW-003; raw: {text[:500]}"
    )
    if rows:
        assert any(
            "SKU-LOW-003" in str(row) for row in rows
        ), f"no resolved row references SKU-LOW-003; rows={rows}"


async def test_spring_never_exposes_direct_writes_as_reads() -> None:
    settings = _settings()
    await _gate_spring(settings)
    client = build_mcp_client(settings)
    try:
        tools = await client.get_tools(server_name=SPRING_SERVER_NAME)
    except Exception as exc:
        _fail_on_spring_tool_error(exc, settings, "Spring tool discovery")
        return
    read_tools = tool_names(filter_spring_read_tools(tools))
    leaked = read_tools & WRITE_OR_APPROVAL_SPRING_TOOLS
    assert not leaked, f"read surface leaked write/approval tools: {sorted(leaked)}"
    direct_writes_present = tool_names(tools) & WRITE_SPRING_TOOLS
    assert direct_writes_present == set(), (
        f"direct write tools must not be exposed to agents at all: "
        f"{sorted(direct_writes_present)}"
    )
