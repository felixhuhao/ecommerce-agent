from collections.abc import Iterator
from urllib.parse import urlparse, urlunparse

import httpx
import pytest

from ecommerce_agent.config import Settings


def spring_health_url(mcp_url: str) -> str:
    parsed = urlparse(mcp_url)
    return urlunparse(parsed._replace(path="/actuator/health", params="", query="", fragment=""))


def iter_exception_tree(exc: BaseException) -> Iterator[BaseException]:
    if isinstance(exc, BaseExceptionGroup):
        for nested in exc.exceptions:
            yield from iter_exception_tree(nested)
        return

    yield exc


def spring_mcp_auth_error(exc: BaseException) -> httpx.HTTPStatusError | None:
    for nested in iter_exception_tree(exc):
        if (
            isinstance(nested, httpx.HTTPStatusError)
            and nested.response.status_code in {401, 403}
        ):
            return nested

    return None


def skip_on_spring_mcp_auth_error(exc: BaseException, settings: Settings) -> None:
    auth_error = spring_mcp_auth_error(exc)
    if auth_error is None:
        return

    pytest.skip(
        "SpringBoot MCP server is reachable, but /mcp rejected the configured "
        f"SPRING_MCP_SERVICE_TOKEN at {settings.spring_mcp_url}: {auth_error}"
    )


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


async def skip_unless_nl2sql_mcp_is_running(settings: Settings) -> None:
    if not settings.nl2sql_enabled or not settings.nl2sql_mcp_url.strip():
        pytest.skip("NL2SQL MCP is not configured")

    from ecommerce_agent.mcp_client import NL2SQL_SERVER_NAME, build_mcp_client

    try:
        await build_mcp_client(settings).get_tools(server_name=NL2SQL_SERVER_NAME)
    except Exception as exc:
        pytest.skip(f"NL2SQL MCP is not reachable at {settings.nl2sql_mcp_url}: {exc}")


async def skip_unless_mongo_is_running(settings: Settings) -> None:
    try:
        from motor.motor_asyncio import AsyncIOMotorClient
    except ImportError as exc:  # pragma: no cover
        pytest.skip(f"motor is not installed: {exc}")

    client = AsyncIOMotorClient(settings.mongo_url, serverSelectionTimeoutMS=1500)
    try:
        await client.admin.command("ping")
    except Exception as exc:
        pytest.skip(f"MongoDB is not reachable at {settings.mongo_url}: {exc}")
    finally:
        client.close()


def skip_unless_docker_available() -> None:
    try:
        import docker
    except ImportError as exc:  # pragma: no cover
        pytest.skip(f"docker SDK not installed: {exc}")

    try:
        client = docker.from_env()
        client.ping()
    except Exception as exc:
        pytest.skip(f"Docker daemon not reachable: {exc}")
