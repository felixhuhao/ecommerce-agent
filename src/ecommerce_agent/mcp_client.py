from datetime import timedelta
from typing import Any

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

from ecommerce_agent.config import Settings, get_settings, nl2sql_configured
from ecommerce_agent.tools.metadata import VIZ_TOOL_NAMES, select_names

SPRING_SERVER_NAME = "spring"
MODELSCOPE_SERVER_NAME = "modelscope"
PYTHON_SERVER_NAME = "python"
NL2SQL_SERVER_NAME = "nl2sql"

# Compatibility shims derived from the single source of truth in
# tools/metadata.py. Prefer tools.metadata directly in new code; these frozensets
# are kept so existing importers (diagnostics, evals, trace) stay byte-identical.
# The per-specialist sets mirror the provider tool_tags in specialists/providers.py.
READ_ONLY_SPRING_TOOLS: frozenset[str] = select_names(frozenset({"spring.read"}))
APPROVAL_SPRING_TOOLS: frozenset[str] = select_names(frozenset({"approval.request"}))
WRITE_SPRING_TOOLS: frozenset[str] = select_names(frozenset({"spring.write"}))
WRITE_OR_APPROVAL_SPRING_TOOLS: frozenset[str] = WRITE_SPRING_TOOLS | APPROVAL_SPRING_TOOLS
# Phase B: order-manager narrowed to order status; purchase-order/supplier tools
# moved to purchasing.
ORDER_MANAGER_SPRING_TOOLS: frozenset[str] = select_names(
    frozenset({"orders.query", "approval.request"})
)
PURCHASING_SPRING_TOOLS: frozenset[str] = select_names(
    frozenset(
        {
            "products.search",
            "suppliers.query",
            "suppliers.top",
            "purchase_orders.query",
            "approval.request",
        }
    )
)
INVENTORY_SPRING_TOOLS: frozenset[str] = select_names(
    frozenset({"products.search", "inventory.query", "inventory.low_stock"})
)
CUSTOMER_INSIGHTS_SPRING_TOOLS: frozenset[str] = select_names(
    frozenset({"customers.query", "orders.query", "analytics.aggregate"})
)
VIZ_TOOLS: frozenset[str] = select_names(frozenset({"viz.chart"}))
MODELSCOPE_VIZ_TOOLS: frozenset[str] = frozenset(VIZ_TOOL_NAMES)
NL2SQL_TOOLS: frozenset[str] = select_names(
    frozenset({"warehouse.schema", "warehouse.query", "warehouse.explain", "warehouse.metric"})
)


def spring_headers(
    settings: Settings,
    *,
    user_id: str | None = None,
    session_id: str | None = None,
) -> dict[str, str]:
    return {
        "X-Service-Token": settings.spring_mcp_service_token,
        "X-User-Id": user_id or settings.spring_mcp_user_id,
        "X-Session-Id": session_id or settings.spring_mcp_session_id,
    }


def nl2sql_headers(settings: Settings) -> dict[str, str]:
    if not settings.nl2sql_mcp_service_token:
        return {}
    return {"X-Service-Token": settings.nl2sql_mcp_service_token}


def build_mcp_connections(
    settings: Settings | None = None,
    *,
    user_id: str | None = None,
    session_id: str | None = None,
) -> dict[str, dict[str, Any]]:
    settings = settings or get_settings()
    timeout = timedelta(seconds=settings.mcp_request_timeout_seconds)
    sse_read_timeout = timedelta(seconds=settings.mcp_sse_read_timeout_seconds)

    connections: dict[str, dict[str, Any]] = {
        SPRING_SERVER_NAME: {
            "transport": "streamable_http",
            "url": settings.spring_mcp_url,
            "headers": spring_headers(settings, user_id=user_id, session_id=session_id),
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

    if nl2sql_configured(settings):
        connections[NL2SQL_SERVER_NAME] = {
            "transport": "streamable_http",
            "url": settings.nl2sql_mcp_url,
            "headers": nl2sql_headers(settings),
            "timeout": timeout,
            "sse_read_timeout": sse_read_timeout,
        }

    return connections


def build_mcp_client(
    settings: Settings | None = None,
    *,
    user_id: str | None = None,
    session_id: str | None = None,
) -> MultiServerMCPClient:
    return MultiServerMCPClient(
        build_mcp_connections(settings, user_id=user_id, session_id=session_id)
    )


def filter_spring_read_tools(tools: list[BaseTool]) -> list[BaseTool]:
    return [tool for tool in tools if tool.name in READ_ONLY_SPRING_TOOLS]


def filter_order_manager_tools(tools: list[BaseTool]) -> list[BaseTool]:
    return [tool for tool in tools if tool.name in ORDER_MANAGER_SPRING_TOOLS]


def filter_purchasing_tools(tools: list[BaseTool]) -> list[BaseTool]:
    return [tool for tool in tools if tool.name in PURCHASING_SPRING_TOOLS]


def filter_inventory_tools(tools: list[BaseTool]) -> list[BaseTool]:
    return [tool for tool in tools if tool.name in INVENTORY_SPRING_TOOLS]


def filter_customer_insights_tools(tools: list[BaseTool]) -> list[BaseTool]:
    return [tool for tool in tools if tool.name in CUSTOMER_INSIGHTS_SPRING_TOOLS]


def filter_viz_tools(tools: list[BaseTool]) -> list[BaseTool]:
    return [tool for tool in tools if tool.name in MODELSCOPE_VIZ_TOOLS]


def filter_nl2sql_tools(tools: list[BaseTool]) -> list[BaseTool]:
    return [tool for tool in tools if tool.name in NL2SQL_TOOLS]


async def load_spring_read_tools(client: MultiServerMCPClient) -> list[BaseTool]:
    tools = await client.get_tools(server_name=SPRING_SERVER_NAME)
    return filter_spring_read_tools(tools)


async def load_order_manager_tools(client: MultiServerMCPClient) -> list[BaseTool]:
    tools = await client.get_tools(server_name=SPRING_SERVER_NAME)
    return filter_order_manager_tools(tools)


# Retained for optional/legacy ModelScope chart MCP diagnostics; the default
# session runtime uses the first-party create_chart_spec tool.
async def load_modelscope_viz_tools(client: MultiServerMCPClient) -> list[BaseTool]:
    tools = await client.get_tools(server_name=MODELSCOPE_SERVER_NAME)
    return filter_viz_tools(tools)


async def load_nl2sql_tools(client: MultiServerMCPClient) -> list[BaseTool]:
    tools = await client.get_tools(server_name=NL2SQL_SERVER_NAME)
    return filter_nl2sql_tools(tools)


def tool_names(tools: list[BaseTool]) -> set[str]:
    return {tool.name for tool in tools}
