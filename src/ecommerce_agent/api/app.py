import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any

from fastapi import FastAPI

from ecommerce_agent.api.sessions import router as sessions_router
from ecommerce_agent.config import Settings, get_settings
from ecommerce_agent.mcp_client import (
    MODELSCOPE_SERVER_NAME,
    PYTHON_SERVER_NAME,
    READ_ONLY_SPRING_TOOLS,
    SPRING_SERVER_NAME,
    VIZ_TOOLS,
    WRITE_OR_APPROVAL_SPRING_TOOLS,
    build_mcp_client,
    filter_spring_read_tools,
    filter_viz_tools,
    tool_names,
)
from ecommerce_agent.sessions.bus import SessionBus
from ecommerce_agent.sessions.factory import build_session_runtime
from ecommerce_agent.sessions.registry import SessionRegistry
from ecommerce_agent.threads.mongo import MongoThreadStore


def make_runtime_builder(settings: Settings):
    async def build_runtime(session_id: str):
        return await build_session_runtime(session_id, settings)

    return build_runtime


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    app.state.mcp_client = getattr(app.state, "mcp_client", None) or build_mcp_client(settings)
    app.state.thread_store = getattr(
        app.state, "thread_store", None
    ) or MongoThreadStore.from_settings(settings)
    app.state.session_bus = getattr(app.state, "session_bus", None) or SessionBus()
    app.state.background_tasks = getattr(app.state, "background_tasks", None) or set()
    app.state.session_registry = getattr(app.state, "session_registry", None) or SessionRegistry(
        build_runtime=make_runtime_builder(settings),
        idle_ttl_seconds=settings.session_idle_ttl_seconds,
        max_live_sessions=settings.max_live_sessions,
    )
    app.state.reaper_task = asyncio.create_task(_reap_loop(app))
    try:
        yield
    finally:
        reaper_task = getattr(app.state, "reaper_task", None)
        if reaper_task is not None:
            reaper_task.cancel()
            with suppress(asyncio.CancelledError):
                await reaper_task
        pending_background_tasks = list(getattr(app.state, "background_tasks", set()))
        for task in pending_background_tasks:
            task.cancel()
        if pending_background_tasks:
            await asyncio.gather(*pending_background_tasks, return_exceptions=True)
            app.state.background_tasks.clear()
        await app.state.session_registry.close_all()


async def _reap_loop(app: FastAPI) -> None:
    registry = app.state.session_registry
    try:
        while True:
            await asyncio.sleep(60)
            await registry.reap_idle()
    except asyncio.CancelledError:
        pass


def configured_mcp_servers(settings: Settings) -> list[str]:
    servers = [SPRING_SERVER_NAME]
    if settings.modelscope_mcp_url:
        servers.append(MODELSCOPE_SERVER_NAME)
    if settings.python_mcp_url:
        servers.append(PYTHON_SERVER_NAME)
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
    elif server_name == MODELSCOPE_SERVER_NAME:
        viz_tools = filter_viz_tools(tools)
        result.update(
            {
                "agent_allowed_tool_count": len(viz_tools),
                "agent_allowed_tools": sorted(tool_names(viz_tools)),
                "missing_expected_viz_tools": sorted(VIZ_TOOLS - names),
            }
        )

    return result


def create_app(
    settings: Settings | None = None,
    mcp_client: Any | None = None,
) -> FastAPI:
    app = FastAPI(title="E-Commerce Agent", lifespan=lifespan)
    app.state.settings = settings or get_settings()
    app.state.mcp_client = mcp_client
    app.state.last_trace = None
    app.state.thread_store = None
    app.state.session_bus = None
    app.state.session_registry = None
    app.state.background_tasks = None

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "app": app.state.settings.app_name,
            "environment": app.state.settings.environment,
            "configured_mcp_servers": configured_mcp_servers(app.state.settings),
            "agent_ready": app.state.session_registry is not None,
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

    app.include_router(sessions_router)
    return app


app = create_app()
