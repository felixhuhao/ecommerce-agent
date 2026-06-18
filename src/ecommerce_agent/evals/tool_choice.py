from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, ConfigDict, Field

from ecommerce_agent.tools.analytics import (
    SALES_BY_CATEGORY_TOOL_NAME,
    build_sales_by_category_tool,
)
from ecommerce_agent.tools.forecasting import SALES_FORECAST_TOOL_NAME, SalesForecastInput
from ecommerce_agent.tools.staging import (
    ORDERS_RAW_PATH,
    PRODUCTS_RAW_PATH,
    STAGE_SALES_ANALYSIS_DESCRIPTION,
    STAGE_SALES_ANALYSIS_TOOL_NAME,
    StageSalesAnalysisInput,
)
from ecommerce_agent.trace.capture import capture
from ecommerce_agent.trace.schema import TraceRecord
from ecommerce_agent.trace.tools import fired_tools

_DATASET_PATH = Path(__file__).parent / "datasets" / "tool_choice.yaml"
# Tool choice is observable early; keep the live eval close to the phase-1 decision
# so missing sandbox execution does not dominate the trace.
DEFAULT_RECURSION_LIMIT = 15
GET_STATISTICS_TOOL = "get_statistics"
AUTHORITATIVE_AGGREGATE_TOOLS = frozenset({GET_STATISTICS_TOOL, SALES_BY_CATEGORY_TOOL_NAME})
FAMILY_TAGS = {"aggregate", "forecast", "lookup"}


@dataclass(frozen=True)
class ToolChoiceCase:
    id: str
    prompt: str
    expected_tool: str
    forbidden_tools: list[str]
    tags: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ToolChoiceCaseResult:
    case_id: str
    expected_tool: str
    fired_tools: list[str]
    passed: bool
    tags: list[str]
    raised: bool = False
    post_choice_error: bool = False
    errored_before_choice: bool = False


@dataclass(frozen=True)
class ToolChoiceReport:
    n: int
    passed: int
    accuracy: float
    per_tag_accuracy: dict[str, float]
    per_expected_tool_accuracy: dict[str, float]
    aggregate_authority_miss_rate: float
    post_choice_errors: int
    errors_before_choice: int
    cases: list[ToolChoiceCaseResult]


class _FlexibleArgs(BaseModel):
    model_config = ConfigDict(extra="allow")


class _GetStatisticsArgs(_FlexibleArgs):
    metric: str | None = Field(
        default=None,
        description="Backend aggregate to return, such as total sales or top sellers.",
    )
    period: str | None = Field(default=None, description="Optional time period.")
    group_by: str | None = Field(default=None, description="Optional grouping dimension.")
    limit: int | None = Field(default=None, ge=1, le=500)


class _ReadToolArgs(_FlexibleArgs):
    query: str | None = Field(default=None, description="Natural-language lookup query.")
    sku: str | None = Field(default=None, description="Optional product SKU.")
    limit: int | None = Field(default=None, ge=1, le=500)


def _validate_family(case_id: str, tags: list[str]) -> None:
    families = FAMILY_TAGS & set(tags)
    if len(families) != 1:
        raise ValueError(
            f"case {case_id!r} must carry exactly one family tag from {sorted(FAMILY_TAGS)}"
        )


def load_tool_choice_cases(path: str | None = None) -> list[ToolChoiceCase]:
    target = Path(path) if path else _DATASET_PATH
    raw = yaml.safe_load(target.read_text(encoding="utf-8")) or []
    cases: list[ToolChoiceCase] = []
    for entry in raw:
        case_id = entry.get("id")
        expected_tool = entry.get("expected_tool")
        forbidden_tools = entry.get("forbidden_tools")
        tags = list(entry.get("tags", []))
        if not isinstance(expected_tool, str) or not expected_tool.strip():
            raise ValueError(f"case {case_id!r} expected_tool must be a non-empty string")
        if not isinstance(forbidden_tools, list) or not all(
            isinstance(tool, str) for tool in forbidden_tools
        ):
            raise ValueError(f"case {case_id!r} forbidden_tools must be a list of strings")
        _validate_family(str(case_id), tags)
        cases.append(
            ToolChoiceCase(
                id=entry["id"],
                prompt=entry["prompt"],
                expected_tool=expected_tool,
                forbidden_tools=forbidden_tools,
                tags=tags,
            )
        )
    return cases


def score_case(
    record: TraceRecord,
    case: ToolChoiceCase,
    *,
    raised: bool = False,
) -> ToolChoiceCaseResult:
    tools = fired_tools(record)
    forbidden = set(case.forbidden_tools) & set(tools)
    passed = case.expected_tool in tools and not forbidden
    return ToolChoiceCaseResult(
        case_id=case.id,
        expected_tool=case.expected_tool,
        fired_tools=tools,
        passed=passed,
        tags=case.tags,
        raised=raised,
        post_choice_error=raised and passed,
        errored_before_choice=raised and case.expected_tool not in tools,
    )


def aggregate(results: list[ToolChoiceCaseResult]) -> ToolChoiceReport:
    n = len(results)
    passed = sum(1 for result in results if result.passed)

    per_tag_accuracy: dict[str, float] = {}
    for tag in {tag for result in results for tag in result.tags}:
        tagged = [result for result in results if tag in result.tags]
        per_tag_accuracy[tag] = sum(result.passed for result in tagged) / len(tagged)

    per_expected_tool_accuracy: dict[str, float] = {}
    for tool in {result.expected_tool for result in results}:
        expected = [result for result in results if result.expected_tool == tool]
        per_expected_tool_accuracy[tool] = sum(result.passed for result in expected) / len(expected)

    aggregate_results = [result for result in results if "aggregate" in result.tags]
    aggregate_authority_miss_rate = (
        sum(
            1
            for result in aggregate_results
            if AUTHORITATIVE_AGGREGATE_TOOLS.isdisjoint(result.fired_tools)
        )
        / len(aggregate_results)
        if aggregate_results
        else 0.0
    )

    return ToolChoiceReport(
        n=n,
        passed=passed,
        accuracy=passed / n if n else 0.0,
        per_tag_accuracy=per_tag_accuracy,
        per_expected_tool_accuracy=per_expected_tool_accuracy,
        aggregate_authority_miss_rate=aggregate_authority_miss_rate,
        post_choice_errors=sum(1 for result in results if result.post_choice_error),
        errors_before_choice=sum(1 for result in results if result.errored_before_choice),
        cases=results,
    )


async def _stage_sales_analysis_inputs(
    order_limit: int = 100,
    product_limit: int = 100,
) -> dict[str, Any]:
    return {
        "orders_path": ORDERS_RAW_PATH,
        "products_path": PRODUCTS_RAW_PATH,
        "order_count": min(order_limit, 4),
        "product_count": min(product_limit, 3),
        "order_limit": order_limit,
        "product_limit": product_limit,
        "note": (
            "Raw payloads were staged directly into the sandbox. Use these paths with "
            "ecommerce_analysis.load_orders_df; do not call write_file for these payloads."
        ),
    }


async def _sales_forecast(**kwargs: object) -> dict[str, Any]:
    return {
        "kind": "analytics_result",
        "metric": SALES_FORECAST_TOOL_NAME,
        "source": "sandbox",
        "status": "ok",
        "filters": dict(kwargs),
        "rows": [
            {
                "time": "2026-06",
                "value": 1200.0,
                "group": "All categories forecast",
                "is_forecast": True,
            }
        ],
        "summary": {"row_count": 1},
    }


def _get_statistics(**kwargs: object) -> dict[str, object]:
    return {
        "total_sales": 15320.75,
        "order_count": 128,
        "inventory_on_hand": 912,
        "sales_by_category": [
            {"category": "Electronics", "sales": 9240.25},
            {"category": "Audio", "sales": 6080.50},
        ],
        "top_sellers": [
            {"sku": "SKU-9", "units": 42},
            {"sku": "SKU-3", "units": 37},
        ],
        "request": dict(kwargs),
    }


_READ_FIXTURES: dict[str, list[dict[str, object]]] = {
    "product_query": [
        {"productId": 9, "sku": "SKU-9", "unitCost": 12.50, "category": "Audio"},
        {"productId": 3, "sku": "SKU-3", "unitCost": 4.00, "category": "Electronics"},
    ],
    "supplier_query": [
        {"supplierId": 7, "name": "Acme Supplies", "skus": ["SKU-9"]},
        {"supplierId": 12, "name": "Globex Wholesale", "skus": ["SKU-3"]},
    ],
    "inventory_query": [
        {"productId": 9, "sku": "SKU-9", "onHand": 40},
        {"productId": 3, "sku": "SKU-3", "onHand": 18},
    ],
    "order_query": [
        {"orderId": 1001, "createdAt": "2026-05-12", "total": 155.20},
        {"orderId": 1002, "createdAt": "2026-05-13", "total": 88.40},
    ],
    "purchase_order_query": [{"poId": 4471, "status": "open", "sku": "SKU-9"}],
}

_READ_DESCRIPTIONS: dict[str, str] = {
    "product_query": "Read product catalog rows, including SKU, category, and unit cost.",
    "supplier_query": "Read supplier records and the SKUs each supplier can provide.",
    "inventory_query": "Read current inventory snapshots for products and SKUs.",
    "order_query": "Read raw order rows for detailed time-series or cohort analysis.",
    "purchase_order_query": "Read existing purchase order records and statuses.",
}


def build_stub_sales_analyst_tools() -> list[BaseTool]:
    tools: list[BaseTool] = [
        stats_tool := StructuredTool.from_function(
            func=_get_statistics,
            name=GET_STATISTICS_TOOL,
            description=(
                "Return authoritative backend-computed commerce aggregates such as total sales, "
                "sales by category, top sellers, order counts, and inventory snapshots."
            ),
            args_schema=_GetStatisticsArgs,
        ),
        build_sales_by_category_tool(get_statistics=stats_tool),
        StructuredTool.from_function(
            coroutine=_stage_sales_analysis_inputs,
            name=STAGE_SALES_ANALYSIS_TOOL_NAME,
            description=STAGE_SALES_ANALYSIS_DESCRIPTION,
            args_schema=StageSalesAnalysisInput,
        ),
        StructuredTool.from_function(
            coroutine=_sales_forecast,
            name=SALES_FORECAST_TOOL_NAME,
            description="Return chart-ready monthly sales history and forecast rows.",
            args_schema=SalesForecastInput,
        ),
    ]
    for name, rows in _READ_FIXTURES.items():
        tools.append(
            StructuredTool.from_function(
                func=(lambda read_rows: lambda **kwargs: read_rows)(rows),
                name=name,
                description=_READ_DESCRIPTIONS[name],
                args_schema=_ReadToolArgs,
            )
        )
    return tools


def build_stub_sales_analyst(settings: Any, *, backend: Any | None = None) -> Any:
    from ecommerce_agent.agents import build_sales_analyst
    from ecommerce_agent.models import get_primary_model

    tools = build_stub_sales_analyst_tools()
    staging_tools = [tool for tool in tools if tool.name == STAGE_SALES_ANALYSIS_TOOL_NAME]
    spring_read_tools = [tool for tool in tools if tool.name != STAGE_SALES_ANALYSIS_TOOL_NAME]
    return build_sales_analyst(
        get_primary_model(settings),
        spring_read_tools=spring_read_tools,
        staging_tools=staging_tools,
        viz_tools=[],
        backend=backend,
    )


async def _run_case(
    agent: Any,
    prompt: str,
    *,
    recursion_limit: int,
) -> tuple[TraceRecord, BaseException | None]:
    record = TraceRecord()
    error: BaseException | None = None
    inputs = {"messages": [{"role": "user", "content": prompt}]}
    try:
        raw_events = agent.astream_events(
            inputs,
            config={"recursion_limit": recursion_limit},
            version="v2",
        )
        async for _ in capture(raw_events, record):
            pass
    except Exception as exc:
        error = exc
    record.finish()
    return record, error


async def run_tool_choice_eval(
    agent: Any,
    cases: list[ToolChoiceCase],
    *,
    recursion_limit: int = DEFAULT_RECURSION_LIMIT,
) -> ToolChoiceReport:
    results: list[ToolChoiceCaseResult] = []
    for case in cases:
        record, error = await _run_case(agent, case.prompt, recursion_limit=recursion_limit)
        results.append(score_case(record, case, raised=error is not None))
    return aggregate(results)
