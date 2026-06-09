import ecommerce_agent.agent as agent_module
import ecommerce_agent.agents as agents_module
from ecommerce_agent.agent import build_agent
from ecommerce_agent.agents import build_sales_analyst, sales_analyst_subagent


class _Tool:
    def __init__(self, name: str) -> None:
        self.name = name


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
        viz_tools=[_Tool("generate_line_chart")],  # type: ignore[list-item]
        backend=backend,
    )

    assert result == "ANALYST"
    assert captured["backend"] is backend
    assert [tool.name for tool in captured["tools"]] == [
        "order_query",
        "get_statistics",
        "generate_line_chart",
    ]
    assert "read-only" in captured["system_prompt"].lower()
    middleware_types = {type(middleware).__name__ for middleware in captured["middleware"]}
    assert {"ModelCallLimitMiddleware", "ToolCallLimitMiddleware"} <= middleware_types
    assert captured["kwargs"]["subagents"] == []
    assert captured["kwargs"]["skills"] == []


def test_sales_analyst_subagent_seam_shape() -> None:
    subagent = sales_analyst_subagent(
        spring_read_tools=[_Tool("order_query")],  # type: ignore[list-item]
        viz_tools=[_Tool("generate_line_chart")],  # type: ignore[list-item]
    )

    assert subagent["name"] == "sales-analyst"
    assert "description" in subagent
    assert "system_prompt" in subagent
    assert {tool.name for tool in subagent["tools"]} == {
        "order_query",
        "generate_line_chart",
    }
