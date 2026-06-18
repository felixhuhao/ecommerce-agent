from ecommerce_agent.grounding.model import Authority, Grounding, GroundingSource
from ecommerce_agent.tools.analytics import (
    CUSTOMER_SPEND_SUMMARY_TOOL_NAME,
    SALES_BY_CATEGORY_TOOL_NAME,
)
from ecommerce_agent.tools.forecasting import SALES_FORECAST_TOOL_NAME
from ecommerce_agent.trace.schema import TraceEvent, TraceRecord


def test_grounding_to_dict_roundtrips() -> None:
    grounding = Grounding(
        authority=Authority.AUTHORITATIVE,
        sources=[
            GroundingSource(
                span_id="s1",
                tool_name="get_statistics",
                args_summary="{}",
                result_summary="rows",
            )
        ],
    )

    data = grounding.to_dict()

    assert data["authority"] == "authoritative"
    assert data["sources"][0]["tool_name"] == "get_statistics"
    assert data["diagnostic"] is None


def _rec(answer: str, *events: TraceEvent) -> TraceRecord:
    return TraceRecord(answer=answer, events=list(events))


def _start(name: str) -> TraceEvent:
    return TraceEvent(event_type="tool_call", name=name, phase="start", tool_call_id=name)


def _end(name: str, result: str | None = "rows", evidence: str | None = "rows") -> TraceEvent:
    return TraceEvent(
        event_type="tool_call",
        name=name,
        phase="end",
        tool_call_id=name,
        result_summary=result,
        evidence=evidence,
        args_summary="{}",
    )


def _error_end(name: str, result: str | None = "error") -> TraceEvent:
    return TraceEvent(
        event_type="tool_call",
        name=name,
        phase="end",
        status="error",
        tool_call_id=name,
        result_summary=result,
        error_message=result,
    )


def test_authoritative_when_get_statistics_fired() -> None:
    from ecommerce_agent.grounding.build import build_grounding

    rec = _rec("Total was $42,180.", _start("get_statistics"), _end("get_statistics"))

    grounding = build_grounding(rec)

    assert grounding.authority == Authority.AUTHORITATIVE
    assert [source.tool_name for source in grounding.sources] == ["get_statistics"]


def test_authoritative_requires_successful_tool_end() -> None:
    from ecommerce_agent.grounding.build import build_grounding

    start_only = _rec(
        "Top customer spend was $42,180.",
        _start(CUSTOMER_SPEND_SUMMARY_TOOL_NAME),
    )
    errored = _rec(
        "Top customer spend was $42,180.",
        _start(CUSTOMER_SPEND_SUMMARY_TOOL_NAME),
        _error_end(CUSTOMER_SPEND_SUMMARY_TOOL_NAME),
    )

    assert build_grounding(start_only).authority == Authority.UNVERIFIED
    grounding = build_grounding(errored)
    assert grounding.authority == Authority.UNVERIFIED
    assert grounding.sources == []


def test_authoritative_when_shaped_analytics_tool_fired() -> None:
    from ecommerce_agent.grounding.build import build_grounding

    rec = _rec(
        "Top customer spend was $42,180.",
        _start(CUSTOMER_SPEND_SUMMARY_TOOL_NAME),
        _end(CUSTOMER_SPEND_SUMMARY_TOOL_NAME),
        _start(SALES_BY_CATEGORY_TOOL_NAME),
        _end(SALES_BY_CATEGORY_TOOL_NAME),
    )

    grounding = build_grounding(rec)

    assert grounding.authority == Authority.AUTHORITATIVE
    assert [source.tool_name for source in grounding.sources] == [
        CUSTOMER_SPEND_SUMMARY_TOOL_NAME,
        SALES_BY_CATEGORY_TOOL_NAME,
    ]


def test_authoritative_when_inventory_fact_fired() -> None:
    from ecommerce_agent.grounding.build import build_grounding

    rec = _rec(
        "SKU-LOW-003 has 12 units against safety stock 80.",
        _start("inventory_low_stock"),
        _end("inventory_low_stock"),
    )

    grounding = build_grounding(rec)

    assert grounding.authority == Authority.AUTHORITATIVE
    assert [source.tool_name for source in grounding.sources] == ["inventory_low_stock"]


def test_authoritative_when_nl2sql_query_fired() -> None:
    from ecommerce_agent.grounding.build import build_grounding

    rec = _rec(
        "Warehouse cohort retention was 42%.",
        _start("query_readonly"),
        _end("query_readonly"),
    )

    grounding = build_grounding(rec)

    assert grounding.authority == Authority.AUTHORITATIVE
    assert [source.tool_name for source in grounding.sources] == ["query_readonly"]


def test_nl2sql_metadata_source_alone_is_not_authoritative() -> None:
    from ecommerce_agent.grounding.build import build_grounding

    rec = _rec(
        "There are 12 candidate warehouse tables.",
        _start("list_tables"),
        _end("list_tables"),
    )

    grounding = build_grounding(rec)

    assert grounding.authority == Authority.UNVERIFIED
    assert [source.tool_name for source in grounding.sources] == ["list_tables"]


def test_derived_when_execute_evidence_and_no_statistics() -> None:
    from ecommerce_agent.grounding.build import build_grounding

    rec = _rec(
        "Forecast is 1,250 units.",
        _start("stage_sales_analysis_inputs"),
        _end("stage_sales_analysis_inputs"),
        _start("execute"),
        _end("execute", result="forecast=1250", evidence="forecast=1250"),
    )

    assert build_grounding(rec).authority == Authority.DERIVED


def test_derived_when_sales_forecast_tool_fired() -> None:
    from ecommerce_agent.grounding.build import build_grounding

    rec = _rec(
        "Forecast is $1,250.",
        _start(SALES_FORECAST_TOOL_NAME),
        _end(SALES_FORECAST_TOOL_NAME),
    )

    grounding = build_grounding(rec)

    assert grounding.authority == Authority.DERIVED
    assert [source.tool_name for source in grounding.sources] == [SALES_FORECAST_TOOL_NAME]


def test_execute_without_output_is_not_derived() -> None:
    from ecommerce_agent.grounding.build import build_grounding

    rec = _rec(
        "Forecast is 1,250 units.",
        _start("stage_sales_analysis_inputs"),
        _end("stage_sales_analysis_inputs"),
        _start("execute"),
        _end("execute", result=None, evidence=None),
    )

    assert build_grounding(rec).authority == Authority.UNVERIFIED


def test_unverified_when_numeric_claim_but_no_authority_tool() -> None:
    from ecommerce_agent.grounding.build import build_grounding

    rec = _rec("I count 1,240 orders.", _start("order_query"), _end("order_query"))

    assert build_grounding(rec).authority == Authority.UNVERIFIED


def test_not_applicable_when_no_numbers_no_data_tools() -> None:
    from ecommerce_agent.grounding.build import build_grounding

    rec = _rec("Hello, how can I help?")
    grounding = build_grounding(rec)

    assert grounding.authority == Authority.NOT_APPLICABLE
    assert grounding.sources == []


def test_sources_exclude_viz_and_approval_and_filesystem() -> None:
    from ecommerce_agent.grounding.build import build_grounding

    rec = _rec(
        "Total $5.",
        _start("get_statistics"),
        _end("get_statistics"),
        _start("write_file"),
        _end("write_file"),
        _start("generate_line_chart"),
        _end("generate_line_chart"),
        _start("request_approval"),
        _end("request_approval"),
    )

    names = [source.tool_name for source in build_grounding(rec).sources]

    assert names == ["get_statistics"]


def test_numeric_claim_heuristic() -> None:
    from ecommerce_agent.grounding.build import has_numeric_claim

    assert has_numeric_claim("revenue was $1,200")
    assert has_numeric_claim("up 12%")
    assert has_numeric_claim("about 1,240 orders")
    assert has_numeric_claim("a ratio of 3.5")
    assert not has_numeric_claim("here are the top products")
    assert not has_numeric_claim("I found 5 results")


def test_fail_closed_to_unverified_on_error(monkeypatch) -> None:
    from ecommerce_agent.grounding.build import build_grounding

    rec = _rec("Total was 1,000.")
    monkeypatch.setattr(
        "ecommerce_agent.grounding.build._successful_tool_ends",
        lambda record: 1 / 0,
    )

    grounding = build_grounding(rec)

    assert grounding.authority == Authority.UNVERIFIED
    assert grounding.diagnostic == "grounding_error"
