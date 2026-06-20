from types import SimpleNamespace

from ecommerce_agent.config import Settings
from ecommerce_agent.mcp_client import (
    APPROVAL_SPRING_TOOLS,
    CHART_ARTIFACT_TOOLS,
    NL2SQL_SERVER_NAME,
    NL2SQL_TOOLS,
    ORDER_MANAGER_SPRING_TOOLS,
    READ_ONLY_SPRING_TOOLS,
    SPRING_SERVER_NAME,
    WRITE_OR_APPROVAL_SPRING_TOOLS,
    WRITE_SPRING_TOOLS,
    build_mcp_connections,
    filter_nl2sql_tools,
    filter_order_manager_tools,
    filter_spring_read_tools,
    spring_headers,
    tool_names,
)
from ecommerce_agent.tools.charting import CREATE_CHART_SPEC_TOOL_NAME


def make_settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)


def test_build_mcp_connections_uses_trusted_spring_headers() -> None:
    settings = make_settings(
        spring_mcp_url="http://spring.example/mcp",
        spring_mcp_service_token="token",
        spring_mcp_user_id="42",
        spring_mcp_session_id="session-42",
    )

    connections = build_mcp_connections(settings)

    assert set(connections) == {SPRING_SERVER_NAME}
    assert connections[SPRING_SERVER_NAME]["transport"] == "streamable_http"
    assert connections[SPRING_SERVER_NAME]["url"] == "http://spring.example/mcp"
    assert connections[SPRING_SERVER_NAME]["headers"] == {
        "X-Service-Token": "token",
        "X-User-Id": "42",
        "X-Session-Id": "session-42",
    }


def test_future_mcp_servers_are_configured_when_urls_are_present() -> None:
    settings = make_settings(python_mcp_url="http://python.example/mcp")

    connections = build_mcp_connections(settings)

    assert set(connections) == {"spring", "python"}
    assert connections["python"]["url"] == "http://python.example/mcp"


def test_nl2sql_connection_is_configured_only_when_enabled() -> None:
    disabled = make_settings(
        nl2sql_enabled=False,
        nl2sql_mcp_url="http://nl2sql.example/mcp",
        nl2sql_mcp_service_token="tok",
    )
    assert NL2SQL_SERVER_NAME not in build_mcp_connections(disabled)

    enabled = make_settings(
        nl2sql_enabled=True,
        nl2sql_mcp_url="http://nl2sql.example/mcp",
        nl2sql_mcp_service_token="tok",
    )

    connections = build_mcp_connections(enabled)

    assert connections[NL2SQL_SERVER_NAME]["transport"] == "streamable_http"
    assert connections[NL2SQL_SERVER_NAME]["url"] == "http://nl2sql.example/mcp"
    assert connections[NL2SQL_SERVER_NAME]["headers"] == {"X-Service-Token": "tok"}


def test_nl2sql_connection_uses_shared_configured_predicate() -> None:
    settings = make_settings(nl2sql_enabled=True, nl2sql_mcp_url="   ")

    assert NL2SQL_SERVER_NAME not in build_mcp_connections(settings)


def test_spring_headers_are_never_tool_parameters() -> None:
    settings = make_settings(
        spring_mcp_service_token="token",
        spring_mcp_user_id="1",
        spring_mcp_session_id="local-session",
    )

    assert spring_headers(settings) == {
        "X-Service-Token": "token",
        "X-User-Id": "1",
        "X-Session-Id": "local-session",
    }


def test_spring_headers_override_user_and_session() -> None:
    settings = make_settings(spring_mcp_service_token="tok")

    headers = spring_headers(settings, user_id="7", session_id="sess-abc")

    assert headers["X-Service-Token"] == "tok"
    assert headers["X-User-Id"] == "7"
    assert headers["X-Session-Id"] == "sess-abc"


def test_build_mcp_connections_uses_session_headers() -> None:
    settings = make_settings()

    connections = build_mcp_connections(settings, user_id="7", session_id="sess-abc")

    assert connections["spring"]["headers"]["X-Session-Id"] == "sess-abc"
    assert connections["spring"]["headers"]["X-User-Id"] == "7"


def test_filter_spring_read_tools_excludes_write_and_approval_tools() -> None:
    tools = [
        SimpleNamespace(name="inventory_query"),
        SimpleNamespace(name="request_approval"),
        SimpleNamespace(name="purchase_order_create"),
        SimpleNamespace(name="get_statistics"),
    ]

    filtered = filter_spring_read_tools(tools)  # type: ignore[arg-type]

    assert tool_names(filtered) == {"inventory_query", "get_statistics"}  # type: ignore[arg-type]
    assert WRITE_OR_APPROVAL_SPRING_TOOLS.isdisjoint(tool_names(filtered))  # type: ignore[arg-type]
    assert tool_names(filtered).issubset(READ_ONLY_SPRING_TOOLS)  # type: ignore[arg-type]


def test_filter_order_manager_tools_keeps_reads_plus_request_approval() -> None:
    tools = [
        SimpleNamespace(name="product_query"),
        SimpleNamespace(name="inventory_query"),
        SimpleNamespace(name="order_query"),
        SimpleNamespace(name="supplier_query"),
        SimpleNamespace(name="purchase_order_query"),
        SimpleNamespace(name="request_approval"),
        SimpleNamespace(name="get_statistics"),
        SimpleNamespace(name="purchase_order_create"),
        SimpleNamespace(name="purchase_order_receive"),
        SimpleNamespace(name="order_update"),
    ]

    filtered = filter_order_manager_tools(tools)  # type: ignore[arg-type]
    names = tool_names(filtered)  # type: ignore[arg-type]

    assert names == ORDER_MANAGER_SPRING_TOOLS
    assert APPROVAL_SPRING_TOOLS <= names
    assert WRITE_SPRING_TOOLS.isdisjoint(names)
    assert "request_approval" not in READ_ONLY_SPRING_TOOLS


def test_chart_artifact_tools_are_first_party_only() -> None:
    assert CHART_ARTIFACT_TOOLS == frozenset({CREATE_CHART_SPEC_TOOL_NAME})


def test_filter_nl2sql_tools_keeps_only_allowlisted_warehouse_tools() -> None:
    tools = [
        SimpleNamespace(name="list_tables"),
        SimpleNamespace(name="get_table_schema"),
        SimpleNamespace(name="query_readonly"),
        SimpleNamespace(name="explain_query"),
        SimpleNamespace(name="metric_catalog_search"),
        SimpleNamespace(name="execute_sql_unsafe"),
    ]

    filtered = filter_nl2sql_tools(tools)  # type: ignore[arg-type]

    assert tool_names(filtered) == NL2SQL_TOOLS  # type: ignore[arg-type]
