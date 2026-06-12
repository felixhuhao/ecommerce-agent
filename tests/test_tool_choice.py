from types import SimpleNamespace

import pytest

from ecommerce_agent.evals.tool_choice import (
    DEFAULT_RECURSION_LIMIT,
    GET_STATISTICS_TOOL,
    ToolChoiceCase,
    ToolChoiceReport,
    aggregate,
    build_stub_sales_analyst,
    build_stub_sales_analyst_tools,
    fired_tools,
    load_tool_choice_cases,
    run_tool_choice_eval,
    score_case,
)
from ecommerce_agent.tools.staging import (
    STAGE_SALES_ANALYSIS_DESCRIPTION,
    STAGE_SALES_ANALYSIS_TOOL_NAME,
    StageSalesAnalysisInput,
)
from ecommerce_agent.trace.schema import TraceEvent, TraceRecord


def _record_with_tools(*names: str, phase: str = "start") -> TraceRecord:
    record = TraceRecord()
    for name in names:
        record.events.append(TraceEvent(event_type="tool_call", name=name, phase=phase))
    return record


def _case(
    expected_tool: str,
    forbidden_tools: list[str] | None = None,
    tags: list[str] | None = None,
) -> ToolChoiceCase:
    return ToolChoiceCase(
        id="c",
        prompt="p",
        expected_tool=expected_tool,
        forbidden_tools=forbidden_tools or [],
        tags=tags or ["aggregate"],
    )


def test_fired_tools_uses_start_events_only_and_preserves_order() -> None:
    record = TraceRecord()
    record.events.extend(
        [
            TraceEvent(event_type="tool_call", name="get_statistics", phase="end"),
            TraceEvent(event_type="tool_call", name="product_query", phase="start"),
            TraceEvent(event_type="tool_call", name="product_query", phase="start"),
            TraceEvent(event_type="tool_call", name="get_statistics", phase="start"),
        ]
    )

    assert fired_tools(record) == ["product_query", "get_statistics"]


def test_score_case_passes_expected_and_forbids_wrong_tool() -> None:
    case = _case(GET_STATISTICS_TOOL, [STAGE_SALES_ANALYSIS_TOOL_NAME])

    ok = score_case(_record_with_tools(GET_STATISTICS_TOOL), case)
    missing = score_case(_record_with_tools("product_query"), case)
    forbidden = score_case(
        _record_with_tools(GET_STATISTICS_TOOL, STAGE_SALES_ANALYSIS_TOOL_NAME), case
    )

    assert ok.passed is True
    assert missing.passed is False
    assert forbidden.passed is False


def test_score_case_marks_post_choice_and_pre_choice_errors() -> None:
    forecast = _case(STAGE_SALES_ANALYSIS_TOOL_NAME, [GET_STATISTICS_TOOL], ["forecast"])

    post_choice = score_case(
        _record_with_tools(STAGE_SALES_ANALYSIS_TOOL_NAME),
        forecast,
        raised=True,
    )
    pre_choice = score_case(TraceRecord(), forecast, raised=True)

    assert post_choice.passed is True
    assert post_choice.post_choice_error is True
    assert post_choice.errored_before_choice is False
    assert pre_choice.passed is False
    assert pre_choice.post_choice_error is False
    assert pre_choice.errored_before_choice is True


def test_aggregate_reports_accuracy_and_authority_miss_rate() -> None:
    results = [
        score_case(
            _record_with_tools(GET_STATISTICS_TOOL),
            ToolChoiceCase("a1", "p", GET_STATISTICS_TOOL, [], ["aggregate"]),
        ),
        score_case(
            _record_with_tools("order_query"),
            ToolChoiceCase("a2", "p", GET_STATISTICS_TOOL, [], ["aggregate"]),
        ),
        score_case(
            _record_with_tools(STAGE_SALES_ANALYSIS_TOOL_NAME),
            ToolChoiceCase(
                "f1",
                "p",
                STAGE_SALES_ANALYSIS_TOOL_NAME,
                [GET_STATISTICS_TOOL],
                ["forecast"],
            ),
            raised=True,
        ),
    ]

    report = aggregate(results)

    assert isinstance(report, ToolChoiceReport)
    assert report.n == 3
    assert report.passed == 2
    assert report.accuracy == pytest.approx(2 / 3)
    assert report.per_tag_accuracy["aggregate"] == pytest.approx(0.5)
    assert report.per_expected_tool_accuracy[GET_STATISTICS_TOOL] == pytest.approx(0.5)
    assert report.aggregate_authority_miss_rate == pytest.approx(0.5)
    assert report.post_choice_errors == 1
    assert report.errors_before_choice == 0


def test_load_tool_choice_cases_validates_shape(tmp_path) -> None:
    good = tmp_path / "ok.yaml"
    good.write_text(
        "- id: c1\n"
        "  prompt: p\n"
        "  expected_tool: get_statistics\n"
        "  forbidden_tools: [stage_sales_analysis_inputs]\n"
        "  tags: [aggregate]\n",
        encoding="utf-8",
    )
    assert load_tool_choice_cases(str(good))[0].expected_tool == GET_STATISTICS_TOOL

    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "- id: c1\n"
        "  prompt: p\n"
        "  expected_tool: ''\n"
        "  forbidden_tools: [stage_sales_analysis_inputs]\n"
        "  tags: [aggregate]\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="expected_tool"):
        load_tool_choice_cases(str(bad))


def test_loader_rejects_missing_or_duplicate_family_tags(tmp_path) -> None:
    bad = tmp_path / "bad-family.yaml"
    bad.write_text(
        "- id: c1\n"
        "  prompt: p\n"
        "  expected_tool: get_statistics\n"
        "  forbidden_tools: []\n"
        "  tags: [aggregate, forecast]\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="exactly one family tag"):
        load_tool_choice_cases(str(bad))


def test_default_dataset_loads_and_is_balanced() -> None:
    cases = load_tool_choice_cases()
    assert len(cases) >= 9
    assert sum("aggregate" in case.tags for case in cases) >= 3
    assert sum("forecast" in case.tags for case in cases) >= 3
    assert sum("lookup" in case.tags for case in cases) >= 2
    assert all(case.expected_tool for case in cases)
    assert all(isinstance(tool, str) for case in cases for tool in case.forbidden_tools)


def test_stub_tools_expose_required_names_and_fidelity() -> None:
    tools = build_stub_sales_analyst_tools()
    by_name = {tool.name: tool for tool in tools}

    assert GET_STATISTICS_TOOL in by_name
    assert STAGE_SALES_ANALYSIS_TOOL_NAME in by_name
    assert {"product_query", "supplier_query", "inventory_query"} <= set(by_name)

    staging = by_name[STAGE_SALES_ANALYSIS_TOOL_NAME]
    assert staging.args_schema is StageSalesAnalysisInput
    assert staging.description == STAGE_SALES_ANALYSIS_DESCRIPTION

    spring_descriptions = [
        tool.description for name, tool in by_name.items() if name != STAGE_SALES_ANALYSIS_TOOL_NAME
    ]
    assert all(description and len(description) > 40 for description in spring_descriptions)


@pytest.mark.asyncio
async def test_staging_stub_returns_real_shape() -> None:
    staging = next(
        tool
        for tool in build_stub_sales_analyst_tools()
        if tool.name == STAGE_SALES_ANALYSIS_TOOL_NAME
    )

    result = await staging.ainvoke({"order_limit": 2, "product_limit": 3})

    assert result["orders_path"] == "/workspace/orders_raw.json"
    assert result["products_path"] == "/workspace/products_raw.json"
    assert result["order_count"] == 2
    assert result["product_count"] == 3
    assert "note" in result


def test_build_stub_sales_analyst_wires_backend_none_and_stub_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ecommerce_agent.agents as agents_module
    import ecommerce_agent.models as models_module

    captured: dict = {}

    def fake_build_sales_analyst(
        model,
        *,
        spring_read_tools,
        staging_tools,
        viz_tools,
        backend,
    ):
        captured["model"] = model
        captured["spring_read_tools"] = spring_read_tools
        captured["staging_tools"] = staging_tools
        captured["viz_tools"] = viz_tools
        captured["backend"] = backend
        return "ANALYST"

    monkeypatch.setattr(models_module, "get_primary_model", lambda settings: "MODEL")
    monkeypatch.setattr(agents_module, "build_sales_analyst", fake_build_sales_analyst)

    agent = build_stub_sales_analyst(object())

    assert agent == "ANALYST"
    assert captured["model"] == "MODEL"
    assert captured["backend"] is None
    assert captured["viz_tools"] == []
    assert {tool.name for tool in captured["staging_tools"]} == {STAGE_SALES_ANALYSIS_TOOL_NAME}
    assert GET_STATISTICS_TOOL in {tool.name for tool in captured["spring_read_tools"]}


@pytest.mark.asyncio
async def test_run_tool_choice_eval_scores_success_post_choice_and_pre_choice_error() -> None:
    class FakeAnalyst:
        async def astream_events(self, inputs, *, config, version):
            text = inputs["messages"][-1]["content"]
            if text == "aggregate":
                yield {
                    "event": "on_tool_start",
                    "name": GET_STATISTICS_TOOL,
                    "data": {"input": {}},
                }
                yield {
                    "event": "on_chat_model_stream",
                    "data": {"chunk": SimpleNamespace(content="done")},
                }
            elif text == "forecast":
                yield {
                    "event": "on_tool_start",
                    "name": STAGE_SALES_ANALYSIS_TOOL_NAME,
                    "data": {"input": {}},
                }
                raise RuntimeError("no sandbox execute tool")
            else:
                raise RuntimeError("boom")

    cases = [
        ToolChoiceCase("a", "aggregate", GET_STATISTICS_TOOL, [], ["aggregate"]),
        ToolChoiceCase(
            "f",
            "forecast",
            STAGE_SALES_ANALYSIS_TOOL_NAME,
            [GET_STATISTICS_TOOL],
            ["forecast"],
        ),
        ToolChoiceCase("e", "error", "supplier_query", [], ["lookup"]),
    ]

    report = await run_tool_choice_eval(FakeAnalyst(), cases, recursion_limit=5)

    assert report.passed == 2
    assert report.post_choice_errors == 1
    assert report.errors_before_choice == 1
    assert report.cases[1].post_choice_error is True
    assert report.cases[2].errored_before_choice is True


@pytest.mark.asyncio
async def test_run_tool_choice_eval_uses_live_friendly_recursion_default() -> None:
    seen_configs: list[dict] = []

    class CapturingAgent:
        async def astream_events(self, inputs, *, config, version):
            seen_configs.append(config)
            yield {
                "event": "on_chat_model_stream",
                "data": {"chunk": SimpleNamespace(content="done")},
            }

    await run_tool_choice_eval(
        CapturingAgent(),
        [ToolChoiceCase("c", "p", GET_STATISTICS_TOOL, [], ["aggregate"])],
    )

    assert seen_configs == [{"recursion_limit": DEFAULT_RECURSION_LIMIT}]
