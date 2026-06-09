from datetime import timedelta
from typing import Any

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

from ecommerce_agent.config import Settings, get_settings

SPRING_SERVER_NAME = "spring"
MODELSCOPE_SERVER_NAME = "modelscope"
PYTHON_SERVER_NAME = "python"

READ_ONLY_SPRING_TOOLS: frozenset[str] = frozenset(
    {
        "product_query",
        "product_search",
        "order_query",
        "inventory_query",
        "inventory_low_stock",
        "user_query",
        "supplier_query",
        "supplier_top",
        "purchase_order_query",
        "get_statistics",
    }
)

WRITE_OR_APPROVAL_SPRING_TOOLS: frozenset[str] = frozenset(
    {
        "request_approval",
        "purchase_order_create",
        "purchase_order_receive",
        "order_update",
    }
)

VIZ_TOOLS: frozenset[str] = frozenset({"generate_visualization"})


def spring_headers(settings: Settings) -> dict[str, str]:
    return {
        "X-Service-Token": settings.spring_mcp_service_token,
        "X-User-Id": settings.spring_mcp_user_id,
        "X-Session-Id": settings.spring_mcp_session_id,
    }


def build_mcp_connections(settings: Settings | None = None) -> dict[str, dict[str, Any]]:
    settings = settings or get_settings()
    timeout = timedelta(seconds=settings.mcp_request_timeout_seconds)
    sse_read_timeout = timedelta(seconds=settings.mcp_sse_read_timeout_seconds)

    connections: dict[str, dict[str, Any]] = {
        SPRING_SERVER_NAME: {
            "transport": "streamable_http",
            "url": settings.spring_mcp_url,
            "headers": spring_headers(settings),
            "timeout": timeout,
            "sse_read_timeout": sse_read_timeout,
        }
    }

    if settings.modelscope_mcp_url:
        connections[MODELSCOPE_SERVER_NAME] = {
            "transport": "streamable_http",
            "url": settings.modelscope_mcp_url,
            "timeout": timeout,
            "sse_read_timeout": sse_read_timeout,
        }

    if settings.python_mcp_url:
        connections[PYTHON_SERVER_NAME] = {
            "transport": "streamable_http",
            "url": settings.python_mcp_url,
            "timeout": timeout,
            "sse_read_timeout": sse_read_timeout,
        }

    return connections


def build_mcp_client(settings: Settings | None = None) -> MultiServerMCPClient:
    return MultiServerMCPClient(build_mcp_connections(settings))


def filter_spring_read_tools(tools: list[BaseTool]) -> list[BaseTool]:
    return [tool for tool in tools if tool.name in READ_ONLY_SPRING_TOOLS]


def filter_viz_tools(tools: list[BaseTool]) -> list[BaseTool]:
    return [tool for tool in tools if tool.name in VIZ_TOOLS]


async def load_spring_read_tools(client: MultiServerMCPClient) -> list[BaseTool]:
    tools = await client.get_tools(server_name=SPRING_SERVER_NAME)
    return filter_spring_read_tools(tools)


async def load_modelscope_viz_tools(client: MultiServerMCPClient) -> list[BaseTool]:
    tools = await client.get_tools(server_name=MODELSCOPE_SERVER_NAME)
    return filter_viz_tools(tools)


def tool_names(tools: list[BaseTool]) -> set[str]:
    return {tool.name for tool in tools}
