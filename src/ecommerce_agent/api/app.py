from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from ecommerce_agent.api.chat import router as chat_router
from ecommerce_agent.config import Settings, get_settings
from ecommerce_agent.mcp_client import build_mcp_client


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    app.state.mcp_client = build_mcp_client(settings)
    app.state.agent = getattr(app.state, "agent", None)
    app.state.tool_count = 0
    yield


def create_app(settings: Settings | None = None, agent: Any | None = None) -> FastAPI:
    app = FastAPI(title="E-Commerce Agent", lifespan=lifespan)
    app.state.settings = settings or get_settings()
    app.state.agent = agent

    @app.get("/health")
    async def health() -> dict[str, Any]:
        configured_servers = ["spring"]
        if app.state.settings.modelscope_mcp_url:
            configured_servers.append("modelscope")
        if app.state.settings.python_mcp_url:
            configured_servers.append("python")

        return {
            "status": "ok",
            "app": app.state.settings.app_name,
            "environment": app.state.settings.environment,
            "configured_mcp_servers": configured_servers,
            "agent_ready": app.state.agent is not None,
            "tool_count": getattr(app.state, "tool_count", 0),
        }

    app.include_router(chat_router)
    return app


app = create_app()
