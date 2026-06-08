from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from ecommerce_agent.api.chat import router as chat_router
from ecommerce_agent.config import Settings, get_settings
from ecommerce_agent.mcp_client import (
    READ_ONLY_SPRING_TOOLS,
    SPRING_SERVER_NAME,
    WRITE_OR_APPROVAL_SPRING_TOOLS,
    build_mcp_client,
    filter_spring_read_tools,
    tool_names,
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    app.state.mcp_client = getattr(app.state, "mcp_client", None) or build_mcp_client(settings)
    app.state.agent = getattr(app.state, "agent", None)
    app.state.tool_count = 0
    yield


def configured_mcp_servers(settings: Settings) -> list[str]:
    servers = [SPRING_SERVER_NAME]
    if settings.modelscope_mcp_url:
        servers.append("modelscope")
    if settings.python_mcp_url:
        servers.append("python")
    return servers


async def probe_mcp_server(mcp_client: Any, server_name: str) -> dict[str, Any]:
    try:
        tools = await mcp_client.get_tools(server_name=server_name)
    except Exception as exc:
        return {
            "status": "unavailable",
            "error": f"{type(exc).__name__}: {exc}",
        }

    names = tool_names(tools)
    result: dict[str, Any] = {
        "status": "ok",
        "tool_count": len(tools),
        "tools": sorted(names),
    }

    if server_name == SPRING_SERVER_NAME:
        read_tools = filter_spring_read_tools(tools)
        result.update(
            {
                "agent_allowed_tool_count": len(read_tools),
                "agent_allowed_tools": sorted(tool_names(read_tools)),
                "blocked_write_or_approval_tools": sorted(names & WRITE_OR_APPROVAL_SPRING_TOOLS),
                "missing_expected_read_tools": sorted(READ_ONLY_SPRING_TOOLS - names),
            }
        )

    return result


def create_app(
    settings: Settings | None = None,
    agent: Any | None = None,
    mcp_client: Any | None = None,
) -> FastAPI:
    app = FastAPI(title="E-Commerce Agent", lifespan=lifespan)
    app.state.settings = settings or get_settings()
    app.state.agent = agent
    app.state.mcp_client = mcp_client

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "app": app.state.settings.app_name,
            "environment": app.state.settings.environment,
            "configured_mcp_servers": configured_mcp_servers(app.state.settings),
            "agent_ready": app.state.agent is not None,
            "tool_count": getattr(app.state, "tool_count", 0),
        }

    @app.get("/health/mcp")
    async def mcp_health() -> dict[str, Any]:
        servers = configured_mcp_servers(app.state.settings)
        server_results = {
            server_name: await probe_mcp_server(app.state.mcp_client, server_name)
            for server_name in servers
        }
        overall_status = (
            "ok"
            if all(result["status"] == "ok" for result in server_results.values())
            else "degraded"
        )
        return {"status": overall_status, "servers": server_results}

    app.include_router(chat_router)
    return app


app = create_app()
