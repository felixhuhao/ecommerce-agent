from urllib.parse import urlparse, urlunparse

import httpx
import pytest

from ecommerce_agent.config import Settings
from ecommerce_agent.mcp_client import (
    READ_ONLY_SPRING_TOOLS,
    SPRING_SERVER_NAME,
    WRITE_OR_APPROVAL_SPRING_TOOLS,
    build_mcp_client,
    load_spring_read_tools,
    tool_names,
)


def _spring_health_url(mcp_url: str) -> str:
    parsed = urlparse(mcp_url)
    return urlunparse(parsed._replace(path="/actuator/health", params="", query="", fragment=""))


async def _skip_unless_spring_mcp_is_running(settings: Settings) -> None:
    health_url = _spring_health_url(settings.spring_mcp_url)
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            response = await client.get(health_url)
    except httpx.HTTPError as exc:
        pytest.skip(f"SpringBoot MCP server is not reachable at {health_url}: {exc}")

    if response.status_code != 200:
        pytest.skip(
            f"SpringBoot MCP server health check returned {response.status_code} at {health_url}"
        )


@pytest.mark.integration
async def test_spring_mcp_discovers_read_tools_and_calls_inventory_query() -> None:
    settings = Settings(
        mcp_request_timeout_seconds=5,
        mcp_sse_read_timeout_seconds=30,
    )
    await _skip_unless_spring_mcp_is_running(settings)

    client = build_mcp_client(settings)
    read_tools = await load_spring_read_tools(client)
    names = tool_names(read_tools)

    assert SPRING_SERVER_NAME == "spring"
    assert READ_ONLY_SPRING_TOOLS.issubset(names)
    assert WRITE_OR_APPROVAL_SPRING_TOOLS.isdisjoint(names)

    inventory_query = next(tool for tool in read_tools if tool.name == "inventory_query")
    result = await inventory_query.ainvoke({"productId": 1, "warehouse": None, "limit": 1})
    result_text = str(result)

    assert "productId" in result_text
    assert "quantity" in result_text
