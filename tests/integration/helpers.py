from urllib.parse import urlparse, urlunparse

import httpx
import pytest

from ecommerce_agent.config import Settings


def spring_health_url(mcp_url: str) -> str:
    parsed = urlparse(mcp_url)
    return urlunparse(parsed._replace(path="/actuator/health", params="", query="", fragment=""))


async def skip_unless_spring_mcp_is_running(settings: Settings) -> None:
    health_url = spring_health_url(settings.spring_mcp_url)
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            response = await client.get(health_url)
    except httpx.HTTPError as exc:
        pytest.skip(f"SpringBoot MCP server is not reachable at {health_url}: {exc}")

    if response.status_code != 200:
        pytest.skip(
            f"SpringBoot MCP server health check returned {response.status_code} at {health_url}"
        )
