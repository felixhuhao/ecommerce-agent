from types import SimpleNamespace

from ecommerce_agent.config import Settings
from ecommerce_agent.mcp_client import (
    READ_ONLY_SPRING_TOOLS,
    SPRING_SERVER_NAME,
    VIZ_TOOLS,
    WRITE_OR_APPROVAL_SPRING_TOOLS,
    build_mcp_connections,
    filter_spring_read_tools,
    filter_viz_tools,
    spring_headers,
    tool_names,
)


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
    settings = make_settings(
        modelscope_mcp_url="http://modelscope.example/mcp",
        python_mcp_url="http://python.example/mcp",
    )

    connections = build_mcp_connections(settings)

    assert set(connections) == {"spring", "modelscope", "python"}
    assert connections["modelscope"]["transport"] == "streamable_http"
    assert connections["python"]["url"] == "http://python.example/mcp"


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


def test_filter_viz_tools_keeps_only_allowlisted_viz_tools() -> None:
    tools = [
        SimpleNamespace(name="generate_line_chart"),
        SimpleNamespace(name="generate_bar_chart"),
        SimpleNamespace(name="generate_column_chart"),
        SimpleNamespace(name="generate_area_chart"),
        SimpleNamespace(name="some_other_modelscope_tool"),
    ]

    filtered = filter_viz_tools(tools)  # type: ignore[arg-type]

    assert tool_names(filtered) == {  # type: ignore[arg-type]
        "generate_line_chart",
        "generate_bar_chart",
        "generate_column_chart",
    }
    assert VIZ_TOOLS == frozenset(
        {
            "generate_line_chart",
            "generate_bar_chart",
            "generate_column_chart",
        }
    )
