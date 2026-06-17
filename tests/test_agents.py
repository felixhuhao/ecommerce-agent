import ecommerce_agent.agent as agent_module
import ecommerce_agent.agents as agents_module
from ecommerce_agent.agent import build_agent
from ecommerce_agent.agents import (
    build_coordinator,
    build_customer_insights,
    build_inventory,
    build_monitor_cause_agent,
    build_order_manager,
    build_purchasing,
    build_sales_analyst,
    order_manager_subagent,
    sales_analyst_subagent,
)


class _Tool:
    def __init__(self, name: str) -> None:
        self.name = name


def _excluded_tools(middleware: list[object]) -> set[str]:
    for item in middleware:
        if type(item).__name__ == "_ToolExclusionMiddleware":
            return set(item._excluded)
    raise AssertionError("missing _ToolExclusionMiddleware")


def test_build_agent_threads_backend_and_slots(monkeypatch) -> None:
    captured = {}

    def fake_create_deep_agent(
        *,
        model,
        tools,
        system_prompt,
        subagents,
        middleware,
        skills,
        backend,
    ):
        captured.update(
            model=model,
            tools=tools,
            system_prompt=system_prompt,
            subagents=subagents,
            middleware=middleware,
            skills=skills,
            backend=backend,
        )
        return "AGENT"

    monkeypatch.setattr(agent_module, "create_deep_agent", fake_create_deep_agent)

    sentinel_backend = object()
    result = build_agent(
        "MODEL",  # type: ignore[arg-type]
        [_Tool("order_query")],  # type: ignore[list-item]
        system_prompt="PROMPT",
        backend=sentinel_backend,
        subagents=[],
        skills=[],
    )

    assert result == "AGENT"
    assert captured["model"] == "MODEL"
    assert [tool.name for tool in captured["tools"]] == ["order_query"]
    assert captured["system_prompt"] == "PROMPT"
    assert captured["backend"] is sentinel_backend
    assert captured["subagents"] == []
    assert captured["skills"] == []


def test_build_sales_analyst_combines_tools_and_threads_backend(monkeypatch) -> None:
    captured = {}

    def fake_build_agent(model, tools, *, system_prompt, backend, middleware=(), **kwargs):
        captured.update(
            model=model,
            tools=tools,
            system_prompt=system_prompt,
            backend=backend,
            middleware=list(middleware),
            kwargs=kwargs,
        )
        return "ANALYST"

    monkeypatch.setattr(agents_module, "build_agent", fake_build_agent)

    backend = object()
    result = build_sales_analyst(
        "MODEL",  # type: ignore[arg-type]
        spring_read_tools=[_Tool("order_query"), _Tool("get_statistics")],  # type: ignore[list-item]
        staging_tools=[_Tool("stage_sales_analysis_inputs")],  # type: ignore[list-item]
        viz_tools=[_Tool("generate_line_chart")],  # type: ignore[list-item]
        backend=backend,
    )

    assert result == "ANALYST"
    assert captured["backend"] is backend
    assert [tool.name for tool in captured["tools"]] == [
        "stage_sales_analysis_inputs",
        "order_query",
        "get_statistics",
        "generate_line_chart",
    ]
    assert "read-only" in captured["system_prompt"].lower()
    middleware_types = {type(middleware).__name__ for middleware in captured["middleware"]}
    assert {
        "ModelCallLimitMiddleware",
        "ToolCallLimitMiddleware",
        "_ToolExclusionMiddleware",
    } <= middleware_types
    assert {"task", "write_todos"} <= _excluded_tools(captured["middleware"])
    assert "execute" not in _excluded_tools(captured["middleware"])
    assert captured["kwargs"]["subagents"] == []
    assert captured["kwargs"]["skills"] == []


def test_build_order_manager_uses_approval_tools_directly(monkeypatch) -> None:
    captured = {}

    def fake_build_agent(model, tools, *, system_prompt, backend, middleware=(), **kwargs):
        captured.update(
            model=model,
            tools=tools,
            system_prompt=system_prompt,
            backend=backend,
            middleware=list(middleware),
            kwargs=kwargs,
        )
        return "ORDER_MANAGER"

    monkeypatch.setattr(agents_module, "build_agent", fake_build_agent)

    backend = object()
    result = build_order_manager(
        "MODEL",  # type: ignore[arg-type]
        order_manager_tools=[
            _Tool("product_query"),  # type: ignore[list-item]
            _Tool("request_approval"),  # type: ignore[list-item]
        ],
        backend=backend,
    )

    assert result == "ORDER_MANAGER"
    assert captured["backend"] is None
    assert [tool.name for tool in captured["tools"]] == [
        "product_query",
        "request_approval",
    ]
    assert "request_approval" in captured["system_prompt"]
    assert captured["kwargs"]["subagents"] == []
    assert captured["kwargs"]["skills"] == []
    middleware_types = {type(middleware).__name__ for middleware in captured["middleware"]}
    assert {
        "ModelCallLimitMiddleware",
        "ToolCallLimitMiddleware",
        "_ToolExclusionMiddleware",
    } <= middleware_types
    assert {"task", "write_todos", "execute", "write_file"} <= _excluded_tools(
        captured["middleware"]
    )


def test_build_purchasing_uses_procurement_tools_directly(monkeypatch) -> None:
    captured = {}

    def fake_build_agent(model, tools, *, system_prompt, backend, middleware=(), **kwargs):
        captured.update(
            model=model,
            tools=tools,
            system_prompt=system_prompt,
            backend=backend,
            middleware=list(middleware),
            kwargs=kwargs,
        )
        return "PURCHASING"

    monkeypatch.setattr(agents_module, "build_agent", fake_build_agent)

    backend = object()
    result = build_purchasing(
        "MODEL",  # type: ignore[arg-type]
        purchasing_tools=[
            _Tool("supplier_query"),  # type: ignore[list-item]
            _Tool("request_approval"),  # type: ignore[list-item]
        ],
        backend=backend,
    )

    assert result == "PURCHASING"
    assert captured["backend"] is None
    assert [tool.name for tool in captured["tools"]] == [
        "supplier_query",
        "request_approval",
    ]
    assert "purchase_order_create" in captured["system_prompt"]
    assert captured["kwargs"]["subagents"] == []
    assert captured["kwargs"]["skills"] == []
    middleware_types = {type(middleware).__name__ for middleware in captured["middleware"]}
    assert {
        "ModelCallLimitMiddleware",
        "ToolCallLimitMiddleware",
        "_ToolExclusionMiddleware",
    } <= middleware_types
    assert {"task", "write_todos", "execute", "write_file"} <= _excluded_tools(
        captured["middleware"]
    )


def test_build_inventory_threads_tools_and_backend(monkeypatch) -> None:
    captured = {}

    def fake_build_agent(model, tools, *, system_prompt, backend, middleware=(), **kwargs):
        captured.update(
            model=model,
            tools=tools,
            system_prompt=system_prompt,
            backend=backend,
            middleware=list(middleware),
            kwargs=kwargs,
        )
        return "INVENTORY"

    monkeypatch.setattr(agents_module, "build_agent", fake_build_agent)

    backend = object()
    result = build_inventory(
        "MODEL",  # type: ignore[arg-type]
        inventory_tools=[_Tool("inventory_query"), _Tool("inventory_low_stock")],  # type: ignore[list-item]
        backend=backend,
    )

    assert result == "INVENTORY"
    assert captured["backend"] is None
    assert [tool.name for tool in captured["tools"]] == [
        "inventory_query",
        "inventory_low_stock",
    ]
    assert "read-only" in captured["system_prompt"].lower()
    assert "inventory_query" in captured["system_prompt"]
    assert captured["kwargs"]["subagents"] == []
    assert captured["kwargs"]["skills"] == []
    middleware_types = {type(middleware).__name__ for middleware in captured["middleware"]}
    assert {
        "ModelCallLimitMiddleware",
        "ToolCallLimitMiddleware",
        "_ToolExclusionMiddleware",
    } <= middleware_types
    assert {"task", "write_todos", "execute", "write_file"} <= _excluded_tools(
        captured["middleware"]
    )


def test_build_customer_insights_threads_tools_without_backend(monkeypatch) -> None:
    captured = {}

    def fake_build_agent(model, tools, *, system_prompt, backend, middleware=(), **kwargs):
        captured.update(
            model=model,
            tools=tools,
            system_prompt=system_prompt,
            backend=backend,
            middleware=list(middleware),
            kwargs=kwargs,
        )
        return "CUSTOMER_INSIGHTS"

    monkeypatch.setattr(agents_module, "build_agent", fake_build_agent)

    backend = object()
    result = build_customer_insights(
        "MODEL",  # type: ignore[arg-type]
        customer_insights_tools=[_Tool("user_query"), _Tool("order_query")],  # type: ignore[list-item]
        backend=backend,
    )

    assert result == "CUSTOMER_INSIGHTS"
    assert captured["backend"] is None
    assert [tool.name for tool in captured["tools"]] == [
        "user_query",
        "order_query",
    ]
    assert "read-only" in captured["system_prompt"].lower()
    assert "user_query" in captured["system_prompt"]
    assert captured["kwargs"]["subagents"] == []
    assert captured["kwargs"]["skills"] == []
    middleware_types = {type(middleware).__name__ for middleware in captured["middleware"]}
    assert {
        "ModelCallLimitMiddleware",
        "ToolCallLimitMiddleware",
        "_ToolExclusionMiddleware",
    } <= middleware_types
    assert {"task", "write_todos", "execute", "write_file"} <= _excluded_tools(
        captured["middleware"]
    )


def test_build_monitor_cause_agent_is_read_only_without_backend(monkeypatch) -> None:
    captured = {}

    def fake_build_agent(model, tools, *, system_prompt, backend, middleware=(), **kwargs):
        captured.update(
            model=model,
            tools=tools,
            system_prompt=system_prompt,
            backend=backend,
            middleware=list(middleware),
            kwargs=kwargs,
        )
        return "MONITOR_CAUSE"

    monkeypatch.setattr(agents_module, "build_agent", fake_build_agent)

    result = build_monitor_cause_agent(
        "MODEL",  # type: ignore[arg-type]
        spring_read_tools=[_Tool("inventory_low_stock"), _Tool("get_statistics")],  # type: ignore[list-item]
    )

    assert result == "MONITOR_CAUSE"
    assert captured["backend"] is None
    assert [tool.name for tool in captured["tools"]] == ["inventory_low_stock", "get_statistics"]
    assert "read-only" in captured["system_prompt"]
    assert "approve" in captured["system_prompt"]
    assert captured["kwargs"]["subagents"] == []
    assert captured["kwargs"]["skills"] == []
    middleware_types = {type(middleware).__name__ for middleware in captured["middleware"]}
    assert "_ToolExclusionMiddleware" in middleware_types
    tool_exclusion = next(
        middleware
        for middleware in captured["middleware"]
        if type(middleware).__name__ == "_ToolExclusionMiddleware"
    )
    assert {"execute", "write_file", "task"} <= tool_exclusion._excluded


def test_sales_analyst_subagent_seam_shape() -> None:
    subagent = sales_analyst_subagent(
        spring_read_tools=[_Tool("order_query")],  # type: ignore[list-item]
        staging_tools=[_Tool("stage_sales_analysis_inputs")],  # type: ignore[list-item]
        viz_tools=[_Tool("generate_line_chart")],  # type: ignore[list-item]
    )

    assert subagent["name"] == "sales-analyst"
    assert "description" in subagent
    assert "system_prompt" in subagent
    assert {tool.name for tool in subagent["tools"]} == {
        "stage_sales_analysis_inputs",
        "order_query",
        "generate_line_chart",
    }


def test_order_manager_subagent_shape() -> None:
    subagent = order_manager_subagent(
        order_manager_tools=[
            _Tool("inventory_query"),  # type: ignore[list-item]
            _Tool("request_approval"),  # type: ignore[list-item]
        ]
    )

    assert subagent["name"] == "order-manager"
    assert "approval" in subagent["description"].lower()
    assert "request_approval" in subagent["system_prompt"]
    assert {tool.name for tool in subagent["tools"]} == {
        "inventory_query",
        "request_approval",
    }


def test_build_coordinator_has_no_business_tools_and_delegates(monkeypatch) -> None:
    captured = {}

    def fake_build_agent(model, tools, *, system_prompt, backend, middleware=(), **kwargs):
        captured.update(
            model=model,
            tools=tools,
            system_prompt=system_prompt,
            backend=backend,
            middleware=list(middleware),
            kwargs=kwargs,
        )
        return "COORDINATOR"

    monkeypatch.setattr(agents_module, "build_agent", fake_build_agent)

    backend = object()
    analyst = {"name": "sales-analyst", "tools": [_Tool("order_query")]}
    order_manager = {"name": "order-manager", "tools": [_Tool("request_approval")]}

    result = build_coordinator(
        "MODEL",  # type: ignore[arg-type]
        sales_analyst_subagent=analyst,
        order_manager_subagent=order_manager,
        backend=backend,
    )

    assert result == "COORDINATOR"
    assert captured["model"] == "MODEL"
    assert captured["tools"] == []
    assert captured["backend"] is backend
    assert captured["kwargs"]["subagents"] == [analyst, order_manager]
    assert captured["kwargs"]["skills"] == []
    assert "sales-analyst" in captured["system_prompt"]
    assert "order-manager" in captured["system_prompt"]
    middleware_types = {type(middleware).__name__ for middleware in captured["middleware"]}
    assert {"ModelCallLimitMiddleware", "ToolCallLimitMiddleware"} <= middleware_types
